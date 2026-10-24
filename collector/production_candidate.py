"""Promoted, explicitly unverified original-audio chunked archive pipeline."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import wave

from . import local_worker as worker, quality_trial as trial

followup, pilot = trial.followup, trial.pilot
PIPELINE = "original-audio-independent-30s-v1"


def cached(task, tools, work, profile_hash):
    # Reuse the completed authorized private trial without touching its files.
    for root in (work / "production-candidate", trial.WORK):
        path = root / "outputs" / worker.task_key(task) / "result.json"
        if not followup.complete(path):
            continue
        data = pilot.read_json(path)
        if (data["task"] == task and data["profile_sha256"] == profile_hash
                and data["model_sha256"] == tools["model_sha256"]
                and all(data[k] is False for k in followup.FLAGS)):
            return data, path
    return None, None


def publish_result(task, data, native_path, output):
    """Publish durable raw TXT and presentation SRT, with metadata written last."""
    output.mkdir(parents=True, exist_ok=True)
    meta, txt, srt = worker.result_paths(task, output)
    start = task.get("start_s") or 0
    # Keep the archive's established section-relative SRT coordinate system.
    segments = pilot.offset_segments(data["presentation"]["segments"], -start)
    text_data = data["text"].encode("utf-8")
    srt_data = ("\n\n".join(f"{i}\n{trial.speaker._srt_time(s['start']*1000)} --> {trial.speaker._srt_time(s['end']*1000)}\n{s['text']}"
        for i, s in enumerate(segments, 1)) + ("\n" if segments else "")).encode("utf-8")
    stats = pilot.read_json(native_path.parent / "filter.json")
    evidence = data["audio"]["owner_evidence"]
    record = dict(task_id=task["task_id"], source_id=task["source_id"], source_url=task["source_url"], kind=task["kind"],
        source_window_start_s=task.get("start_s"), source_window_end_s=task.get("end_s"),
        processing_status="transcribed" if pilot.normalize(data["text"]) else "no_speech",
        recognition_model="whisper.cpp ggml-"+worker.MODEL_NAME, pipeline=PIPELINE,
        model_sha256=data["model_sha256"], model_file="ggml-"+worker.MODEL_NAME+".bin",
        processed_audio_seconds=data["audio"]["duration"], transcription_seconds=data["seconds"],
        elapsed_seconds=data["pipeline_seconds"], retrieved_at_utc=worker.now(), source_provenance="frozen_manifest",
        recognized_words=len(data["text"].split()), speaker_status="all_speakers_with_unverified_profile_overlap",
        voice_filter={**stats, "applied_to_decoder_input":False, "usage":"post_decode_overlap_annotation"},
        raw_transcript_preserved=True, repetition_removed=False, repetition_metrics=data["metrics"],
        original_pcm_sha256=data["audio"]["pcm_sha256"], original_wav_sha256=data["audio"]["wav_sha256"],
        native_artifacts=str(native_path.relative_to(worker.REPO)) if native_path.is_relative_to(worker.REPO) else str(native_path),
        time_alignment="relative_to_downloaded_section; source cut approximate; native timestamps preserved privately",
        srt_timing="section_relative_nonoverlapping_presentation; not improved speech alignment",
        subtitle_timing_issues=trial.speaker.subtitle_timing_issues(srt_data.decode(),data["audio"]["duration"]),
        source_owner_verified=bool(evidence.get("owner_verified")), source_owner_evidence=evidence.get("proof"),
        text_sha256=hashlib.sha256(text_data).hexdigest(), srt_sha256=hashlib.sha256(srt_data).hexdigest(),
        **followup.FLAGS)
    for path, content in ((txt,text_data),(srt,srt_data)):
        temporary=path.with_name(path.name+".tmp")
        temporary.write_bytes(content)
        os.replace(temporary,path)
    worker.atomic_json(meta,record)
    return record


def process_task(task, tools, output=worker.OUTPUT, work=worker.WORK, threads=6,
                 speaker_profile=None, required_speaker_profile_hash=None):
    if threads != 6 or speaker_profile is None or not required_speaker_profile_hash:
        raise RuntimeError("Promoted candidate requires six threads and the unchanged annotation profile")
    if worker.sha256_file(speaker_profile) != required_speaker_profile_hash:
        raise RuntimeError("Speaker profile changed")
    data, path = cached(task,tools,work,required_speaker_profile_hash)
    if data is not None:
        return publish_result(task,data,path,output)
    tmpbase=work/"tmp"
    tmpbase.mkdir(parents=True,exist_ok=True)
    native_work=work/"production-candidate"
    native_work.mkdir(mode=0o700,parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=worker.task_key(task)+"-",dir=tmpbase) as td:
        directory=Path(td)
        evidence=worker.authorize(task,tools["yt_dlp"],work/"source_checks")
        argv=[tools["yt_dlp"],"--no-playlist","--no-progress","--no-part","--socket-timeout","20",
            "--retries","0","--fragment-retries","0","--extractor-retries","0",
            "--concurrent-fragments","1","--max-filesize","384M","-f","worstaudio/worst/bestaudio/best",
            "-o",str(directory/"source.%(ext)s")]
        start,end=task.get("start_s"),task.get("end_s")
        duration=end-start if start is not None else 180
        if start is not None:
            argv += ["--download-sections",f"*{start:g}-{end:g}"]
        worker.run_command([*argv,task["source_url"]],240 if start is not None else 120)
        media=sorted(directory.glob("source.*"))
        if not media:
            raise RuntimeError("Download produced no media")
        wav=directory/"original.wav"
        worker.run_command([tools["ffmpeg"],"-y","-nostdin","-hide_banner","-loglevel","error",
            "-i",str(media[0]),"-vn","-t",str(duration),"-ar","16000","-ac","1","-c:a","pcm_s16le",str(wav)],120)
        for p in media:
            p.unlink()
        with wave.open(str(wav),"rb") as audio:
            actual=audio.getnframes()/audio.getframerate()
        if actual <= 0 or start is not None and abs(actual-duration)>2:
            raise RuntimeError(f"Incomplete window: {actual}s for {duration}s")
        audio=dict(duration=actual,wav_sha256=worker.sha256_file(wav),pcm_sha256=followup.pcm_sha256(wav),owner_evidence=evidence)
        frozen=dict(profile=str(speaker_profile),profile_sha256=required_speaker_profile_hash)
        ledger=dict(deadline_unix=time.time()+max(240,duration*5))
        data=trial.process(task,wav,audio,tools,native_work,ledger,frozen)
        path=native_work/"outputs"/worker.task_key(task)/"result.json"
        return publish_result(task,data,path,output)


def main():
    os.umask(0o077)
    args=worker.parser().parse_args()
    args.speaker_filter=True  # Required for profile validation, not audio removal.
    if args.threads != 6 or args.model_name != worker.MODEL_NAME:
        raise RuntimeError("Promoted production configuration requires Turbo and six threads")
    if not (0 < args.max_hours <= 8):
        raise RuntimeError("A production session must be explicitly capped at eight hours or less")
    if args.command == "run":
        pilot.require_guards()
    return worker.execute(args,processor=process_task)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError,OSError) as exc:
        print("ERROR:",exc,file=sys.stderr)
        sys.exit(2)
