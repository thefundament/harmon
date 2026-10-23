import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from collector import benchmark_adapters as adapters
from collector import benchmark_asr as benchmark
from collector import benchmark_inputs as inputs
from collector import benchmark_metrics as metrics


class ScoringTests(unittest.TestCase):
    def test_known_alignment_and_empty_hypothesis(self):
        result = metrics.score("one two three four", "one too three five six")
        self.assertEqual((result["substitutions"], result["deletions"], result["insertions"]), (2, 0, 1))
        self.assertEqual(result["wer"], .75)
        self.assertEqual(metrics.score("one two", "")["deletions"], 2)
        self.assertIsNone(metrics.score("", "extra")["wer"])

    def test_corpus_weighting_and_insertions_above_one(self):
        result = metrics.aggregate([metrics.score("a b c d", "a b c d"), metrics.score("e", "e f g")])
        self.assertEqual(result["wer"], .4)
        self.assertEqual(metrics.score("a", "b c d")["wer"], 3)

    def test_identical_normalization_keeps_contractions_and_numbers(self):
        self.assertEqual(metrics.normalize("[MUSIC] “We’re” twenty-one, 25."), ["we're", "twenty", "one", "25"])
        self.assertEqual(metrics.score("ＨＥＬＬＯ world", "Hello, WORLD!")["errors"], 0)

    def test_bootstrap_paired_and_deterministic(self):
        base = [{"id": p, "dataset": p, "score": {"errors": 4, "reference_words": 10}} for p in adapters.PANELS[:2]]
        candidate = copy.deepcopy(base)
        for row in candidate:
            row["score"]["errors"] = 2
        result = metrics.paired_bootstrap(base, candidate, 40)
        self.assertEqual(result["relative_improvement_95_ci"], [.5, .5])
        self.assertEqual(result, metrics.paired_bootstrap(base, candidate, 40))


class TimestampTests(unittest.TestCase):
    def test_native_offsets_and_endpoint_clamp_are_explicit(self):
        segments, adjustments = metrics.offset_segments([dict(start=.2, end=4, text="hello")], 10, 13, "native_segment")
        self.assertEqual(segments[0]["start"], 10.2)
        self.assertEqual(segments[0]["end"], 13)
        self.assertEqual(adjustments, [{"native_end": 14, "clamped_end": 13}])

    def test_invalid_timestamp_rejected(self):
        for start, end in ((2, 1), (-1, 1), (0, float("nan"))):
            with self.assertRaises(ValueError):
                metrics.offset_segments([dict(start=start, end=end, text="x")], 0, 10, "native_segment")

    def test_zero_duration_native_cue_is_explicit_without_dropping_text(self):
        segments, adjustments = metrics.offset_segments([dict(start=2, end=2, text="[Music]")], 10, 20, "native_segment")
        self.assertEqual(segments, [])
        self.assertEqual(adjustments[0]["text"], "[Music]")
        self.assertEqual(adjustments[0]["native_start"], 12)
        self.assertIn("zero_duration", adjustments[0]["reason"])

    def test_qwen_uses_region_times_without_alignment_claim(self):
        sample = dict(id="sample", dataset="corpus", audio_seconds=30, audio_sha256="frozen")
        vad = {"samples": {"sample": [dict(start=12, end=20, path="unused.wav")]}}
        fake = ([dict(text="Hello there", stop="complete")], {"model_process_seconds": 1})
        with tempfile.TemporaryDirectory() as temp, patch.object(adapters, "recognize", return_value=fake):
            rows, _ = adapters.evaluate_group("qwen3-asr-0.6b", [sample], vad, {"id": "qwen3-asr-0.6b"}, {}, Path(temp))
        self.assertEqual(rows[0]["segments"], [dict(start=12, end=20, text="Hello there", timestamp_kind="vad_region_no_word_alignment")])
        self.assertFalse(rows[0]["speaker_verified"])

    def test_subtitle_rounding_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp) / "out"
            hashes = metrics.write_outputs(prefix, "Hello", [dict(start=59.9996, end=61.1, text="Hello")])
            self.assertIn("00:01:00,000 --> 00:01:01,100", prefix.with_suffix(".srt").read_text())
            self.assertEqual(hashes["text_sha256"], hashlib.sha256(b"Hello\n").hexdigest())

    def test_parakeet_preserves_raw_native_token_times_with_region_origin(self):
        sample = dict(id="sample", dataset="corpus", audio_seconds=30, audio_sha256="frozen")
        vad = {"samples": {"sample": [dict(start=12, end=20, path="unused.wav")]}}
        native = dict(text="Hello", stop="complete", segments=[dict(start=.2, end=1, text="Hello")],
                      tokens=[dict(start=.2, end=.8, text="Hello")])
        with tempfile.TemporaryDirectory() as temp, patch.object(adapters, "recognize", return_value=([native], {"model_process_seconds": 1})):
            rows, _ = adapters.evaluate_group("parakeet-tdt-0.6b-v3", [sample], vad,
                {"id": "parakeet-tdt-0.6b-v3"}, {}, Path(temp))
        self.assertEqual(rows[0]["native_regions"][0]["result"]["tokens"], native["tokens"])
        self.assertEqual(rows[0]["native_regions"][0]["start"], 12)
        self.assertEqual(rows[0]["segments"][0]["start"], 12.2)


class AdapterTests(unittest.TestCase):
    def test_early_stop_requires_fixed_quality_failure_or_proven_median_failure(self):
        def trial(name, repetition, seconds, hypothesis):
            return dict(configuration=name, repetition=repetition, status="complete",
                panels={"corpus": {"processing_seconds": seconds}},
                samples=[dict(dataset=p, score=metrics.score("one two three four", hypothesis)) for p in adapters.PANELS[:2]])
        base = [trial("small.en-unsegmented", i, 10, "one two three five") for i in (1, 2, 3)]
        bad_quality = trial("candidate", 1, 5, "one two three five")
        self.assertIn("quality", benchmark.ineligibility_reason([*base, bad_quality], "candidate"))
        slow = trial("candidate", 1, 16, "one two three four")
        self.assertIsNone(benchmark.ineligibility_reason([*base, slow], "candidate"))
        self.assertIsNone(benchmark.ineligibility_reason([*base, slow, trial("candidate", 2, 14, "one two three four")], "candidate"))
        self.assertIn("median", benchmark.ineligibility_reason([*base, slow, trial("candidate", 2, 16, "one two three four")], "candidate"))

    def test_resumption_retains_only_unique_completed_repetitions(self):
        trials = [dict(configuration="x", repetition=1, status="complete"),
                  dict(configuration="x", repetition=2, status="failed"),
                  dict(configuration="x", repetition=2, status="complete")]
        self.assertEqual(benchmark.completed_repetitions(trials, "x"), {1, 2})
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            benchmark.completed_repetitions([*trials, trials[0]], "x")

    def test_whisper_gpu_confirmation_requires_logs(self):
        model = {"id": "small.en", "runtime": "whisper.cpp", "path": "weights.bin"}
        lock = {"runtimes": {"whisper.cpp": {"executable": "whisper-cli"}}}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "native-0.json").write_text(json.dumps({"transcription": [dict(offsets={"from": 0, "to": 1000}, text="hi")]}))
            with patch.object(inputs, "command", return_value=("", "using Vulkan0 backend\nload time = 200 ms")) as command:
                rows, timing = adapters.recognize(model, [Path("audio.wav")], directory, lock)
                self.assertNotIn("-np", command.call_args.args[0])
                self.assertEqual(rows[0]["segments"][0]["end"], 1)
                self.assertEqual(timing["reported_model_load_seconds"], .2)
            with patch.object(inputs, "command", return_value=("", "using CPU backend")):
                with self.assertRaisesRegex(RuntimeError, "GPU backend"):
                    adapters.recognize(model, [Path("audio.wav")], directory, lock)

    def test_failed_pass_is_explicit_and_not_complete(self):
        with patch.object(adapters, "model_for", return_value={"id": "x", "sha256": "x", "runtime": "speech.cpp"}), \
             patch.object(adapters, "MemorySample", return_value=MagicMock()), \
             patch.object(adapters, "evaluate_group", side_effect=RuntimeError("model_limit")), \
             patch.object(adapters, "groups_for", return_value=[[{}]]), tempfile.TemporaryDirectory() as temp, \
             patch.object(benchmark, "WORK", Path(temp)):
            result = benchmark.trial("x", [], {}, {}, 1)
        self.assertEqual(result["status"], "failed")
        self.assertIn("model_limit", result["error"])

    def test_timing_includes_preprocessing_and_outputs_excludes_scoring(self):
        samples = [dict(id=p, dataset=p) for p in adapters.PANELS]
        def evaluate(config, group, *args):
            return [{**s, "subtitle_writing_seconds": .5} for s in group], {
                "model_process_seconds": 5, "timestamp_postprocessing_seconds": 1}
        vad = {"panels": {p: {"seconds": 2} for p in adapters.PANELS}}
        with patch.object(adapters, "model_for", return_value={"id": "x", "sha256": "x", "runtime": "speech.cpp"}), \
             patch.object(adapters, "MemorySample", return_value=MagicMock()), \
             patch.object(adapters, "evaluate_group", side_effect=evaluate), \
             patch.object(benchmark.time, "monotonic", side_effect=[0, 1000, 2000, 3000, 4000, 5000]), \
             tempfile.TemporaryDirectory() as temp, patch.object(benchmark, "WORK", Path(temp)):
            result = benchmark.trial("x", samples, vad, {}, 1)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(all(p["processing_seconds"] == 8.5 for p in result["panels"].values()))
        self.assertTrue(all(p["observed_panel_wall_seconds_including_scoring"] == 1000 for p in result["panels"].values()))

    def test_timeout_reaps_process(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            inputs.command([sys.executable, "-c", "import time; time.sleep(10)"], .03)

    def test_checksum_failure_does_not_install_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "model.gguf"
            def transfer(*args, **kwargs):
                target.with_name(target.name + ".part").write_bytes(b"wrong")
                return "", ""
            with patch.object(inputs, "command", side_effect=transfer):
                with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
                    inputs.download("https://example.invalid/model", target, "0" * 64)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(target.name + ".part").exists())

    def test_missing_memory_cap_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            group = Path(temp)
            (group / "memory.max").write_text("max")
            (group / "memory.swap.max").write_text("0")
            with patch.object(adapters.guard, "cgroup_dir", return_value=group):
                with self.assertRaisesRegex(RuntimeError, "6 GiB"):
                    adapters.require_guards()


class SamplingAndSelectionTests(unittest.TestCase):
    def test_sampling_is_independent_of_dataset_order(self):
        ids = [str(i) for i in range(200)]
        self.assertEqual(inputs.select_ids("test", ids), inputs.select_ids("test", list(reversed(ids))))
        with self.assertRaises(RuntimeError):
            inputs.select_ids("test", ["same"] * 200)

    def test_source_attempt_limit_survives_failure_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            tasks = [dict(task_id="clip:" + str(i), kind="clip", source_url="https://clips.twitch.tv/" + str(i)) for i in range(12)]
            with patch.object(inputs, "REPORT", folder), patch.object(inputs, "WORK", folder), \
                 patch.object(inputs, "corpus_tasks", return_value=tasks), \
                 patch.object(inputs, "authorize_source", return_value={"owner_verified": True}), \
                 patch.object(inputs.worker, "sha256_file", return_value="inventory"), \
                 patch.object(inputs, "command", side_effect=RuntimeError("unavailable")) as command:
                self.assertEqual(inputs.prepare_corpus(), [])
                self.assertEqual(inputs.prepare_corpus(), [])
                self.assertEqual(command.call_count, 10)
            ledger = json.loads((folder / "source_attempts.json").read_text())
            self.assertEqual(len(ledger["attempts"]), 10)
            self.assertTrue(all(r["status"] == "failed" for r in ledger["attempts"]))
            self.assertFalse(list(folder.glob("source-*")))

    def test_source_gate_rejection_cannot_download_media(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            task = dict(task_id="clip:wrong", kind="clip", source_url="https://clips.twitch.tv/wrong")
            with patch.object(inputs, "REPORT", folder), patch.object(inputs, "WORK", folder), \
                 patch.object(inputs, "corpus_tasks", return_value=[task]), \
                 patch.object(inputs, "authorize_source", side_effect=RuntimeError("foreign broadcaster")), \
                 patch.object(inputs, "command") as command:
                self.assertEqual(inputs.prepare_corpus(), [])
                command.assert_not_called()
            self.assertIn("foreign broadcaster", json.loads((folder / "source_attempts.json").read_text())["attempts"][0]["error"])

    def test_selection_enforces_speed_dataset_regression_and_uncertainty(self):
        def row(seconds, wer):
            return {"complete": True, "corpus_median_seconds": seconds,
                    "quality": {p: {"wer": wer} for p in (*adapters.PANELS[:2], "overall")},
                    "paired_bootstrap": {"relative_improvement_95_ci": [.01, .3]}}
        baseline = row(10, .2)
        candidate = row(15, .17)
        summary = {"small.en-unsegmented": baseline, "candidate": candidate}
        self.assertEqual(metrics.choose(copy.deepcopy(summary))["model"], "candidate")
        candidate["corpus_median_seconds"] = 15.01
        self.assertEqual(metrics.choose(copy.deepcopy(summary))["model"], "small.en")
        candidate["corpus_median_seconds"] = 14
        candidate["quality"][adapters.PANELS[0]]["wer"] = .211
        self.assertEqual(metrics.choose(copy.deepcopy(summary))["model"], "small.en")
        candidate["quality"][adapters.PANELS[0]]["wer"] = .17
        candidate["paired_bootstrap"]["relative_improvement_95_ci"] = [-.02, .3]
        self.assertEqual(metrics.choose(copy.deepcopy(summary))["model"], "small.en")


if __name__ == "__main__":
    unittest.main()
