#!/usr/bin/env python3
"""Rebuild cumulative source index from current checked-in metadata."""
import datetime, json, pathlib
root=pathlib.Path(__file__).resolve().parents[1]
base=root/"corpus"
records=[]
for file in sorted((base/"unreviewed").glob("*.metadata.json")):
    try:
        r=json.loads(file.read_text())
        r["metadata_file"]="unreviewed/"+file.name
        records.append(r)
    except Exception as e:
        print("SKIP BAD RECORD",file,e)
unique={}
for r in records:
    unique[r.get("source_key") or r["metadata_file"]]=r
records=list(unique.values())
(base/"records.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in records))
ok=[r for r in records if r.get("transcription_ok")]
summary={
    "generated_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "unique_sources":len(records),
    "videos_transcribed":len(ok),
    "raw_recognized_words":sum(int(r.get("word_count_raw") or 0) for r in ok),
    "nonplaceholder_recognized_words":sum(int(r.get("word_count_nonplaceholder") or 0) for r in ok),
    "speaker_verified":0,
    "training_permission_verified":0,
    "corpus_status":"unreviewed ASR / not training-ready",
    "video_source_types":{k:sum(r.get("kind")==k and r.get("transcription_ok",False) for r in records) for k in ["clips","vods","youtube"]}
}
(base/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
