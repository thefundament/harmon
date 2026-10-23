#!/usr/bin/env python3
"""Finite, restartable local archive worker. No discovery, API keys or hosted compute."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import wave
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from source_gate import authorize, SourceRejected

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "corpus/history/manifest.jsonl"
OUTPUT = REPO / "corpus/local_transcripts"
WORK = REPO / ".harmon-work"
ALLOWED = {"www.twitch.tv", "twitch.tv", "clips.twitch.tv",
           "youtube.com", "www.youtube.com", "youtu.be"}
KINDS = {"clip", "vod", "youtube"}
MODEL_NAME = "small.en"
SPEECH_RE = re.compile(r"\[(?:BLANK_AUDIO|MUSIC|SILENCE|APPLAUSE)\]", re.I)
MAX_ATTEMPTS = 3

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def task_key(task):
    return task["kind"] + "_" + hashlib.sha256(task["task_id"].encode()).hexdigest()[:20]

def load_manifest(path=PLAN):
    if not path.is_file():
        raise RuntimeError("Missing frozen worklist: " + str(path))
    tasks, ids = [], set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            task = json.loads(line)
            kind, url, task_id = task["kind"], task["source_url"], task["task_id"]
            if kind not in KINDS or not isinstance(url, str) or not isinstance(task_id, str):
                raise ValueError("invalid kind/url/id")
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED:
                raise ValueError("disallowed source host")
            if task_id in ids:
                raise ValueError("duplicate task id")
            ids.add(task_id)
            start, end = task.get("start_s"), task.get("end_s")
            if kind == "clip":
                if start is not None or end is not None:
                    raise ValueError("clip has time window")
            else:
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    raise ValueError("long-form tasks require a complete time window")
                if not (0 <= start < end <= 24*3600 or kind == "vod" and 0 <= start < end <= 72*3600):
                    raise ValueError("invalid time window")
                if end-start > 1200:
                    raise ValueError("time window exceeds 20-minute safety limit")
            tasks.append(task)
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Invalid manifest at line {n}: {exc}") from exc
    return tasks

def load_journal(path):
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (ValueError, OSError):
        raise RuntimeError(f"Corrupt journal at {path}. Back it up and repair it; cannot safely overwrite.")

def result_paths(task, output=OUTPUT):
    stem = task_key(task)
    return output / (stem + ".metadata.json"), output / (stem + ".txt"), output / (stem + ".srt")

def completed(task, output=OUTPUT):
    meta, txt, srt = result_paths(task, output)
    if not meta.is_file() or not txt.is_file() or not srt.is_file():
        return False
    try:
        record=json.loads(meta.read_text(encoding="utf-8"))
        return (record.get("task_id")==task["task_id"]
                and record.get("processing_status") in ("transcribed", "no_speech")
                and hashlib.sha256(txt.read_bytes()).hexdigest() == record.get("text_sha256")
                and hashlib.sha256(srt.read_bytes()).hexdigest() == record.get("srt_sha256"))
    except (ValueError, OSError):
        return False

def eligible(task, journal, output, include_existing=False, retry_exhausted=False, clock=None):
    if completed(task, output):
        return False, "completed"
    if task.get("state")=="asr_completed_unreviewed" and not include_existing:
        return False, "existing_legacy_clip"
    state=journal.get(task["task_id"], {})
    if state.get("source_rejected"):
        return False, "source_rejected"
    attempts=int(state.get("attempts", 0))
    if attempts>=MAX_ATTEMPTS and not retry_exhausted:
        return False, "exhausted_retries"
    next_time=float(state.get("retry_after_unix", 0))
    if (clock if clock is not None else time.time()) < next_time:
        return False, "deferred_retry"
    return True, "pending"

def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()

def run_command(argv, timeout, cwd=None):
    proc = None
    try:
        proc = subprocess.Popen([str(a) for a in argv], cwd=cwd, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode:
            message=(stderr or stdout)[-450:].replace("\n", " ").strip()
            raise RuntimeError(f"{Path(str(argv[0])).name} failed ({proc.returncode}): {message}")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{Path(str(argv[0])).name} timed out after {timeout}s") from exc
    except OSError as exc:
        raise RuntimeError(f"{Path(str(argv[0])).name} could not start: {exc}") from exc
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()

def find_tools(model_dir=None, whisper_cli=None, yt_dlp=None, ffmpeg=None, model_name=MODEL_NAME):
    tools_dir=REPO / ".harmon-tools"
    model=Path(model_dir) if model_dir else tools_dir / "whisper.cpp" / "models" / f"ggml-{model_name}.bin"
    exe="whisper-cli.exe" if os.name=="nt" else "whisper-cli"
    compiled=(tools_dir/"whisper.cpp"/"build"/"bin"/exe)
    whisper=whisper_cli or (str(compiled) if compiled.is_file() else shutil.which("whisper-cli"))
    ffmpeg_path=ffmpeg or shutil.which("ffmpeg")
    yt=yt_dlp or (str(REPO/".venv"/("Scripts" if os.name=="nt" else "bin")/("yt-dlp.exe" if os.name=="nt" else "yt-dlp")))
    if not Path(yt).is_file() and shutil.which("yt-dlp"):
        yt=shutil.which("yt-dlp")
    missing=[]
    for label,path in (("yt-dlp",yt),("ffmpeg",ffmpeg_path),("whisper-cli",whisper),(model.name,model)):
        if not path or (not Path(path).is_file() and not shutil.which(str(path))):
            missing.append(label)
    if missing:
        raise RuntimeError("Missing tools: "+", ".join(missing)+". Run scripts/bootstrap_linux.sh.")
    return {"yt_dlp":str(yt),"ffmpeg":str(ffmpeg_path),"whisper":str(whisper),"model":str(model),"model_sha256":sha256_file(model)}

def clean_orphaned_media(work_dir):
    """With the worker lock held, remove abandoned per-task downloads from crashes.
    Never touch user media or paths outside the worker's own tmp directory.
    """
    tmp=work_dir / "tmp"
    if not tmp.exists():
        return 0
    removed=0
    for candidate in tmp.iterdir():
        if not re.fullmatch(r"(?:clip|vod|youtube)_[a-f0-9]{20}-.+", candidate.name):
            continue
        if candidate.is_symlink():
            candidate.unlink()
        elif candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink()
        removed+=1
    return removed

def free_disk_bytes(path):
    path.mkdir(parents=True,exist_ok=True)
    return shutil.disk_usage(path).free

def process_task(task, tools, output=OUTPUT, work=WORK, threads=4):
    meta, txt, srt=result_paths(task, output)
    output.mkdir(parents=True, exist_ok=True)
    tmpbase=work/"tmp"
    tmpbase.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=task_key(task)+"-", dir=tmpbase) as td:
        d=Path(td)
        # Fast, cached uploader/broadcaster check before any media bytes are fetched.
        # An upload-channel match is NOT a speaker/voice match.
        source_evidence=authorize(task,tools["yt_dlp"],work/"source_checks")
        filename=str(d/"source.%(ext)s")
        cmd=[tools["yt_dlp"],"--no-playlist","--no-progress","--no-part",
             "--socket-timeout","20","--retries","2","--fragment-retries","2",
             "--concurrent-fragments","1","--max-filesize","384M",
             "-f","worstaudio/worst/bestaudio/best", "-o",filename]
        start,end=task.get("start_s"),task.get("end_s")
        if start is not None and end is not None:
            cmd += ["--download-sections", f"*{start:g}-{end:g}"]
        cmd.append(task["source_url"])
        started=time.monotonic()
        run_command(cmd, timeout=240 if start is not None else 120)
        downloaded=sorted(p for p in d.glob("source.*") if p.is_file() and not p.name.endswith(".part"))
        if not downloaded:
            raise RuntimeError("yt-dlp exited successfully but produced no media file")
        media=downloaded[0]
        digest=sha256_file(media)
        wav=d/"speech.wav"
        clip_limit=end-start if start is not None else 180
        run_command([tools["ffmpeg"],"-y","-nostdin","-hide_banner","-loglevel","error",
                     "-i",str(media),"-vn","-t",str(clip_limit),
                     "-ar","16000","-ac","1","-c:a","pcm_s16le",str(wav)], 120)
        media.unlink(missing_ok=True)
        if not wav.is_file() or wav.stat().st_size < 44:
            raise RuntimeError("conversion produced no valid WAV")
        with wave.open(str(wav), "rb") as audio:
            audio_seconds=audio.getnframes()/audio.getframerate()
        if start is not None and audio_seconds < clip_limit - 2:
            raise RuntimeError(f"incomplete window: {audio_seconds:.2f}s audio for {clip_limit:g}s request")
        transcript_prefix=d/"transcript"
        asr_started=time.monotonic()
        run_command([tools["whisper"],"-m",tools["model"],"-f",str(wav),
                     "-t",str(threads),"-otxt","-osrt","-of",str(transcript_prefix),
                     "-l","en","-np"], timeout=max(240,clip_limit*5))
        asr_seconds=time.monotonic()-asr_started
        source_txt=d/"transcript.txt";source_srt=d/"transcript.srt"
        if not (source_txt.is_file() and source_srt.is_file()):
            raise RuntimeError("whisper exited successfully but TXT/SRT missing")
        text_data=source_txt.read_bytes();srt_data=source_srt.read_bytes()
        raw_text=text_data.decode("utf-8", errors="replace")
        speech=SPEECH_RE.sub("",raw_text).strip()
        record={
            "task_id":task["task_id"],"source_id":task["source_id"],
            "source_url":task["source_url"],"kind":task["kind"],
            "source_window_start_s":start,"source_window_end_s":end,
            "time_alignment":"relative_to_downloaded_section; source cut may start at an earlier keyframe",
            "processing_status":"transcribed" if speech else "no_speech",
            "recognition_model":"whisper.cpp " + Path(tools["model"]).stem,
            "model_file":Path(tools["model"]).name,
            "model_sha256":tools.get("model_sha256") or sha256_file(tools["model"]),
            "processed_audio_seconds":round(audio_seconds,3),
            "transcription_seconds":round(asr_seconds,3),
            "speaker_status":"unverified","speaker_verified":False,
            "training_permission_verified":False,"training_ready":False,
            "retrieved_at_utc":now(),"media_sha256_not_retained":digest,
            "text_sha256":hashlib.sha256(text_data).hexdigest(),
            "srt_sha256":hashlib.sha256(srt_data).hexdigest(),
            "recognized_words":len(raw_text.split()),
            "elapsed_seconds":round(time.monotonic()-started,2),
            "source_provenance":"frozen_manifest",
            "source_owner_verified":bool(source_evidence.get("owner_verified")),
            "source_owner_evidence":source_evidence.get("proof"),
        }
        # Metadata LAST is the completion marker. Interrupted writes get retried.
        txt_tmp=txt.with_name(txt.name+".tmp")
        srt_tmp=srt.with_name(srt.name+".tmp")
        txt_tmp.write_bytes(text_data);srt_tmp.write_bytes(srt_data)
        os.replace(txt_tmp,txt);os.replace(srt_tmp,srt)
        atomic_json(meta,record)
        return record

def git_cmd(args, timeout=90, capture=False):
    proc=subprocess.run(["git", *args], cwd=REPO, text=True,
                        capture_output=True, timeout=timeout,
                        stdin=subprocess.DEVNULL,
                        env={**os.environ,"GIT_TERMINAL_PROMPT":"0"})
    if proc.returncode:
        raise RuntimeError("git "+args[0]+" failed: "+(proc.stderr or proc.stdout)[-500:])
    return proc.stdout if capture else ""

def checkpoint():
    # Only the worker-owned directory may be committed, and no user-staged work is touched.
    staged=git_cmd(["diff","--cached","--name-only"],capture=True).splitlines()
    foreign=[x for x in staged if not x.startswith("corpus/local_transcripts/")]
    if foreign:
        raise RuntimeError("Refusing to commit: unrelated staged files: "+str(foreign[:5]))
    if OUTPUT.exists():
        git_cmd(["add","--","corpus/local_transcripts"])
    if git_cmd(["diff","--cached","--name-only"],capture=True).strip():
        git_cmd(["-c","user.name=harmon-local-agent","-c",
             "user.email=harmon-local-agent@users.noreply.github.com",
                 "commit","-m","Add locally transcribed archival work units"])

def push_commits():
    # Avoid losing locally committed results if push or authentication fails.
    git_cmd(["fetch","origin","main"],timeout=120)
    git_cmd(["rebase","origin/main"],timeout=120)
    ahead=int((git_cmd(["rev-list","--count","origin/main..HEAD"],capture=True) or "0").strip())
    if ahead==0:
        return "up to date"
    git_cmd(["push","origin","HEAD:main"],timeout=120)
    return f"pushed {ahead} commit(s)"

def publish():
    checkpoint()
    return push_commits()

def summarize(tasks, journal, output=OUTPUT, include_existing=False, retry_exhausted=False):
    counts={}
    for t in tasks:
        _,reason=eligible(t,journal,output,include_existing,retry_exhausted)
        counts[reason]=counts.get(reason,0)+1
    return counts

def execute(args):
    tasks=load_manifest(args.manifest)
    journal_file=args.work_dir/"state.json"
    journal=load_journal(journal_file)
    if args.command=="status":
        print(json.dumps({"total_tasks":len(tasks),"states":summarize(tasks,journal,args.output,args.include_existing,args.retry_exhausted)},indent=2))
        return 0
    if args.command=="publish":
        print(publish())
        return 0
    if args.command=="plan":
        for t in tasks:
            yes,reason=eligible(t,journal,args.output,args.include_existing,args.retry_exhausted)
            if yes:print(t["task_id"])
        return 0
    if args.push and args.output.resolve() != OUTPUT.resolve():
        raise RuntimeError("Publishing requires the worker-owned default output directory; use --no-push for custom output")
    tools=find_tools(args.model,args.whisper_cli,args.yt_dlp,args.ffmpeg,args.model_name)
    args.work_dir.mkdir(parents=True,exist_ok=True)
    # POSIX advisory lock automatically releases if the process crashes, avoiding manual stale-lock repairs.
    lock=args.work_dir/"worker.lock"
    lock_handle=lock.open("a+",encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise RuntimeError("Another local worker is already running on this checkout") from exc
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(json.dumps({"pid":os.getpid(),"started_utc":now()})+"\n")
    lock_handle.flush()
    removed=clean_orphaned_media(args.work_dir)
    if removed:
        print(f"CLEANUP: removed {removed} stale temporary media directories",flush=True)
    processed=0;completed_count=0;failures=0;since_commit=0;started=time.monotonic()
    upload_enabled=args.push
    interrupted=False
    def save_checkpoint():
        nonlocal since_commit, upload_enabled
        try:
            checkpoint()
            since_commit=0
        except Exception as exc:
            print("LOCAL CHECKPOINT ERROR, files retained: "+str(exc),file=sys.stderr,flush=True)
            return
        if upload_enabled:
            try:
                print("CHECKPOINT",push_commits(),flush=True)
            except Exception as exc:
                print("UPLOAD ERROR, continuing local checkpoints: "+str(exc),file=sys.stderr,flush=True)
                upload_enabled=False
    old_term=signal.getsignal(signal.SIGTERM)
    old_int=signal.getsignal(signal.SIGINT)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        for i,task in enumerate(tasks):
            yes,reason=eligible(task,journal,args.output,args.include_existing,args.retry_exhausted)
            if not yes:continue
            if free_disk_bytes(args.work_dir)<args.min_free_gb*1024**3:
                print("DISK GUARD: insufficient free space; stopping before download",file=sys.stderr,flush=True)
                break
            if args.max_items>0 and processed>=args.max_items:break
            if args.max_hours>0 and (time.monotonic()-started)>3600*args.max_hours:break
            processed+=1
            try:
                record=process_task(task,tools,args.output,args.work_dir,args.threads)
                journal.pop(task["task_id"],None)
                atomic_json(journal_file,journal)
                completed_count+=1;since_commit+=1
                print(f"[{processed}] OK {task['task_id']} {record['recognized_words']} words",flush=True)
            except SourceRejected as exc:
                # Permanent fail-closed quarantine: no download, no ASR, no retry.
                journal[task["task_id"]]={"source_rejected":True,"reason":str(exc)[:120],
                     "last_attempt_utc":now()}
                atomic_json(journal_file,journal)
                print(f"[{processed}] SOURCE SKIPPED {task['task_id']}: {exc}",file=sys.stderr,flush=True)
            except Exception as exc:
                failures+=1
                old=journal.get(task["task_id"],{})
                attempts=int(old.get("attempts",0))+1
                backoff=min(86400,300*(6**min(3,attempts-1)))
                journal[task["task_id"]]={"attempts":attempts,"last_error":str(exc)[:600],
                      "last_attempt_utc":now(),"retry_after_unix":time.time()+backoff}
                atomic_json(journal_file,journal)
                print(f"[{processed}] FAILED {task['task_id']}: {exc}",file=sys.stderr,flush=True)
            if args.push and since_commit>=args.commit_every:
                save_checkpoint()
    except KeyboardInterrupt:
        interrupted=True
        print("INTERRUPTED: checkpointing completed results",file=sys.stderr,flush=True)
    finally:
        # Ignore a second termination signal while preserving the completed checkpoint.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if args.push:
                save_checkpoint()
            print(json.dumps({"attempted":processed,"produced":completed_count,"failed":failures,
                 "interrupted":interrupted,"min_successes":args.min_successes,
                 "remaining":summarize(tasks,journal,args.output,args.include_existing,args.retry_exhausted)},indent=2))
        finally:
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    if interrupted:
        return 130
    if completed_count < args.min_successes:
        print(f"VALIDATION FAILED: produced {completed_count}, required {args.min_successes}",file=sys.stderr)
        return 1
    # Source failures are recorded; don't fail the whole batch over ordinary unavailable media.
    return 0

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command",nargs="?",choices=["run","status","plan","publish"],default="run")
    p.add_argument("--manifest",type=Path,default=PLAN)
    p.add_argument("--output",type=Path,default=OUTPUT)
    p.add_argument("--work-dir",type=Path,default=WORK)
    p.add_argument("--model",type=Path,help="explicit GGML model path; overrides --model-name")
    p.add_argument("--model-name",default=os.environ.get("HARMON_MODEL",MODEL_NAME),help="GGML model name (default: HARMON_MODEL or small.en)")
    p.add_argument("--min-successes",type=int,default=0,help="fail validation unless this many new results are produced")
    p.add_argument("--whisper-cli",help="path to whisper-cli")
    p.add_argument("--yt-dlp",help="path to yt-dlp executable")
    p.add_argument("--ffmpeg",help="path to ffmpeg")
    p.add_argument("--threads",type=int,default=4)
    p.add_argument("--max-items",type=int,default=0,help="0 processes the full eligible worklist")
    p.add_argument("--max-hours",type=float,default=0,help="0 = no walltime cap")
    p.add_argument("--commit-every",type=int,default=10)
    p.add_argument("--min-free-gb",type=float,default=2.0,
                   help="refuse new downloads if filesystem free space is below this threshold")
    p.add_argument("--no-push",dest="push",action="store_false",default=True)
    p.add_argument("--include-existing",action="store_true",help="re-transcribe legacy clip sources")
    p.add_argument("--retry-exhausted",action="store_true")
    return p

if __name__=="__main__":
    try:
        options=parser().parse_args()
        if options.threads<1 or options.threads>64 or options.commit_every<1 or options.max_items<0 or options.max_hours<0 or options.min_free_gb<0 or options.min_successes<0:
            raise RuntimeError("invalid threads, commit-every, max-hours, or max-items")
        sys.exit(execute(options))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print("ERROR:",exc,file=sys.stderr)
        sys.exit(2)
