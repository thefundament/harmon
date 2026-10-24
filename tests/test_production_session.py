from pathlib import Path
import tempfile
import unittest
from unittest import mock

from collector import production_session as session


class ProductionSessionTests(unittest.TestCase):
    def test_prepare_freezes_pending_work_and_existing_artifacts_once(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            work=root/'work';work.mkdir()
            profile=work/'speaker_profile.json';profile.write_text('unchanged profile')
            out=root/'out';out.mkdir();(out/'existing.txt').write_text('preserved')
            task=dict(kind='youtube',source_id='fixture',task_id='fixture',source_url='https://www.youtube.com/watch?v=fixture',start_s=0,end_s=600)
            with mock.patch.object(session.worker,'WORK',work), \
                 mock.patch.object(session.worker,'OUTPUT',out), \
                 mock.patch.object(session.worker,'load_manifest',return_value=[task]), \
                 mock.patch.object(session.worker,'completed',return_value=False), \
                 mock.patch.object(session.worker,'eligible',return_value=(True,'pending')), \
                 mock.patch.object(session.candidate.trial.speaker,'load_profile',return_value=([],0,dict(_loaded_sha256=session.worker.sha256_file(profile)))), \
                 mock.patch.object(session.pilot,'WORK',root/'pilot'), \
                 mock.patch.object(session.followup,'WORK',root/'followup'), \
                 mock.patch.object(session.candidate.trial,'WORK',root/'trial'):
                session.prepare(root/'session')
                with self.assertRaises(FileExistsError):
                    session.prepare(root/'session')
            frozen=session.pilot.read_json(root/'session/session.json')
            self.assertEqual(frozen['max_seconds'],28800)
            self.assertEqual(frozen['pending_windows'],1)
            self.assertIn(str(out/'existing.txt'),frozen['preserved_sha256'])
            session.pilot.verify_preserved(frozen['preserved_sha256'])

    def test_detached_launcher_has_hard_stop_guards_and_no_restart(self):
        source=(session.worker.REPO/'scripts/start_production_8h.sh').read_text()
        for text in ('--no-block','RuntimeMaxSec=8h','Restart=no','MemoryMax=6G','MemorySwapMax=0',
                     'ExecStopPost=','collector/memory_guard.py','--max-hours 8','--commit-every 25','--no-push'):
            self.assertIn(text,source)
        self.assertNotIn('--wait',source)


if __name__=='__main__':
    unittest.main()
