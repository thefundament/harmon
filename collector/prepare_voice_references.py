#!/usr/bin/env python3
"""Prepare a small private voice-reference candidate set from the frozen manifest.

This is a one-time operator step, NOT performed during the unattended harvest.
This script assigns no voice labels. Temporary video is deleted; short WAV
excerpts remain in .harmon-work for automatic or optional reviewed enrollment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"collector"))
try:
    from .source_gate import authorize, SourceRejected
    from .local_worker import atomic_json, free_disk_bytes, run_command
except ImportError:
    from source_gate import authorize, SourceRejected
    from local_worker import atomic_json, free_disk_bytes, run_command

def choose_sources(manifest,count):
    seen=set();tasks=[]
    rows=[json.loads(line) for line in Path(manifest).read_text(encoding="utf-8").splitlines()
          if line.strip()]
    # Prefer distinct long-form recordings over clips whose shared stream is unknown.
    rows.sort(key=lambda r: {"youtube":0,"vod":1,"clip":2}.get(r.get("kind"),3))
    for row in rows:
        if row["kind"] not in ("youtube","vod","clip"):continue
        source=(row["kind"],str(row["source_id"]))
        if source in seen:continue
        seen.add(source)
        # Small candidate set across separate recordings.
        tasks.append(row)
        if len(tasks)>=count*3:break
    return tasks

def command(args,timeout):
    return run_command(args,timeout)

def prepare(manifest,output,max_samples=16,seconds=10,
            yt_dlp="yt-dlp",ffmpeg="ffmpeg",checker=authorize,
            runner=command,max_attempts=20):
    if max_samples<1 or max_samples>50 or seconds<4 or seconds>20:
        raise ValueError("small one-time samples only: 1-50 items of 4-20 seconds")
    if not 1<=max_attempts<=50:
        raise ValueError("reference preparation allows 1-50 total source attempts")
    out=Path(output);out.mkdir(mode=0o700,parents=True,exist_ok=True)
    candidate_file=out/"unlabelled_references.json"
    existing=[]
    if candidate_file.exists():
        existing=json.loads(candidate_file.read_text(encoding="utf-8"))
    keys={e["source_id"] for e in existing}
    rows=list(existing)
    ledger=out/"reference_attempts.json"
    attempted=json.loads(ledger.read_text(encoding="utf-8")) if ledger.exists() else []
    if not isinstance(attempted,list):raise ValueError("invalid reference attempt ledger")
    for task in choose_sources(manifest,max_samples):
        if len(rows)>=max_samples or len(attempted)>=max_attempts:break
        source=task["kind"]+":"+task["source_id"]
        if source in keys or source in attempted:continue
        if free_disk_bytes(out)<2*1024**3:
            raise RuntimeError("reference preparation requires at least 2 GiB free disk")
        # Record BEFORE the attempt so failed/interrupted restarts cannot exceed the cap.
        attempted.append(source)
        atomic_json(ledger,attempted)
        os.chmod(ledger,0o600)
        wav=None
        saved=False
        try:
            checker(task,yt_dlp,out/"source_checks")
            # Channel ownership never assigns a target-voice label here.
            with tempfile.TemporaryDirectory(prefix="ref-media-",dir=out) as td:
                template=str(Path(td)/"item.%(ext)s")
                start=task.get("start_s")
                if start is not None:
                    # 70-80 seconds into the first 10m task if available.
                    end=task.get("end_s") or (start+120)
                    sample_start=start+min(70,max(0,(end-start-seconds)/3))
                    sample_end=min(end,sample_start+seconds)
                else:sample_start,sample_end=0,seconds
                args=[yt_dlp,"--no-playlist","--no-progress","--no-part","--retries","1",
                      "--socket-timeout","20","--fragment-retries","1",
                      "--concurrent-fragments","1","--max-filesize","384M",
                      "-f","worstaudio/worst/bestaudio/best","--download-sections",
                      f"*{sample_start:g}-{sample_end:g}","-o",template,task["source_url"]]
                runner(args,110)
                media=[p for p in Path(td).glob("item.*") if p.is_file() and not p.name.endswith(".part")]
                if not media:raise RuntimeError("yt-dlp yielded no media")
                key=hashlib.sha256(task["task_id"].encode()).hexdigest()[:12]
                wav=out/(key+".wav")
                runner([ffmpeg,"-y","-nostdin","-hide_banner","-loglevel","error",
                        "-i",str(media[0]),"-t",str(seconds),"-ar","16000",
                        "-ac","1","-c:a","pcm_s16le",str(wav)],40)
                if not wav.exists() or wav.stat().st_size<4*32000:
                    wav.unlink(missing_ok=True)
                    raise RuntimeError("reference recording too short")
                os.chmod(wav,0o600)
            rows.append({"path":wav.name,"source_id":source,
                         "source_url":task["source_url"],
                         "sample_requested_start_s":sample_start,
                         "label":"UNREVIEWED","reviewer":""})
            keys.add(source)
            atomic_json(candidate_file,rows)
            os.chmod(candidate_file,0o600)
            saved=True
            print(f"Prepared {len(rows)}/{max_samples}: {wav.name}",flush=True)
        except (RuntimeError,SourceRejected,ValueError) as exc:
            print(f"Skipped reference source: {task['task_id']} ({str(exc)[:150]})",
                  file=sys.stderr,flush=True)
        finally:
            if wav is not None and not saved:
                wav.unlink(missing_ok=True)
    return len(rows)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",type=Path,default=ROOT/"corpus/history/manifest.jsonl")
    p.add_argument("--output",type=Path,default=ROOT/".harmon-work/reference_review")
    p.add_argument("--count",type=int,default=16)
    p.add_argument("--seconds",type=int,default=10)
    p.add_argument("--max-attempts",type=int,default=20,
                   help="total source-attempt cap across restarts (default: 20)")
    p.add_argument("--yt-dlp",default=str(ROOT/".venv/bin/yt-dlp"))
    p.add_argument("--ffmpeg",default="ffmpeg")
    a=p.parse_args()
    count=prepare(a.manifest,a.output,a.count,a.seconds,a.yt_dlp,a.ffmpeg,
                  max_attempts=a.max_attempts)
    print(json.dumps({"references_prepared":count,"requested":a.count}))
    sys.exit(0 if count>=a.count else 1)
