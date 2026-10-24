"""Publish authorized, complete production outputs alongside a running worker."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from . import production_candidate as candidate

worker, pilot = candidate.worker, candidate.pilot


def destination():
    raw=worker.git_cmd(["remote","get-url","--push","origin"],capture=True).strip()
    fetch=worker.git_cmd(["remote","get-url","origin"],capture=True).strip()
    if "://" in raw:
        parsed=urlsplit(raw)
        name=(parsed.hostname or "")+parsed.path
    else:
        name=raw.split("@")[-1].replace(":","/",1)
    return dict(remote="origin",destination=name,remote_sha256=hashlib.sha256((raw+"\0"+fetch).encode()).hexdigest())


def authorize(directory):
    path=directory/"session.json"
    session=pilot.read_json(path)
    session.update(remote_publishing_enabled=True,publication=destination(),
                   publication_authorized_utc=worker.now(),
                   publication_authorization="User explicitly authorized publishing this production run to its configured repository remote.")
    worker.atomic_json(path,session)


def pending(directory):
    tasks={t["task_id"]:t for t in worker.load_manifest(directory/"manifest.jsonl")}
    untracked=worker.git_cmd(["ls-files","--others","--exclude-standard","--","corpus/local_transcripts/"],capture=True)
    modified=worker.git_cmd(["diff","--name-only","HEAD","--","corpus/local_transcripts/"],capture=True)
    result=[]
    for name in sorted(set((untracked+"\n"+modified).splitlines())):
        if not name.endswith(".metadata.json"):
            continue
        path=worker.REPO/name
        metadata=pilot.read_json(path)
        task=tasks.get(metadata.get("task_id"))
        if task is None or metadata.get("pipeline")!=candidate.PIPELINE:
            continue
        if any(metadata.get(key) is not False for key in candidate.followup.FLAGS):
            raise RuntimeError("Refusing verified/training-ready production output")
        if worker.completed(task,worker.OUTPUT):
            result.append(task)
    return result


def publish(directory,force=False):
    with (worker.WORK/"publisher.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        session=pilot.read_json(directory/"session.json")
        if not session.get("remote_publishing_enabled") or session.get("publication")!=destination():
            raise RuntimeError("Publishing requires authorization for the unchanged configured remote")
        tasks=pending(directory)
        if not force and len(tasks)<25:
            return None
        paths=[str(p.relative_to(worker.REPO)) for task in tasks for p in worker.result_paths(task,worker.OUTPUT)]
        staged=worker.git_cmd(["diff","--cached","--name-only"],capture=True).splitlines()
        if set(staged)-set(paths):
            raise RuntimeError("Refusing to include unrelated staged work")
        for index in range(0,len(paths),300):
            worker.git_cmd(["add","--",*paths[index:index+300]])
        if paths:
            worker.git_cmd(["-c","user.name=harmon-local-agent","-c","user.email=harmon-local-agent@users.noreply.github.com",
                "commit","-m","Add complete unverified archival transcription outputs"])
        message=worker.push_commits()
        result=dict(published_utc=worker.now(),completed_outputs=len(tasks),destination=session["publication"]["destination"],
                    head=worker.git_cmd(["rev-parse","HEAD"],capture=True).strip(),result=message)
        worker.atomic_json(directory/"publishing.json",result)
        print("PUBLISHED",len(tasks),"complete outputs;",message,flush=True)
        return result


def watch(directory):
    # This service uses no agent/model tokens and never changes the worker's
    # original eight-hour deadline. Publish every 25 outputs and at session end.
    session=pilot.read_json(directory/"session.json")
    deadline=session["started_unix"]+session["max_seconds"]+180
    first,retry=True,False
    while True:
        finished=(directory/"summary.json").exists() or time.time()>=deadline
        try:
            publish(directory,force=first or retry or finished)
            retry=False
        except Exception as exc:
            worker.atomic_json(directory/"publishing-error.json",dict(utc=worker.now(),error=str(exc)))
            print("PUBLISH RETAINED LOCALLY:",str(exc),flush=True)
            retry=True
        first=False
        if finished and not retry:
            break
        if time.time()>=deadline+300:
            break
        time.sleep(60 if retry else 15)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("authorize","publish","watch"))
    parser.add_argument("--directory",type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    if args.action=="authorize":
        authorize(args.directory)
    elif args.action=="publish":
        publish(args.directory,force=True)
    else:
        watch(args.directory)


if __name__=="__main__":
    main()
