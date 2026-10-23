#!/usr/bin/env python3
"""Cheap strict identity/metadata gate BEFORE media download or transcription.

Proves an upload/stream source belongs to the expected channel; does not prove
every audible voice belongs to the owner. Never claim speaker verified.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import os
from urllib.parse import urlparse, parse_qs

YOUTUBE_CHANNEL="UCTiP-fGq-Uuzcor4fmp_R2Q"
TWITCH_BROADCASTER="simplez4ck"
# Reject only unmistakable standalone third-party media.
UNRELATED_PATTERNS=(
    r"\bofficial (?:music |lyric |audio )?video\b",
    r"\bofficial (?:soundtrack|trailer)\b",
    r"\bfull (?:album|soundtrack)\b",
    r"\bno commentary\b",
    r"\b(?:karaoke|lyric video|music only)\b",
)

class SourceRejected(RuntimeError):
    """Permanent source exclusion, not a network/transcription error."""

def source_key(task):
    # Per recording, *not* per 10-minute source window.
    return task["kind"]+"_"+hashlib.sha256(
        (task["kind"]+":"+task["source_id"]).encode()).hexdigest()[:24]

def disallowed_title(title):
    title=str(title or "")
    return next((pattern for pattern in UNRELATED_PATTERNS if re.search(pattern,title,re.I)),None)

def assess(task, info):
    """Return small evidence record or reject. No network or media involved."""
    if not isinstance(info,dict):
        raise SourceRejected("metadata_missing")
    kind=task["kind"]
    title=str(info.get("title") or task.get("title") or "")
    bad=disallowed_title(title)
    if bad:
        raise SourceRejected("non_commentary_title")
    if kind=="youtube":
        ident=str(info.get("id") or "")
        if ident != task["source_id"]:
            raise SourceRejected("youtube_video_id_mismatch")
        owner=str(info.get("channel_id") or "")
        if owner != YOUTUBE_CHANNEL:
            raise SourceRejected("youtube_owner_unknown" if not owner else "youtube_foreign_channel")
        return {"owner_verified":True,"source_kind":kind,"source_id":ident,
                "owner_id":owner,"proof":"video_channel_id","title":title}
    if kind=="vod":
        ident=str(info.get("id") or "").lstrip("v")
        if ident != str(task["source_id"]):
            raise SourceRejected("twitch_vod_id_mismatch")
        owner=str(info.get("uploader_id") or "").casefold()
        if owner != TWITCH_BROADCASTER:
            raise SourceRejected("twitch_vod_owner_unknown" if not owner else "twitch_foreign_vod")
        return {"owner_verified":True,"source_kind":kind,"source_id":task["source_id"],
                "owner_id":owner,"proof":"vod_uploader_id","title":title}
    if kind=="clip":
        # Twitch uploader/curator is often NOT the stream broadcaster.
        slug=str(info.get("display_id") or "")
        if slug and slug!=task["source_id"]:
            raise SourceRejected("twitch_clip_slug_mismatch")
        channel=str(info.get("channel") or "").casefold()
        creators=[str(x).casefold() for x in (info.get("creators") or []) if isinstance(x,str)]
        if channel != TWITCH_BROADCASTER and TWITCH_BROADCASTER not in creators:
            raise SourceRejected("twitch_clip_broadcaster_unknown" if not channel and not creators else "twitch_foreign_broadcaster")
        return {"owner_verified":True,"source_kind":kind,"source_id":task["source_id"],
                "owner_id":channel or TWITCH_BROADCASTER,"proof":"clip_broadcaster","title":title}
    raise SourceRejected("unsupported_source_kind")

def fetch_metadata(url, yt_dlp, timeout=75):
    # Real extractor JSON can be large: no downloads or saved media. Time-limited.
    cmd=[str(yt_dlp),"--no-playlist","--skip-download","--dump-single-json",
         "--no-warnings","--socket-timeout","15","--retries","1",str(url)]
    try:
        proc=subprocess.run(cmd,capture_output=True,text=True,
                            timeout=timeout,stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired,OSError) as exc:
        raise RuntimeError("source_metadata_query_failed: "+str(exc)[:200]) from exc
    if proc.returncode:
        raise RuntimeError("source_metadata_query_failed: "+proc.stderr[-250:].replace("\n"," "))
    try:
        info=json.loads(proc.stdout)
        if not isinstance(info,dict):
            raise ValueError("not an object")
        return info
    except ValueError as exc:
        raise RuntimeError("source_metadata_invalid_json") from exc

def authorize(task, yt_dlp, cache_dir, fetcher=fetch_metadata):
    """Cached per-video upstream identity probe; never caches network errors.

    Authoritative cached rejections prevent wasting future time on the same video.
    """
    cache_dir=Path(cache_dir)
    cache_dir.mkdir(parents=True,exist_ok=True)
    file=cache_dir/(source_key(task)+".json")
    if file.is_file():
        try:
            saved=json.loads(file.read_text(encoding="utf-8"))
            if (saved.get("source_id")==task["source_id"]
                    and saved.get("source_kind")==task["kind"]):
                if saved.get("state")=="rejected":
                    raise SourceRejected(saved["reason"])
                if saved.get("state")=="allowed" and saved.get("evidence",{}).get("owner_verified"):
                    return saved["evidence"]
        except (OSError,ValueError,KeyError):
            pass
    try:
        evidence=assess(task,fetcher(task["source_url"],yt_dlp))
    except SourceRejected as exc:
        payload={"state":"rejected","source_id":task["source_id"],
                 "source_kind":task["kind"],"reason":str(exc)}
        _atomic(file,payload)
        raise
    _atomic(file,{"state":"allowed","source_id":task["source_id"],
                  "source_kind":task["kind"],"evidence":evidence})
    return evidence

def _atomic(path,obj):
    fd,tmp=tempfile.mkstemp(prefix=path.name+".",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(obj,f,ensure_ascii=False,sort_keys=True)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
