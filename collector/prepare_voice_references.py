#!/usr/bin/env python3
"""Prepare a SMALL local voice-reference review set from the frozen manifest.

This is a one-time operator step, NOT performed during the unattended harvest.
Nothing is labelled by inference or by the video channel. Temporary video is
deleted; short WAV excerpts remain in .harmon-work until reviewer decisions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"collector"))
from source_gate import authorize, SourceRejected

def choose_sources(manifest,count):
    seen=set();tasks=[]
    for line in Path(manifest).read_text(encoding="utf-8").splitlines():
        if not line.strip():continue
        row=json.loads(line)
        if row["kind"] not in ("youtube","vod","clip"):continue
        source=(row["kind"],str(row["source_id"]))
        if source in seen:continue
        seen.add(source)
        # Small positive-selection set across separate recordings/days.
        tasks.append(row)
        if len(tasks)>=count*3:break
    return tasks

def command(args,timeout):
    try:
        p=subprocess.run([str(x) for x in args],capture_output=True,text=True,
                         stdin=subprocess.DEVNULL,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as exc:raise RuntimeError(str(exc)) from exc
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-400:])
    return p

def prepare(manifest,output,max_samples=16,seconds=10,
            yt_dlp="yt-dlp",ffmpeg="ffmpeg",checker=authorize,
            runner=command):
    if max_samples<1 or max_samples>50 or seconds<4 or seconds>20:
        raise ValueError("small one-time samples only: 1-50 items of 4-20 seconds")
    out=Path(output);out.mkdir(mode=0o700,parents=True,exist_ok=True)
    candidate_file=out/"unlabelled_references.json"
    existing=[]
    if candidate_file.exists():
        existing=json.loads(candidate_file.read_text(encoding="utf-8"))
    keys={e["source_id"] for e in existing}
    rows=list(existing)
    for task in choose_sources(manifest,max_samples):
        if len(rows)>=max_samples:break
        if task["source_id"] in keys:continue
        try:
            checker(task,yt_dlp,out/"source_checks")
            # A source may contain someone else. The reviewer, NOT this script,
            # decides whether it is the target voice.
            with tempfile.TemporaryDirectory(prefix="ref-media-",dir=out) as td:
                template=str(Path(td)/"item.%(ext)s")
                start=task.get("start_s")
                if start is not None:
                    # 70-80 seconds into the first 10m task if available.
                    end=task.get("end_s") or (start+120)
                    sample_start=start+min(70,max(0,(end-start-seconds)/3))
                    sample_end=min(end,sample_start+seconds)
                else:sample_start,sample_end=0,seconds
                args=[yt_dlp,"--no-playlist","--no-progress","--retries","1",
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
            rows.append({"path":wav.name,"source_id":task["kind"]+":"+task["source_id"],
                         "source_url":task["source_url"],
                         "sample_requested_start_s":sample_start,
                         "label":"UNREVIEWED","reviewer":""})
            keys.add(task["source_id"])
            candidate_file.write_text(json.dumps(rows,indent=2)+"\n",encoding="utf-8")
            os.chmod(candidate_file,0o600)
            print(f"Prepared {len(rows)}/{max_samples}: {wav.name}",flush=True)
        except (RuntimeError,SourceRejected,ValueError) as exc:
            print(f"Skipped reference source: {task['task_id']} ({str(exc)[:150]})",
                  file=sys.stderr,flush=True)
    return len(rows)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",type=Path,default=ROOT/"corpus/history/manifest.jsonl")
    p.add_argument("--output",type=Path,default=ROOT/".harmon-work/reference_review")
    p.add_argument("--count",type=int,default=16)
    p.add_argument("--seconds",type=int,default=10)
    p.add_argument("--yt-dlp",default=str(ROOT/".venv/bin/yt-dlp"))
    p.add_argument("--ffmpeg",default="ffmpeg")
    a=p.parse_args()
    print(json.dumps({"references_prepared":prepare(a.manifest,a.output,a.count,a.seconds,
                        a.yt_dlp,a.ffmpeg)}))
