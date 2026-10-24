#!/usr/bin/env python3
"""Index both historical hosted-runner transcripts and newer local-worker outputs."""
from __future__ import annotations
import argparse, json, pathlib

def run(root: pathlib.Path):
    base=root/"corpus"
    entries=[]
    for dirname in ("unreviewed","local_transcripts"):
        for path in sorted((base/dirname).glob("*.metadata.json")):
            try:
                row=json.loads(path.read_text(encoding="utf-8"))
            except (ValueError,UnicodeDecodeError):
                continue
            if dirname=="local_transcripts":
                success=row.get("processing_status") in ("transcribed","no_speech")
                task=row.get("task_id", path.name)
                entry={
                    "key":"local:"+task,
                    "task_id":task,
                    "source_url":row.get("source_url"),
                    "source_kind":row.get("kind"),
                    "window_start_s":row.get("source_window_start_s"),
                    "window_end_s":row.get("source_window_end_s"),
                    "recognized_words":row.get("recognized_words",0),
                    "recognition_model":row.get("recognition_model"),
                    "asr_completed":success,
                    "processing_status":row.get("processing_status"),
                    "processing_completed":success or row.get("processing_status")=="no_target_speech",
                    "speaker_status":row.get("speaker_status","unverified"),
                    "speaker_verified":False,
                    "training_permission_verified":False,
                    "transcript_path":str(path.relative_to(base)).replace(".metadata.json",".txt"),
                }
            else:
                entry={
                    "key":"legacy:"+str(row.get("source_key",path.name)),
                    "task_id":None,
                    "source_url":row.get("source_url"),
                    "source_kind":row.get("kind"),
                    "window_start_s":None,"window_end_s":None,
                    "recognized_words":row.get("word_count_nonplaceholder",0),
                    "recognition_model":row.get("recognition_model"),
                    "asr_completed":bool(row.get("transcription_ok")),
                    "speaker_verified":False,
                    "training_permission_verified":False,
                    "transcript_path":str(path.relative_to(base)).replace(".metadata.json",".txt"),
                }
            entries.append(entry)
    dest=base/"combined";dest.mkdir(parents=True,exist_ok=True)
    (dest/"records.jsonl").write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in entries),encoding="utf-8")
    grouped={}
    for row in entries:
        grouped[row["source_kind"]]=grouped.get(row["source_kind"],0)+1
    summary={
        "local_completed_outputs":sum(x["key"].startswith("local:") and x.get("processing_completed",False) for x in entries),
        "local_no_target_speech_outputs":sum(x.get("processing_status")=="no_target_speech" for x in entries),
        "legacy_asr_outputs":sum(x["key"].startswith("legacy:") and x["asr_completed"] for x in entries),
        "local_window_asr_outputs":sum(x["key"].startswith("local:") and x["asr_completed"] for x in entries),
        "total_asr_outputs":sum(x["asr_completed"] for x in entries),
        "total_recognized_words_not_deduplicated":sum(int(x["recognized_words"] or 0) for x in entries),
        "source_kinds_raw":grouped,
        "warning":"VOD windows can overlap old VOD samples. These counts are NOT unique streamer speech or training-ready words.",
        "speaker_verified_words":0,
        "training_ready_words":0,
    }
    (dest/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    return summary

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--root",type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[1])
    print(json.dumps(run(p.parse_args().root),indent=2))
