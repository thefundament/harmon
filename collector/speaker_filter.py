#!/usr/bin/env python3
"""No-LLM speaker prefilter. Real reference enrollment is required for acceptance.

Uses local WebRTC VAD and Resemblyzer voice embeddings. Only PCM WAV input.
No audio is saved by enrollment; the worker's temporary compact WAV is deleted.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
from array import array
import hashlib
import json
import math
import os
import re
from pathlib import Path
import statistics
import sys
import wave

RATE=16000
FRAME_MS=30
FRAME_SAMPLES=RATE*FRAME_MS//1000
MODEL="resemblyzer_voiceencoder_pretrained_v1"
PROFILE_SCHEMA=1
MIN_REFERENCES=5
MIN_NEGATIVES=3

class UncalibratedProfile(RuntimeError): pass

def read_pcm(path):
    with wave.open(str(path),"rb") as f:
        if (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getcomptype()) != (1,2,RATE,"NONE"):
            raise ValueError("Speaker gate requires mono 16kHz 16-bit PCM WAV")
        data=f.readframes(f.getnframes())
    pcm=array("h")
    pcm.frombytes(data)
    if sys.byteorder!="little":
        pcm.byteswap()
    return pcm

def write_pcm(path,samples):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),"wb") as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(RATE)
        raw=array("h",samples)
        if sys.byteorder!="little":raw.byteswap()
        f.writeframes(raw.tobytes())

def dot(a,b):
    return sum(x*y for x,y in zip(a,b))

def unit(values):
    values=[float(x) for x in values]
    if not values or not all(math.isfinite(x) for x in values):
        raise ValueError("invalid embedding")
    norm=math.sqrt(sum(x*x for x in values))
    if norm<1e-10:raise ValueError("degenerate embedding")
    return [x/norm for x in values]

def mean_embed(xs):
    if not xs:raise ValueError("missing speaker embeddings")
    if len({len(x) for x in xs})!=1:raise ValueError("embedding size mismatch")
    return unit([sum(x[i] for x in xs)/len(xs) for i in range(len(xs[0]))])

def speech_spans(pcm, vad=None, min_voiced_seconds=1.8, max_span_seconds=12,
                 gap_seconds=0.38, aggressiveness=2):
    """Cheap WebRTC gate; contiguous voiced regions grouped into speaker-size chunks.

    Segmentation is voice activity, NOT identity. Short islands are left unknown,
    and silence doesn't consume speaker encoder time.
    """
    if vad is None:
        try:import webrtcvad
        except ImportError as exc:raise RuntimeError("pip install webrtcvad-wheels") from exc
        vad=webrtcvad.Vad(aggressiveness)
    frame=FRAME_SAMPLES
    voiced=[]
    for i in range(0,len(pcm)-frame+1,frame):
        chunk=pcm[i:i+frame]
        raw=array("h",chunk)
        if sys.byteorder!="little":raw.byteswap()
        if vad.is_speech(raw.tobytes(),RATE):
            voiced.append((i,i+frame))
    if not voiced:return []
    maxgap=int(gap_seconds*RATE)
    limit=int(max_span_seconds*RATE)
    merged=[]
    a,b=voiced[0]
    for x,y in voiced[1:]:
        if x-b<=maxgap and y-a<=limit:b=y
        else:
            merged.append((a,b));a,b=x,y
    merged.append((a,b))
    min_len=int(min_voiced_seconds*RATE)
    return [(a,b) for a,b in merged if b-a>=min_len]

@lru_cache(maxsize=1)
def backend():
    """Lazy import: ordinary repository CI has no ML dependencies."""
    try:
        import numpy as np
        import torch
        from resemblyzer import VoiceEncoder
    except ImportError as exc:
        raise RuntimeError("Speaker filter dependencies missing: run scripts/bootstrap_speaker.sh") from exc
    torch.set_num_threads(1)
    encoder=VoiceEncoder(device="cpu",verbose=False)
    def embed(pcm):
        samples=np.asarray(pcm,dtype=np.float32)/32768.0
        return unit(encoder.embed_utterance(samples).tolist())
    return embed

def max_false_positive_threshold(positives,negatives,margin=.025):
    """No magic fixed similarity. Derive conservative threshold from references.

    Positive validation uses leave-one-out centroids, negative validation the full
    positive centroid. If distributions overlap, refuse enrollment.
    """
    if len(positives)<MIN_REFERENCES or len(negatives)<MIN_NEGATIVES:
        raise UncalibratedProfile("Need >=5 verified positive and >=3 verified negative reference clips")
    centroid=mean_embed(positives)
    if any(len(x)!=len(centroid) for x in positives+negatives):
        raise UncalibratedProfile("Embedding dimension mismatch")
    pos_scores=[dot(p,mean_embed(positives[:i]+positives[i+1:])) for i,p in enumerate(positives)]
    neg_scores=[dot(n,centroid) for n in negatives]
    # Conservative sensitivity threshold, not a confidence probability.
    pos_floor=sorted(pos_scores)[max(0,int(len(pos_scores)*.15)-1)]
    highest_negative=max(neg_scores)
    threshold=max(highest_negative+margin, pos_floor-.02)
    if threshold>pos_floor+.005:
        raise UncalibratedProfile(
            f"Positive/negative voice scores overlap: pos floor={pos_floor:.3f}, "
            f"max negative={highest_negative:.3f}; use cleaner reference examples")
    return {"threshold":round(threshold,5),"positive_holdout_min":round(min(pos_scores),5),
            "positive_holdout_floor":round(pos_floor,5),
            "negative_max":round(highest_negative,5),
            "training_false_accepts":sum(s>=threshold for s in neg_scores),
            "positive_holdout_accepts":sum(s>=threshold for s in pos_scores),
            "positive_total":len(pos_scores),"negative_total":len(neg_scores),
            "margin":margin,"model":MODEL,"schema":PROFILE_SCHEMA}

def build_profile(reference_manifest,output,embedder=None):
    """Manifest must contain independently HUMAN-LABELLED recordings.

    Each entry: path, label='harmon'|'other', source_id, reviewer.
    Never automatically relabel owner-channel audio as the target voice.
    """
    manifest=Path(reference_manifest)
    rows=json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(rows,list):raise ValueError("reference manifest must be an array")
    pos=[];neg=[];positive_sources=set();negative_sources=set()
    model=embedder or backend()
    labels=[]
    for row in rows:
        if not isinstance(row,dict) or row.get("label") not in ("harmon","other"):
            raise ValueError("each reference needs an explicit harmon/other label")
        if not row.get("reviewer") or not row.get("source_id"):
            raise ValueError("reviewer and source_id required for all reference clips")
        file=Path(row["path"]).expanduser()
        if not file.is_absolute():file=manifest.parent/file
        if not file.is_file():raise ValueError(f"Missing reference WAV: {file}")
        pcm=read_pcm(file)
        if len(pcm)<int(RATE*2.5):
            raise ValueError("A reference must be at least 2.5 seconds")
        embedding=model(pcm)
        src=str(row["source_id"])
        if row["label"]=="harmon":
            pos.append(embedding);positive_sources.add(src)
        else:
            neg.append(embedding);negative_sources.add(src)
        labels.append({"source_id":src,"label":row["label"],"reviewer":row["reviewer"],
                       "audio_sha256":hashlib.sha256(file.read_bytes()).hexdigest()})
    if len(positive_sources)<3 or len(negative_sources)<2:
        raise UncalibratedProfile("Use target speech from >=3 sessions and other voices from >=2 sources")
    stats=max_false_positive_threshold(pos,neg)
    profile={"schema":PROFILE_SCHEMA,"model":MODEL,
             "centroid":mean_embed(pos),"calibration":stats,
             "reference_provenance":labels,"speaker":"likely_harmon",
             "identity_evidence":"human_labeled_reference_clips",
             "note":"Threshold is not an accuracy probability; per-segment attribution remains probabilistic."}
    out=Path(output);out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    # The profile embeds biometric features; NEVER commit reference/profile.
    tmp=out.with_name(out.name+".tmp")
    tmp.write_text(json.dumps(profile,indent=2)+"\n",encoding="utf-8")
    os.chmod(tmp,0o600)
    os.replace(tmp,out)
    return {"positive":len(pos),"negative":len(neg),
            "threshold":stats["threshold"],"positive_sessions":len(positive_sources)}

def load_profile(file):
    p=Path(file)
    if not p.is_file():
        raise UncalibratedProfile("No reviewed Harmon reference profile; speaker-gated processing disabled")
    record=json.loads(p.read_text(encoding="utf-8"))
    if record.get("schema")!=PROFILE_SCHEMA or record.get("model")!=MODEL:
        raise UncalibratedProfile("Unsupported reference embedding model or schema")
    calibration=record.get("calibration") or {}
    threshold=calibration.get("threshold")
    if not isinstance(threshold,(int,float)) or not (-1<threshold<1):
        raise UncalibratedProfile("Missing calibrated voice threshold")
    if len(record.get("reference_provenance",[]))<MIN_REFERENCES+MIN_NEGATIVES:
        raise UncalibratedProfile("Insufficient reviewed reference provenance")
    centroid=unit(record["centroid"])
    return centroid,float(threshold),record

def segments_with_match(pcm,centroid,threshold,embedder=None,vad=None):
    model=embedder or backend()
    regions=speech_spans(pcm,vad=vad)
    kept=[];unknown=[]
    for start,end in regions:
        try:
            score=dot(model(pcm[start:end]),centroid)
            if score>=threshold:kept.append((start,end,score))
            else:unknown.append((start,end,score))
        except (ValueError,RuntimeError) as exc:
            unknown.append((start,end,None))
    return kept,unknown

def compact_accepted(pcm,spans,pad_s=.30):
    """Concatenate accepted speech with a silence separator; returns offset map."""
    combined=array("h");mapping=[]
    gap=array("h",[0])*int(RATE*pad_s)
    for start,end,score in spans:
        if mapping:combined.extend(gap)
        cstart=len(combined)
        combined.extend(pcm[start:end])
        mapping.append({"compact_start_s":round(cstart/RATE,5),
                        "compact_end_s":round(len(combined)/RATE,5),
                        "source_start_s":round(start/RATE,5),
                        "source_end_s":round(end/RATE,5)})
    return combined,mapping

SRT_TIMING=re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s+-->\s+(\d\d):(\d\d):(\d\d),(\d\d\d)")

def _srt_ms(groups):
    a,b,c,ms=map(int,groups)
    return ((a*60+b)*60+c)*1000+ms

def _srt_time(ms):
    ms=max(0,int(round(ms)))
    h,rest=divmod(ms,3600000);m,rest=divmod(rest,60000)
    sec,millis=divmod(rest,1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{millis:03d}"

def map_time(t,mapping,start_side=True):
    """Approximate source time, even if ASR crosses compacted speech boundaries."""
    if not mapping:
        return t
    for i,region in enumerate(mapping):
        a,b=region["compact_start_s"],region["compact_end_s"]
        if a<=t<=b:
            return region["source_start_s"]+max(0,min(t-a,b-a))
        if t<a:
            # A short inserted silence between segments is not source speech.
            prev=mapping[max(i-1,0)]
            return (region["source_start_s"] if start_side
                    else prev["source_end_s"])
    return mapping[-1]["source_end_s"]

def remap_srt(srt,mapping):
    """Map Whisper subtitles from compact target-only audio back to source-relative time.

    The returned SRT is approximate where one subtitle spans multiple trimmed
    gaps. Precise compact-to-source intervals are preserved in metadata.
    """
    def change(match):
        start=_srt_ms(match.groups()[:4])/1000
        end=_srt_ms(match.groups()[4:])/1000
        a=map_time(start,mapping,True)
        b=map_time(end,mapping,False)
        return _srt_time(a*1000)+" --> "+_srt_time(max(a,b)*1000)
    return SRT_TIMING.sub(change,srt)

def filter_wav(source,out,profile,embedder=None,vad=None):
    centroid,threshold,reference=load_profile(profile)
    pcm=read_pcm(source)
    accepted,rejected=segments_with_match(pcm,centroid,threshold,embedder=embedder,vad=vad)
    compact,mapping=compact_accepted(pcm,accepted)
    if compact:write_pcm(out,compact)
    stats={"speaker_gate":"calibrated_reference_embedding",
        "speaker_profile_sha256":hashlib.sha256(Path(profile).read_bytes()).hexdigest(),
        "speaker_model":MODEL,"speaker_threshold":threshold,
        "matched_regions":len(accepted),"other_or_uncertain_regions":len(rejected),
        "matched_audio_seconds":round(sum((b-a)/RATE for a,b,_ in accepted),3),
        "examined_audio_seconds":round(len(pcm)/RATE,3),
        "time_mapping":mapping,"no_match":not bool(accepted)}
    return stats

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    b=sub.add_parser("enroll");b.add_argument("--references",type=Path,required=True)
    b.add_argument("--out",type=Path,default=Path(".harmon-work/speaker_profile.json"))
    f=sub.add_parser("filter");f.add_argument("--wav",type=Path,required=True)
    f.add_argument("--out",type=Path,required=True)
    f.add_argument("--profile",type=Path,default=Path(".harmon-work/speaker_profile.json"))
    args=p.parse_args()
    if args.command=="enroll":result=build_profile(args.references,args.out)
    else:result=filter_wav(args.wav,args.out,args.profile)
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":
    try:main()
    except (ValueError,RuntimeError,UncalibratedProfile) as exc:
        print("ERROR:",str(exc),file=sys.stderr)
        sys.exit(2)
