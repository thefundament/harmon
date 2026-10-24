from pathlib import Path
import tempfile
import unittest
from unittest import mock

from collector import production_publisher as p


class ProductionPublisherTests(unittest.TestCase):
    def test_destination_redacts_credentials_and_pins_full_remote(self):
        raw='https://private-token@github.com/thefundament/harmon.git'
        with mock.patch.object(p.worker,'git_cmd',return_value=raw):
            result=p.destination()
        self.assertEqual(result['destination'],'github.com/thefundament/harmon.git')
        self.assertNotIn('private-token',str(result))

    def test_authorization_preserves_original_runtime_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p.worker.atomic_json(root/'session.json',dict(started_unix=100,max_seconds=28800,remote_publishing_enabled=False))
            with mock.patch.object(p,'destination',return_value=dict(remote='origin',destination='approved',remote_sha256='pin')):
                p.authorize(root)
            record=p.pilot.read_json(root/'session.json')
            self.assertTrue(record['remote_publishing_enabled'])
            self.assertEqual((record['started_unix'],record['max_seconds']),(100,28800))

    def test_pending_stages_only_complete_unverified_session_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);out=root/'corpus/local_transcripts';out.mkdir(parents=True)
            task=dict(task_id='valid',kind='youtube')
            metadata=out/'valid.metadata.json'
            p.worker.atomic_json(metadata,dict(task_id='valid',pipeline=p.candidate.PIPELINE,**p.candidate.followup.FLAGS))
            p.worker.atomic_json(out/'foreign.metadata.json',dict(task_id='foreign',pipeline=p.candidate.PIPELINE,**p.candidate.followup.FLAGS))
            with mock.patch.object(p.worker,'REPO',root),mock.patch.object(p.worker,'OUTPUT',out), \
                 mock.patch.object(p.worker,'load_manifest',return_value=[task]), \
                 mock.patch.object(p.worker,'git_cmd',side_effect=['corpus/local_transcripts/valid.metadata.json\ncorpus/local_transcripts/foreign.metadata.json','']), \
                 mock.patch.object(p.worker,'completed',return_value=True):
                self.assertEqual(p.pending(root),[task])

    def test_unapproved_destination_never_stages_or_pushes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p.worker.atomic_json(root/'session.json',dict(remote_publishing_enabled=True,publication=dict(remote_sha256='old')))
            with mock.patch.object(p.worker,'WORK',root),mock.patch.object(p,'destination',return_value=dict(remote_sha256='changed')), \
                 mock.patch.object(p.worker,'git_cmd') as git,mock.patch.object(p.worker,'push_commits') as push:
                with self.assertRaisesRegex(RuntimeError,'unchanged configured remote'):
                    p.publish(root,force=True)
            git.assert_not_called();push.assert_not_called()

    def test_session_end_publication_retries_without_restarting_transcription(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p.worker.atomic_json(root/'session.json',dict(started_unix=p.time.time(),max_seconds=28800))
            p.worker.atomic_json(root/'summary.json',dict(completed=25))
            with mock.patch.object(p,'publish',side_effect=[RuntimeError('temporary network failure'),{}]) as publish, \
                 mock.patch.object(p.time,'sleep') as sleep:
                p.watch(root)
            self.assertEqual(publish.call_count,2)
            sleep.assert_called_once_with(60)
            self.assertTrue((root/'publishing-error.json').exists())


if __name__=='__main__':
    unittest.main()
