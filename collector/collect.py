import json, subprocess, pathlib, re, sys
out=pathlib.Path("output"); out.mkdir(exist_ok=True)
url=pathlib.Path("requests/initial.txt").read_text().strip()
def cmd(args, timeout=150):
 try:
  p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
  return {"code":p.returncode,"stdout":p.stdout[-3000:],"stderr":p.stderr[-3000:]}
 except Exception as e: return {"error":str(e)}
info=cmd(["yt-dlp","--no-playlist","--dump-single-json","--skip-download",url],90)
(out/"probe.json").write_text(json.dumps({"url":url,"metadata":info},indent=2))
captions=cmd(["yt-dlp","--no-playlist","--skip-download","--write-auto-subs","--write-subs","--sub-langs","en.*","--sub-format","vtt","-o","output/source.%(ext)s",url],120)
(out/"status.json").write_text(json.dumps({"url":url,"caption_attempt":captions,"files":[p.name for p in out.iterdir()]},indent=2))
print(json.dumps({"metadata_exit":info.get("code"),"caption_exit":captions.get("code"),"files":[p.name for p in out.iterdir()]}))
