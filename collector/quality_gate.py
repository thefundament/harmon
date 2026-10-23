#!/usr/bin/env python3
"""Offline, heuristic ASR triage. Scores are NOT correctness probabilities."""
import argparse, collections, hashlib, json, pathlib, re

NOISE_RE = re.compile(r"(?i)\[(?:music|silence|blank_audio|applause|laughter|noise)\]")
TOKEN_RE = re.compile(r"\b[\w']+\b", re.UNICODE)
SRT_TIME_RE = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d),(\d\d\d)")

def clean_text(text):
    return re.sub(r"\s+"," ",NOISE_RE.sub(" ",text)).strip()

def tokens(text):
    return TOKEN_RE.findall(clean_text(text).casefold())

def normalized_fingerprint(text):
    return hashlib.sha256(" ".join(tokens(text)).encode("utf-8")).hexdigest()

def segments_from_srt(content):
    blocks = re.split(r"\n\s*\n", content.strip())
    segments=[]
    for block in blocks:
        match=SRT_TIME_RE.search(block)
        if not match: continue
        numbers=list(map(int,match.groups()))
        start=((numbers[0]*60+numbers[1])*60+numbers[2])*1000+numbers[3]
        end=((numbers[4]*60+numbers[5])*60+numbers[6])*1000+numbers[7]
        payload=block[match.end():].strip()
        if end>start and payload:
            segments.append({"start_ms":start,"end_ms":end,"text":clean_text(payload)})
    return segments

def evaluate(metadata, text, srt=""):
    words=tokens(text)
    segments=segments_from_srt(srt) if srt else []
    nonempty=[s for s in segments if tokens(s["text"])]
    duration_s=(max(s["end_ms"] for s in segments)-min(s["start_ms"] for s in segments))/1000 if segments else None
    unique_ratio=len(set(words))/len(words) if words else 0.0
    long_repetition= len(words)>12 and unique_ratio<0.18
    flags=[]
    if not metadata.get("transcription_ok"): flags.append("asr_failed")
    if not words: flags.append("no_recognized_speech")
    if 0<len(words)<8: flags.append("too_short_for_style")
    if long_repetition: flags.append("possible_asr_loop")
    if not segments: flags.append("missing_timestamps")
    if metadata.get("kind")=="vods" and len(words)<12: flags.append("low_speech_density")
    if not metadata.get("source_url"): flags.append("missing_source")
    if len(words)>0 and len(nonempty)==0: flags.append("no_timed_speech")
    # This is a *triage priority* only. No transcript accuracy or speaker probability inferred.
    score=(min(50,len(words))*1.0 + min(20,len(nonempty)*3)
           + (10 if metadata.get("source_url") else 0)
           + (10 if metadata.get("transcription_ok") else 0))
    if long_repetition: score-=40
    if len(words)<8: score-=35
    score=max(0,min(100,int(score)))
    if not words or not metadata.get("transcription_ok"):
        disposition="exclude_pending_review"
    elif score>=65 and not long_repetition:
        disposition="high_priority_for_speaker_review"
    else:
        disposition="manual_review"
    return {
        "source_key":metadata.get("source_key"),
        "source_url":metadata.get("source_url"),
        "source_kind":metadata.get("kind"),
        "recognition_model":metadata.get("recognition_model","unknown"),
        "word_count":len(words),
        "timestamped_segments":len(nonempty),
        "duration_s":duration_s,
        "text_sha256":hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "normalized_text_sha256":normalized_fingerprint(text),
        "review_priority_score_not_accuracy":score,
        "disposition":disposition,
        "flags":flags,
        "speaker_status":"unknown",
        "speaker_verified":False,
        "transcription_verified":False,
        "training_permission_verified":False,
        "training_ready":False
    }

def run(root):
    source_dirs=(root/"corpus"/"unreviewed",root/"corpus"/"local_transcripts")
    out=root/"corpus"/"quality"
    out.mkdir(parents=True,exist_ok=True)
    records=[]
    for source in source_dirs:
        for p in sorted(source.glob("*.metadata.json")):
            try:
                m=json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError,UnicodeDecodeError):
                continue
            stem=p.name[:-len(".metadata.json")]
            txt=source/(stem+".txt")
            if not txt.is_file(): continue
            srt=source/(stem+".srt")
            if source.name=="local_transcripts":
                m["transcription_ok"]=m.get("processing_status") in ("transcribed","no_speech")
                m["source_key"]="local:"+str(m.get("task_id",stem))
                if m.get("kind")=="vod":m["kind"]="vods"
                elif m.get("kind")=="clip":m["kind"]="clips"
                m["recognition_model"]=m.get("recognition_model","small.en")
            row=evaluate(m,txt.read_text(encoding="utf-8",errors="replace"),
                         srt.read_text(encoding="utf-8",errors="replace") if srt.exists() else "")
            row["transcript_path"]="corpus/"+source.name+"/"+txt.name
            records.append(row)
    duplicate_groups=collections.defaultdict(list)
    for r in records:
        if r["word_count"]>=5: duplicate_groups[r["normalized_text_sha256"]].append(r["source_key"])
    for r in records:
        dup=duplicate_groups.get(r["normalized_text_sha256"],[])
        r["duplicate_of"]=dup[0] if len(dup)>1 and r["source_key"]!=dup[0] else None
        if r["duplicate_of"]:
            r["flags"].append("duplicated_text")
            r["disposition"]="manual_review"
    records.sort(key=lambda r:(-r["review_priority_score_not_accuracy"],-r["word_count"],str(r["source_key"])))
    (out/"review_queue.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in records),encoding="utf-8")
    aggregate={
        "transcripts_evaluated":len(records),
        "review_priority_high":sum(r["disposition"]=="high_priority_for_speaker_review" for r in records),
        "review_priority_manual":sum(r["disposition"]=="manual_review" for r in records),
        "excluded_pending_review":sum(r["disposition"]=="exclude_pending_review" for r in records),
        "exact_duplicate_text":sum(bool(r["duplicate_of"]) for r in records),
        "words_in_queue":sum(r["word_count"] for r in records),
        "confirmed_speaker_words":0,
        "training_ready_words":0,
        "note":"Review priority is heuristic, not a probability of accuracy or speaker identity."
    }
    (out/"summary.json").write_text(json.dumps(aggregate,indent=2)+"\n",encoding="utf-8")
    return aggregate

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    print(json.dumps(run(args.root),indent=2))
