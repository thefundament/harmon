#!/usr/bin/env python3
"""Zero-LLM summary of voice-gate costs from actual worker-produced metadata."""
import argparse,json,statistics
from pathlib import Path

def report(directory):
    records=[]
    for p in Path(directory).glob("*.metadata.json"):
        try:record=json.loads(p.read_text(encoding="utf-8"))
        except (OSError,ValueError):continue
        f=record.get("voice_filter")
        if isinstance(f,dict):
            records.append((record,f))
    if not records:
        return {"measured_records":0,"verdict":"not benchmarked"}
    filter_seconds=sum(f.get("voice_filter_elapsed_s") or 0 for _,f in records)
    whisper_seconds=sum(row.get("transcription_seconds") or 0 for row,_ in records)
    source_seconds=sum(f.get("examined_audio_seconds") or 0 for _,f in records)
    matched_seconds=sum(f.get("matched_audio_seconds") or 0 for _,f in records)
    # This is directly measured gate walltime, not a controlled A/B benchmark.
    ratio=filter_seconds/max(whisper_seconds,0.000001)
    return {
        "measured_records":len(records),
        "total_filter_seconds":round(filter_seconds,2),
        "total_filtered_whisper_seconds":round(whisper_seconds,2),
        "gate_to_filtered_asr_time_ratio":round(ratio,4),
        "fraction_audio_sent_to_whisper":round(matched_seconds/max(source_seconds,.000001),4),
        "no_target_voice_windows":sum(bool(f.get("no_match")) for _,f in records),
        "model_names":sorted(set(row.get("recognition_model","") for row,_ in records)),
        "note":"This is observational. Compare against unfiltered runs on identical source windows "
               "before claiming total time saved or a 10% overhead target."
    }

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--directory",type=Path,default=Path("corpus/local_transcripts"))
    print(json.dumps(report(p.parse_args().directory),indent=2))
