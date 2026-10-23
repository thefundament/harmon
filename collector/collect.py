import json, subprocess, pathlib, sys
out=pathlib.Path("output"); out.mkdir(exist_ok=True)
url=pathlib.Path("requests/initial.txt").read_text().strip()
def cmd(args,timeout=120):
 try:
  p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
  return {"code":p.returncode,"stdout":p.stdout[-1000:],"stderr":p.stderr[-2500:]}
 except Exception as e: return {"error":str(e)}
meta=cmd(["yt-dlp","--no-playlist","--print","%(title)s | %(duration)s | %(uploader)s", "--skip-download",url],90)
audio=cmd(["yt-dlp","--no-playlist","--no-progress","-f","bestaudio/best","-o","output/clip.%(ext)s",url],120)
print("Metadata:",meta,"Audio:",audio,flush=True)
report={"url":url,"metadata":meta,"audio_result":audio}
sources=[p for p in out.iterdir() if p.name.startswith("clip.") and p.suffix not in (".json",".txt")]
if sources:
 if True:
  try:
   wav=out/"clip.wav"
   cv=cmd(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(sources[0]),"-t","60","-ar","16000","-ac","1",str(wav)],90)
   if cv.get("code")!=0: raise RuntimeError("audio conversion failed: "+str(cv))
   t=cmd(["whisper.cpp/build/bin/whisper-cli","-m","whisper.cpp/models/ggml-tiny.en.bin","-f",str(wav),"-otxt","-osrt","-of","output/transcript","-nt","-np"],150)
   if t.get("code")!=0: raise RuntimeError("whisper failed: "+str(t))
   report["words"]=len((out/"transcript.txt").read_text().split())
   report["transcript_engine"]="whisper.cpp ggml-tiny.en"
   (out/"transcript.json").write_text(json.dumps({"source_url":url,"speaker_verified":False,"training_permission_verified":False,"text":(out/"transcript.txt").read_text()},indent=2))
   wav.unlink(missing_ok=True)
   print("TRANSCRIBED WORDS:",report["words"],flush=True)
  except Exception as e:report["transcription_error"]=str(e)
 sources[0].unlink(missing_ok=True)
(out/"result.json").write_text(json.dumps(report,indent=2))
print("FINAL:",json.dumps({k:v for k,v in report.items() if k not in ("metadata","audio_result")}),flush=True)
