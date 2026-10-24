import copy
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

from collector import quality_followup as f


def cue(a, b, text):
    return dict(start=a, end=b, text=text)


def wav(path, seconds=2):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0"*round(16000*seconds))


class QualityFollowupTests(unittest.TestCase):
    def test_presentation_preserves_each_cues_entire_coverage_and_verbatim_text(self):
        native = [cue(3, 6, "same"), cue(0, 4, "odd\nwording!"), cue(3, 5, "same"),
                  cue(8, 8, "zero duration retained"), cue(9, 7, "backwards retained")]
        before = copy.deepcopy(native)
        result = f.presentation(native)
        self.assertEqual(native, before)
        self.assertEqual(result["native_segments"], before)
        self.assertEqual(result["undisplayable_cue_indices"], [3, 4])
        segments = result["segments"]
        self.assertEqual([(s["start"], s["end"]) for s in segments], [(0, 3), (3, 4), (4, 5), (5, 6)])
        self.assertEqual(segments[1]["text"], "same\nodd\nwording!\nsame")
        for left, right in zip(segments, segments[1:]):
            self.assertLessEqual(left["end"], right["start"])
        for i, original in enumerate(native[:3]):
            spans = [(s["start"], s["end"]) for s in segments if i in s["cue_indices"]]
            self.assertEqual(f.pilot.intervals_length(spans), original["end"]-original["start"])
            for segment in segments:
                self.assertEqual(segment["text"], "\n".join(native[j]["text"] for j in segment["cue_indices"]))
        self.assertFalse(result["speech_alignment_improved"])

    def test_presentation_millisecond_rounding_empty_and_nonfinite(self):
        self.assertEqual(f.presentation([])["segments"], [])
        r = f.presentation([cue(0, 1.00049, "a"), cue(1.00048, 2, "b"), cue(3, float("nan"), "invalid")])
        self.assertEqual([(s["start"], s["end"]) for s in r["segments"]], [(0, 1), (1, 2)])
        self.assertEqual(r["undisplayable_cue_indices"], [2])

    def test_disagreement_is_symmetric_bounded_and_handles_empty(self):
        for a, b, expected in [("", "", 0), ("", "one two", 1), ("a b", "a c", .5),
                                ("Missions!", "missions", 0), ("a a a", "b", 1)]:
            self.assertEqual(f.disagreement(a, b)["fraction"], expected)
            self.assertEqual(f.disagreement(b, a)["fraction"], expected)
            self.assertFalse(f.disagreement(a, b)["reference_accuracy"])

    def test_attempts_persist_before_network_and_never_reset(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"ledger.json"
            allowed = set("abcde")
            ledger = dict(sources={})
            for key in "abcd":
                self.assertTrue(f.reserve_attempt(ledger, key, path, allowed))
                ledger = f.pilot.read_json(path)
                ledger["sources"][key]["status"] = "failed"
                self.assertFalse(f.reserve_attempt(ledger, key, path, allowed))
            self.assertFalse(f.reserve_attempt(ledger, "e", path, allowed))
            with self.assertRaisesRegex(RuntimeError, "outside"):
                f.reserve_attempt(ledger, "replacement", path, allowed)

    def test_wav_hash_proves_pcm_equality_and_mismatch_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"original.wav"
            wav(path)
            row = dict(duration=2, pilot_audio=dict(sha256=f.worker.sha256_file(path)))
            audio = f.check_audio(path, row)
            self.assertTrue(audio["identical_pilot_pcm"])
            self.assertEqual(audio["pcm_sha256"], f.hashlib.sha256(b"\0\0"*32000).hexdigest())
            row["pilot_audio"]["sha256"] = "different"
            with self.assertRaises(f.HashMismatch):
                f.check_audio(path, row)

    def test_cached_attempt_uses_no_network_and_mismatch_gets_no_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = dict(kind="youtube", task_id="fixed")
            directory = root/"audio"/f.worker.task_key(task)
            directory.mkdir(parents=True)
            path = directory/"original.wav"
            wav(path)
            row = dict(task=task, duration=2, pilot_audio=dict(sha256=f.worker.sha256_file(path)))
            ledger = dict(sources={"fixed":dict(status="verified")})
            frozen = dict(sources=[row])
            with mock.patch.object(f, "command") as command:
                f.acquire(row, {}, root, ledger, frozen)
                command.assert_not_called()
                row["pilot_audio"]["sha256"] = "mismatch"
                with self.assertRaises(f.HashMismatch):
                    f.acquire(row, {}, root, ledger, frozen)
                path.unlink()
                with self.assertRaisesRegex(RuntimeError, "consumed"):
                    f.acquire(row, {}, root, ledger, frozen)
                command.assert_not_called()

    def test_speech_support_distinguishes_vad_exclusion_and_match_rejection(self):
        d = dict(raw_voiced_intervals=[[0, 1], [1, 2], [4, 5]], eligible_speech_intervals=[[0, 4]],
                 accepted_speaker_intervals=[cue(0, 2, "")], rejected_speaker_intervals=[cue(2, 4, "")],
                 excluded_before_speaker_intervals=[[4, 6]])
        self.assertEqual(f.support(d, 2, 4)["rejected_match_fraction"], 1)
        s = f.support(d, 4, 6)
        self.assertEqual(s["raw_voiced_fraction"], .5)
        self.assertEqual(s["excluded_before_speaker_fraction"], 1)
        self.assertEqual(f.complement([[3, 5], [1, 4]], 0, 6), [[0, 1], [5, 6]])

    def test_confidence_excludes_special_tokens_and_retains_low_probability_tokens(self):
        native = dict(transcription=[dict(tokens=[dict(id=1, text="odd", p=.1),
            dict(id=2, text="word", p=.9), dict(id=50257, text="[EOT]", p=0)])])
        result = f.token_confidence(native)
        self.assertEqual(result["tokens"], 2)
        self.assertEqual(result["mean_probability"], .5)
        self.assertEqual(result["low_probability_fraction"], .5)

    def test_decoder_independent_context_full_tokens_resume_and_input_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            part = root/"passage.wav"
            wav(part)
            ledger = dict(deadline_unix=f.time.time()+60)
            tools = dict(whisper="whisper", model="installed", provenance={"installed":"hash"})
            calls = []
            def command(argv, *_):
                calls.append(argv)
                prefix = Path(argv[argv.index("-of")+1])
                f.worker.atomic_json(prefix.with_suffix(".json"), dict(transcription=[dict(
                    offsets=dict(**{"from":0, "to":1000}), text=" unusual words", tokens=[dict(id=1, text="unusual", p=.3)])]))
                prefix.with_suffix(".txt").write_text(" unusual words\n")
                prefix.with_suffix(".srt").write_text("native cue")
                return "", "using Vulkan0 backend"
            with mock.patch.object(f, "command", side_effect=command):
                a = f.decode(part, root/"turbo", "turbo", tools, ledger)
                b = f.decode(part, root/"turbo", "turbo", tools, ledger)
                self.assertEqual(a, b)
                self.assertEqual(len(calls), 1)
                self.assertIn("-ojf", calls[0])
                self.assertEqual(calls[0][calls[0].index("--max-context")+1], "0")
                self.assertEqual(f.pilot.offset_segments(a["native_segments"], 275)[0]["start"], 275)
                wav(part, 3)
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    f.decode(part, root/"turbo", "turbo", tools, ledger)
            (root/"turbo/native.json").write_text("damaged")
            with self.assertRaisesRegex(RuntimeError, "Protected inputs changed"):
                f.complete(root/"turbo/result.json")

    def run_fixture(self, root, process=None, acquire=None, ledger=None):
        task = dict(kind="youtube", task_id="fixed", source_id="fixed-source")
        frozen = dict(sources=[dict(task=task)], passages=[dict(id="passage", source="fixed-source")], protected_sha256={})
        if ledger is not None:
            f.worker.atomic_json(root/"ledger.json", ledger)
        with mock.patch.object(f.pilot, "require_guards"), \
             mock.patch.object(f, "freeze", return_value=frozen), \
             mock.patch.object(f, "render"), \
             mock.patch.object(f, "find_tools", return_value=dict(provenance={})) as tools, \
             mock.patch.object(f, "acquire", side_effect=acquire) as download, \
             mock.patch.object(f, "process", side_effect=process), \
             mock.patch.object(f, "report"):
            f.run(root)
        return tools, download

    def test_graceful_failure_cleans_audio_and_resume_never_downloads_again(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def acquire(row, tools, work, ledger, frozen):
                directory = work/"audio"/f.worker.task_key(row["task"])
                directory.mkdir(parents=True)
                (directory/"original.wav").write_bytes(b"temporary private audio")
                f.reserve_attempt(ledger, "fixed", work/"ledger.json", {"fixed"})
                raise f.HashMismatch("different PCM")
            self.run_fixture(root, acquire=acquire)
            self.assertFalse((root/"audio").exists())
            ledger = f.pilot.read_json(root/"ledger.json")
            self.assertTrue(ledger["sources"]["fixed"]["hash_mismatch"])
            deadline = ledger["deadline_unix"]
            _, download = self.run_fixture(root)
            download.assert_not_called()
            self.assertEqual(f.pilot.read_json(root/"ledger.json")["deadline_unix"], deadline)

    def test_completed_resume_skips_tools_and_expired_budget_cleans_audio(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/"passages/passage").mkdir(parents=True)
            f.seal(root/"passages/passage/result.json", {}, [])
            tools, download = self.run_fixture(root)
            tools.assert_not_called()
            download.assert_not_called()
            (root/"passages/passage/result.json").unlink()
            (root/"audio").mkdir()
            (root/"audio/leftover.wav").write_bytes(b"private")
            tools, download = self.run_fixture(root, ledger=dict(deadline_unix=f.time.time()-1, sources={}))
            tools.assert_not_called()
            download.assert_not_called()
            self.assertFalse((root/"audio").exists())
            self.assertIn("deadline", f.pilot.read_json(root/"ledger.json")["stop_reason"])

    def test_signal_during_processing_cleans_audio_and_preserves_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def acquire(row, tools, work, ledger, frozen):
                directory = work/"audio"/f.worker.task_key(row["task"])
                directory.mkdir(parents=True)
                path = directory/"original.wav"
                wav(path)
                f.reserve_attempt(ledger, "fixed", work/"ledger.json", {"fixed"})
                return path, {}
            self.run_fixture(root, acquire=acquire, process=f.pilot.BudgetExpired("signal"))
            self.assertFalse((root/"audio").exists())
            self.assertEqual(len(f.pilot.read_json(root/"ledger.json")["sources"]), 1)

    def test_seal_distinguishes_nested_model_checkpoints(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = []
            for name in ("turbo", "parakeet"):
                p = root/name/"result.json"
                p.parent.mkdir()
                p.write_text(name)
                paths.append(p)
            f.seal(root/"result.json", {}, paths)
            self.assertEqual(len(f.pilot.read_json(root/"result.json")["output_sha256"]), 2)
            self.assertTrue(f.complete(root/"result.json"))

    def test_render_and_audit_check_text_coverage_native_timing_and_nested_flags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, work = root/"pilot", root/"followup"
            task = dict(kind="youtube", task_id="fixed", source_id="fixed-source", **f.FLAGS)
            row = dict(task=task)
            protected = []
            for config in f.pilot.CONFIGS:
                directory = source/"outputs"/f.worker.task_key(task)/config
                directory.mkdir(parents=True)
                f.worker.atomic_json(directory/"result.json", dict(segments=[cue(0, 2, "odd!"), cue(1, 3, "wording")], **f.FLAGS))
                (directory/"transcript.txt").write_text("odd!\nwording\n")
                protected.extend(directory.iterdir())
            frozen = dict(protected_sha256=f.hashes(protected), render_sources=[row], sources=[], passages=[],
                          pilot=str(source), profile_sha256="frozen-profile")
            f.render(work, frozen)
            self.assertEqual(f.audit(work, frozen, dict(sources={}))['rendered_outputs'], 3)
            path = work/"rendered"/f.worker.task_key(task)/f.pilot.CONFIGS[0]/"result.json"
            data = f.pilot.read_json(path)
            data["native_segments"][0]["speaker_verified"] = True
            f.worker.atomic_json(path, data)
            with self.assertRaisesRegex(RuntimeError, "verification flag changed"):
                f.audit(work, frozen, dict(sources={}))

    def test_process_extracts_relative_audio_and_offsets_both_model_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio_path = root/"original.wav"
            wav(audio_path, 5)
            row = dict(task=dict(kind="youtube", task_id="fixed", source_id="fixed", start_s=1000))
            passage = dict(id="focus", source="fixed", start=1001, end=1002, focus=[1001,1002])
            frozen = dict(passages=[passage])
            diagnostics = dict(raw_voiced_intervals=[[0, 5]], eligible_speech_intervals=[[0, 5]],
                accepted_speaker_intervals=[cue(0, 5, "")], rejected_speaker_intervals=[],
                excluded_before_speaker_intervals=[])
            def decode(part, dest, model, *_):
                with wave.open(str(part)) as extracted:
                    self.assertEqual(extracted.getnframes(), 16000)
                dest.mkdir(parents=True)
                data = dict(pcm_sha256=f.pcm_sha256(part), text="odd speech", native_segments=[cue(.25,.75,"odd speech")], **f.FLAGS)
                f.seal(dest/"result.json", data, [])
                return data
            with mock.patch.object(f, "activity_diagnostics", return_value=diagnostics), \
                 mock.patch.object(f, "decode", side_effect=decode), \
                 mock.patch.object(f, "excerpt", return_value=dict(text="saved")), \
                 mock.patch.object(f, "energy", return_value=[]):
                f.process(row, audio_path, dict(duration=5), {}, root, dict(deadline_unix=f.time.time()+60), frozen)
            data = f.pilot.read_json(root/"passages/focus/result.json")
            for model in ("turbo", "parakeet"):
                self.assertEqual(data["models"][model]["source_segments"], [cue(1001.25,1001.75,"odd speech")])
                self.assertEqual(data["models"][model]["native_segments"], [cue(.25,.75,"odd speech")])
            self.assertFalse((root/"passage.wav").exists())

    def test_cli_cleans_orphan_audio_even_on_early_guard_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/"audio").mkdir()
            (root/"audio/orphan.wav").write_bytes(b"private")
            old_umask = f.os.umask(0o077)
            try:
                with mock.patch.object(f, "WORK", root), \
                     mock.patch.object(f, "locks", return_value=contextlib.nullcontext()), \
                     mock.patch.object(f, "run", side_effect=RuntimeError("guard failure")), \
                     mock.patch("sys.argv", ["quality_followup", "run"]):
                    with self.assertRaisesRegex(RuntimeError, "guard failure"):
                        f.main()
                self.assertFalse((root/"audio").exists())
            finally:
                f.os.umask(old_umask)


if __name__ == "__main__":
    unittest.main()
