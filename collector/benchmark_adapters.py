"""Pinned CLI adapters and shared segmentation for the local STT benchmark."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import tempfile
import threading
import time
import wave

try:
    from . import benchmark_inputs as inputs, benchmark_metrics as metrics, memory_guard as guard
except ImportError:
    import benchmark_inputs as inputs
    import benchmark_metrics as metrics
    import memory_guard as guard

ROOT, WORK = inputs.ROOT, inputs.WORK
PANELS = ("librispeech-test-other", "fleurs-en_us-test", "corpus")


def require_guards():
    group = guard.cgroup_dir()
    cap = (group / "memory.max").read_text().strip()
    if cap == "max" or int(cap) > 6 * guard.GIB or (group / "memory.swap.max").read_text().strip() != "0":
        raise RuntimeError("Use scripts/run_limited.sh: 6 GiB cap and zero job swap required")
    if not guard.gpu_bytes() or guard.available_bytes() < 2 * guard.GIB:
        raise RuntimeError("GPU counters missing or host below memory floor")


class MemorySample:
    """Per-phase measurements; the outer memory_guard continuously enforces limits."""
    def __enter__(self):
        self.group, self.baseline = guard.cgroup_dir(), guard.gpu_bytes()
        self.result = dict(peak_cgroup_bytes=0, peak_gpu_delta_bytes=0, peak_combined_bytes=0,
                           minimum_host_available_bytes=guard.available_bytes(), sample_interval_s=.1)
        self.errors, self.stop = [], threading.Event()
        def sample():
            try:
                current = int((self.group / "memory.current").read_text())
                delta = sum(max(0, v - self.baseline[k]) for k, v in guard.gpu_bytes().items())
                for key, value in (("peak_cgroup_bytes", current), ("peak_gpu_delta_bytes", delta),
                                   ("peak_combined_bytes", current + delta)):
                    self.result[key] = max(self.result[key], value)
                self.result["minimum_host_available_bytes"] = min(self.result["minimum_host_available_bytes"], guard.available_bytes())
            except Exception as exc:
                self.errors.append(str(exc))
        def loop():
            while not self.stop.wait(.1):
                sample()
        self.sample = sample
        sample()
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()
        return self.result

    def __exit__(self, *args):
        self.sample()
        self.stop.set()
        self.thread.join()
        if self.errors:
            self.result["sampling_errors"] = self.errors
            if args[0] is None:
                raise RuntimeError("Memory sampling failed: " + self.errors[0])


def verify_provenance(lock):
    result = {"runtimes": {}, "models": {}}
    for name, runtime in lock["runtimes"].items():
        executable = ROOT / runtime["executable"]
        record = {**runtime, "executable_sha256": inputs.worker.sha256_file(executable)}
        if name == "whisper.cpp":
            source = ROOT / ".harmon-tools/whisper.cpp"
            commit, _ = inputs.command(["git", "-C", source, "rev-parse", "HEAD"])
            if commit.strip() != runtime["commit"]:
                raise RuntimeError("Unexpected whisper.cpp source commit")
            record["source_diff"], _ = inputs.command(["git", "-C", source, "diff"])
            record["linked_library_sha256"] = {p.name: inputs.worker.sha256_file(p)
                for p in sorted(executable.parent.glob("*.so.*")) if p.is_file() and not p.is_symlink()}
            record["build_settings"] = [s for s in (source / "build/CMakeCache.txt").read_text().splitlines()
                if s.startswith(("GGML_VULKAN:", "CMAKE_BUILD_TYPE:", "GGML_NATIVE:"))]
        else:
            if record["executable_sha256"] != runtime["executable_sha256"]:
                raise RuntimeError("speech.cpp executable checksum mismatch")
            version, _ = inputs.command([executable, "--version"])
            if "speech.cpp 0.8.11," not in version:
                raise RuntimeError("Unexpected speech.cpp runtime")
        result["runtimes"][name] = record
    for model in lock["models"]:
        path = ROOT / model["path"]
        if not path.exists() and model["fallback"]:
            continue
        if not path.is_file() or path.stat().st_size != model["bytes"] or inputs.worker.sha256_file(path) != model["sha256"]:
            raise RuntimeError("Model checksum/size mismatch: " + model["id"])
        result["models"][model["id"]] = model
    return result


def model_for(configuration, lock):
    name = "small.en" if configuration == "small.en-unsegmented" else configuration
    return next(m for m in lock["models"] if m["id"] == name)


def recognize(model, paths, directory, lock):
    """One model load per source, or per reference dataset's batch of utterances."""
    if not paths:
        return [], {"model_process_seconds": 0, "backend_log": "no voiced regions"}
    exe = ROOT / lock["runtimes"][model["runtime"]]["executable"]
    weights = ROOT / model["path"]
    tick = time.monotonic()
    if model["runtime"] == "whisper.cpp":
        argv = [exe, "-m", weights, "-t", "6", "-l", "en", "-oj"]
        prefixes = [directory / f"native-{i}" for i in range(len(paths))]
        for path, prefix in zip(paths, prefixes):
            argv += ["-f", path, "-of", prefix]
        out, err = inputs.command(argv, 7200)
        records = []
        for prefix in prefixes:
            data = json.loads(prefix.with_suffix(".json").read_text())
            native = [{"start": s["offsets"]["from"] / 1000,
                       "end": s["offsets"]["to"] / 1000, "text": s["text"]}
                      for s in data["transcription"]]
            records.append({"text": "".join(s["text"] for s in native).strip(), "segments": native, "stop": "complete"})
        if "using Vulkan" not in err and "using GPU" not in err:
            raise RuntimeError("Whisper did not confirm GPU backend: " + err[:1800])
    else:
        argv = [exe, "asr", weights, "--device", "gpu", "--threads", "6", "--language", "en", "--format", "json"]
        if model["id"].startswith("parakeet"):
            argv += ["--timestamps"]
        out, err = inputs.command([*argv, *paths], 7200)
        records = [json.loads(line) for line in out.splitlines()]
        if len(records) != len(paths) or any(Path(r["file"]) != p for r, p in zip(records, paths)):
            raise RuntimeError("speech.cpp returned missing/reordered files")
        if any(r.get("stop") != "complete" for r in records):
            raise RuntimeError("Recognition stopped early")
    elapsed = time.monotonic() - tick
    (directory / "runtime.stderr").write_text(err)
    log = [line for line in err.splitlines() if any(s in line.lower() for s in ("vulkan", "load", "device", "warm"))]
    startup = re.search(r"load time\s*=\s*([\d.]+) ms", err)
    if startup:
        startup_seconds = float(startup.group(1)) / 1000
    else:
        startup = re.search(r"Loaded .+ in ([\d.]+) s", err)
        startup_seconds = float(startup.group(1)) if startup else None
    return records, {"model_process_seconds": elapsed, "reported_model_load_seconds": startup_seconds,
                     "backend_log": log[:30], "command": [str(a) for a in argv[:16]]}


def groups_for(samples, panel):
    selected = [s for s in samples if s["dataset"] == panel]
    return [[s] for s in selected] if panel == "corpus" else [selected]


def prepare_vad(samples, lock, repetition):
    model = model_for("silero-vad", lock)
    exe = ROOT / lock["runtimes"]["speech.cpp"]["executable"]
    result = {"repetition": repetition, "panels": {}, "samples": {}}
    for panel in PANELS:
        tick = time.monotonic()
        with MemorySample() as memory:
            for group in groups_for(samples, panel):
                paths = [ROOT / s["path"] for s in group]
                argv = [exe, "vad", ROOT / model["path"], "--device", "cpu", "--threads", "6", "--format", "json",
                        "--threshold", "0.5", "--speech-pad-ms", "300", "--min-silence-duration-ms", "500",
                        "--min-speech-duration-ms", "250", "--max-speech-duration-s", "30", *paths]
                stdout, _ = inputs.command(argv)
                rows = [json.loads(line) for line in stdout.splitlines()]
                if len(rows) != len(group):
                    raise RuntimeError("VAD omitted input files")
                for sample, row, path in zip(group, rows, paths):
                    if Path(row["file"]) != path:
                        raise RuntimeError("VAD reordered files")
                    folder = WORK / "regions" / hashlib.sha256(sample["id"].encode()).hexdigest()[:20]
                    folder.mkdir(parents=True, exist_ok=True)
                    with wave.open(str(path), "rb") as source:
                        regions = []
                        for index, region in enumerate(row["regions"]):
                            a, b = max(0, round(region["start"] * 16000)), min(source.getnframes(), round(region["end"] * 16000))
                            if b <= a or b - a > 30 * 16000 + 1:
                                raise RuntimeError("VAD region violates 30-second cap")
                            if regions and a < round(regions[-1]["end"] * 16000):
                                raise RuntimeError("VAD regions overlap")
                            region_path = folder / f"{index:04}.wav"
                            source.setpos(a)
                            with wave.open(str(region_path), "wb") as target:
                                target.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                                target.writeframes(source.readframes(b - a))
                            regions.append(dict(start=a / 16000, end=b / 16000, path=str(region_path.relative_to(ROOT))))
                        result["samples"][sample["id"]] = regions
        result["panels"][panel] = {"seconds": time.monotonic() - tick, "memory": memory}
    return result


def evaluate_group(configuration, group, vad, model, lock, directory):
    raw = configuration == "small.en-unsegmented"
    mapping, paths = [], []
    for sample in group:
        regions = ([dict(start=0, end=sample["audio_seconds"], path=sample["path"])] if raw
                   else vad["samples"][sample["id"]])
        for region in regions:
            mapping.append((sample["id"], region))
            paths.append(ROOT / region["path"])
    native, timing = recognize(model, paths, directory, lock)
    output_tick = time.monotonic()
    by_sample = {s["id"]: {"text": [], "segments": [], "voiced_regions": [], "native_regions": [], "timestamp_adjustments": []} for s in group}
    qwen = model["id"].startswith("qwen")
    for (sample_id, region), record in zip(mapping, native):
        row, text = by_sample[sample_id], record["text"].strip()
        row["native_regions"].append({"start": region["start"], "end": region["end"],
            "runtime_timestamps_relative_to_region": True,
            "result": {key: value for key, value in record.items() if key != "file"}})
        kind = "vad_region_no_word_alignment" if qwen else "native_segment"
        local = ([dict(start=0, end=region["end"] - region["start"], text=text)] if qwen else record.get("segments", []))
        segments, adjustments = metrics.offset_segments(local, region["start"], region["end"], kind)
        row["text"].append(text)
        row["segments"].extend(segments)
        row["timestamp_adjustments"].extend(adjustments)
        row["voiced_regions"].append({"start": region["start"], "end": region["end"], "text": text})
    timing["timestamp_postprocessing_seconds"] = time.monotonic() - output_tick
    records = []
    for index, sample in enumerate(group):
        row = by_sample[sample["id"]]
        tick = time.monotonic()
        row["text"] = " ".join(row["text"]).strip()
        hashes = metrics.write_outputs(directory / f"output-{index}", row["text"], row["segments"])
        row["subtitle_writing_seconds"] = time.monotonic() - tick
        row.update(id=sample["id"], dataset=sample["dataset"], audio_sha256=sample["audio_sha256"],
                   audio_seconds=sample["audio_seconds"], **hashes)
        row["diagnostics"] = metrics.diagnostics(row["text"], row["voiced_regions"], sample["audio_seconds"])
        if "reference" in sample:
            row["score"] = metrics.score(sample["reference"], row["text"])
        else:
            row.update(speaker_verified=False, training_permission_verified=False, training_ready=False)
        records.append(row)
    return records, timing
