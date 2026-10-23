import json, pathlib, subprocess, re
out=pathlib.Path("output"); out.mkdir(exist_ok=True)
urls=[x.strip() for x in pathlib.Path("requests/initial.txt").read_text().splitlines() if x.strip() and not x.startswith("#")][:7]
def run(a,timeout=120):
 try:
  p=subprocess.run(a,capture_output=True,text=True,timeout=timeout)
  return p.returncode,p.stderr[-700:]
 except Exception as e:return -1,str(e)
results=[]
for i,url in enumerate(urls):
 name=f"clip_{i+1:02d}"
 row={"source_url":url,"speaker_verified":False,"training_permission_verified":False}
 template=str(out/(name+".%(ext)s"))
 code,error=run(["yt-dlp","--no-playlist","--no-progress","--socket-timeout","15","-f","bestaudio/best","-o",template,url],110)
 media=[f for f in out.glob(name+".*") if f.suffix not in (".txt",".srt",".json",".wav")]
 row["download_ok"]=code==0 and bool(media)
 row["download_error"]=error if not row["download_ok"] else ""
 if media:
  wav=out/(name+".wav")
  a,e=run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(media[0]),"-t","60","-ar","16000","-ac","1",str(wav)],80)
  media[0].unlink(missing_ok=True)
  if a==0:
   b,err=run(["whisper.cpp/build/bin/whisper-cli","-m","whisper.cpp/models/ggml-tiny.en.bin","-f",str(wav),"-otxt","-osrt","-of",str(out/name),"-nt","-np"],120)
   row["transcription_ok"]=b==0 and (out/(name+".txt")).exists()
   if row["transcription_ok"]:
    row["word_count"]=len((out/(name+".txt")).read_text().split())
    row["text_file"]=name+".txt"
   else:row["transcription_error"]=err
  else:row["transcription_error"]=e
  wav.unlink(missing_ok=True)
 results.append(row)
 print(json.dumps({"source":url,"download_ok":row["download_ok"],"transcription_ok":row.get("transcription_ok",False),"words":row.get("word_count",0)}),flush=True)
(out/"records.json").write_text(json.dumps(results,indent=2))
(out/"summary.json").write_text(json.dumps({"attempted":len(results),"transcribed":sum(bool(x.get("transcription_ok")) for x in results),"words":sum(x.get("word_count",0) for x in results)},indent=2))
