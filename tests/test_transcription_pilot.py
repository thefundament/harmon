import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from collector import transcription_pilot as pilot


def cue(a, b, text):
    return {"start": a, "end": b, "text": text}


class TranscriptionPilotTests(unittest.TestCase):
    def test_chunk_stride_tail_and_offsets(self):
        self.assertEqual(list(pilot.chunks(60)), [(0, 30), (29, 59), (58, 60)])
        self.assertEqual(list(pilot.chunks(30)), [(0, 30)])
        self.assertEqual(list(pilot.chunks(0)), [])
        result = pilot.offset_segments([cue(0, 1, "words")], 29)
        self.assertEqual(result, [cue(29, 30, "words")])
        mapped = pilot.map_source(result, [], 600)
        self.assertEqual(mapped, [cue(629, 630, "words")])

    def test_overlap_dedup_is_local_and_keeps_raw_and_suspicious_text(self):
        before = [cue(1, 2, "repeat these words"), cue(29, 29.9, "Hello world.")]
        incoming = [cue(29, 29.9, "hello world!"), cue(30, 31, "Hello world again.")]
        original = copy.deepcopy(incoming)
        merged, count = pilot.merge_boundary(before, incoming, 29, 30)
        self.assertEqual(count, 2)
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[-1]["text"], "Hello world again.")
        self.assertEqual(incoming, original)
        # Repeated phrases outside the shared second are never removed.
        merged, count = pilot.merge_boundary(before[:1], [cue(29, 30, "repeat these words")], 29, 30)
        self.assertEqual(count, 0)
        self.assertEqual(len(merged), 2)
        # We have no word timestamps for a cue crossing the boundary: retain it.
        merged, count = pilot.merge_boundary(before, [cue(29, 34, "hello world")], 29, 30)
        self.assertEqual(count, 0)

    def test_source_mapping_preserves_defect_and_annotation_retains_all_text(self):
        mapping = [{"compact_start_s": 0, "compact_end_s": 2,
                    "source_start_s": 10, "source_end_s": 12}]
        segments = pilot.map_source([cue(0, 1, "match"), cue(3, 4, "suspicious")], mapping, 600)
        self.assertEqual(segments[0], cue(610, 611, "match"))
        self.assertEqual(segments[1], cue(612, 612, "suspicious"))
        self.assertIn("nonpositive_duration", pilot.timing_issues(segments, 600, 20)[0]["issues"])
        raw = [cue(610, 611, "match"), cue(615, 618, "different voice")]
        annotated = pilot.annotate(raw, mapping, 600)
        self.assertEqual([s["text"] for s in annotated], ["match", "different voice"])
        self.assertEqual([s["speaker_match"] for s in annotated], ["profile_match_overlap", "other_or_uncertain"])
        self.assertTrue(all(s["speaker_verified"] is False for s in annotated))

    def test_repetition_independent_of_lines_empty_and_coverage(self):
        phrase = "one two three four five six seven "
        a = pilot.diagnostics(phrase*6, [], 0, 60, 40, 42, 0)
        b = pilot.diagnostics((phrase+"\n")*6, [], 0, 60, 40, 42, 0)
        self.assertEqual(a["repeated_5gram_excess"], b["repeated_5gram_excess"])
        self.assertGreater(a["repeated_5gram_excess"], 0)
        self.assertEqual(a["processed_source_fraction"], 2/3)
        self.assertTrue(pilot.diagnostics("[BLANK_AUDIO]", [], 0, 30, 30, 30, 1)["empty"])
        self.assertEqual(pilot.intervals_length([(0, 30), (29, 59), (58, 60)]), 60)
        defects = pilot.timing_issues([cue(1, 3, "a"), cue(2, 4, "b"), cue(1, 70, "c")], 0, 60)
        self.assertIn("overlap", defects[0]["issues"])
        self.assertIn("beyond_audio", defects[1]["issues"])

    def test_attempts_reserved_before_io_and_survive_restart_and_failures(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/"ledger.json"
            ledger = {"sources": {}}
            for i in range(6):
                self.assertTrue(pilot.reserve_attempt(ledger, str(i), p))
                # A fresh process sees the reservation even without a success marker.
                saved = pilot.read_json(p)
                self.assertFalse(pilot.reserve_attempt(saved, str(i), p))
                ledger = saved
            self.assertFalse(pilot.reserve_attempt(ledger, "seventh", p))
            self.assertEqual(len(pilot.read_json(p)["sources"]), 6)

    def test_consumed_attempt_never_downloads_again(self):
        with tempfile.TemporaryDirectory() as td:
            task = {"task_id": "youtube:id:0-60", "kind": "youtube"}
            ledger = {"sources": {task["task_id"]: {"status": "attempting"}}}
            with mock.patch.object(pilot, "command") as command:
                with self.assertRaisesRegex(RuntimeError, "already consumed"):
                    pilot.download({"task": task}, {}, Path(td), ledger)
                command.assert_not_called()

    def test_completed_resume_skips_tools_and_downloads_preserves_existing_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            protected = root/"production.txt"; protected.write_text("original words")
            snapshot = {str(protected): pilot.worker.sha256_file(protected)}
            task = {"kind": "youtube", "task_id": "youtube:example:0-30", "source_id": "example"}
            frozen = {"sources": [{"task": task, "role": "problem"}],
                      "profile": str(root/"profile.json"), "protected_sha256": snapshot}
            for config in pilot.CONFIGS:
                dest = root/"outputs"/pilot.worker.task_key(task)/config
                dest.mkdir(parents=True)
                pilot.write_result(dest, {"text": "words", "segments": [cue(0, 1, "words")],
                    "pipeline_seconds": 1, "metrics": pilot.diagnostics("words", [cue(0, 1, "words")], 0, 30, 30, 30, 0)})
            with mock.patch.object(pilot, "freeze", return_value=frozen), \
                 mock.patch.object(pilot, "require_guards"), \
                 mock.patch.object(pilot.speaker, "load_profile"), \
                 mock.patch.object(pilot.worker, "find_tools") as tools, \
                 mock.patch.object(pilot, "download") as download:
                pilot.run(root)
                tools.assert_not_called(); download.assert_not_called()
            self.assertEqual(protected.read_text(), "original words")
            self.assertTrue((root/"REPORT.md").exists())
            dest = root/"outputs"/pilot.worker.task_key(task)/pilot.CONFIGS[0]
            (dest/"transcript.txt").write_text("damaged")
            with self.assertRaisesRegex(RuntimeError, "damaged"):
                pilot.result_complete(dest)

    def test_decoder_offsets_resumption_and_context_flags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); wav = root/"original.wav"
            with pilot.wave.open(str(wav), "wb") as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
                f.writeframes(b"\0\0"*16000*31)
            ledger = {"deadline_unix": pilot.time.time()+60}
            commands = []
            def fake(argv, *_):
                commands.append(argv)
                prefix = Path(argv[argv.index("-of")+1])
                prefix.with_suffix(".json").write_text(json.dumps({"transcription": [
                    {"offsets": {"from": 0, "to": 1500}, "text": "hello words"}]}))
                prefix.with_suffix(".txt").write_text("hello words")
                prefix.with_suffix(".srt").write_text("fixture")
            tools = {"whisper": "fake", "model": "installed-turbo"}
            with mock.patch.object(pilot, "command", side_effect=fake):
                a = pilot.decode(wav, root/"decode", tools, ledger, True)
                b = pilot.decode(wav, root/"decode", tools, ledger, True)
            self.assertEqual(len(commands), 2)
            self.assertTrue(all(c[-2:] == ["--max-context", "0"] for c in commands))
            self.assertEqual([s["start"] for s in a["raw_segments"]], [0, 29])
            self.assertEqual(a, b)
            self.assertFalse((root/"decode/chunk.wav").exists())

    def test_native_timing_checks_individual_chunk_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/"native").mkdir()
            pilot.worker.atomic_json(root/"native/chunk-001.checkpoint.json", {
                "start":29, "end":59, "segments":[cue(28, 31, "out of chunk"), cue(30,30,"retained")]})
            issues=pilot.native_timing_issues(root)
            self.assertEqual(len(issues),2)
            self.assertIn("beyond_audio",issues[0]["issues"])
            self.assertIn("nonpositive_duration",issues[1]["issues"])

    def test_cached_audio_recovers_consumed_attempt_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); task={"task_id":"youtube:cached:0-30", "kind":"youtube"}
            d=root/"audio"/pilot.worker.task_key(task); d.mkdir(parents=True)
            wav=d/"original.wav"; wav.write_bytes(b"private cached audio fixture")
            meta={"sha256":pilot.worker.sha256_file(wav),"duration":30}
            pilot.worker.atomic_json(d/"audio.json",meta)
            ledger={"sources":{task["task_id"]:{"status":"attempting"}}}
            with mock.patch.object(pilot,"command") as command:
                self.assertEqual(pilot.download({"task":task},{},root,ledger),(wav,meta))
                command.assert_not_called()
            self.assertEqual(len(ledger["sources"]),1)

    def test_freeze_selects_sorted_distinct_controls_and_does_not_replace(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); out=root/"corpus"; out.mkdir(); profile=root/"profile.json"
            profile.write_text("private unchanged profile")
            tasks=[]
            sources=list(pilot.PROBLEMS)+["z-control", "b-control", "a-repeat", "c-defect", "a-control"]
            for source in sources:
                task={"task_id": "youtube:"+source+":0-600", "kind": "youtube",
                      "source_id": source, "source_url": "https://youtube.com/watch?v="+source}
                tasks.append(task)
                meta,txt,srt=pilot.worker.result_paths(task,out)
                txt.write_text(" ".join("word"+str(i) for i in range(220)))
                if source=="a-repeat": txt.write_text((txt.read_text()+"\n")*2)
                srt.write_text("1\n00:00:00,000 --> 00:00:00,000\ninvalid\n" if source=="c-defect" else
                               "1\n00:00:00,000 --> 00:00:01,000\nvalid words\n")
                pilot.worker.atomic_json(meta,{"task_id":task["task_id"], "processed_audio_seconds":600,
                    "processing_status":"transcribed", "text_sha256":pilot.worker.sha256_file(txt),
                    "srt_sha256":pilot.worker.sha256_file(srt)})
            with mock.patch.object(pilot.worker,"load_manifest",return_value=tasks):
                frozen=pilot.freeze(root/"pilot",out,profile)
                selected=[r["task"]["source_id"] for r in frozen["sources"] if r["role"]=="control"]
                self.assertEqual(selected,["a-control","b-control"])
                self.assertEqual(pilot.freeze(root/"pilot",out,profile),frozen)
                self.assertEqual(profile.read_text(),"private unchanged profile")
                txt.write_text("changed production transcript")
                with self.assertRaisesRegex(RuntimeError,"Protected inputs changed"):
                    pilot.freeze(root/"pilot",out,profile)

    def test_deadline_cannot_restart_budget(self):
        with self.assertRaises(pilot.BudgetExpired):
            pilot.remaining({"deadline_unix": pilot.time.time()-1})


if __name__ == "__main__": unittest.main()
