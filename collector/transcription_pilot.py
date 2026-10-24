#!/usr/bin/env python3
"""Private six-source transcription experiment; never publish or run the archive."""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import time
import wave

try:
    from . import local_worker as worker, speaker_filter as speaker
    from .benchmark_adapters import require_guards
    from .benchmark_metrics import normalize
    from .source_gate import authorize, source_key
except ImportError:
    import local_worker as worker
    import speaker_filter as speaker
    from benchmark_adapters import require_guards
    from benchmark_metrics import normalize
    from source_gate import authorize, source_key

WORK = worker.WORK / "quality-pilot"
PROFILE = worker.WORK / "speaker_profile.json"
PROBLEMS = ("fKWZvJmKia8", "CC9gnJoko50", "HwGaIhx_JW4", "Ar56zONY1-s")
CONFIGS = ("current_filtered", "chunked_filtered", "chunked_original")
ATTEMPT_LIMIT = 6
MAX_SECONDS = 1800
WORD = re.compile(r"\b[\w']+\b")


def read_json(path):
    return json.loads(Path(path).read_text())


def repeated_line_flag(text):
    lines = [" ".join(normalize(line)) for line in text.splitlines() if normalize(line)]
    return any(n > 1 for n in Counter(lines).values())


def intervals_length(intervals):
    end = -math.inf
    total = 0.0
    for a, b in sorted(intervals):
        if b > a:
            total += max(0, b - max(a, end))
            end = max(end, b)
    return total


def timing_issues(segments, start, duration):
    issues, previous_start, previous_end = [], -math.inf, -math.inf
    for i, s in enumerate(segments):
        a, b = s["start"], s["end"]
        reasons = []
        if not math.isfinite(a + b):
            reasons.append("nonfinite")
        else:
            if a < start - .001: reasons.append("before_window")
            if b <= a: reasons.append("nonpositive_duration")
            if a < previous_start: reasons.append("nonmonotonic_start")
            if a < previous_end - .001: reasons.append("overlap")
            if b > start + duration + .05: reasons.append("beyond_audio")
        if reasons: issues.append({"cue": i + 1, "issues": reasons, "start": a, "end": b})
        previous_start, previous_end = a, b
    return issues


def parse_srt(content):
    # Unlike the quality gate, keep zero-duration cues and their suspicious text.
    result = []
    for block in re.split(r"\n\s*\n", content.strip()):
        match = speaker.SRT_TIMING.search(block)
        if match:
            result.append({"start": speaker._srt_ms(match.groups()[:4]) / 1000,
                           "end": speaker._srt_ms(match.groups()[4:]) / 1000,
                           "text": block[match.end():].strip()})
    return result


def diagnostics(text, segments, start, duration, coverage, decoded_seconds, empty_chunks):
    words = normalize(text)
    grams = Counter(tuple(words[i:i+5]) for i in range(max(0, len(words)-4)))
    repeated = [(g, n) for g, n in grams.items() if n >= 3]
    repeats = sum(n-1 for _, n in repeated)
    valid = [(max(start, s["start"]), min(start+duration, s["end"]))
             for s in segments if normalize(s["text"]) and math.isfinite(s["start"]+s["end"])]
    return {"words": len(words), "empty": not words, "empty_chunks": empty_chunks,
            "repeated_5gram_excess": repeats,
            "repeated_5gram_fraction": repeats / max(1, len(words)-4),
            "max_5gram_count": max(grams.values(), default=0),
            "repeated_5gram_examples": [{"text": " ".join(g), "count": n}
                for g, n in sorted(repeated, key=lambda x: -x[1])[:5]],
            "repeated_line_flag": repeated_line_flag(text),
            "processed_source_seconds": round(coverage, 3),
            "processed_source_fraction": coverage / duration,
            "decoder_input_seconds_including_overlap": round(decoded_seconds, 3),
            "timed_text_union_seconds": round(intervals_length(valid), 3),
            "timestamp_issues": timing_issues(segments, start, duration)}


def preservation_snapshot(output=worker.OUTPUT, profile=PROFILE):
    paths = list(output.glob("*")) + [profile, worker.WORK / "state.json", worker.PLAN,
        Path(worker.__file__), Path(speaker.__file__), worker.REPO / "scripts/run_archive.sh"]
    return {str(p.resolve()): worker.sha256_file(p) for p in paths if p.is_file()}


def verify_preserved(snapshot):
    changed = [p for p, digest in snapshot.items()
               if not Path(p).is_file() or worker.sha256_file(p) != digest]
    if changed: raise RuntimeError("Protected inputs changed: " + ", ".join(changed[:5]))


def freeze(work=WORK, output=worker.OUTPUT, profile=PROFILE):
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest = work / "manifest.json"
    if manifest.exists():
        frozen = read_json(manifest)
        verify_preserved(frozen["protected_sha256"])
        return frozen
    tasks = {t["task_id"]: t for t in worker.load_manifest()}
    completed = []
    for p in output.glob("*.metadata.json"):
        m = read_json(p)
        task = tasks.get(m["task_id"])
        if task and worker.completed(task, output):
            _, txt, srt = worker.result_paths(task, output)
            text = txt.read_text()
            segments = parse_srt(srt.read_text())
            defects = timing_issues(segments, 0, m["processed_audio_seconds"])
            completed.append((task, m, text, defects))
    chosen = []
    for source in PROBLEMS:
        matches = sorted((r for r in completed if r[0]["source_id"] == source),
                         key=lambda r: r[0]["task_id"])
        if not matches: raise RuntimeError("Missing completed problem source " + source)
        chosen.append((matches[0], "problem"))
    controls = sorted((r for r in completed if r[0]["source_id"] not in PROBLEMS
        and len(r[2].split()) >= 200 and not repeated_line_flag(r[2])
        and not r[3] and not r[1].get("subtitle_timing_issues")), key=lambda r: r[0]["task_id"])
    control_sources = set()
    for row in controls:
        if row[0]["source_id"] not in control_sources:
            chosen.append((row, "control")); control_sources.add(row[0]["source_id"])
        if len(control_sources) == 2: break
    if len(chosen) != 6: raise RuntimeError("Need exactly two distinct eligible controls")
    rows = []
    for (task, meta, _, _), role in chosen:
        # Freeze the window that was actually processed, including short final sections.
        start = meta.get("source_window_start_s") or 0
        duration = meta["processed_audio_seconds"]
        task = {**task, "start_s": start, "end_s": start + duration}
        rows.append({"task": task, "role": role, "duration": duration,
                     "existing_metadata": str(worker.result_paths(task, output)[0].resolve())})
        cache = worker.WORK / "source_checks" / (source_key(task) + ".json")
        if cache.exists():
            dest = work / "source_checks" / cache.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cache, dest)
    frozen = {"schema": 1, "created_utc": worker.now(), "sources": rows,
        "attempt_limit": ATTEMPT_LIMIT, "max_seconds": MAX_SECONDS,
        "configs": list(CONFIGS), "profile": str(profile.resolve()),
        "profile_sha256": worker.sha256_file(profile),
        "protected_sha256": preservation_snapshot(output, profile),
        "control_rule": "Sorted task ID; >=200 whitespace words; no duplicate normalized nonempty line; no stored or measured timing defect; distinct sources",
        "chunk_policy": "Independent 30s WAVs, 1s overlap (29s stride), English, six threads, --max-context 0",
        "dedup_policy": "Exact suffix/prefix tokens only in cues wholly inside the shared 1s; preserve raw chunks and all other suspicious text"}
    worker.atomic_json(manifest, frozen)
    return frozen


def chunks(duration):
    start = 0.0
    while start < duration:
        end = min(duration, start + 30)
        yield start, end
        if end == duration: break
        start += 29


def offset_segments(segments, offset):
    return [{**s, "start": s["start"] + offset, "end": s["end"] + offset} for s in segments]


def merge_boundary(previous, incoming, start, end):
    """Conservative: never deduplicate text outside the shared audio interval.

    No fabricated word times. Long cues straddling the overlap stay untouched.
    Raw, unedited chunk segments are checkpointed separately for review.
    """
    incoming = [dict(s) for s in incoming]
    left = []
    for s in reversed(previous):
        if start <= s["start"] < s["end"] <= end: left.append(s)
        else: break
    left.reverse()
    right = []
    for s in incoming:
        if start <= s["start"] < s["end"] <= end: right.append(s)
        else: break
    a = [m.group().casefold() for s in left for m in WORD.finditer(s["text"])]
    b = [m.group().casefold() for s in right for m in WORD.finditer(s["text"])]
    count = 0
    for n in range(1, min(len(a), len(b)) + 1):
        if a[-n:] == b[:n]: count = n
    remaining = count
    for s in right:
        matches = list(WORD.finditer(s["text"]))
        take = min(remaining, len(matches))
        if take:
            s["text"] = "" if take == len(matches) else s["text"][matches[take].start():]
            remaining -= take
        if not remaining: break
    return previous + [s for s in incoming if s["text"].strip()], count


def map_source(segments, mapping, source_start):
    result = []
    for s in segments:
        a = speaker.map_time(s["start"], mapping, True) if mapping else s["start"]
        b = speaker.map_time(s["end"], mapping, False) if mapping else s["end"]
        # Match the existing remapper, including observable zero-duration defects.
        result.append({**s, "start": source_start+a, "end": source_start+max(a, b)})
    return result


def annotate(segments, mapping, source_start):
    result = []
    for s in segments:
        overlaps = [[max(s["start"], source_start+r["source_start_s"]),
                     min(s["end"], source_start+r["source_end_s"])] for r in mapping]
        overlaps = [r for r in overlaps if r[1] > r[0]]
        matched = intervals_length(overlaps)
        result.append({**s, "speaker_match": "profile_match_overlap" if overlaps else "other_or_uncertain",
                       "profile_matched_intervals": overlaps,
                       "profile_matched_fraction": matched / (s["end"]-s["start"]) if s["end"] > s["start"] else 0,
                       "speaker_verified": False})
    return result


class BudgetExpired(RuntimeError): pass


def remaining(ledger):
    seconds = ledger["deadline_unix"] - time.time()
    if seconds <= 0: raise BudgetExpired("30-minute pilot deadline reached")
    return seconds


def command(argv, ledger, timeout=240):
    worker.run_command(argv, timeout=max(.01, min(timeout, remaining(ledger))))


def reserve_attempt(ledger, task_id, path):
    """Persist before network I/O; interrupted/failed attempts are never refunded."""
    if task_id in ledger["sources"]: return False
    if len(ledger["sources"]) >= ATTEMPT_LIMIT: return False
    ledger["sources"][task_id] = {"status": "attempting", "started_utc": worker.now()}
    worker.atomic_json(path, ledger)
    return True


def download(row, tools, work, ledger):
    task = row["task"]
    d = work / "audio" / worker.task_key(task)
    record = d / "audio.json"
    if record.exists():
        meta = read_json(record)
        wav = d / "original.wav"
        if wav.exists() and worker.sha256_file(wav) == meta["sha256"]: return wav, meta
    if not reserve_attempt(ledger, task["task_id"], work / "ledger.json"):
        raise RuntimeError("Source attempt already consumed; no redownload or replacement")
    d.mkdir(parents=True, exist_ok=True)
    tick = time.monotonic()
    evidence = authorize(task, tools["yt_dlp"], work / "source_checks")
    argv = [tools["yt_dlp"], "--no-playlist", "--no-progress", "--no-part",
        "--socket-timeout", "20", "--retries", "0", "--fragment-retries", "0",
        "--concurrent-fragments", "1", "--max-filesize", "384M",
        "-f", "worstaudio/worst/bestaudio/best", "-o", str(d / "source.%(ext)s")]
    if task["kind"] != "clip":
        argv += ["--download-sections", f"*{task['start_s']:g}-{task['end_s']:g}"]
    argv.append(task["source_url"])
    command(argv, ledger)
    media = sorted(d.glob("source.*"))
    if not media: raise RuntimeError("Download produced no media")
    wav = d / "original.wav"
    command([tools["ffmpeg"], "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", media[0], "-vn", "-t", str(row["duration"]), "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", wav], ledger, 120)
    for p in media: p.unlink()
    with wave.open(str(wav)) as audio: duration = audio.getnframes() / audio.getframerate()
    if abs(duration-row["duration"]) > 2:
        raise RuntimeError(f"Incomplete processed window: {duration}s for {row['duration']}s")
    meta = {"sha256": worker.sha256_file(wav), "duration": duration,
            "download_convert_seconds": time.monotonic()-tick, "owner_evidence": evidence,
            "time_alignment": "source window offset + section-relative time; extractor/keyframe cuts can be approximate"}
    worker.atomic_json(record, meta)
    ledger["sources"][task["task_id"]].update(status="downloaded", audio=meta)
    worker.atomic_json(work / "ledger.json", ledger)
    return wav, meta


def extract(wav, target, start, end):
    with wave.open(str(wav)) as src, wave.open(str(target), "wb") as dest:
        dest.setparams(src.getparams())
        src.setpos(round(start*src.getframerate()))
        dest.writeframes(src.readframes(round((end-start)*src.getframerate())))


def decode(wav, directory, tools, ledger, chunked):
    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav)) as f: duration = f.getnframes()/f.getframerate()
    windows = list(chunks(duration)) if chunked else [(0, duration)]
    segments, raw, removed, seconds, empty = [], [], [], 0.0, 0
    input_hash = worker.sha256_file(wav)
    for i, (a, b) in enumerate(windows):
        prefix = directory / f"chunk-{i:03d}"
        checkpoint = prefix.with_suffix(".checkpoint.json")
        if checkpoint.exists():
            data = read_json(checkpoint)
            if (data["input_sha256"], data["start"], data["end"], data["chunked"]) != (input_hash, a, b, chunked):
                raise RuntimeError("Chunk input changed on resume")
        else:
            remaining(ledger)
            tick = time.monotonic()
            part = directory / "chunk.wav"
            try:
                extract(wav, part, a, b)
                argv = [tools["whisper"], "-m", tools["model"], "-f", part,
                    "-t", "6", "-otxt", "-osrt", "-oj", "-of", prefix, "-l", "en", "-np"]
                if chunked: argv += ["--max-context", "0"]
                command(argv, ledger, max(240, duration*5))
                native = read_json(prefix.with_suffix(".json"))["transcription"]
                native = [{"start": s["offsets"]["from"]/1000,
                           "end": s["offsets"]["to"]/1000, "text": s["text"].strip()} for s in native]
                data = {"input_sha256": input_hash, "start": a, "end": b, "chunked": chunked,
                        "segments": native, "text": prefix.with_suffix(".txt").read_text(),
                        "seconds": time.monotonic()-tick}
                worker.atomic_json(checkpoint, data)
            finally:
                part.unlink(missing_ok=True)
        incoming = offset_segments(data["segments"], a)
        raw.extend(incoming)
        if i:
            segments, count = merge_boundary(segments, incoming, a, min(a+1, b))
            removed.append({"chunk": i, "overlap": [a, min(a+1,b)], "removed_prefix_words": count})
        else: segments = incoming
        seconds += data["seconds"]
        empty += not normalize(data["text"])
    # Baseline retains the CLI's TXT verbatim; chunked TXT retains all segment text except audited overlap duplicates.
    text = data["text"] if not chunked else "\n".join(s["text"] for s in segments)
    return {"segments": segments, "raw_segments": raw, "text": text, "seconds": seconds,
            "empty_chunks": empty, "deduplicated_boundaries": removed,
            "decoder_seconds": sum(b-a for a,b in windows)}


def result_complete(directory):
    path = directory / "result.json"
    if not path.exists(): return False
    data = read_json(path)
    for name, digest in data["output_sha256"].items():
        p = directory / name
        if not p.exists() or worker.sha256_file(p) != digest:
            raise RuntimeError("Completed pilot output damaged: " + str(p))
    return True


def write_result(directory, data):
    text = data["text"]
    srt = "\n\n".join(f"{i}\n{speaker._srt_time(s['start']*1000)} --> {speaker._srt_time(s['end']*1000)}\n{s['text']}"
                       for i, s in enumerate(data["segments"], 1))
    (directory / "transcript.txt").write_text(text)
    (directory / "transcript.srt").write_text(srt + ("\n" if srt else ""))
    data["output_sha256"] = {p.name: worker.sha256_file(p) for p in
        (directory / "transcript.txt", directory / "transcript.srt")}
    worker.atomic_json(directory / "result.json", data)


def native_timing_issues(directory):
    issues = []
    for p in sorted((directory / "native").glob("*.checkpoint.json")):
        chunk = read_json(p)
        issues.extend({**issue, "chunk": p.name} for issue in
            timing_issues(chunk["segments"], 0, chunk["end"]-chunk["start"]))
    return issues


def process(row, wav, audio, tools, work, ledger, frozen):
    task, start = row["task"], row["task"]["start_s"]
    root = work / "outputs" / worker.task_key(task)
    stats_path = root / "filter.json"
    root.mkdir(parents=True, exist_ok=True)
    filtered = wav.with_name("filtered.wav")
    tick = time.monotonic()
    stats = speaker.filter_wav(wav, filtered, frozen["profile"])
    if stats["speaker_profile_sha256"] != frozen["profile_sha256"]:
        raise RuntimeError("Speaker profile changed")
    filter_seconds = time.monotonic()-tick
    if stats_path.exists() and stats != read_json(stats_path):
        raise RuntimeError("Speaker filter changed on resume")
    worker.atomic_json(stats_path, stats)
    for config in CONFIGS:
        dest = root / config
        if result_complete(dest): continue
        remaining(ledger)
        dest.mkdir(parents=True, exist_ok=True)
        is_filtered = config != "chunked_original"
        if is_filtered and stats["no_match"]:
            decoded = {"segments": [], "raw_segments": [], "text": "", "seconds": 0,
                       "empty_chunks": 0, "deduplicated_boundaries": [], "decoder_seconds": 0}
        else:
            decoded = decode(filtered if is_filtered else wav, dest / "native", tools, ledger,
                             chunked=config != "current_filtered")
        mapping = stats["time_mapping"] if is_filtered else []
        segments = map_source(decoded["segments"], mapping, start)
        if not is_filtered: segments = annotate(segments, stats["time_mapping"], start)
        coverage = stats["matched_audio_seconds"] if is_filtered else audio["duration"]
        result = {**decoded, "segments": segments, "task_id": task["task_id"], "configuration": config,
            "original_audio_sha256": audio["sha256"], "model_sha256": tools["model_sha256"],
            "profile_sha256": frozen["profile_sha256"], "speaker_verified": False,
            "training_ready": False, "training_permission_verified": False,
            "filter_seconds": filter_seconds, "pipeline_seconds": decoded["seconds"]+filter_seconds,
            "metrics": diagnostics(decoded["text"], segments, start, audio["duration"], coverage,
                                   decoded["decoder_seconds"], decoded["empty_chunks"])}
        write_result(dest, result)
        print("RESULT", task["task_id"], config, json.dumps({k: v for k,v in result["metrics"].items()
              if k not in ("timestamp_issues", "repeated_5gram_examples")}), flush=True)


def report(work, frozen, ledger):
    lines = ["# Small transcription quality pilot", "",
        "Private artifacts; production defaults unchanged. Six fixed sources, no replacements.", "",
        "| Source | Role | Pipeline | Words | Repetition excess (fraction) | Empty | Source coverage | Timing source/native | Seconds |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: |"]
    rows = []
    for row in frozen["sources"]:
        task = row["task"]
        for config in CONFIGS:
            d = work / "outputs" / worker.task_key(task) / config
            if result_complete(d):
                data = read_json(d / "result.json"); m = data["metrics"]
                m["native_timestamp_issues"] = native_timing_issues(d)
                m["timestamp_issue_counts"] = dict(Counter(reason for issue in m["timestamp_issues"]
                                                         for reason in issue["issues"]))
                rows.append({"source": task["source_id"], "role": row["role"], "configuration": config,
                             "metrics": m, "seconds": data["pipeline_seconds"]})
                lines.append(f"| {task['source_id']} | {row['role']} | {config} | {m['words']} | {m['repeated_5gram_excess']} ({m['repeated_5gram_fraction']:.1%}) | {m['empty']} | {m['processed_source_fraction']:.1%} | {len(m['timestamp_issues'])}/{len(m['native_timestamp_issues'])} | {data['pipeline_seconds']:.2f} |")
            else:
                state = ledger.get("sources", {}).get(task["task_id"], {})
                reason = state.get("error", "unprocessed/deadline reached").replace("\n", " ").replace("|", "/")
                lines.append(f"| {task['source_id']} | {row['role']} | {config} | — | — | — | — | — | {reason[:200]} |")
    lines += ["", "Repetition counts normalized five-word sequences appearing at least three times, summing occurrences beyond the first; line breaks do not affect it. These are flags, including potentially real repeated speech.",
        "The repetition fraction divides excess by the number of five-word windows. Controls qualified on pre-existing outputs; their labels do not guarantee a clean rerun baseline or correct speaker attribution.",
        "Coverage measures unique source audio supplied to ASR, not correctly transcribed speech. Filtered coverage excludes removed spans and inserted separators. Chunk input duration includes overlaps.",
        "Timing counts show final source cues / raw native cues. Native cues are also checked against their individual chunk bounds. Defects include nonpositive duration, nonmonotonic starts, overlap and out-of-window cues. Native chunks and suspicious text are retained; deduplication is restricted to cues wholly inside shared boundaries. Source cuts and compact-audio remapping remain approximate.",
        "Seconds include decoding, startup per chunk, extraction/serialization and the shared speaker pass charged once per pipeline; downloads and diagnostics are excluded. One pass only, no runtime confidence interval.",
        "The third pipeline retains every speaker and adds profile-match overlap annotations after decoding. Those annotations are not verified speaker identities. No reference transcript or labelled speaker ground truth is available: word accuracy, omissions and correct attribution remain unresolved."]
    comparisons = []
    for row in frozen["sources"]:
        source = row["task"]["source_id"]
        by = {r["configuration"]: r for r in rows if r["source"] == source}
        if len(by) == 3:
            a,b,c = [by[k] for k in CONFIGS]
            comparisons.append({"source": source, "role": row["role"],
                "repetition_excess": [r["metrics"]["repeated_5gram_excess"] for r in (a,b,c)],
                "timing_defects": [len(r["metrics"]["timestamp_issues"]) for r in (a,b,c)],
                "timing_defect_types": [r["metrics"]["timestamp_issue_counts"] for r in (a,b,c)]})
    if comparisons:
        lines += ["", "Observed source-timing defect types (baseline → filtered chunks → original chunks):", ""]
        for c in comparisons:
            lines.append(f"- {c['source']}: " + " → ".join(str(v) if v else "none" for v in c["timing_defect_types"]) + ".")
        problems = [c for c in comparisons if c["role"] == "problem"]
        if problems:
            improvements = [sum(c["repetition_excess"][i] < c["repetition_excess"][0] for c in problems) for i in (1,2)]
            lines += ["", f"Repetition flags decreased in {improvements[0]}/{len(problems)} completed problem recordings with filtered chunks and {improvements[1]}/{len(problems)} with original chunks. See timing defect types above for remaining or introduced problems."]
    lines += ["", "Recommendation: keep production defaults unchanged. Use this pilot to target a manually checked word/speaker sample before promoting chunking or changing speaker filtering.",
              f"Source attempts consumed: {len(ledger.get('sources', {}))}/6. Failed sources are not replaced.",
              "Temporary audio is removed after each source and at exit; resumption reuses completed outputs without downloading an attempted source again."]
    validation = work / "validation.json"
    if validation.exists():
        v = read_json(validation)
        lines += ["", f"Validation: {v['tests_passed']} offline tests passed; {v['protected_files']} protected file hashes unchanged; {v['completed_pipelines']}/18 pipeline outputs audited; temporary audio removed: {v['temporary_audio_removed']}."]
        if v.get("memory_guard"):
            g = v["memory_guard"]
            lines += [f"Guarded elapsed time {g['elapsed_seconds']:.1f}s; peak process/GPU combined allocation {g['peak_combined_bytes']/1024**3:.3f} GiB; stop reason: {g['stop_reason']}."]
        if v.get("resume"):
            r = v["resume"]
            lines += [f"Live resume: {r['new_source_attempts']} new source attempts; {r['unchanged_output_files']} output files retained with identical hashes and modification times."]
    worker.atomic_json(work / "summary.json", {"rows": rows, "comparisons": comparisons,
        "attempts": len(ledger.get("sources", {})), "protected_inputs_unchanged": True})
    (work / "REPORT.md").write_text("\n".join(lines)+"\n")


def run(work=WORK):
    require_guards()
    frozen = freeze(work)
    speaker.load_profile(frozen["profile"])
    ledger_path = work / "ledger.json"
    ledger = read_json(ledger_path) if ledger_path.exists() else {
        "created_utc": worker.now(), "deadline_unix": time.time()+MAX_SECONDS, "sources": {}}
    if len(ledger["sources"]) > ATTEMPT_LIMIT: raise RuntimeError("Attempt ledger exceeds cap")
    worker.atomic_json(ledger_path, ledger)
    def interrupt(signum, frame): raise BudgetExpired("Pilot stopped by signal " + str(signum))
    old = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    try:
        pending = [r for r in frozen["sources"] if not all(result_complete(
            work / "outputs" / worker.task_key(r["task"]) / c) for c in CONFIGS)]
        if not pending: return
        signal.setitimer(signal.ITIMER_REAL, remaining(ledger))
        tools = worker.find_tools()
        provenance = {"model_sha256": tools["model_sha256"], "whisper_sha256": worker.sha256_file(tools["whisper"])}
        if "tools" in ledger and ledger["tools"] != provenance:
            raise RuntimeError("Installed model/runtime changed on resume")
        ledger["tools"] = provenance
        worker.atomic_json(ledger_path, ledger)
        for row in pending:
            remaining(ledger)
            task = row["task"]
            try:
                wav, audio = download(row, tools, work, ledger)
                process(row, wav, audio, tools, work, ledger, frozen)
                ledger["sources"][task["task_id"]]["status"] = "complete"
            except BudgetExpired: raise
            except Exception as exc:
                state = ledger["sources"].setdefault(task["task_id"], {"status": "failed"})
                state["status"] = "failed"
                state.setdefault("error", str(exc))
                print("FAILED", task["task_id"], str(exc), flush=True)
            finally:
                shutil.rmtree(work / "audio" / worker.task_key(task), ignore_errors=True)
                worker.atomic_json(ledger_path, ledger)
    except BudgetExpired as exc:
        ledger["stop_reason"] = str(exc)
        print(str(exc), flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for s, handler in old.items(): signal.signal(s, handler)
        shutil.rmtree(work / "audio", ignore_errors=True)
        verify_preserved(frozen["protected_sha256"])
        ledger["last_exit_utc"] = worker.now()
        worker.atomic_json(ledger_path, ledger)
        report(work, frozen, ledger)


@contextlib.contextmanager
def locks(work):
    # Same nonblocking lock as the archive worker; never start or stop that batch.
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (worker.WORK / "worker.lock").open("a") as archive, (work / "pilot.lock").open("a") as pilot:
        for f in (archive, pilot): fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "report"))
    args = parser.parse_args()
    os.umask(0o077)
    with locks(WORK):
        if args.action == "freeze":
            frozen = freeze()
            print(json.dumps([{**r["task"], "role": r["role"]} for r in frozen["sources"]], indent=2))
        elif args.action == "run": run()
        else:
            frozen = read_json(WORK / "manifest.json")
            verify_preserved(frozen["protected_sha256"])
            report(WORK, frozen, read_json(WORK / "ledger.json"))


if __name__ == "__main__": main()
