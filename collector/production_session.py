"""Prepare and summarize one explicitly authorized eight-hour production session."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import time

from . import production_candidate as candidate

worker, followup, pilot = candidate.worker, candidate.followup, candidate.pilot


def prepare(directory):
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    tasks=worker.load_manifest()
    journal=worker.load_journal(worker.WORK/"state.json")
    tasks=[t for t in tasks if not worker.completed(t,worker.OUTPUT)
           and worker.eligible(t,journal,worker.OUTPUT)[0]]
    if not tasks:
        raise RuntimeError("No eligible pending production work")
    profile=worker.WORK/"speaker_profile.json"
    _,_,loaded=candidate.trial.speaker.load_profile(profile)
    preserved={**followup.hashes(pilot.WORK.rglob("*")), **followup.hashes(followup.WORK.rglob("*")),
        **followup.hashes(candidate.trial.WORK.rglob("*")), **followup.hashes(worker.OUTPUT.glob("*")),
        str(profile):loaded["_loaded_sha256"]}
    (directory/"manifest.jsonl").write_text("".join(pilot.json.dumps(t)+"\n" for t in tasks))
    record=dict(created_utc=worker.now(),started_unix=time.time(),max_seconds=28800,
        pipeline=candidate.PIPELINE,pending_windows=len(tasks),profile_sha256=loaded["_loaded_sha256"],
        raw_transcripts_preserved=True,repetition_cleanup_deferred=True,
        authorization="User approved production promotion as an unverified first pass, accepting later repetition cleanup, and requested one unattended eight-hour run.",
        preserved_sha256=preserved, **followup.FLAGS)
    worker.atomic_json(directory/"session.json",record)
    print(directory,flush=True)


def finish(directory):
    frozen=pilot.read_json(directory/"session.json")
    # The worker has exited. Clean only worker-owned orphan media, under its lock.
    with (worker.WORK/"worker.lock").open("a") as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        worker.clean_orphaned_media(worker.WORK)
        candidate.trial.cleanup(worker.WORK/"production-candidate")
    worker_paths=frozen["preserved_sha256"]
    pilot.verify_preserved(worker_paths)
    results=[]
    for task in worker.load_manifest(directory/"manifest.jsonl"):
        if worker.completed(task,worker.OUTPUT):
            metadata=pilot.read_json(worker.result_paths(task,worker.OUTPUT)[0])
            if metadata.get("pipeline")!=candidate.PIPELINE:
                continue
            if any(metadata[k] is not False for k in followup.FLAGS):
                raise RuntimeError("Production verification/training flag changed")
            results.append(metadata)
    memory=directory/"memory.json"
    metrics=pilot.read_json(memory) if memory.exists() else {}
    summary=dict(finished_utc=worker.now(),elapsed_seconds=time.time()-frozen["started_unix"],
        completed=len(results),source_audio_seconds=sum(r["processed_audio_seconds"] for r in results),
        recognized_words=sum(r["recognized_words"] for r in results),
        repetition_flagged=sum(r["repetition_metrics"]["repeated_5gram_excess"]>0 for r in results),
        strong_repetition_flags=sum(r["repetition_metrics"]["repeated_5gram_fraction"]>=.1 for r in results),
        preserved_files=len(worker_paths),preserved_inputs_unchanged=True,raw_transcripts_preserved=True,
        memory_guard=metrics, **followup.FLAGS)
    worker.atomic_json(directory/"summary.json",summary)
    (directory/"REPORT.md").write_text("# Eight-hour production session\n\n"
        f"{summary['completed']} completed outputs; {summary['source_audio_seconds']/3600:.2f} hours of source audio; "
        f"{summary['recognized_words']} words. {summary['repetition_flagged']} repetition flags, "
        f"including {summary['strong_repetition_flags']} at or above the 10% diagnostic threshold.\n\n"
        "Original text and native timestamps are retained. Repetition cleanup is deferred; legitimate repetition stays intact. "
        "All voices are decoded and profile overlap remains unverified. Speaker/training verification flags remain false.\n\n"
        f"{summary['preserved_files']} pre-session file hashes unchanged. Worker-owned temporary audio cleaned. "
        "The session stops automatically and has no restart policy or recurring schedule. See production.log and memory.json for exit details.\n")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("prepare","finish"))
    parser.add_argument("--directory",type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    if args.action=="prepare":
        with followup.locks(worker.WORK/"production-launch"):
            prepare(args.directory)
    else:
        finish(args.directory)


if __name__=="__main__":
    main()
