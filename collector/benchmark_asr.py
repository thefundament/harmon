#!/usr/bin/env python3
"""Guarded local STT comparison. Never starts or modifies the production worker."""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import signal
import statistics
import tempfile
import time

try:
    from . import benchmark_adapters as adapters, benchmark_inputs as inputs, benchmark_metrics as metrics
except ImportError:
    import benchmark_adapters as adapters
    import benchmark_inputs as inputs
    import benchmark_metrics as metrics

ROOT, WORK, REPORT = inputs.ROOT, inputs.WORK, inputs.REPORT
PANELS = adapters.PANELS
ADDITIONAL = ("medium.en-q8_0", "parakeet-tdt-0.6b-v3", "qwen3-asr-0.6b")
CONFIGURATIONS = ("small.en-unsegmented", "small.en", "large-v3-turbo-q5_0", *ADDITIONAL)


def trial(configuration, samples, vad, lock, repetition):
    model = adapters.model_for(configuration, lock)
    result = {"configuration": configuration, "repetition": repetition, "model": model["id"],
              "model_sha256": model["sha256"], "runtime": model["runtime"], "backend": "Vulkan",
              "settings": {"threads": 6, "language": "en", "decoding": "pinned runtime defaults",
                           "preprocessing": "unsegmented" if configuration.endswith("-unsegmented") else "shared Silero VAD <=30s"},
              "panels": {}, "status": "started", "samples": []}
    try:
        for panel in PANELS:
            tick = time.monotonic()
            with adapters.MemorySample() as memory:
                timings = []
                for group in adapters.groups_for(samples, panel):
                    with tempfile.TemporaryDirectory(prefix="trial-", dir=WORK) as temp:
                        rows, timing = adapters.evaluate_group(configuration, group, vad, model, lock, Path(temp))
                        result["samples"].extend(rows)
                        timings.append(timing)
            own_seconds = sum(p["model_process_seconds"] + p["timestamp_postprocessing_seconds"] for p in timings) + sum(
                s["subtitle_writing_seconds"] for s in result["samples"] if s["dataset"] == panel)
            common = 0 if configuration.endswith("-unsegmented") else vad["panels"][panel]["seconds"]
            result["panels"][panel] = {"processing_seconds": own_seconds + common,
                "recognition_and_output_seconds": own_seconds, "shared_preprocessing_seconds": common,
                "observed_panel_wall_seconds_including_scoring": time.monotonic() - tick,
                "memory": memory, "processes": timings}
            print("TIMED", configuration, repetition, panel, round(own_seconds + common, 3), flush=True)
        result["status"] = "complete"
    except Exception as exc:
        result.update(status="failed", error=str(exc))
    return result


def summarize(trials, samples):
    summary = {}
    for configuration in dict.fromkeys(t["configuration"] for t in trials):
        passes = [t for t in trials if t["configuration"] == configuration and t["status"] == "complete"]
        row = {"complete": len(passes) == 3, "completed_repetitions": len(passes)}
        summary[configuration] = row
        if not row["complete"]:
            continue
        row["quality"] = {}
        # Preregistered quality pass: first timed repetition, never the best repeat.
        for panel in PANELS[:2]:
            row["quality"][panel] = metrics.aggregate([s["score"] for s in passes[0]["samples"] if s["dataset"] == panel])
        row["quality"]["overall"] = metrics.aggregate([s["score"] for s in passes[0]["samples"] if "score" in s])
        row["quality_by_repetition"] = [metrics.aggregate([s["score"] for s in t["samples"] if "score" in s]) for t in passes]
        for panel in PANELS:
            values = [t["panels"][panel]["processing_seconds"] for t in passes]
            row[panel + "_median_seconds"] = statistics.median(values)
            row[panel + "_range_seconds"] = [min(values), max(values)]
        duration = sum(s["audio_seconds"] for s in samples if s["dataset"] == "corpus")
        row["corpus_realtime_throughput"] = duration / row["corpus_median_seconds"]
        row["estimated_127_79_hours_processing_hours"] = 127.79 / row["corpus_realtime_throughput"]
        row["changed_transcript_ids"] = [sample["id"] for sample in passes[0]["samples"] if len({
            next(s["text_sha256"] for s in t["samples"] if s["id"] == sample["id"]) for t in passes}) > 1]
        for key in ("peak_combined_bytes", "peak_cgroup_bytes", "peak_gpu_delta_bytes"):
            row[key] = max(t["panels"][panel]["memory"][key] for t in passes for panel in PANELS)
    return summary


def run():
    adapters.require_guards()
    lock = json.loads(inputs.LOCK.read_text())
    samples = json.loads((REPORT / "inputs.json").read_text())["samples"]
    if any(sum(s["dataset"] == p for s in samples) != 100 for p in PANELS[:2]):
        raise RuntimeError("Require exactly 100 frozen utterances per reference dataset")
    if not any(s["dataset"] == "corpus" for s in samples):
        raise RuntimeError("Require corpus recordings")
    for sample in samples:
        if inputs.worker.sha256_file(ROOT / sample["path"]) != sample["audio_sha256"]:
            raise RuntimeError("Input checksum mismatch: " + sample["id"])
    result = {"schema_version": 1, "normalization": metrics.NORMALIZATION,
              "provenance": adapters.verify_provenance(lock),
              "inputs_sha256": inputs.worker.sha256_file(REPORT / "inputs.json"),
              "reference_manifest_sha256": inputs.worker.sha256_file(REPORT / "reference_samples.json"),
              "ten_hour_batch_started": False, "production_default": "small.en", "smoke": [], "trials": [],
              "timing_policy": "Initial smoke separate. Each repeat includes CLI startup/load, decoding, native serialization, timestamp offsets, common VAD/extraction and TXT/SRT writing. Downloads, hash verification, scoring, diagnostics and publishing excluded.",
              "memory_caveat": "Cgroup includes cache; global GPU deltas may double count shared RAM and other workloads. Phase sampling 100ms, outer guard 250ms.",
              "started_utc": inputs.worker.now()}
    output = REPORT / "results.json"
    if output.exists():
        raise RuntimeError("Results exist; preserve them before an explicit new comparison")
    def save():
        inputs.worker.atomic_json(output, result)
    save()
    try:
        vads = []
        for repetition in (1, 2, 3):
            vad = adapters.prepare_vad(samples, lock, repetition)
            if vads and vad["samples"] != vads[0]["samples"]:
                raise RuntimeError("VAD regions changed between repetitions")
            vads.append(vad)
            print("VAD", repetition, {p: round(vad["panels"][p]["seconds"], 3) for p in PANELS}, flush=True)
        result["shared_vad"] = vads
        save()
        configurations = list(CONFIGURATIONS)
        while configurations:
            configuration = configurations.pop(0)
            model = adapters.model_for(configuration, lock)
            print("SMOKE", configuration, flush=True)
            tick, smoke = time.monotonic(), {"configuration": configuration}
            try:
                with adapters.MemorySample() as memory:
                    with tempfile.TemporaryDirectory(prefix="smoke-", dir=WORK) as temp:
                        sample = next(s for s in samples if s["dataset"] == PANELS[0] and vads[0]["samples"][s["id"]])
                        rows, timing = adapters.evaluate_group(configuration, [sample], vads[0], model, lock, Path(temp))
                        if not metrics.normalize(rows[0]["text"]):
                            raise RuntimeError("Empty smoke speech transcript")
                smoke.update(status="complete", initial_startup_and_smoke_seconds=time.monotonic() - tick,
                             memory=memory, timing=timing, sample=rows[0])
            except Exception as exc:
                smoke.update(status="failed", error=str(exc), elapsed_seconds=time.monotonic() - tick)
            result["smoke"].append(smoke)
            save()
            if smoke["status"] == "complete":
                for repetition, vad in enumerate(vads, 1):
                    measured = trial(configuration, samples, vad, lock, repetition)
                    result["trials"].append(measured)
                    save()
                    if measured["status"] != "complete":
                        break
            if not configurations and configuration != "large-v3-q5_0":
                complete = summarize(result["trials"], samples)
                if sum(complete.get(name, {}).get("complete", False) for name in ADDITIONAL) < 2:
                    print("FALLBACK large-v3-q5_0", flush=True)
                    tick = time.monotonic()
                    try:
                        inputs.provision(fallback=True)
                        result["fallback_download_seconds_excluded"] = time.monotonic() - tick
                        result["provenance"] = adapters.verify_provenance(lock)
                        configurations.append("large-v3-q5_0")
                    except Exception as exc:
                        result["fallback_error"] = str(exc)
        result["summary"] = summarize(result["trials"], samples)
        base_pass = next((t for t in result["trials"] if t["configuration"] == "small.en-unsegmented" and t["status"] == "complete"), None)
        if base_pass:
            for name, row in result["summary"].items():
                if row["complete"] and name != "small.en-unsegmented":
                    candidate = next(t for t in result["trials"] if t["configuration"] == name and t["status"] == "complete")
                    row["paired_bootstrap"] = metrics.paired_bootstrap(base_pass["samples"], candidate["samples"])
        result["selection"] = metrics.choose(result["summary"])
        result["finished_utc"] = inputs.worker.now()
        result["status"] = "complete" if all(result["summary"].get(c, {}).get("complete") for c in CONFIGURATIONS) else "completed_with_failures"
    except BaseException as exc:
        result.update(status="interrupted_or_failed", error=repr(exc))
        raise
    finally:
        save()
    print("SELECTION", result["selection"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run",))
    parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / "comparison.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()


if __name__ == "__main__":
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    main()
