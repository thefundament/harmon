import tempfile
from pathlib import Path
import unittest
from unittest import mock

from collector import quality_trial as trial


def task(source, start=0):
    return dict(kind="youtube", source_id=source, task_id=f"youtube:{source}:{start}-{start+600}",
                source_url="https://www.youtube.com/watch?v="+source, start_s=start, end_s=start+600)


class QualityTrialTests(unittest.TestCase):
    def test_selection_is_distinct_authorized_pending_and_skips_failed_recordings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = [task("pilot"), task("failed"), task("failed",600), task("good"),
                    task("good",600), task("unknown"), task("last")]
            for row in rows:
                trial.worker.atomic_json(root/(trial.pilot.source_key(row)+".json"), dict(state="allowed",
                    source_id=row["source_id"], source_kind="youtube", evidence=dict(owner_verified=True)))
            (root/(trial.pilot.source_key(task("unknown"))+".json")).unlink()
            selected = trial.select(rows, {rows[1]["task_id"]:dict(attempts=1)}, root/"empty", {"pilot"},root)
            self.assertEqual([t["source_id"] for t in selected], ["good","last"])

    def test_sixty_attempt_cap_persists_and_does_not_allow_retries(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"ledger.json"
            rows = [task(str(i)) for i in range(61)]
            frozen, ledger = dict(tasks=rows), dict(sources={})
            for row in rows[:60]:
                self.assertTrue(trial.reserve(ledger,row,path,frozen))
                ledger = trial.pilot.read_json(path)
                self.assertFalse(trial.reserve(ledger,row,path,frozen))
            self.assertFalse(trial.reserve(ledger,rows[60],path,frozen))
            with self.assertRaisesRegex(RuntimeError,"outside"):
                trial.reserve(ledger,task("replacement"),path,frozen)

    def test_process_keeps_original_input_offsets_annotations_and_partitioned_display(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wav = root/"original.wav"
            wav.write_bytes(b"original audio fixture")
            row = task("example",600)
            native = [dict(start=0,end=2,text="odd"),dict(start=1,end=3,text="wording")]
            decoded = dict(segments=native,raw_segments=native,text="odd\nwording",seconds=1,
                empty_chunks=0,decoder_seconds=3,deduplicated_boundaries=[])
            mapping = [dict(source_start_s=0,source_end_s=3)]
            stats = dict(speaker_profile_sha256="unchanged",time_mapping=mapping)
            with mock.patch.object(trial.speaker,"filter_wav",return_value=stats), \
                 mock.patch.object(trial.pilot,"decode",return_value=decoded) as decode:
                result=trial.process(row,wav,dict(duration=3),dict(model_sha256="model"),root,{},
                    dict(profile="profile",profile_sha256="unchanged"))
            self.assertEqual(decode.call_args.args[0],wav)
            self.assertTrue(decode.call_args.kwargs["chunked"])
            self.assertEqual([s["start"] for s in result["segments"]],[600,601])
            self.assertEqual(len(result["presentation"]["segments"]),3)
            self.assertTrue(all(result[key] is False for key in trial.followup.FLAGS))
            self.assertTrue(trial.followup.complete(root/"outputs"/trial.worker.task_key(row)/"result.json"))

    def test_cleanup_removes_source_audio_and_orphan_chunk_audio_only_in_trial(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/"audio/source").mkdir(parents=True)
            (root/"audio/source/source.webm").write_bytes(b"private")
            (root/"outputs/id/native").mkdir(parents=True)
            (root/"outputs/id/native/chunk.wav").write_bytes(b"private")
            (root/"outputs/id/native/chunk.json").write_text("diagnostics")
            trial.cleanup(root)
            self.assertFalse((root/"audio").exists())
            self.assertFalse((root/"outputs/id/native/chunk.wav").exists())
            self.assertTrue((root/"outputs/id/native/chunk.json").exists())

    def test_expired_resume_preserves_deadline_and_performs_no_acquisition(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            deadline=trial.time.time()-1
            trial.worker.atomic_json(root/"ledger.json",dict(started_unix=deadline-1800,deadline_unix=deadline,sources={}))
            frozen=dict(tasks=[task("fixed")],protected_sha256={})
            with mock.patch.object(trial.pilot,"require_guards"), \
                 mock.patch.object(trial,"freeze",return_value=frozen), \
                 mock.patch.object(trial.worker,"find_tools") as tools, \
                 mock.patch.object(trial,"acquire") as acquire, \
                 mock.patch.object(trial,"report",return_value={}):
                trial.run(root)
            tools.assert_not_called(); acquire.assert_not_called()
            ledger=trial.pilot.read_json(root/"ledger.json")
            self.assertEqual(ledger["deadline_unix"],deadline)
            self.assertIn("deadline",ledger["stop_reason"])

    def test_completed_resume_cannot_start_new_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            row=task("fixed")
            trial.worker.atomic_json(root/"ledger.json",dict(started_unix=trial.time.time(),deadline_unix=trial.time.time()+60,
                sources={row["task_id"]:dict(status="complete")}))
            frozen=dict(tasks=[row],protected_sha256={})
            with mock.patch.object(trial.pilot,"require_guards"), \
                 mock.patch.object(trial,"freeze",return_value=frozen), \
                 mock.patch.object(trial.worker,"find_tools") as tools, \
                 mock.patch.object(trial,"acquire") as acquire, \
                 mock.patch.object(trial,"report",return_value={}):
                trial.run(root)
            tools.assert_not_called(); acquire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
