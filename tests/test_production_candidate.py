import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

from collector import production_candidate as p


TASK=dict(kind="youtube",source_id="fixture",task_id="youtube:fixture:600-1200",
          source_url="https://www.youtube.com/watch?v=fixture",start_s=600,end_s=1200)


def fixture():
    segments=[dict(start=600,end=602,text="hey hey hey",speaker_verified=False),
              dict(start=601,end=603,text="unusual speech",speaker_verified=False)]
    return dict(task=TASK,profile_sha256="profile",model_sha256="model",text="hey hey hey\nunusual speech",
        segments=segments,presentation=p.followup.presentation(segments),seconds=1,pipeline_seconds=2,
        audio=dict(duration=3,pcm_sha256="pcm",wav_sha256="wav",owner_evidence=dict(owner_verified=True,proof="fixture")),
        metrics=dict(repeated_5gram_excess=0,repeated_5gram_fraction=0),**p.followup.FLAGS)


class ProductionCandidateTests(unittest.TestCase):
    def test_publication_preserves_repetition_and_uses_section_relative_presentation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            native=root/"private/result.json"
            native.parent.mkdir()
            p.worker.atomic_json(native.parent/"filter.json",dict(speaker_profile_sha256="profile",no_match=True))
            original=fixture()
            p.worker.atomic_json(native,original)
            digest=p.worker.sha256_file(native)
            row=p.publish_result(TASK,original,native,root/"out")
            self.assertTrue(p.worker.completed(TASK,root/"out"))
            _,txt,srt=p.worker.result_paths(TASK,root/"out")
            self.assertEqual(txt.read_text(),original["text"])
            cues=p.pilot.parse_srt(srt.read_text())
            self.assertEqual([(s['start'],s['end']) for s in cues],[(0,1),(1,2),(2,3)])
            self.assertIn("hey hey hey",cues[1]["text"])
            self.assertFalse(row["voice_filter"]["applied_to_decoder_input"])
            self.assertFalse(row["repetition_removed"])
            self.assertTrue(all(row[key] is False for key in p.followup.FLAGS))
            self.assertEqual(p.worker.sha256_file(native),digest)

    def test_completed_private_trial_reused_without_downloading_or_editing(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            profile=root/"profile.json";profile.write_text("unchanged profile")
            profile_hash=p.worker.sha256_file(profile)
            directory=root/"trial/outputs"/p.worker.task_key(TASK)
            directory.mkdir(parents=True)
            data=fixture();data['profile_sha256']=profile_hash
            p.worker.atomic_json(directory/"filter.json",dict(speaker_profile_sha256=profile_hash))
            p.followup.seal(directory/"result.json",data,[directory/"filter.json"])
            before=p.worker.sha256_file(directory/"result.json")
            with mock.patch.object(p.trial,"WORK",root/"trial"),mock.patch.object(p.worker,"run_command") as command:
                row=p.process_task(TASK,dict(model_sha256="model"),root/"out",root/"work",6,profile,profile_hash)
            command.assert_not_called()
            self.assertEqual(row['pipeline'],p.PIPELINE)
            self.assertEqual(before,p.worker.sha256_file(directory/"result.json"))

    def test_profile_change_fails_before_network(self):
        with tempfile.TemporaryDirectory() as td:
            profile=Path(td)/"profile";profile.write_text("changed")
            with mock.patch.object(p.worker,"run_command") as command:
                with self.assertRaisesRegex(RuntimeError,"profile changed"):
                    p.process_task(TASK,{},Path(td)/"out",Path(td)/"work",6,profile,"previous")
            command.assert_not_called()

    def test_eight_hour_cap_and_required_guard(self):
        with mock.patch('sys.argv',['candidate','run','--max-hours','9']):
            with self.assertRaisesRegex(RuntimeError,'eight hours'):
                p.main()
        with mock.patch('sys.argv',['candidate','run','--max-hours','8']), \
             mock.patch.object(p.pilot,'require_guards',side_effect=RuntimeError('guard required')), \
             mock.patch.object(p.worker,'execute') as execute:
            with self.assertRaisesRegex(RuntimeError,'guard required'):
                p.main()
            execute.assert_not_called()

    def test_incomplete_download_never_reaches_decoder_and_cleans_audio(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);profile=root/'profile';profile.write_text('profile')
            def command(argv,*_):
                if argv[0]=='yt':
                    Path(argv[argv.index('-o')+1].replace('%(ext)s','mp4')).write_bytes(b'media')
                else:
                    with wave.open(str(argv[-1]),'wb') as f:
                        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*16000)
            with mock.patch.object(p.worker,'authorize',return_value={}), \
                 mock.patch.object(p.worker,'run_command',side_effect=command), \
                 mock.patch.object(p.trial,'process') as decode:
                with self.assertRaisesRegex(RuntimeError,'Incomplete window'):
                    p.process_task(TASK,dict(model_sha256='model',yt_dlp='yt',ffmpeg='ffmpeg'),root/'out',root/'work',6,profile,p.worker.sha256_file(profile))
            decode.assert_not_called()
            self.assertFalse(list((root/'work/tmp').iterdir()))


if __name__=='__main__':
    unittest.main()
