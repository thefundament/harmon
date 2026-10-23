#!/usr/bin/env python3
"""Small bounded development benchmark: compare Whisper Base and Small, no false WER claims."""
import difflib, hashlib, json, os, pathlib, re, subprocess, time

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"benchmark-output"
MEDIA=OUT/"temp-media"
MEDIA.mkdir(parents=True,exist_ok=True)
SOURCES=[
    "https://www.twitch.tv/simplez4ck/clip/BoredBetterStingrayStrawBeary-55MU-s8DDAgR3bPq",
    "https://clips.twitch.tv/HonorableDeterminedEggplantVoHiYo-pPXss_2so-KZ2hck"
]
def run(cmd,timeout):
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout)
    if p.returncode:
        raise RuntimeError("exit %s %s"%(p.returncode,p.stderr[-800:]))
    return p.stdout

def normalize(s):
    return re.findall(r"\b[\w']+\b",s.lower(),re.UNICODE)

def disagreement(a,b):
    aa,bb=normalize(a),normalize(b)
    matcher=difflib.SequenceMatcher(None,aa,bb,autojunk=False)
    different=sum(max(i2-i1,j2-j1) for tag,i1,i2,j1,j2 in matcher.get_opcodes() if tag!="equal")
    return round(different/max(1,max(len(aa),len(bb))),4)

def benchmark():
    all_rows=[]
    for i,url in enumerate(SOURCES,1):
        template=str(MEDIA/("sample"+str(i)+".%(ext)s"))
        result={"url":url,"results":{},"model_disagreement_not_error_rate":None}
        try:
            run(["yt-dlp","--no-playlist","--no-progress","-f","bestaudio/best","-o",template,url],70)
            candidates=list(MEDIA.glob("sample%d.*"%i))
            if not candidates:raise RuntimeError("no media downloaded")
            wav=MEDIA/("sample%d.wav"%i)
            run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(candidates[0]),"-t","45","-ar","16000","-ac","1",str(wav)],30)
            candidates[0].unlink(missing_ok=True)
            texts={}
            for model in ("base.en","small.en"):
                prefix=MEDIA/("sample%d_%s"%(i,model))
                cmd=["whisper.cpp/build/bin/whisper-cli","-m",str(ROOT/"whisper.cpp"/"models"/("ggml-"+model+".bin")),
                     "-f",str(wav),"-otxt","-of",str(prefix),"-nt","-np"]
                tick=time.monotonic()
                run(cmd,150)
                sec=round(time.monotonic()-tick,2)
                text=(pathlib.Path(str(prefix)+".txt")).read_text()
                texts[model]=text
                result["results"][model]={"words":len(normalize(text)),"seconds":sec,
                                          "text_sha256":hashlib.sha256(text.encode()).hexdigest()}
                pathlib.Path(str(prefix)+".txt").unlink(missing_ok=True)
            result["model_disagreement_not_error_rate"]=disagreement(texts["base.en"],texts["small.en"])
            # Diff markers, not full copyrighted transcripts.
            a,b=normalize(texts["base.en"]),normalize(texts["small.en"])
            diffs=[]
            for tag,i1,i2,j1,j2 in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
                if tag!="equal" and len(diffs)<10:
                    diffs.append({"base_tokens":" ".join(a[i1:i2])[:80],"small_tokens":" ".join(b[j1:j2])[:80]})
            result["disagreements"]=diffs
            wav.unlink(missing_ok=True)
        except Exception as e:
            result["error"]=str(e)
        all_rows.append(result)
    output={"purpose":"testing ASR pipeline; NOT benchmark word error rate without gold labels",
            "samples":all_rows}
    (OUT/"asr_comparison.json").write_text(json.dumps(output,indent=2)+"\n")
    for f in MEDIA.glob("*"):f.unlink(missing_ok=True)
    return output

if __name__=="__main__":
    print(json.dumps(benchmark(),indent=2))
