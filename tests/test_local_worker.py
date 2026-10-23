import importlib.util
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

FILE=pathlib.Path(__file__).resolve().parents[1]/"collector"/"local_worker.py"
spec=importlib.util.spec_from_file_location("harmon_local_worker",FILE)
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

TASK={"kind":"clip","task_id":"clip:Demonstration-1","source_id":"Demonstration-1",
      "source_url":"https://clips.twitch.tv/Demonstration-1","start_s":None,"end_s":None,
      "state":"pending"}
VOD={"kind":"vod","task_id":"vod:1234:600-1200","source_id":"1234",
     "source_url":"https://www.twitch.tv/videos/1234","start_s":600,"end_s":1200,
     "state":"legacy_sample_range_unknown"}

class LocalWorkerTests(unittest.TestCase):
 def test_manifest_rejects_bad_host_and_duplicates(self):
  with tempfile.TemporaryDirectory() as td:
   p=pathlib.Path(td)/"manifest.jsonl"
   p.write_text(json.dumps(TASK)+"\n"+json.dumps(TASK)+"\n")
   with self.assertRaisesRegex(RuntimeError,"duplicate"):
    worker.load_manifest(p)
   bad={**TASK,"source_url":"https://evil.example/Demonstration-1"}
   p.write_text(json.dumps(bad)+"\n")
   with self.assertRaisesRegex(RuntimeError,"disallowed"):
    worker.load_manifest(p)
 def test_retry_and_done(self):
  with tempfile.TemporaryDirectory() as td:
   out=pathlib.Path(td)
   self.assertEqual(worker.eligible(TASK,{},out),(True,"pending"))
   journal={TASK["task_id"]:{"attempts":3}}
   self.assertEqual(worker.eligible(TASK,journal,out),(False,"exhausted_retries"))
   meta,txt,srt=worker.result_paths(TASK,out)
   txt.write_text("This is a synthetic fixture.")
   srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nThis is a synthetic fixture.\n")
   worker.atomic_json(meta,{"task_id":TASK["task_id"],"processing_status":"transcribed",
                  "text_sha256":hashlib.sha256(txt.read_bytes()).hexdigest(),
                  "srt_sha256":hashlib.sha256(srt.read_bytes()).hexdigest()})
   self.assertTrue(worker.completed(TASK,out))
   self.assertEqual(worker.eligible(TASK,journal,out),(False,"completed"))
   txt.write_text("damaged")
   self.assertFalse(worker.completed(TASK,out))
 def test_legacy_clip_skipped_but_old_vod_window_not_skipped(self):
  with tempfile.TemporaryDirectory() as td:
   clip={**TASK,"state":"asr_completed_unreviewed"}
   out=pathlib.Path(td)
   self.assertEqual(worker.eligible(clip,{},out),(False,"existing_legacy_clip"))
   self.assertEqual(worker.eligible(VOD,{},out),(True,"pending"))
 def test_cleanup_only_our_temp_dirs(self):
  with tempfile.TemporaryDirectory() as td:
   d=pathlib.Path(td);tmp=d/"tmp";tmp.mkdir()
   abandon=tmp/(worker.task_key(TASK)+"-old")
   abandon.mkdir();(abandon/"source.mp4").write_bytes(b"partial media")
   unrelated=tmp/"my_personal_video";unrelated.mkdir()
   (unrelated/"important.txt").write_text("preserve")
   n=worker.clean_orphaned_media(d)
   self.assertEqual(n,1)
   self.assertFalse(abandon.exists())
   self.assertTrue((unrelated/"important.txt").exists())
 def test_transcription_success_publishes_no_media(self):
  with tempfile.TemporaryDirectory() as td:
   root=pathlib.Path(td);out=root/"corpus";work=root/"work"
   def fake(argv,timeout,cwd=None):
    head=str(argv[0])
    if head=="fake-ytdlp":
     template=argv[argv.index("-o")+1]
     pathlib.Path(template.replace("%(ext)s","mp4")).write_bytes(b"fake video")
    elif head=="fake-ffmpeg":
     pathlib.Path(argv[-1]).write_bytes(b"RIFF"+b"x"*200)
    elif head=="fake-whisper":
     prefix=pathlib.Path(argv[argv.index("-of")+1])
     pathlib.Path(str(prefix)+".txt").write_text("Synthetic test fixture says streets.")
     pathlib.Path(str(prefix)+".srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nSynthetic test fixture says streets.\n")
    else:raise AssertionError(head)
   tools={"yt_dlp":"fake-ytdlp","ffmpeg":"fake-ffmpeg","whisper":"fake-whisper","model":"fake-model"}
   with mock.patch.object(worker,"run_command",side_effect=fake):
    result=worker.process_task(VOD,tools,out,work,threads=3)
   self.assertEqual(result["processing_status"],"transcribed")
   self.assertFalse(result["speaker_verified"])
   self.assertFalse(result["training_ready"])
   self.assertTrue(worker.completed(VOD,out))
   self.assertFalse(list((work/"tmp").iterdir()),"Media/WAV must not remain")
   self.assertEqual(sorted(p.suffix for p in out.iterdir()),[".json",".srt",".txt"])
 def test_failures_also_delete_media_and_do_not_claim_success(self):
  with tempfile.TemporaryDirectory() as td:
   root=pathlib.Path(td);out=root/"corpus";work=root/"work"
   def fake(argv,timeout,cwd=None):
    if str(argv[0])=="fake-ytdlp":
     pathlib.Path(argv[argv.index("-o")+1].replace("%(ext)s","mp4")).write_bytes(b"fake")
    else:
     raise RuntimeError("synthetic failure")
   tools={"yt_dlp":"fake-ytdlp","ffmpeg":"fake-ffmpeg","whisper":"fake-whisper","model":"fake-model"}
   with mock.patch.object(worker,"run_command",side_effect=fake):
    with self.assertRaisesRegex(RuntimeError,"synthetic failure"):
     worker.process_task(TASK,tools,out,work)
   self.assertFalse(list((work/"tmp").iterdir()))
   self.assertFalse(worker.completed(TASK,out))
 def test_space_guard_reports_real_disk_space(self):
  with tempfile.TemporaryDirectory() as td:
   self.assertGreater(worker.free_disk_bytes(pathlib.Path(td)),0)
if __name__=="__main__":
 unittest.main()
