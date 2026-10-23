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
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

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
            elif start is not None or end is not None:
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    raise ValueError("incomplete time window")
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
    attempts=int(state.get("attempts", 0))
    if attempts>=MAX_ATTEMPTS and not retry_exhausted:
        return False, "exhausted_retries"
    next_time=float(state.get("retry_after_unix", 0))
    if (clock if clock is not None else time.time()) < next_time:
        return False, "deferred_retry"
    return True, "pending"

def run_command(argv, timeout, cwd=None):
    try:
        proc=subprocess.run([str(a) for a in argv], cwd=cwd, text=True,
            capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{Path(str(argv[0])).name} timed out after {timeout}s") from exc
    except OSError as exc:
        raise RuntimeError(f"{Path(str(argv[0])).name} could not start: {exc}") from exc
    if proc.returncode:
        # Do not emit full downloaded-media URLs, cookies or megabytes of raw output.
        message=(proc.stderr or proc.stdout)[-450:].replace("\n", " ").strip()
        raise RuntimeError(f"{Path(str(argv[0])).name} failed ({proc.returncode}): {message}")

def find_tools(model_dir=None, whisper_cli=None, yt_dlp=None, ffmpeg=None):
    tools_dir=REPO / ".harmon-tools"
    model=Path(model_dir) if model_dir else tools_dir / "whisper.cpp" / "models" / "ggml-small.en.bin"
    exe="whisper-cli.exe" if os.name=="nt" else "whisper-cli"
    compiled=(tools_dir/"whisper.cpp"/"build"/"bin"/exe)
    whisper=whisper_cli or (str(compiled) if compiled.is_file() else shutil.which("whisper-cli"))
    ffmpeg_path=ffmpeg or shutil.which("ffmpeg")
    yt=yt_dlp or (str(REPO/".venv"/("Scripts" if os.name=="nt" else "bin")/("yt-dlp.exe" if os.name=="nt" else "yt-dlp")))
    if not Path(yt).is_file() and shutil.which("yt-dlp"):
        yt=shutil.which("yt-dlp")
    missing=[]
    for label,path in (("yt-dlp",yt),("ffmpeg",ffmpeg_path),("whisper-cli",whisper),("ggml-small.en.bin",model)):
        if not path or (not Path(path).is_file() and not shutil.which(str(path))):
            missing.append(label)
    if missing:
        raise RuntimeError("Missing tools: "+", ".join(missing)+". Run scripts/bootstrap_linux.sh or scripts/bootstrap_windows.ps1.")
    return {"yt_dlp":str(yt),"ffmpeg":str(ffmpeg_path),"whisper":str(whisper),"model":str(model)}

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
        digest=hashlib.sha256(media.read_bytes()).hexdigest()
        wav=d/"speech.wav"
        clip_limit=min(1210, int((end-start)+15)) if start is not None else 180
        run_command([tools["ffmpeg"],"-y","-nostdin","-hide_banner","-loglevel","error",
                     "-i",str(media),"-vn","-t",str(clip_limit),
                     "-ar","16000","-ac","1","-c:a","pcm_s16le",str(wav)], 120)
        media.unlink(missing_ok=True)
        if not wav.is_file() or wav.stat().st_size < 44:
            raise RuntimeError("conversion produced no valid WAV")
        transcript_prefix=d/"transcript"
        run_command([tools["whisper"],"-m",tools["model"],"-f",str(wav),
                     "-t",str(threads),"-otxt","-osrt","-of",str(transcript_prefix),
                     "-nt","-np"], timeout=max(240,clip_limit*5))
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
            "recognition_model":"whisper.cpp ggml-small.en",
            "speaker_status":"unverified","speaker_verified":False,
            "training_permission_verified":False,"training_ready":False,
            "retrieved_at_utc":now(),"media_sha256_not_retained":digest,
            "text_sha256":hashlib.sha256(text_data).hexdigest(),
            "srt_sha256":hashlib.sha256(srt_data).hexdigest(),
            "recognized_words":len(raw_text.split()),
            "elapsed_seconds":round(time.monotonic()-started,2),
            "source_provenance":"frozen_manifest",
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

def publish():
    # Only the worker-owned directory may be committed, and no user-staged work is touched.
    staged=git_cmd(["diff","--cached","--name-only"],capture=True).splitlines()
    foreign=[x for x in staged if not x.startswith("corpus/local_transcripts/")]
    if foreign:
        raise RuntimeError("Refusing to commit: unrelated staged files: "+str(foreign[:5]))
    git_cmd(["add","--","corpus/local_transcripts"])
    if git_cmd(["diff","--cached","--name-only"],capture=True).strip():
        git_cmd(["-c","user.name=harmon-local-agent","-c",
             "user.email=harmon-local-agent@users.noreply.github.com",
                 "commit","-m","Add locally transcribed archival work units"])
    # Avoid losing locally committed results if push or authentication fails.
    git_cmd(["fetch","origin","main"],timeout=120)
    git_cmd(["rebase","origin/main"],timeout=120)
    ahead=int((git_cmd(["rev-list","--count","origin/main..HEAD"],capture=True) or "0").strip())
    if ahead==0:
        return "up to date"
    git_cmd(["push","origin","HEAD:main"],timeout=120)
    return f"pushed {ahead} commit(s)"

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
    tools=find_tools(args.model,args.whisper_cli,args.yt_dlp,args.ffmpeg)
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
                try:
                    print("CHECKPOINT",publish(),flush=True)
                    since_commit=0
                except Exception as exc:
                    print("CHECKPOINT ERROR, local files retained: "+str(exc),file=sys.stderr,flush=True)
                    # Avoid spamming credential prompts or repeated failed pushes.
                    args.push=False
        if args.push and since_commit:
            try:print("FINAL CHECKPOINT",publish(),flush=True)
            except Exception as exc:
                print("PUSH FAILED, local results preserved: "+str(exc),file=sys.stderr)
        print(json.dumps({"attempted":processed,"produced":completed_count,"failed":failures,
             "remaining":summarize(tasks,journal,args.output,args.include_existing,args.retry_exhausted)},indent=2))
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    # Source failures are recorded; don't fail the whole batch over ordinary unavailable media.
    return 0

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command",nargs="?",choices=["run","status","plan","publish"],default="run")
    p.add_argument("--manifest",type=Path,default=PLAN)
    p.add_argument("--output",type=Path,default=OUTPUT)
    p.add_argument("--work-dir",type=Path,default=WORK)
    p.add_argument("--model",type=Path,help="path to ggml-small.en.bin")
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
        if options.threads<1 or options.threads>64 or options.commit_every<1 or options.max_items<0 or options.max_hours<0 or options.min_free_gb<0:
            raise RuntimeError("invalid threads, commit-every, max-hours, or max-items")
        sys.exit(execute(options))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print("ERROR:",exc,file=sys.stderr)
        sys.exit(2)
