#!/usr/bin/env python3
"""Infer a recurring channel-primary voice without inventing human review labels.

Calibration measures separation of inferred voice cohorts. The withheld-source
check measures recurrence, not identity accuracy or a measured false-positive rate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from . import speaker_filter as speaker
except ImportError:
    import speaker_filter as speaker

MIN_EXAMPLES=speaker.MIN_REFERENCES+speaker.MIN_NEGATIVES

def cohort_cutoff(vectors):
    scores=sorted(speaker.dot(a,b) for i,a in enumerate(vectors) for b in vectors[i+1:])
    if len(scores)<3 or scores[-1]-scores[0]<.05:
        raise speaker.UncalibratedProfile("No separated voice cohorts in candidate recordings")
    # Otsu split of pairwise similarities; no fixed global voice-match threshold.
    best=None
    total=sum(scores)
    left=0.
    for i in range(1,len(scores)):
        left+=scores[i-1]
        if scores[i]==scores[i-1]:continue
        separation=i*(len(scores)-i)*(left/i-(total-left)/(len(scores)-i))**2
        if best is None or separation>best[0]:
            best=(separation,(scores[i-1]+scores[i])/2)
    if best is None:raise speaker.UncalibratedProfile("No distinct voice cohorts")
    return best[1]


def infer_cohorts(examples):
    if len(examples)<MIN_EXAMPLES:
        raise speaker.UncalibratedProfile("Insufficient voiced examples for automatic enrollment")
    cutoff=cohort_cutoff([e["embedding"] for e in examples])
    groups=[[i] for i in range(len(examples))]
    # Complete-link clustering prevents chains of ambiguous matches joining voices.
    while True:
        best=None
        for i,a in enumerate(groups):
            for j in range(i+1,len(groups)):
                score=min(speaker.dot(examples[x]["embedding"],examples[y]["embedding"])
                          for x in a for y in groups[j])
                if score>=cutoff and (best is None or score>best[0]):best=(score,i,j)
        if best is None:break
        _,i,j=best
        groups[i]+=groups.pop(j)
    def support(group):return len({examples[i]["source_id"] for i in group})
    groups.sort(key=lambda g:(support(g),len(g)),reverse=True)
    if support(groups[0])<5:
        raise speaker.UncalibratedProfile("No dominant recurring voice across >=5 independent recordings")
    anchors=[examples[i] for i in groups[0]]
    center=speaker.mean_embed([p["embedding"] for p in anchors])
    # The complete-link core finds a reliable recurring voice, but alone models
    # only its tightest acoustic conditions. Include train-only variants that
    # match that core across recordings; leave the score boundary ambiguous.
    positives=[e for e in examples if speaker.dot(e["embedding"],center)>=cutoff+.025
               and len({a["source_id"] for a in anchors
                        if speaker.dot(e["embedding"],a["embedding"])>=cutoff})>=2]
    negatives=[e for e in examples if speaker.dot(e["embedding"],center)<cutoff-.025]
    # Compare against distinct competing voices, rather than acoustic variants
    # of the primary voice split by the deliberately tight complete-link core.
    rivals=[g for g in groups[1:] if speaker.dot(
        speaker.mean_embed([examples[i]["embedding"] for i in g]),center)<cutoff-.025]
    if rivals and len({p["source_id"] for p in positives})<2*max(map(support,rivals)):
        raise speaker.UncalibratedProfile("Multiple recurring voices have comparable recording support")
    if len({n["source_id"] for n in negatives})<2:
        raise speaker.UncalibratedProfile("Need contrasting voice cohorts from >=2 independent recordings")
    return positives,negatives,cutoff


def calibrate(examples,heldout_sources):
    train=[e for e in examples if e["source_id"] not in heldout_sources]
    holdout=[e for e in examples if e["source_id"] in heldout_sources]
    positives,negatives,cutoff=infer_cohorts(train)
    calibration=speaker.max_false_positive_threshold(
        [e["embedding"] for e in positives],[e["embedding"] for e in negatives])
    center=speaker.mean_embed([p["embedding"] for p in positives])
    accepted={e["source_id"] for e in holdout
              if speaker.dot(e["embedding"],center)>=calibration["threshold"]}
    rejected=[e for e in holdout
              if speaker.dot(e["embedding"],center)<calibration["threshold"]]
    validation={"basis":"unlabelled_withheld_recording_recurrence",
                "examined_sources":len({e["source_id"] for e in holdout}),
                "recurring_voice_sources":len(accepted),
                "other_or_uncertain_spans":len(rejected),
                "identity_accuracy_measured":False,
                "passed":len(accepted)>=2 and len(rejected)>=1
                          and len({e["source_id"] for e in holdout})>=3}
    if not validation["passed"]:
        raise speaker.UncalibratedProfile("Recurring voice did not generalize to the withheld recordings: "
                                          +json.dumps(validation))
    provenance=[{k:v for k,v in e.items() if k!="embedding"}|
                {"label":label,"label_method":"automatic_voice_cohort"}
                for rows,label in ((positives,"primary_cohort"),(negatives,"other_cohort")) for e in rows]
    return {"schema":speaker.PROFILE_SCHEMA,"model":speaker.MODEL,"centroid":center,
            "speaker":"probable_channel_primary_speaker",
            "identity_evidence":"recurring_voice_across_independent_recordings",
            "calibration_basis":"inferred_voice_cohorts",
            "calibration":calibration,"reference_provenance":provenance,
            "automatic_validation":validation,"cohort_cutoff":cutoff,
            "heldout_sources":sorted(heldout_sources),
            "note":"Machine-inferred primary voice; no human labels or verified identity. "
                   "Cohort separation and recurrence do not measure speaker accuracy."}


def enroll(candidates,output,embedder=None,vad=None,heldout_sources=None):
    manifest=Path(candidates)
    rows=json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(rows,list):raise ValueError("candidate manifest must be an array")
    # Clip IDs cannot establish that clips came from independent streams.
    sources=sorted({r["source_id"] for r in rows
                    if str(r.get("source_id","")).startswith(("youtube:","vod:"))},
                   key=lambda s:hashlib.sha256(s.encode()).hexdigest())
    if len(sources)<12:
        raise speaker.UncalibratedProfile("Automatic enrollment needs >=12 independent long-form recordings")
    heldout=(set(heldout_sources) if heldout_sources is not None
             else set(sources[:max(3,len(sources)//4)]))
    if len(heldout)<3 or not heldout.issubset(sources) or len(set(sources)-heldout)<8:
        raise ValueError("Use >=3 withheld recordings and >=8 separate development recordings")
    embed=embedder or speaker.backend()
    examples=[]
    for row in rows:
        if row.get("source_id") not in sources:continue
        path=Path(row["path"])
        if not path.is_absolute():path=manifest.parent/path
        pcm=speaker.read_pcm(path)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        for start,end in speaker.speech_spans(pcm,vad=vad,max_span_seconds=5):
            examples.append({"source_id":row["source_id"],"source_url":row.get("source_url"),
                             "audio_sha256":digest,"start_s":start/speaker.RATE,
                             "end_s":end/speaker.RATE,"embedding":speaker.unit(embed(pcm[start:end]))})
    # Private diagnostics permit reproducing failures without refetching audio.
    speaker.save_profile(manifest.parent/"automatic_embeddings.json",
                         {"model":speaker.MODEL,"examples":examples,"heldout_sources":sorted(heldout)})
    profile=calibrate(examples,heldout)
    speaker.save_profile(output,profile)
    return {"speaker":profile["speaker"],"calibration":profile["calibration"],
            "validation":profile["automatic_validation"]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidates",type=Path,default=Path(".harmon-work/reference_review/unlabelled_references.json"))
    p.add_argument("--out",type=Path,default=Path(".harmon-work/speaker_profile.json"))
    p.add_argument("--heldout-sources",type=Path,
                   help="optional private JSON array of preselected withheld recording IDs")
    args=p.parse_args()
    heldout=json.loads(args.heldout_sources.read_text()) if args.heldout_sources else None
    print(json.dumps(enroll(args.candidates,args.out,heldout_sources=heldout),indent=2))


if __name__=="__main__":
    try:main()
    except (ValueError,RuntimeError,OSError) as exc:
        raise SystemExit("ERROR: "+str(exc))
