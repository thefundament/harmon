"""Offline integrity checks of a completed STT comparison and its cleanup."""
from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
import tempfile

try:
    from . import benchmark_inputs as inputs, benchmark_metrics as metrics
except ImportError:
    import benchmark_inputs as inputs
    import benchmark_metrics as metrics


def verify(result, source, references, lock, attempts):
    samples = {s["id"]: s for s in source["samples"]}
    frozen = {ds["id"] + ":" + s["id"]: s["reference"] for ds in references["datasets"] for s in ds["samples"]}
    assert len(frozen) == 200
    assert len(attempts["attempts"]) <= 10
    assert set(frozen) == {i for i, s in samples.items() if "reference" in s}
    assert all(samples[i]["reference"] == text for i, text in frozen.items())
    assert result["normalization"] == metrics.NORMALIZATION
    assert result["production_default"] == "small.en" and result["ten_hour_batch_started"] is False
    for name, runtime in lock["runtimes"].items():
        assert result["provenance"]["runtimes"][name]["commit"] == runtime["commit"]
    for model in result["provenance"]["models"].values():
        assert model == next(m for m in lock["models"] if m["id"] == model["id"])
    by_configuration = {}
    transcript_count = cues = adjusted = 0
    for trial in result["trials"]:
        if trial["status"] != "complete":
            continue
        by_configuration.setdefault(trial["configuration"], []).append(trial)
        assert len(trial["samples"]) == len(samples)
        assert {s["id"] for s in trial["samples"]} == set(samples)
        assert trial["settings"]["threads"] == 6 and trial["backend"] == "Vulkan"
        for sample in trial["samples"]:
            original = samples[sample["id"]]
            assert sample["audio_sha256"] == original["audio_sha256"]
            assert sample["audio_seconds"] == original["audio_seconds"]
            if sample["id"] in frozen:
                assert sample["score"] == metrics.score(frozen[sample["id"]], sample["text"])
            else:
                for flag in ("speaker_verified", "training_permission_verified", "training_ready"):
                    assert sample[flag] is False
            if trial["model"] == "parakeet-tdt-0.6b-v3":
                assert len(sample["native_regions"]) == len(sample["voiced_regions"])
                for region in sample["native_regions"]:
                    assert region["runtime_timestamps_relative_to_region"] is True
                    assert "tokens" in region["result"] and "segments" in region["result"]
            previous_start = -1
            for cue in sample["segments"]:
                assert math.isfinite(cue["start"] + cue["end"])
                assert 0 <= cue["start"] < cue["end"] <= sample["audio_seconds"]
                assert cue["start"] >= previous_start
                previous_start = cue["start"]
                if trial["model"] == "qwen3-asr-0.6b":
                    assert cue["timestamp_kind"] == "vad_region_no_word_alignment"
                    assert any(cue["start"] == r["start"] and cue["end"] == r["end"] for r in sample["voiced_regions"])
                else:
                    assert cue["timestamp_kind"] == "native_segment"
                cues += 1
            with tempfile.TemporaryDirectory() as temp:
                hashes = metrics.write_outputs(Path(temp) / "check", sample["text"], sample["segments"])
                assert all(sample[key] == digest for key, digest in hashes.items())
            transcript_count += 1
            adjusted += len(sample["timestamp_adjustments"])
        for panel, timing in trial["panels"].items():
            shared = timing["shared_preprocessing_seconds"]
            assert math.isclose(timing["processing_seconds"], timing["recognition_and_output_seconds"] + shared)
            vad = result["shared_vad"][trial["repetition"] - 1]
            assert shared == (0 if trial["configuration"] == "small.en-unsegmented" else vad["panels"][panel]["seconds"])
    for name, summary in result["summary"].items():
        if summary["complete"]:
            trials = by_configuration[name]
            assert {t["repetition"] for t in trials} == {1, 2, 3}
            assert summary["corpus_median_seconds"] == statistics.median(t["panels"]["corpus"]["processing_seconds"] for t in trials)
            first = trials[0]
            assert summary["quality"]["overall"] == metrics.aggregate([s["score"] for s in first["samples"] if "score" in s])
    assert all(vad["samples"] == result["shared_vad"][0]["samples"] for vad in result["shared_vad"])
    assert result["selection"] == metrics.choose(result["summary"])
    return {"status": "passed", "checked_transcript_records": transcript_count,
            "checked_timestamp_cues": cues, "explicit_timestamp_adjustments": adjusted,
            "reference_utterances": len(frozen), "source_attempts": len(attempts["attempts"])}


def main():
    def read(name):
        return json.loads((inputs.REPORT / name).read_text())
    result = read("results.json")
    assert result["inputs_sha256"] == inputs.worker.sha256_file(inputs.REPORT / "inputs.json")
    assert result["reference_manifest_sha256"] == inputs.worker.sha256_file(inputs.REPORT / "reference_samples.json")
    validation = verify(result, read("inputs.json"), read("reference_samples.json"), read("lock.json"), read("source_attempts.json"))
    leftovers = [str(p) for folder in (inputs.WORK / "audio", inputs.WORK / "regions", inputs.WORK / "datasets")
                 if folder.exists() for p in folder.rglob("*") if p.is_file()]
    assert not leftovers, "Benchmark media/dataset cleanup incomplete"
    assert not list(inputs.WORK.glob("source-*")) and not list(inputs.WORK.glob("trial-*")) and not list(inputs.WORK.glob("smoke-*"))
    validation["temporary_media_removed"] = True
    guard = json.loads((inputs.ROOT / ".harmon-work/logs/stt-comparison-final.memory.json").read_text())
    assert guard["stop_reason"] is None and guard["child_returncode"] == 0
    assert guard["peak_combined_bytes"] <= 8 * 1024**3
    assert guard["minimum_host_available_bytes"] >= 2 * 1024**3
    assert "oom_kill 0" in guard["memory_events"]
    validation["final_guard_passed"] = True
    inputs.worker.atomic_json(inputs.REPORT / "validation.json", validation)
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
