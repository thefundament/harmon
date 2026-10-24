"""Private, bounded follow-up to the completed transcription pilot."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import time
import wave

from . import benchmark_inputs as inputs, benchmark_metrics as metrics
from . import transcription_pilot as pilot

worker, speaker = pilot.worker, pilot.speaker
PILOT = pilot.WORK
WORK = worker.WORK / "quality-followup"
ATTEMPT_LIMIT = 4
MAX_SECONDS = 1800
FLAGS = dict(speaker_verified=False, training_ready=False, training_permission_verified=False)
PASSAGES = (
    dict(id="boss_context", source="fKWZvJmKia8", start=14, end=44,
         focus=[29, 33], purpose="Shift the 29s boundary earlier to supply preceding charge-attack context"),
    dict(id="introduction", source="CC9gnJoko50", start=275, end=305,
         focus=[281.48, 293.62], purpose="Investigate the excluded welcome and part-number correction"),
    dict(id="game_dialogue", source="CC9gnJoko50", start=0, end=30,
         focus=[19.8, 21.48], purpose="Compare game dialogue with speech-activity and profile decisions"),
    dict(id="paul_rudd", source="HwGaIhx_JW4", start=300, end=320,
         focus=[309.84, 312.88], purpose="User-confirmed contextual example: Paul Rudd movies"),
    dict(id="missions", source="Ar56zONY1-s", start=20, end=40,
         focus=[26, 29], purpose="User-confirmed contextual example: missions"),
)


class HashMismatch(RuntimeError):
    pass


def hashes(paths):
    return {str(p.resolve()): worker.sha256_file(p) for p in paths if p.is_file()}


def freeze(work=WORK, source=PILOT):
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = work / "manifest.json"
    if path.exists():
        frozen = pilot.read_json(path)
        pilot.verify_preserved(frozen["protected_sha256"])
        return frozen
    original = pilot.read_json(source / "manifest.json")
    pilot.verify_preserved(original["protected_sha256"])
    ledger = pilot.read_json(source / "ledger.json")
    protected = {**original["protected_sha256"], **pilot.preservation_snapshot(),
                 **hashes(source.rglob("*"))}
    rows = []
    for row in original["sources"]:
        task = row["task"]
        for config in pilot.CONFIGS:
            if not pilot.result_complete(source / "outputs" / worker.task_key(task) / config):
                raise RuntimeError("Follow-up requires the completed six-source pilot")
        if task["source_id"] in pilot.PROBLEMS:
            audio = ledger["sources"][task["task_id"]]["audio"]
            rows.append({**row, "pilot_audio": audio})
            cache = source / "source_checks" / (pilot.source_key(task) + ".json")
            if cache.exists():
                dest = work / "source_checks" / cache.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cache, dest)
    if {r["task"]["source_id"] for r in rows} != set(pilot.PROBLEMS) or len(rows) != 4:
        raise RuntimeError("Need the four fixed problem sources; no replacements")
    frozen = dict(schema=1, created_utc=worker.now(), pilot=str(source.resolve()),
                  sources=rows, render_sources=original["sources"], passages=list(PASSAGES),
                  profile=original["profile"], profile_sha256=original["profile_sha256"],
                  protected_sha256=protected, attempt_limit=ATTEMPT_LIMIT, max_seconds=MAX_SECONDS,
                  contextual_feedback=pilot.read_json(source / "USER_CONTEXT_FEEDBACK.json"),
                  hash_policy="Exact pilot WAV SHA-256 equality proves identical PCM; also record a new PCM-only digest. Pilot has no standalone PCM digest.")
    worker.atomic_json(path, frozen)
    return frozen


def presentation(segments):
    """Partition cue boundaries at SRT precision; retain each active cue verbatim.

    A cue may appear in multiple display intervals, with its index linking back
    to the untouched native cue. Invalid/zero-duration cues stay in the sidecar;
    assigning them display duration would fabricate alignment.
    """
    valid, excluded = [], []
    for i, s in enumerate(segments):
        a, b = s["start"], s["end"]
        if not math.isfinite(a + b) or a < 0 or round(b*1000) <= round(a*1000):
            excluded.append(i)
        else:
            valid.append((round(a*1000), round(b*1000), i))
    boundaries = sorted({t for a, b, _ in valid for t in (a, b)})
    result = []
    for a, b in zip(boundaries, boundaries[1:]):
        active = [i for x, y, i in valid if x <= a and b <= y]
        if active:
            result.append(dict(start=a/1000, end=b/1000, cue_indices=active,
                               text="\n".join(segments[i]["text"] for i in active)))
    return dict(segments=result, native_segments=segments, undisplayable_cue_indices=excluded,
                presentation_fix_only=True, speech_alignment_improved=False)


def complete(path):
    if not path.exists():
        return False
    data = pilot.read_json(path)
    pilot.verify_preserved({str(path.parent / name): digest
                            for name, digest in data["output_sha256"].items()})
    return True


def seal(path, data, files):
    data["output_sha256"] = {str(p.relative_to(path.parent)): worker.sha256_file(p) for p in files}
    worker.atomic_json(path, data)


def render(work, frozen):
    pilot.verify_preserved(frozen["protected_sha256"])
    for row in frozen["render_sources"]:
        task = row["task"]
        for config in pilot.CONFIGS:
            source = Path(frozen["pilot"]) / "outputs" / worker.task_key(task) / config
            dest = work / "rendered" / worker.task_key(task) / config
            if complete(dest / "result.json"):
                continue
            dest.mkdir(parents=True, exist_ok=True)
            native = pilot.read_json(source / "result.json")
            data = presentation(native["segments"])
            data.update(task_id=task["task_id"], source_id=task["source_id"], configuration=config,
                        original_result_sha256=worker.sha256_file(source / "result.json"), **FLAGS)
            txt, srt = dest / "transcript.txt", dest / "presentation.srt"
            shutil.copyfile(source / "transcript.txt", txt)
            srt.write_text("\n\n".join(
                f"{i}\n{speaker._srt_time(s['start']*1000)} --> {speaker._srt_time(s['end']*1000)}\n{s['text']}"
                for i, s in enumerate(data["segments"], 1)) + "\n")
            seal(dest / "result.json", data, [txt, srt])


def remaining(ledger):
    value = ledger["deadline_unix"] - time.time()
    if value <= 0:
        raise pilot.BudgetExpired("30-minute follow-up deadline reached")
    return value


def command(argv, ledger, timeout=240):
    return inputs.command(argv, max(.01, min(timeout, remaining(ledger))))


def reserve_attempt(ledger, task_id, path, allowed):
    if task_id not in allowed:
        raise RuntimeError("Source is outside the frozen selection")
    if task_id in ledger["sources"] or len(ledger["sources"]) >= ATTEMPT_LIMIT:
        return False
    ledger["sources"][task_id] = dict(status="attempting", started_utc=worker.now())
    worker.atomic_json(path, ledger)
    return True


def pcm_sha256(wav):
    digest = hashlib.sha256()
    with wave.open(str(wav), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise RuntimeError("Expected mono 16kHz signed 16-bit PCM")
        while block := audio.readframes(16000*30):
            digest.update(block)
    return digest.hexdigest()


def check_audio(wav, row):
    actual = worker.sha256_file(wav)
    if actual != row["pilot_audio"]["sha256"]:
        raise HashMismatch("Processed WAV differs from pilot; PCM equality cannot be established")
    with wave.open(str(wav), "rb") as f:
        duration = f.getnframes()/f.getframerate()
    if abs(duration-row["duration"]) > .001:
        raise HashMismatch("Processed duration differs from pilot")
    return dict(wav_sha256=actual, pcm_sha256=pcm_sha256(wav), duration=duration,
                pilot_wav_sha256=actual, identical_pilot_pcm=True)


def acquire(row, tools, work, ledger, frozen):
    task = row["task"]
    directory = work / "audio" / worker.task_key(task)
    wav = directory / "original.wav"
    # A hard-killed process may leave audio. Verify it before resuming; never
    # recover a failed/mismatched source with another network attempt.
    state = ledger["sources"].get(task["task_id"], {})
    if wav.exists() and state.get("status") in ("attempting", "verified"):
        return wav, check_audio(wav, row)
    allowed = {r["task"]["task_id"] for r in frozen["sources"]}
    if not reserve_attempt(ledger, task["task_id"], work / "ledger.json", allowed):
        raise RuntimeError("Source attempt consumed; no redownload or replacement")
    directory.mkdir(parents=True, exist_ok=True)
    pilot.authorize(task, tools["yt_dlp"], work / "source_checks")
    argv = [tools["yt_dlp"], "--no-playlist", "--no-progress", "--no-part",
            "--socket-timeout", "20", "--retries", "0", "--fragment-retries", "0",
            "--concurrent-fragments", "1", "--max-filesize", "384M",
            "-f", "worstaudio/worst/bestaudio/best", "-o", directory / "source.%(ext)s",
            "--download-sections", f"*{task['start_s']:g}-{task['end_s']:g}", task["source_url"]]
    command(argv, ledger)
    media = sorted(directory.glob("source.*"))
    if not media:
        raise RuntimeError("Download produced no media")
    command([tools["ffmpeg"], "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", media[0], "-vn", "-t", str(row["duration"]), "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", wav], ledger, 120)
    for p in media:
        p.unlink()
    ledger["sources"][task["task_id"]]["observed_wav_sha256"] = worker.sha256_file(wav)
    audio = check_audio(wav, row)
    ledger["sources"][task["task_id"]].update(status="verified", audio=audio)
    worker.atomic_json(work / "ledger.json", ledger)
    return wav, audio


def find_tools():
    tools = worker.find_tools()
    lock = pilot.read_json(worker.REPO / "docs/stt_benchmark/lock.json")
    turbo = next(m for m in lock["models"] if m["id"] == worker.MODEL_NAME)
    parakeet = next(m for m in lock["models"] if m["id"] == "parakeet-tdt-0.6b-v3")
    runtime = lock["runtimes"]["speech.cpp"]
    tools.update(parakeet=str(worker.REPO / parakeet["path"]),
                 speech=str(worker.REPO / runtime["executable"]))
    expected = {tools["model"]: turbo["sha256"], tools["parakeet"]: parakeet["sha256"],
                tools["speech"]: runtime["executable_sha256"]}
    pilot.verify_preserved(expected)  # Missing weights fail; never fetch models.
    tools["provenance"] = {**expected, tools["whisper"]: worker.sha256_file(tools["whisper"])}
    return tools


def union(intervals):
    result = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def complement(intervals, start, end):
    result, cursor = [], start
    for a, b in union(intervals):
        a, b = max(start, a), min(end, b)
        if b <= a:
            continue
        if a > cursor:
            result.append([cursor, a])
        cursor = max(cursor, b)
    if cursor < end:
        result.append([cursor, end])
    return result


def activity_diagnostics(wav, frozen, row):
    import webrtcvad
    pcm = speaker.read_pcm(wav)
    centroid, threshold, profile = speaker.load_profile(frozen["profile"])
    if profile["_loaded_sha256"] != frozen["profile_sha256"]:
        raise RuntimeError("Profile changed")
    voiced = []
    class RecordingVad:
        def __init__(self):
            self.vad, self.offset = webrtcvad.Vad(2), 0

        def is_speech(self, frame, rate):
            accepted = self.vad.is_speech(frame, rate)
            end = self.offset + len(frame)//2
            if accepted:
                voiced.append([self.offset/rate, end/rate])
            self.offset = end
            return accepted
    accepted, rejected = speaker.segments_with_match(pcm, centroid, threshold, vad=RecordingVad())
    def intervals(spans):
        return [dict(start=a/16000, end=b/16000, cosine=score, speaker_verified=False)
                for a, b, score in spans]
    start = row["task"]["start_s"]
    kept = [[start+a/16000, start+b/16000] for a, b, _ in accepted]
    old = pilot.read_json(Path(frozen["pilot"]) / "outputs" / worker.task_key(row["task"]) / "filter.json")
    old_kept = [[start+r["source_start_s"], start+r["source_end_s"]] for r in old["time_mapping"]]
    matching = [[a/16000, b/16000] for a, b, _ in accepted+rejected]
    return dict(raw_voiced_intervals=union(voiced), eligible_speech_intervals=union(matching),
                accepted_speaker_intervals=intervals(accepted), rejected_speaker_intervals=intervals(rejected),
                excluded_before_speaker_intervals=complement(matching, 0, len(pcm)/16000),
                threshold=threshold, profile_sha256=frozen["profile_sha256"],
                accepted_intervals_reproduce_pilot=kept == old_kept,
                coordinate_system="Processed window relative seconds; add source_window_start_s",
                source_window_start_s=start, vad_settings=dict(frame_ms=30, aggressiveness=2,
                    minimum_group_seconds=1.8, maximum_group_seconds=12, maximum_gap_seconds=.38),
                note="Raw voiced frames differ from eligible groups (short islands excluded, small gaps bridged). Exclusion before speaker matching does not prove silence.", **FLAGS)


def support(diagnostics, start, end):
    def coverage(intervals):
        return pilot.intervals_length([(max(a, start), min(b, end)) for a, b in intervals])/(end-start)
    return dict(raw_voiced_fraction=coverage(diagnostics["raw_voiced_intervals"]),
                eligible_speech_fraction=coverage(diagnostics["eligible_speech_intervals"]),
                accepted_match_fraction=coverage([(s["start"], s["end"]) for s in diagnostics["accepted_speaker_intervals"]]),
                rejected_match_fraction=coverage([(s["start"], s["end"]) for s in diagnostics["rejected_speaker_intervals"]]),
                excluded_before_speaker_fraction=coverage(diagnostics["excluded_before_speaker_intervals"]))


def energy(wav, source_start):
    import numpy as np
    pcm = np.asarray(speaker.read_pcm(wav), dtype=np.float64)/32768
    bins = []
    for i in range(0, len(pcm), 1600):
        x = pcm[i:i+1600]
        rms = float(np.sqrt(np.mean(x*x)))
        bins.append(dict(start=source_start+i/16000, end=source_start+(i+len(x))/16000,
                         rms=rms, dbfs=20*math.log10(max(rms, 1e-10)), peak=float(np.max(np.abs(x)))))
    return bins


def token_confidence(native):
    tokens = [t for s in native["transcription"] for t in s.get("tokens", [])
              if t.get("id", 50257) < 50257 and t["text"].strip() and "p" in t]
    ps = sorted(t["p"] for t in tokens)
    return dict(tokens=len(ps), mean_probability=sum(ps)/len(ps) if ps else None,
                minimum_probability=min(ps) if ps else None,
                low_probability_fraction=sum(p < .5 for p in ps)/len(ps) if ps else None,
                lowest_tokens=sorted(tokens, key=lambda t: t["p"])[:12],
                note="Whisper token probabilities are uncalibrated; no cross-model confidence equivalence. CLI does not export segment no-speech probabilities.")


def disagreement(a, b):
    # Symmetric edit distance / longer token count, bounded by 1. No reference.
    counts = metrics.score(a, b)
    length = max(counts["reference_words"], counts["hypothesis_words"])
    return dict(token_edit_distance=counts["errors"], longer_word_count=length,
                fraction=counts["errors"]/length if length else 0,
                differences=metrics.differences(a, b), reference_accuracy=False)


def excerpt(frozen, row, config, start, end):
    native = pilot.read_json(Path(frozen["pilot"]) / "outputs" / worker.task_key(row["task"]) / config / "result.json")
    segments = [s for s in native["segments"] if s["start"] < end and s["end"] > start]
    return dict(text="\n".join(s["text"] for s in segments), segments=segments,
                note="Whole overlapping cues retained; words outside this interval may be included.")


def decode(part, dest, model, tools, ledger):
    checkpoint = dest / "result.json"
    digest = pcm_sha256(part)
    if complete(checkpoint):
        record = pilot.read_json(checkpoint)
        if record["pcm_sha256"] != digest or record["provenance"] != tools["provenance"]:
            raise RuntimeError("Decode inputs changed on resume")
        return record
    dest.mkdir(parents=True, exist_ok=True)
    tick = time.monotonic()
    if model == "turbo":
        prefix = dest / "native"
        argv = [tools["whisper"], "-m", tools["model"], "-f", part, "-t", "6", "-l", "en",
                "--max-context", "0", "-ojf", "-otxt", "-osrt", "-of", prefix]
        out, err = command(argv, ledger)
        raw = pilot.read_json(prefix.with_suffix(".json"))
        segments = [dict(start=s["offsets"]["from"]/1000, end=s["offsets"]["to"]/1000,
                         text=s["text"]) for s in raw["transcription"]]
        text = prefix.with_suffix(".txt").read_text()
        confidence = token_confidence(raw)
        if "using Vulkan" not in err and "using GPU" not in err:
            raise RuntimeError("Whisper did not confirm GPU backend")
    else:
        argv = [tools["speech"], "asr", tools["parakeet"], "--device", "gpu", "--threads", "6",
                "--language", "en", "--format", "json", "--timestamps", part]
        out, err = command(argv, ledger)
        records = [json.loads(line) for line in out.splitlines() if line.strip()]
        if len(records) != 1 or Path(records[0]["file"]) != part or records[0].get("stop") != "complete":
            raise RuntimeError("Parakeet returned incomplete/mismatched input")
        raw = records[0]
        worker.atomic_json(dest / "native.json", raw)
        text, segments = raw["text"], raw.get("segments", [])
        confidence = dict(note="Parakeet confidence not calibrated against Whisper; retain native tokens.")
    (dest / "stdout.txt").write_text(out)
    (dest / "stderr.txt").write_text(err)
    record = dict(model=model, pcm_sha256=digest, text=text, native_segments=segments, confidence=confidence,
                  seconds=time.monotonic()-tick, provenance=tools["provenance"], command=[str(a) for a in argv], **FLAGS)
    seal(checkpoint, record, [p for p in dest.iterdir() if p.is_file() and p != checkpoint])
    return record


def process(row, wav, audio, tools, work, ledger, frozen):
    task = row["task"]
    dest = work / "diagnostics" / worker.task_key(task)
    dest.mkdir(parents=True, exist_ok=True)
    diagnostic_path = dest / "result.json"
    if complete(diagnostic_path):
        diagnostics = pilot.read_json(diagnostic_path)
        if diagnostics["audio"] != audio:
            raise RuntimeError("Diagnostic audio changed on resume")
    else:
        remaining(ledger)
        diagnostics = activity_diagnostics(wav, frozen, row)
        diagnostics["audio"] = audio
        seal(diagnostic_path, diagnostics, [])
    for passage in frozen["passages"]:
        if passage["source"] != task["source_id"]:
            continue
        directory = work / "passages" / passage["id"]
        if complete(directory / "result.json"):
            continue
        a, b = passage["start"], passage["end"]
        source_start = task["start_s"]
        if a < source_start or b > source_start+audio["duration"]:
            raise RuntimeError("Passage outside frozen processed source window")
        part = wav.with_name("passage.wav")
        try:
            pilot.extract(wav, part, a-source_start, b-source_start)
            decoded = {m: decode(part, directory / m, m, tools, ledger) for m in ("turbo", "parakeet")}
            if decoded["turbo"]["pcm_sha256"] != decoded["parakeet"]["pcm_sha256"]:
                raise RuntimeError("Models received different PCM")
            for value in decoded.values():
                value["source_segments"] = pilot.offset_segments(value["native_segments"], a)
                value["repetition"] = pilot.diagnostics(value["text"], value["source_segments"], a, b-a, b-a, b-a, 0)
            original = excerpt(frozen, row, "chunked_original", a, b)
            filtered = excerpt(frozen, row, "chunked_filtered", a, b)
            fa, fb = passage["focus"]
            evidence = dict(passage=passage, audio=audio, pcm_sha256=pcm_sha256(part), models=decoded,
                speech_support=support(diagnostics, a-source_start, b-source_start),
                focus_speech_support=support(diagnostics, fa-source_start, fb-source_start),
                energy_100ms=energy(part, a), pilot_original=original, pilot_filtered=filtered,
                model_disagreement=disagreement(decoded["turbo"]["text"], decoded["parakeet"]["text"]),
                proposed_corrections=[], **FLAGS)
            seal(directory / "result.json", evidence,
                 [directory / m / "result.json" for m in decoded])
            print("PASSAGE", passage["id"], f"disagreement={evidence['model_disagreement']['fraction']:.1%}", flush=True)
        finally:
            part.unlink(missing_ok=True)


def audit(work, frozen, ledger):
    """Check private outputs against protected originals without loading models."""
    pilot.verify_preserved(frozen["protected_sha256"])
    allowed = {r["task"]["task_id"] for r in frozen["sources"]}
    if len(ledger.get("sources", {})) > ATTEMPT_LIMIT or not set(ledger.get("sources", {})) <= allowed:
        raise RuntimeError("Invalid follow-up source ledger")
    def check_flags(data):
        if isinstance(data, dict):
            if any(data.get(key, False) is not False for key in FLAGS):
                raise RuntimeError("Speaker/training verification flag changed")
            for value in data.values():
                check_flags(value)
        elif isinstance(data, list):
            for value in data:
                check_flags(value)
    rendered_count = 0
    for row in frozen["render_sources"]:
        check_flags(row["task"])
        for config in pilot.CONFIGS:
            directory = work / "rendered" / worker.task_key(row["task"]) / config
            if not complete(directory / "result.json"):
                continue
            source = Path(frozen["pilot"]) / "outputs" / worker.task_key(row["task"]) / config
            native = pilot.read_json(source / "result.json")
            check_flags(native)
            data = pilot.read_json(directory / "result.json")
            check_flags(data)
            expected = presentation(native["segments"])
            if any(data[key] != value for key, value in expected.items()):
                raise RuntimeError("Presentation changed source cues/text/coverage")
            if (directory / "transcript.txt").read_bytes() != (source / "transcript.txt").read_bytes():
                raise RuntimeError("Original transcript text changed")
            srt = pilot.parse_srt((directory / "presentation.srt").read_text())
            if srt != [{key: s[key] for key in ("start", "end", "text")} for s in data["segments"]]:
                raise RuntimeError("Rendered subtitle serialization changed text/times")
            if any(a["end"] > b["start"] for a, b in zip(srt, srt[1:])):
                raise RuntimeError("Rendered subtitles still overlap")
            rendered_count += 1
    passage_count, decoder_count, reproduced = 0, 0, []
    for row in frozen["sources"]:
        path = work / "diagnostics" / worker.task_key(row["task"]) / "result.json"
        if complete(path):
            diagnostics = pilot.read_json(path)
            check_flags(diagnostics)
            if diagnostics["profile_sha256"] != frozen["profile_sha256"]:
                raise RuntimeError("Diagnostic profile differs from pilot")
            reproduced.append(diagnostics["accepted_intervals_reproduce_pilot"])
    for passage in frozen["passages"]:
        directory = work / "passages" / passage["id"]
        if not complete(directory / "result.json"):
            continue
        data = pilot.read_json(directory / "result.json")
        check_flags(data)
        row = next(r for r in frozen["sources"] if r["task"]["source_id"] == passage["source"])
        if data["audio"] != ledger["sources"][row["task"]["task_id"]]["audio"] or data["audio"]["wav_sha256"] != row["pilot_audio"]["sha256"]:
            raise RuntimeError("Passage audio differs from verified pilot input")
        for model in ("turbo", "parakeet"):
            if not complete(directory / model / "result.json"):
                raise RuntimeError("Missing model checkpoint")
            value = data["models"][model]
            check_flags(value)
            if value["pcm_sha256"] != data["pcm_sha256"] or value["provenance"] != ledger["provenance"]:
                raise RuntimeError("Models did not receive identical PCM/runtime")
            if value["source_segments"] != pilot.offset_segments(value["native_segments"], passage["start"]):
                raise RuntimeError("Incorrect passage source offset")
            decoder_count += 1
        if data["model_disagreement"] != disagreement(data["models"]["turbo"]["text"], data["models"]["parakeet"]["text"]):
            raise RuntimeError("Disagreement measurement changed")
        passage_count += 1
    media = [str(p.relative_to(work)) for p in work.rglob("*") if p.is_file()
             and p.suffix.lower() in (".wav", ".mp3", ".mp4", ".webm", ".m4a", ".opus", ".ogg", ".flac", ".part")]
    tests = work / "tests.txt"
    match = pilot.re.search(r"Ran (\d+) tests.*\nOK\s*$", tests.read_text(), pilot.re.S) if tests.exists() else None
    guards = [pilot.read_json(p) for p in sorted(work.glob("guard-*.memory.json"))]
    validation = dict(rendered_outputs=rendered_count, completed_passages=passage_count, completed_decodes=decoder_count,
        protected_files=len(frozen["protected_sha256"]), protected_inputs_unchanged=True,
        profile_sha256=frozen["profile_sha256"], speaker_and_training_flags_false=True,
        accepted_intervals_reproduce_pilot=bool(reproduced) and all(reproduced),
        source_attempts=len(ledger.get("sources", {})), temporary_audio_removed=not media,
        remaining_media=media, tests_passed=int(match[1]) if match else None,
        memory_guards=guards)
    if (work / "resume-verification.json").exists():
        validation["resume"] = pilot.read_json(work / "resume-verification.json")
    worker.atomic_json(work / "validation.json", validation)
    return validation


def report(work, frozen, ledger):
    pilot.verify_preserved(frozen["protected_sha256"])
    validation = audit(work, frozen, ledger)
    rendered = [pilot.read_json(p) for p in sorted((work / "rendered").glob("*/*/result.json")) if complete(p)]
    original = [r for r in rendered if r["configuration"] == "chunked_original"]
    lines = ["# Transcription quality follow-up", "",
        "Private follow-up; production defaults unchanged and archive remains stopped. No human review is required to finish this investigation.", "",
        f"Presentation: {len(rendered)}/18 saved outputs rendered, including {len(original)}/6 original-audio candidates. "
        "Overlapping cues are split at their boundaries and active text is displayed together, retaining order and duplicates. "
        "This repairs subtitle presentation, not speech alignment. Original TXT is copied verbatim; original cues/timestamps remain in each sidecar. "
        f"{sum(len(r['undisplayable_cue_indices']) for r in rendered)} zero/invalid cues stay in sidecars without invented display time.", "",
        "User-confirmed contextual examples remain **Paul Rudd movies** over ball ride movies and **missions** over motions. "
        "Unusual or rambling speech is retained. These examples do not verify complete transcripts, audio accuracy, or speaker identity.", ""]
    assessments = []
    for passage in frozen["passages"]:
        directory = work / "passages" / passage["id"]
        path = directory / "result.json"
        lines += [f"## {passage['source']} {passage['start']}–{passage['end']}s ({passage['id']})", ""]
        if not complete(path):
            row = next(r for r in frozen["sources"] if r["task"]["source_id"] == passage["source"])
            state = ledger.get("sources", {}).get(row["task"]["task_id"], {})
            lines += ["Unavailable: " + state.get("error", ledger.get("stop_reason", "not yet processed")), ""]
            continue
        data = pilot.read_json(path)
        for model in ("turbo", "parakeet"):
            model_path = directory / model / "result.json"
            complete(model_path)
            value = data["models"][model]
            text = " ".join(value["text"].split())
            if len(text) > 300:
                text = text[:300].rsplit(" ", 1)[0] + "…"
            lines += [f"{model.capitalize()}: {text}", ""]
        support_data = data["focus_speech_support"]
        c = data["models"]["turbo"]["confidence"]
        mean = c["mean_probability"]
        mean_text = f"{mean:.3f}" if mean is not None else "unavailable"
        low = c["low_probability_fraction"]
        low_text = f"{low:.1%}" if low is not None else "unavailable"
        repetition = [data["models"][m]["repetition"]["repeated_5gram_excess"] for m in ("turbo", "parakeet")]
        lines += [f"Normalized model disagreement {data['model_disagreement']['fraction']:.1%}; repeated five-gram excess Turbo/Parakeet {repetition[0]}/{repetition[1]}. "
                  f"Turbo mean token probability {mean_text}, tokens below 0.5: {low_text}. "
                  f"At the fixed focus interval {passage['focus'][0]}–{passage['focus'][1]}s: raw voiced frames {support_data['raw_voiced_fraction']:.1%}, "
                  f"eligible groups {support_data['eligible_speech_fraction']:.1%}, accepted match {support_data['accepted_match_fraction']:.1%}, "
                  f"rejected match {support_data['rejected_match_fraction']:.1%}, excluded before matching {support_data['excluded_before_speaker_fraction']:.1%}.", ""]
        if support_data["rejected_match_fraction"] > .5:
            explanation = "Most of the focus interval reached voice matching and was rejected; the exclusion is primarily a profile-match decision. This does not identify the speaker."
        elif support_data["excluded_before_speaker_fraction"] > .5:
            explanation = "Most of the focus interval was excluded before voice matching by speech-activity/group-duration rules. This does not prove silence."
        else:
            explanation = "Most of the focus interval was accepted by the profile; wording differences are compatible with decoding/context effects, without verifying the words or speaker."
        if passage["id"] == "boss_context":
            explanation += " The shifted 14–44s input moves the old 29s chunk boundary into its interior."
            texts = [" ".join(metrics.normalize(data["models"][m]["text"])) for m in ("turbo", "parakeet")]
            if all("charge attacks" in t and "second form" in t for t in texts):
                explanation += " Both new alternatives discuss charge attacks and a second form, replacing the pilot's 'dry order'/'second floor' wording. This supports a boundary/context sensitivity explanation."
            if all(t.count("charge attacks") == 2 for t in texts):
                explanation += " Both alternatives repeat the charge-attack phrase twice (below the three-occurrence repetition flag)."
            explanation += " The intervening wording still differs. Retain all versions; fluent wording alone cannot settle the passage."
        elif passage["id"] == "introduction":
            texts = [" ".join(metrics.normalize(data["models"][m]["text"])) for m in ("turbo", "parakeet")]
            if all("welcome" in t and "part nine" in t and "part 10" in t for t in texts):
                explanation += " Both models reproduce the welcome and part-nine/part-ten correction, supporting the original-audio reading."
            explanation += " Wrong-speaker exclusion versus missed target speech remains unresolved."
        elif passage["id"] == "game_dialogue":
            turbo_text = " ".join(metrics.normalize(data["models"]["turbo"]["text"]))
            parakeet_text = " ".join(metrics.normalize(data["models"]["parakeet"]["text"]))
            if "trident of atlantis" in parakeet_text and "trident of atlantis" not in turbo_text:
                explanation += " Parakeet includes 'trident of Atlantis', also present in the pilot's filtered output, while Turbo omits it on this original-audio input. The accepted speech support points to decoder sensitivity for this candidate omission, rather than removal by the speaker gate."
            explanation += " The extent of game dialogue versus creator speech remains unverified."
        elif passage["id"] in ("paul_rudd", "missions"):
            if passage["id"] == "paul_rudd":
                for token in c["lowest_tokens"]:
                    if token["text"].strip().casefold() == "paul":
                        explanation += f" Turbo assigns the Paul token only {token['p']:.3f} probability despite a much higher passage mean."
                explanation += " Preserve the user-confirmed Paul Rudd reading as a contextual annotation; the differing name readings remain acoustically uncertain."
            else:
                words = {m: metrics.normalize(data["models"][m]["text"]) for m in ("turbo", "parakeet")}
                if "motions" in words["turbo"] and "motion" in words["parakeet"]:
                    explanation += " Turbo says motions and Parakeet says motion, while the pilot's original chunks said missions. This contradicts the user-confirmed contextual preference and shows that model agreement and window changes can still favor an unsuitable reading."
                explanation += " Preserve missions as a contextual annotation. The models' different endings also remain unverified."
        explanation += " Exact word accuracy and speaker identity remain unresolved."
        lines += [explanation, ""]
        assessments.append(dict(id=passage["id"], explanation=explanation, model_disagreement=data["model_disagreement"], focus_support=support_data))
    verified = sum(s.get("audio", {}).get("identical_pilot_pcm", False) for s in ledger.get("sources", {}).values())
    lines += ["## Assessment and recommendation", "",
        "Use the presentation renderer for this private candidate. Independent original-audio chunks remain the stronger diagnostic candidate than compacted filtered audio in the pilot, "
        "but this follow-up establishes neither reference accuracy nor verified creator-only speech. Keep production defaults and the profile unchanged. "
        "Retain model alternatives as annotations and preserve unusual speech; do not promote or automatically correct transcripts. "
        "Model agreement, confidence, energy and speech activity are supporting evidence, not ground truth. Raw voiced frames can include game dialogue, music or other voices; energy alone is not speech. "
        "Confidence is uncalibrated and not comparable across models; the installed Whisper CLI exports token probabilities but no segment no-speech probability.", "",
        "Disagreement is normalized token Levenshtein distance divided by the longer transcript length (zero for two empty transcripts); it is not WER against a reference. "
        "Saved pilot excerpts retain entire intersecting cues and may extend outside selected passages. Full native diagnostics, source-offset segments, 100ms energy and "
        "accepted/rejected/eligible/raw speech intervals are saved privately. Token/native times remain decoder estimates; zero-duration and stretched timings are retained.", "",
        f"Acquisition: {len(ledger.get('sources', {}))}/4 attempts consumed; {verified}/4 WAV hashes match the pilot. " + frozen["hash_policy"],
        "Failed or mismatched sources get no retries or replacements. The first run sets one 30-minute deadline across resumptions. "
        "Completed checkpoints are reused and verified; attempted sources are never reacquired. Temporary audio is deleted after every source and at exit.", "",
        f"Integrity: {len(frozen['protected_sha256'])} protected file hashes unchanged; speaker/training verification flags remain false. "
        f"Temporary audio removed: {not (work/'audio').exists()}. No models downloaded, profile recalibrated, archive started, outputs published or production defaults changed."]
    if validation["tests_passed"] is not None:
        lines += ["", f"Verification: {validation['tests_passed']} repository tests passed; {validation['completed_passages']}/5 passages and {validation['completed_decodes']}/10 model outputs audited; "
                  f"accepted intervals reproduce the pilot: {validation['accepted_intervals_reproduce_pilot']}."]
    guards = validation["memory_guards"]
    if guards:
        lines += [f"Guarded runs total {sum(g['elapsed_seconds'] for g in guards):.1f}s; peak combined process/GPU allocation {max(g['peak_combined_bytes'] for g in guards)/1024**3:.2f} GiB. "
                  f"All guard exits successful: {all(g['child_returncode'] == 0 and g['stop_reason'] is None for g in guards)}."]
    if validation.get("resume"):
        resume = validation["resume"]
        lines += [f"Live resume: {resume['new_source_attempts']} new source attempts; {resume['unchanged_output_files']} saved outputs retained with identical hashes and modification times."]
    annotations = dict(contextual_examples=frozen["contextual_feedback"]["confirmed_readings"],
        assessments=assessments, transcript_rewritten=False, **FLAGS)
    worker.atomic_json(work / "annotations.json", annotations)
    worker.atomic_json(work / "summary.json", dict(assessments=assessments, rendered_outputs=len(rendered),
        attempts=len(ledger.get("sources", {})), hash_verified_sources=verified,
        protected_files=len(frozen["protected_sha256"]), protected_inputs_unchanged=True, **FLAGS))
    (work / "REPORT.md").write_text("\n".join(lines)+"\n")


def run(work=WORK):
    pilot.require_guards()
    frozen = freeze(work)
    render(work, frozen)
    path = work / "ledger.json"
    ledger = pilot.read_json(path) if path.exists() else dict(created_utc=worker.now(),
        deadline_unix=time.time()+MAX_SECONDS, sources={})
    allowed = {r["task"]["task_id"] for r in frozen["sources"]}
    if len(ledger["sources"]) > ATTEMPT_LIMIT or not set(ledger["sources"]) <= allowed:
        raise RuntimeError("Invalid four-source attempt ledger")
    worker.atomic_json(path, ledger)
    def interrupt(signum, frame):
        raise pilot.BudgetExpired("Follow-up stopped by signal " + str(signum))
    old = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    try:
        pending = [r for r in frozen["sources"] if any(not complete(work / "passages" / p["id"] / "result.json")
            for p in frozen["passages"] if p["source"] == r["task"]["source_id"])]
        if not pending:
            return
        signal.setitimer(signal.ITIMER_REAL, remaining(ledger))
        tools = find_tools()
        if "provenance" in ledger and ledger["provenance"] != tools["provenance"]:
            raise RuntimeError("Installed models/runtime changed on resume")
        ledger["provenance"] = tools["provenance"]
        worker.atomic_json(path, ledger)
        for row in pending:
            remaining(ledger)
            task = row["task"]
            state = ledger["sources"].get(task["task_id"], {})
            # A graceful exit deletes audio; keep its finished results and report
            # incomplete passages unavailable, without attempting another download.
            if state and (state.get("status") == "failed" or not (work / "audio" / worker.task_key(task) / "original.wav").exists()):
                state.setdefault("error", "Attempt consumed and temporary audio removed; unfinished work unavailable")
                continue
            try:
                wav, audio = acquire(row, tools, work, ledger, frozen)
                process(row, wav, audio, tools, work, ledger, frozen)
                ledger["sources"][task["task_id"]]["status"] = "complete"
            except pilot.BudgetExpired:
                raise
            except Exception as exc:
                state = ledger["sources"].setdefault(task["task_id"], {})
                state.update(status="failed", error=str(exc), hash_mismatch=isinstance(exc, HashMismatch))
                print("FAILED", task["task_id"], str(exc), flush=True)
            finally:
                shutil.rmtree(work / "audio" / worker.task_key(task), ignore_errors=True)
                worker.atomic_json(path, ledger)
    except pilot.BudgetExpired as exc:
        ledger["stop_reason"] = str(exc)
        print(str(exc), flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for s, handler in old.items():
            signal.signal(s, handler)
        shutil.rmtree(work / "audio", ignore_errors=True)
        pilot.verify_preserved(frozen["protected_sha256"])
        ledger["last_exit_utc"] = worker.now()
        worker.atomic_json(path, ledger)
        report(work, frozen, ledger)


@contextlib.contextmanager
def locks(work):
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (worker.WORK / "worker.lock").open("a") as archive, (work / "followup.lock").open("a") as followup:
        for handle in (archive, followup):
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "run", "report"))
    args = parser.parse_args()
    os.umask(0o077)
    with locks(WORK):
        try:
            if args.action == "run":
                run()
            else:
                frozen = freeze()
                if args.action == "render":
                    render(WORK, frozen)
                ledger = pilot.read_json(WORK / "ledger.json") if (WORK / "ledger.json").exists() else dict(sources={})
                report(WORK, frozen, ledger)
        finally:
            # Also cover guard/setup/protected-input failures before run's own
            # cleanup scope. Only clean while holding both exclusive locks.
            shutil.rmtree(WORK / "audio", ignore_errors=True)


if __name__ == "__main__":
    main()
