#!/usr/bin/env python3
"""One-off metadata-only sweep of the creator's official YouTube surfaces.

No recordings are downloaded. Never discard the existing inventory if a site fails.
Only clearly attributed creator-owned videos enter the executable worklist.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
CHANNEL="UCTiP-fGq-Uuzcor4fmp_R2Q"
BASE="https://www.youtube.com/channel/"+CHANNEL
ID_RE=re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_RE=re.compile(r"^[A-Za-z0-9_-]{8,80}$")
TABS=("videos","streams","shorts","playlists")

def valid_id(v):
    return isinstance(v,str) and ID_RE.fullmatch(v) is not None

def read_jsonl(path):
    if not path.is_file():
        return []
    records=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj=json.loads(line)
            if isinstance(obj,dict):records.append(obj)
        except (ValueError,TypeError):
            continue
    return records

def cmd_rows(url, limit=1200, timeout=140):
    args=["yt-dlp","--flat-playlist","--ignore-errors","--no-warnings",
          "--extractor-retries","1","--socket-timeout","15",
          "--playlist-end",str(limit),"--dump-json","--skip-download",url]
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)
        records=[json.loads(x) for x in p.stdout.splitlines() if x.strip().startswith("{")]
        return records,{"exit_code":p.returncode,"records":len(records),
                        "error_tail":p.stderr[-550:]}
    except (subprocess.TimeoutExpired,OSError,ValueError) as exc:
        return [],{"exit_code":-1,"records":0,"error_tail":str(exc)[:550]}

def youtube_id(obj):
    v=obj.get("id")
    if valid_id(v):
        return v
    url=str(obj.get("webpage_url") or obj.get("url") or "")
    match=re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])",url)
    return match.group(1) if match else None

def normalize(obj,surface,old=None):
    video_id=youtube_id(obj)
    if not video_id:
        return None
    creator_id=obj.get("channel_id") or obj.get("uploader_id")
    foreign=bool(creator_id and creator_id.startswith("UC") and creator_id!=CHANNEL)
    # Playlists may contain videos from others, or no author metadata. Such
    # candidates are held aside rather than silently added to this person's corpus.
    candidate=foreign or (surface.startswith("playlist:") and creator_id!=CHANNEL)
    duration=obj.get("duration")
    try:duration=float(duration)
    except (ValueError,TypeError):duration=None
    if duration is not None and not (0<duration<=24*3600):
        duration=None
    result={
        "id":video_id,
        "webpage_url":"https://www.youtube.com/watch?v="+video_id,
        "title":str(obj.get("title") or (old or {}).get("title") or "")[:200],
        "duration":duration if duration is not None else (old or {}).get("duration"),
        "channel_id":creator_id or (old or {}).get("channel_id"),
        "surfaces":sorted(set(((old or {}).get("surfaces") or [])+[surface])),
        "candidate_only":bool(candidate or (old or {}).get("candidate_only",False)),
        "uploader":obj.get("uploader") or (old or {}).get("uploader"),
    }
    # A video previously confirmed from the official channel is not demoted
    # merely because a later flat playlist lacks metadata.
    if old and not old.get("candidate_only",False) and not foreign:
        result["candidate_only"]=False
    if surface in TABS[:3] and not foreign:
        result["candidate_only"]=False
    # Local worker requires bounded start/end offsets for long videos.
    # Never enqueue a YouTube page that lacks a trusted duration.
    if result["duration"] is None:
        result["candidate_only"]=True
    return result

def create_inventory(root=ROOT,probe=cmd_rows,max_playlists=12):
    discovery=root/"corpus"/"discovery"
    discovery.mkdir(parents=True,exist_ok=True)
    by_id={}
    for name in ("youtube.jsonl","youtube_expanded.jsonl"):
        for obj in read_jsonl(discovery/name):
            r=normalize(obj,"legacy" if name=="youtube.jsonl" else "previous",obj)
            if r:
                by_id[r["id"]]=normalize(obj,"legacy" if name=="youtube.jsonl" else "previous",by_id.get(r["id"],obj))
    diagnostics={}
    playlists=[]
    for tab in TABS:
        entries,info=probe(BASE+"/"+tab,limit=1500 if tab!="playlists" else 50,timeout=140)
        diagnostics[tab]=info
        if tab=="playlists":
            for o in entries:
                p=str(o.get("id") or "")
                if PLAYLIST_RE.fullmatch(p):
                    playlists.append(p)
            continue
        for obj in entries:
            r=normalize(obj,tab,by_id.get(youtube_id(obj)))
            if r:by_id[r["id"]]=r
    for playlist_id in dict.fromkeys(playlists[:max_playlists]):
        surface="playlist:"+playlist_id
        entries,info=probe("https://www.youtube.com/playlist?list="+playlist_id,limit=1500,timeout=120)
        diagnostics[surface]=info
        for obj in entries:
            r=normalize(obj,surface,by_id.get(youtube_id(obj)))
            if r:by_id[r["id"]]=r
    rows=sorted(by_id.values(),key=lambda x:x["id"])
    target=discovery/"youtube_expanded.jsonl"
    target.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in rows),encoding="utf-8")
    summary={
        "official_channel":BASE,
        "listed_video_ids":len(rows),
        "eligible_creator_video_ids":sum(not x["candidate_only"] for x in rows),
        "candidate_only":sum(bool(x["candidate_only"]) for x in rows),
        "total_known_hours":round(sum(float(x["duration"] or 0) for x in rows if not x["candidate_only"])/3600,2),
        "surfaces":{k:{"records":v["records"],"exit_code":v["exit_code"],"error_tail":v["error_tail"]} for k,v in diagnostics.items()},
        "note":"Metadata only, not proof of availability. Playlist items without creator attribution are held as candidates."
    }
    (discovery/"youtube_expansion_report.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return summary

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=ROOT)
    p.add_argument("--max-playlists",type=int,default=12)
    a=p.parse_args()
    print(json.dumps(create_inventory(a.root,max_playlists=max(0,min(20,a.max_playlists))),indent=2))
