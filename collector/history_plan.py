#!/usr/bin/env python3
"""Plan a finite historical pass. This does not download media or start inference."""
import argparse,json,math,datetime as dt
from pathlib import Path
from urllib.parse import urlparse,parse_qs

def read_jsonl(p):
 if not p.is_file(): return []
 rows=[]
 for line in p.read_text(encoding="utf-8").splitlines():
  try:
   obj=json.loads(line)
   if isinstance(obj,dict):rows.append(obj)
  except ValueError:pass
 return rows

def identity(kind,obj):
 url=str(obj.get("webpage_url") or obj.get("url") or "")
 sid=str(obj.get("id") or "")
 if kind=="clip":
  if "/clip/" in url or "clips.twitch.tv/" in url:
   slug=urlparse(url).path.strip("/").split("/")[-1]
   if slug:
    return slug,"https://clips.twitch.tv/"+slug
 elif kind=="vod":
  if sid.startswith("v") and sid[1:].isdigit():sid=sid[1:]
  if not sid.isdigit():sid=urlparse(url).path.rstrip("/").split("/")[-1]
  if sid.isdigit():return sid,"https://www.twitch.tv/videos/"+sid
 elif kind=="youtube":
  if len(sid)!=11:sid=(parse_qs(urlparse(url).query).get("v") or [""])[0]
  if len(sid)==11 and all(c.isalnum() or c in "_-" for c in sid):
   return sid,"https://www.youtube.com/watch?v="+sid
 return None

def build(root,window=600):
 if window<60:raise ValueError("windows must be at least 60 seconds")
 seen={};d=root/"corpus"/"discovery";metadata=root/"corpus"/"unreviewed"
 def add(kind,obj):
  value=identity(kind,obj)
  if not value:return
  sid,url=value
  duration=obj.get("duration")
  try:duration=float(duration)
  except (TypeError,ValueError):duration=None
  if duration is not None and (duration<0 or not math.isfinite(duration)):duration=None
  key=(kind,sid)
  if key not in seen:
   seen[key]={"kind":kind,"source_id":sid,"source_url":url,"duration_s":duration,"title":str(obj.get("title") or "")[:160]}
  elif duration and not seen[key]["duration_s"]:seen[key]["duration_s"]=duration
 for kind,file in [("clip","twitch_clips.jsonl"),("vod","twitch_vods.jsonl"),("youtube","youtube.jsonl")]:
  for obj in read_jsonl(d/file):add(kind,obj)
 completed={"clip":set(),"vod":set(),"youtube":set()}
 for p in metadata.glob("*.metadata.json"):
  try:obj=json.loads(p.read_text(encoding="utf-8"))
  except ValueError:continue
  kind={"clips":"clip","vods":"vod","youtube":"youtube"}.get(obj.get("kind"))
  if not kind:continue
  add(kind,{"url":obj.get("source_url") or obj.get("url"),"duration":obj.get("duration"),"title":obj.get("title")})
  key=str(obj.get("source_key") or "")
  prefix={"clip":"clip_","vod":"vod_","youtube":"yt_"}[kind]
  if obj.get("transcription_ok") and key.startswith(prefix):
   completed[kind].add(key[len(prefix):])
 tasks=[]
 for (kind,sid),obj in sorted(seen.items()):
  duration=obj["duration_s"]
  ranges=[(a,min(a+window,duration)) for a in range(0,math.ceil(duration),window)] if kind in ("vod","youtube") and duration else [(None,None)]
  for start,end in ranges:
   task_id=f"{kind}:{sid}:{start}-{end}" if start is not None else f"{kind}:{sid}"
   if kind=="clip" and sid in completed["clip"]:state="asr_completed_unreviewed"
   elif kind=="vod" and sid in completed["vod"]:state="legacy_sample_range_unknown"
   else:state="pending"
   tasks.append({**obj,"task_id":task_id,"start_s":start,"end_s":end,"state":state,
                 "speaker_verified":False,"training_permission_verified":False})
 counts={st:sum(r["state"]==st for r in tasks) for st in sorted(set(r["state"] for r in tasks))}
 summary={"generated_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
          "mode":"finite historical snapshot; NO recurrence",
          "sources":{k:sum(a=="%s"%k for a,b in seen) for k in ("clip","vod","youtube")},
          "listed_vod_hours":round(sum(o["duration_s"] or 0 for o in seen.values() if o["kind"]=="vod")/3600,2),
          "listed_youtube_hours":round(sum(o["duration_s"] or 0 for o in seen.values() if o["kind"]=="youtube")/3600,2),
          "listed_total_hours":round(sum(o["duration_s"] or 0 for o in seen.values())/3600,2),
          "window_seconds":window,"work_items":len(tasks),"states":counts,
          "warning":"Source durations may overlap; previous VOD transcripts lack source time offsets. No work is scheduled by this manifest."}
 return tasks,summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument("--window",type=int,default=600);args=ap.parse_args()
 rows,summary=build(args.root,args.window)
 p=args.root/"corpus"/"history";p.mkdir(parents=True,exist_ok=True)
 (p/"manifest.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in rows))
 (p/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
 print(json.dumps(summary,indent=2))
if __name__=="__main__":main()
