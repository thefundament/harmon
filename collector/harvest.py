#!/usr/bin/env python3
"""Bounded public-source discovery and speech-to-text. All ASR is unreviewed."""
import datetime, hashlib, json, os, pathlib, re, shutil, subprocess, sys, time
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
TEXT = OUT / "text"
DISC = OUT / "discovery"
for d in (TEXT, DISC):
    d.mkdir(parents=True, exist_ok=True)
MAX_CLIPS = max(0, min(70, int(os.getenv("MAX_CLIPS", "45"))))
MAX_VODS = max(0, min(12, int(os.getenv("MAX_VODS", "8"))))
MAX_YOUTUBE = max(0, min(10, int(os.getenv("MAX_YOUTUBE", "0"))))
VOD_SEGMENT = os.getenv("VOD_SEGMENT", "00:01:00-00:05:00")
MODEL = os.getenv("WHISPER_MODEL", "base.en")
SOURCE_BASE = "https://www.twitch.tv/simplez4ck"
SEEDS = ROOT / "requests" / "initial.txt"

def call(argv, timeout=80):
    try:
        p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr[-1400:]
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as exc:
        return 1, "", repr(exc)

def listing(label, url, limit):
    args = ["yt-dlp", "--flat-playlist", "--ignore-errors", "--no-warnings",
            "--playlist-end", str(limit), "--dump-json", "--socket-timeout", "15", url]
    rc, stdout, err = call(args, 100)
    rows = []
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            pass
    (DISC / (label + ".jsonl")).write_text("".join(json.dumps(o, ensure_ascii=False)+"\n" for o in rows))
    return rows, {"url": url, "found": len(rows), "rc": rc, "error": err}

def safe_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname in ("www.twitch.tv", "twitch.tv", "clips.twitch.tv", "www.youtube.com", "youtube.com", "youtu.be"))

def extract_url(o, kind):
    url = o.get("webpage_url") or o.get("url") or ""
    id_ = str(o.get("id") or "")
    if kind == "clips":
        if url.startswith("https://") and "twitch.tv" in url:
            return url
        if id_ and re.fullmatch("[A-Za-z0-9_-]+", id_):
            return "https://clips.twitch.tv/" + id_
    if kind == "vods":
        if id_.isdigit():
            return "https://www.twitch.tv/videos/" + id_
        if url.startswith("https://www.twitch.tv/videos/"):
            return url
    if kind == "youtube":
        if len(id_) == 11 and re.fullmatch(r"[\w-]+", id_):
            return "https://www.youtube.com/watch?v=" + id_
    return None

def key_for(url):
    u=urlparse(url)
    if "youtube" in u.hostname or u.hostname == "youtu.be":
        return "yt_" + hashlib.sha1(url.encode()).hexdigest()[:15]
    slug=u.path.rstrip("/").split("/")[-1]
    slug=re.sub(r"[^A-Za-z0-9_-]", "-", slug)
    return ("vod_" if "videos/" in u.path else "clip_") + slug[:95]

source_rows=[]
discovery={}
clips,discovery["clips"]=listing("twitch_clips",SOURCE_BASE+"/clips?filter=clips&range=all",350)
vods,discovery["vods"]=listing("twitch_vods",SOURCE_BASE+"/videos?filter=all&sort=time",70)
youtube,discovery["youtube"]=listing("youtube", "https://www.youtube.com/channel/UCTiP-fGq-Uuzcor4fmp_R2Q/videos", 12)
pool=[]
if SEEDS.exists():
    for line in SEEDS.read_text().splitlines():
        line=line.strip()
        if line.startswith("https://") and safe_url(line):
            pool.append({"url":line, "kind":"clips", "discovery":"seed"})
for kind, rows in (("clips",clips),("vods",vods),("youtube",youtube)):
    for obj in rows:
        url=extract_url(obj,kind)
        if url and safe_url(url):
            pool.append({"url":url,"kind":kind,"discovery":"yt-dlp","title":obj.get("title"),
                        "duration":obj.get("duration"),"timestamp":obj.get("timestamp")})
seen=set()
by_kind={"clips":[],"vods":[],"youtube":[]}
for o in pool:
    key=key_for(o["url"])
    if key in seen: continue
    seen.add(key)
    o["source_key"]=key
    by_kind[o["kind"]].append(o)
existing=set()
for metadata in (ROOT/"corpus"/"unreviewed").glob("*.metadata.json"):
    try:
        d=json.loads(metadata.read_text())
        if d.get("transcription_ok"):
            existing.add(d["source_key"])
    except (ValueError, KeyError):
        pass
tasks=([x for x in by_kind["clips"] if x["source_key"] not in existing][:MAX_CLIPS]
       +[x for x in by_kind["vods"] if x["source_key"] not in existing][:MAX_VODS]
       +[x for x in by_kind["youtube"] if x["source_key"] not in existing][:MAX_YOUTUBE])
(DISC / "discovery_summary.json").write_text(json.dumps({"listing":discovery, "unique":{k:len(v) for k,v in by_kind.items()}, "selected":len(tasks), "previously_transcribed":len(existing)},indent=2))
print("DISCOVERY:",json.dumps({"listing":{k:v["found"] for k,v in discovery.items()},"selected":len(tasks)}),flush=True)
if not tasks:
    print("No public sources returned; retaining diagnostics.",flush=True)

model_path=ROOT/"whisper.cpp"/"models"/("ggml-"+MODEL+".bin")
whisper_exe=ROOT/"whisper.cpp"/"build"/"bin"/"whisper-cli"
for i, row in enumerate(tasks,1):
    started=time.monotonic()
    source=row["url"]
    key=row["source_key"]
    result=dict(row, download_ok=False, transcription_ok=False, speaker_verified=False, training_permission_verified=False,
                recognition_model=MODEL, curated=False, retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    template=str(OUT/"media"/(key+".%(ext)s"))
    pathlib.Path(template).parent.mkdir(exist_ok=True)
    args=["yt-dlp","--no-playlist","--no-progress","--socket-timeout","12","--retries","1","--fragment-retries","1",
          "-f","worstaudio/worst/bestaudio/best", "-o",template]
    if row["kind"]=="vods":
        args += ["--download-sections","*"+VOD_SEGMENT]
    args += [source]
    rc, stdout, err = call(args, 95 if row["kind"]=="vods" else 65)
    result["download_ok"]=rc==0
    if rc!=0: result["download_error"]=err
    candidates=list((OUT/"media").glob(key+".*"))
    if candidates:
        candidate=candidates[0]
        wav=OUT/"media"/(key+".wav")
        code,_,error=call(["ffmpeg","-y","-nostdin","-hide_banner","-loglevel","error",
                           "-i",str(candidate),"-t","245" if row["kind"]=="vods" else "60",
                           "-ar","16000","-ac","1",str(wav)],55)
        candidate.unlink(missing_ok=True)
        if code==0:
            base=str(TEXT/key)
            code,_,error=call([str(whisper_exe),"-m",str(model_path),"-f",str(wav),"-otxt","-osrt",
                               "-of",base,"-nt","-np"],100 if row["kind"]=="vods" else 65)
            if code==0 and (TEXT/(key+".txt")).exists():
                transcript=(TEXT/(key+".txt")).read_text()
                # Some ASR outputs are silence placeholders, not natural language.
                actual=re.sub(r"\[(?:BLANK_AUDIO|Music|Silence|Applause)\]","",transcript, flags=re.I).strip()
                result["transcription_ok"]=True
                result["word_count_raw"]=len(transcript.split())
                result["word_count_nonplaceholder"]=len(actual.split())
                result["srt"]=key+".srt"
                result["text_file"]=key+".txt"
                result["text_sha256"]=hashlib.sha256(transcript.encode()).hexdigest()
            else: result["transcription_error"]=error
        else: result["transcription_error"]=error
        wav.unlink(missing_ok=True)
    result["seconds_elapsed"]=round(time.monotonic()-started,1)
    (TEXT/(key+".metadata.json")).write_text(json.dumps(result,indent=2,ensure_ascii=False))
    source_rows.append(result)
    print(f"{i}/{len(tasks)} {key} fetched={result['download_ok']} asr={result['transcription_ok']} words={result.get('word_count_nonplaceholder',0)}",flush=True)
shutil.rmtree(OUT/"media", ignore_errors=True)
(OUT/"records.jsonl").write_text("".join(json.dumps(o,ensure_ascii=False)+"\n" for o in source_rows))
summary={"sources_discovered":{k:len(v) for k,v in by_kind.items()},"attempted":len(source_rows),
         "downloaded":sum(bool(r["download_ok"]) for r in source_rows),
         "transcribed":sum(bool(r["transcription_ok"]) for r in source_rows),
         "raw_words":sum(r.get("word_count_raw",0) for r in source_rows),
         "nonplaceholder_words":sum(r.get("word_count_nonplaceholder",0) for r in source_rows),
         "rights":"not verified","speaker_attribution":"not verified","model":MODEL}
(OUT/"summary.json").write_text(json.dumps(summary,indent=2))
print("SUMMARY:",json.dumps(summary),flush=True)
