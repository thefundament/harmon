"""One private 30-minute trial of the original-audio chunked candidate."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import time
import wave

from . import quality_followup as followup, transcription_pilot as pilot

worker, speaker = pilot.worker, pilot.speaker
WORK = worker.WORK / "quality-trial-30m"
LIMIT = 60
SECONDS = 1800


def select(tasks, journal, output, excluded, cache, limit=LIMIT):
    """One pending window per already authorized recording, in inventory order."""
    failed = {t["source_id"] for t in tasks if journal.get(t["task_id"], {}).get("attempts", 0)
              or journal.get(t["task_id"], {}).get("source_rejected")}
    seen, selected = set(excluded) | failed, []
    for task in tasks:
        if task["source_id"] in seen or not worker.eligible(task, journal, output)[0]:
            continue
        check = cache / (pilot.source_key(task) + ".json")
        if not check.exists():
            continue
        saved = pilot.read_json(check)
        if (saved.get("state") != "allowed" or not saved.get("evidence", {}).get("owner_verified")
                or saved.get("source_id") != task["source_id"] or saved.get("source_kind") != task["kind"]):
            continue
        selected.append(task)
        seen.add(task["source_id"])
        if len(selected) >= limit:
            break
    return selected


def freeze(work):
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = work / "manifest.json"
    if path.exists():
        frozen = pilot.read_json(path)
        pilot.verify_preserved(frozen["protected_sha256"])
        return frozen
    prior = pilot.read_json(pilot.WORK / "manifest.json")
    pilot.verify_preserved(prior["protected_sha256"])
    previous = pilot.read_json(followup.WORK / "manifest.json")
    pilot.verify_preserved(previous["protected_sha256"])
    tasks = worker.load_manifest()
    excluded = {r["task"]["source_id"] for r in prior["sources"]}
    selected = select(tasks, worker.load_journal(worker.WORK / "state.json"), worker.OUTPUT,
                      excluded, worker.WORK / "source_checks")
    if not selected:
        raise RuntimeError("No unprocessed windows from eligible authorized recordings")
    for task in selected:
        cache = worker.WORK / "source_checks" / (pilot.source_key(task) + ".json")
        dest = work / "source_checks" / cache.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache, dest)
    frozen = dict(created_utc=worker.now(), tasks=selected, attempt_limit=LIMIT, max_seconds=SECONDS,
        profile=prior["profile"], profile_sha256=prior["profile_sha256"],
        protected_sha256={**previous["protected_sha256"], **pilot.preservation_snapshot(),
                         **followup.hashes(pilot.WORK.rglob("*")), **followup.hashes(followup.WORK.rglob("*"))},
        selection="First 60 eligible pending windows in frozen inventory order, one per already source-authorized recording; skip recordings with prior source failures and the six pilot recordings; no replacements.",
        pipeline="Original PCM; independent 30s Turbo windows with 1s overlap, English, six threads, max-context 0; annotate profile overlap without excluding voices; partition subtitle presentation.",
        **followup.FLAGS)
    worker.atomic_json(path, frozen)
    return frozen


def reserve(ledger, task, path, frozen):
    key = task["task_id"]
    if key not in {t["task_id"] for t in frozen["tasks"]}:
        raise RuntimeError("Task is outside frozen trial")
    if key in ledger["sources"] or len(ledger["sources"]) >= LIMIT:
        return False
    ledger["sources"][key] = dict(status="attempting", started_utc=worker.now())
    worker.atomic_json(path, ledger)
    return True


def acquire(task, tools, work, ledger, frozen):
    if not reserve(ledger, task, work / "ledger.json", frozen):
        raise RuntimeError("Trial attempt consumed; no retry or replacement")
    directory = work / "audio" / worker.task_key(task)
    directory.mkdir(parents=True, exist_ok=True)
    evidence = pilot.authorize(task, tools["yt_dlp"], work / "source_checks")
    argv = [tools["yt_dlp"], "--no-playlist", "--no-progress", "--no-part", "--socket-timeout", "20",
            "--retries", "0", "--fragment-retries", "0", "--extractor-retries", "0",
            "--concurrent-fragments", "1", "--max-filesize", "384M", "-f", "worstaudio/worst/bestaudio/best",
            "-o", directory / "source.%(ext)s"]
    start, end = task.get("start_s"), task.get("end_s")
    duration = end-start if start is not None else min(180, task.get("duration_s") or 180)
    if start is not None:
        argv += ["--download-sections", f"*{start:g}-{end:g}"]
    followup.command([*argv, task["source_url"]], ledger)
    media = sorted(directory.glob("source.*"))
    if not media:
        raise RuntimeError("Download produced no media")
    wav = directory / "original.wav"
    followup.command([tools["ffmpeg"], "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", media[0], "-vn", "-t", str(duration), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], ledger, 120)
    for p in media:
        p.unlink()
    with wave.open(str(wav), "rb") as audio:
        actual = audio.getnframes()/audio.getframerate()
    if actual <= 0 or start is not None and abs(actual-duration) > 2:
        raise RuntimeError(f"Incomplete window: {actual}s for {duration}s")
    info = dict(duration=actual, wav_sha256=worker.sha256_file(wav), pcm_sha256=followup.pcm_sha256(wav), owner_evidence=evidence)
    ledger["sources"][task["task_id"]].update(status="downloaded", audio=info)
    worker.atomic_json(work / "ledger.json", ledger)
    return wav, info


def write_srt(path, segments):
    path.write_text("\n\n".join(f"{i}\n{speaker._srt_time(s['start']*1000)} --> {speaker._srt_time(s['end']*1000)}\n{s['text']}"
        for i, s in enumerate(segments, 1)) + ("\n" if segments else ""))


def process(task, wav, audio, tools, work, ledger, frozen):
    tick = time.monotonic()
    dest = work / "outputs" / worker.task_key(task)
    dest.mkdir(parents=True, exist_ok=True)
    filtered = wav.with_name("filtered.wav")
    try:
        stats = speaker.filter_wav(wav, filtered, frozen["profile"])
        if stats["speaker_profile_sha256"] != frozen["profile_sha256"]:
            raise RuntimeError("Profile changed")
    finally:
        filtered.unlink(missing_ok=True)
    worker.atomic_json(dest / "filter.json", stats)
    decoded = pilot.decode(wav, dest / "native", tools, ledger, chunked=True)
    start = task.get("start_s") or 0
    segments = pilot.annotate(pilot.map_source(decoded["segments"], [], start), stats["time_mapping"], start)
    display = followup.presentation(segments)
    txt, native_srt, srt = dest / "transcript.txt", dest / "native.srt", dest / "presentation.srt"
    txt.write_text(decoded["text"])
    write_srt(native_srt, segments)
    write_srt(srt, display["segments"])
    data = {**decoded, "task":task, "segments":segments, "presentation":display, "audio":audio,
        "profile_sha256":frozen["profile_sha256"], "model_sha256":tools["model_sha256"],
        "pipeline_seconds":time.monotonic()-tick,
        "metrics":pilot.diagnostics(decoded["text"], segments, start, audio["duration"], audio["duration"],
                                    decoded["decoder_seconds"], decoded["empty_chunks"]), **followup.FLAGS}
    path = dest / "result.json"
    followup.seal(path, data, [p for p in dest.rglob("*") if p.is_file() and p != path])
    return data


def report(work, frozen, ledger):
    pilot.verify_preserved(frozen["protected_sha256"])
    rows = []
    for task in frozen["tasks"]:
        directory = work / "outputs" / worker.task_key(task)
        if not followup.complete(directory / "result.json"):
            continue
        data = pilot.read_json(directory / "result.json")
        if any(data[key] is not False for key in followup.FLAGS):
            raise RuntimeError("Verification flag changed")
        if data["profile_sha256"] != frozen["profile_sha256"] or data["audio"] != ledger["sources"][task["task_id"]]["audio"]:
            raise RuntimeError("Result profile/audio provenance mismatch")
        if data["presentation"] != followup.presentation(data["segments"]):
            raise RuntimeError("Presentation changed text or cue coverage")
        srt = pilot.parse_srt((directory / "presentation.srt").read_text())
        if any(a["end"] > b["start"] for a, b in zip(srt, srt[1:])):
            raise RuntimeError("Rendered subtitle overlap remains")
        for checkpoint in (directory / "native").glob("*.checkpoint.json"):
            native = pilot.read_json(checkpoint)
            if native["input_sha256"] != data["audio"]["wav_sha256"]:
                raise RuntimeError("Chunk used different original audio")
        rows.append(dict(task=task, metrics=data["metrics"], pipeline_seconds=data["pipeline_seconds"],
            displayed_cues=len(data["presentation"]["segments"]), undisplayable_cues=len(data["presentation"]["undisplayable_cue_indices"])))
    states = list(ledger["sources"].values())
    guards = [pilot.read_json(p) for p in sorted(work.glob("guard-*.memory.json"))]
    summary = dict(attempts=len(states), completed=len(rows), failed=sum(s["status"] == "failed" for s in states),
        interrupted=sum(s["status"] in ("attempting", "downloaded", "interrupted") for s in states),
        source_audio_seconds=sum(r["metrics"]["processed_source_seconds"] for r in rows),
        recognized_words=sum(r["metrics"]["words"] for r in rows),
        repetition_flagged_outputs=sum(r["metrics"]["repeated_5gram_excess"] > 0 for r in rows),
        strong_repetition_outputs=sum(r["metrics"]["repeated_5gram_fraction"] >= .1 for r in rows),
        empty_outputs=sum(r["metrics"]["empty"] for r in rows),
        elapsed_seconds=max(0, ledger.get("finished_unix", time.time())-ledger["started_unix"]),
        stop_reason=ledger.get("stop_reason", "running"), protected_files=len(frozen["protected_sha256"]),
        protected_inputs_unchanged=True, temporary_audio_removed=not (work / "audio").exists()
            and not any(work.glob("outputs/*/native/*.wav")), rows=rows, memory_guards=guards, **followup.FLAGS)
    worker.atomic_json(work / "summary.json", summary)
    lines = ["# Private 30-minute candidate trial", "", frozen["pipeline"], "",
        f"{summary['completed']} completed windows; {summary['failed']} failed sources; {summary['interrupted']} interrupted/incomplete; "
        f"{summary['attempts']}/{LIMIT} attempts consumed. {summary['source_audio_seconds']/60:.1f} minutes of original audio; "
        f"{summary['recognized_words']} normalized words. Elapsed {summary['elapsed_seconds']/60:.2f} minutes; stop: {summary['stop_reason']}.", "",
        f"Repetition flags: {summary['repetition_flagged_outputs']}/{len(rows)} outputs (normalized five-grams appearing at least three times); "
        f"{summary['strong_repetition_outputs']} have excess fractions of at least 10% (a diagnostic threshold, not measured error rate); "
        f"empty outputs: {summary['empty_outputs']}. Legitimate repetition and unusual speech are retained. "
        "All voices are decoded; profile overlap annotations are unverified. These diagnostics do not measure word accuracy or speaker identity.", "",
        "| Source | Source window | Audio seconds | Words | Repetition excess | Source cue issues | Display cues |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        task, m = row["task"], row["metrics"]
        lines.append(f"| {task['source_id']} | {task.get('start_s', 0)}–{task.get('end_s', 'clip')} | {m['processed_source_seconds']:.2f} | {m['words']} | {m['repeated_5gram_excess']} | {len(m['timestamp_issues'])} | {row['displayed_cues']} |")
    strongest = sorted((r for r in rows if r["metrics"]["repeated_5gram_fraction"] >= .1),
                       key=lambda r: -r["metrics"]["repeated_5gram_fraction"])
    if strongest:
        lines += ["", "Strongest repetition cases (diagnostic flags, not verified errors):", ""]
        for row in strongest[:5]:
            m = row["metrics"]
            example = m["repeated_5gram_examples"][0]
            directory = work / "outputs" / worker.task_key(row["task"])
            data = pilot.read_json(directory / "result.json")
            phrase = example["text"]
            matches = [s for s in data["segments"] if phrase in " ".join(pilot.normalize(s["text"]))]
            interval = (f"{min(s['start'] for s in matches):.2f}–{max(s['end'] for s in matches):.2f}s"
                        if matches else "across cue boundaries")
            fraction = (sum((s["end"]-s["start"])*s["profile_matched_fraction"] for s in matches)
                        / max(.001, sum(s["end"]-s["start"] for s in matches)))
            lines.append(f"- {row['task']['source_id']}: repetition fraction {m['repeated_5gram_fraction']:.1%}; "
                f"“{phrase}” occurs {example['count']} times, {interval}. "
                f"Profile-match overlap of intersecting whole cues {fraction:.1%} (coverage, not identity probability).")
        lines += ["", "These cases show that independent chunks do not eliminate repetitive decoding. "
            "Real repeated speech, ambiguous game audio, source cuts, and decoding failures remain possible; neither profile overlap nor fluency settles the words. "
            "Retain all text and native chunks, and keep production defaults unchanged."]
    failures = [(key, s.get("error", s["status"])) for key, s in ledger["sources"].items() if s["status"] != "complete"]
    if failures:
        lines += ["", "Incomplete sources (no replacements):", ""] + [f"- {key}: {error}" for key, error in failures]
    lines += ["", f"Integrity: {summary['protected_files']} protected hashes unchanged, including production, pilot, follow-up, and profile. "
        f"Temporary audio removed: {summary['temporary_audio_removed']}. Verification/training flags remain false. "
        "No production changes, publication, profile recalibration, model downloads, archive session, or scheduler.", "",
        "Presentation partitions overlaps while preserving every active cue and its original timestamps in sidecars; it does not improve speech alignment. "
        "Review native timing/repetition flags as candidate limitations. Keep production defaults unchanged; this private run establishes operating behavior, not transcript accuracy."]
    if guards:
        lines += [f"Memory guard: peak combined process/GPU allocation {max(g['peak_combined_bytes'] for g in guards)/1024**3:.2f} GiB; "
                  f"all guards successful: {all(g['child_returncode'] == 0 and g['stop_reason'] is None for g in guards)}."]
    (work / "REPORT.md").write_text("\n".join(lines)+"\n")
    return summary


def cleanup(work):
    shutil.rmtree(work / "audio", ignore_errors=True)
    for path in work.glob("outputs/*/native/*.wav"):
        path.unlink(missing_ok=True)


def run(work):
    pilot.require_guards()
    frozen = freeze(work)
    path = work / "ledger.json"
    ledger = pilot.read_json(path) if path.exists() else dict(started_unix=time.time(),
        created_utc=worker.now(), deadline_unix=time.time()+SECONDS, sources={})
    if len(ledger["sources"]) > LIMIT or not set(ledger["sources"]) <= {t["task_id"] for t in frozen["tasks"]}:
        raise RuntimeError("Invalid trial attempt ledger")
    worker.atomic_json(path, ledger)
    def interrupt(signum, frame):
        raise pilot.BudgetExpired("30-minute trial deadline" if signum == signal.SIGALRM else "Trial interrupted by signal " + str(signum))
    old = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    try:
        pending = [t for t in frozen["tasks"] if t["task_id"] not in ledger["sources"]]
        if not pending:
            ledger.setdefault("stop_reason", "Frozen source attempt list exhausted")
            return
        signal.setitimer(signal.ITIMER_REAL, followup.remaining(ledger))
        tools = worker.find_tools()
        model = next(m for m in pilot.read_json(worker.REPO / "docs/stt_benchmark/lock.json")["models"] if m["id"] == worker.MODEL_NAME)
        if tools["model_sha256"] != model["sha256"]:
            raise RuntimeError("Installed Turbo model differs from pinned model")
        provenance = dict(model_sha256=tools["model_sha256"], whisper_sha256=worker.sha256_file(tools["whisper"]))
        if ledger.get("tools", provenance) != provenance:
            raise RuntimeError("Installed model/runtime changed on resume")
        ledger["tools"] = provenance
        speaker.load_profile(frozen["profile"])
        worker.atomic_json(path, ledger)
        for task in pending:
            followup.remaining(ledger)
            if worker.free_disk_bytes(work) < 2*1024**3:
                ledger["stop_reason"] = "Disk floor reached"
                break
            try:
                wav, audio = acquire(task, tools, work, ledger, frozen)
                data = process(task, wav, audio, tools, work, ledger, frozen)
                ledger["sources"][task["task_id"]].update(status="complete", words=data["metrics"]["words"])
                print("COMPLETE", task["task_id"], data["metrics"]["words"], "words", flush=True)
            except pilot.BudgetExpired:
                state = ledger["sources"].get(task["task_id"])
                if state:
                    state.update(status="interrupted", error="Trial time/signal limit reached; completed chunk diagnostics retained")
                raise
            except Exception as exc:
                ledger["sources"].setdefault(task["task_id"], {}).update(status="failed", error=str(exc))
                print("FAILED", task["task_id"], str(exc), flush=True)
            finally:
                shutil.rmtree(work / "audio" / worker.task_key(task), ignore_errors=True)
                worker.atomic_json(path, ledger)
        else:
            ledger["stop_reason"] = "Frozen source attempt list exhausted"
    except pilot.BudgetExpired as exc:
        ledger["stop_reason"] = str(exc)
        print(str(exc), flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for s, handler in old.items():
            signal.signal(s, handler)
        cleanup(work)
        ledger["finished_unix"] = time.time()
        ledger["last_exit_utc"] = worker.now()
        worker.atomic_json(path, ledger)
        summary = report(work, frozen, ledger)
        print("SUMMARY", {k: v for k, v in summary.items() if k not in ("rows", "memory_guards")}, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "report"))
    args = parser.parse_args()
    os.umask(0o077)
    with followup.locks(WORK):
        try:
            if args.action == "run":
                run(WORK)
            else:
                frozen = freeze(WORK)
                if args.action == "report":
                    cleanup(WORK)
                    report(WORK, frozen, pilot.read_json(WORK / "ledger.json"))
                else:
                    print("Frozen", len(frozen["tasks"]), "distinct source windows", flush=True)
        finally:
            cleanup(WORK)


if __name__ == "__main__":
    main()
