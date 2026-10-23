import importlib.util
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
import wave
import subprocess
from unittest import mock

FILE=pathlib.Path(__file__).resolve().parents[1]/"collector"/"local_worker.py"
sys.path.insert(0,str(FILE.parent))
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
     with wave.open(str(argv[-1]), "wb") as audio:
      audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(1)
      audio.writeframes(b"\0"*1200)
    elif head=="fake-whisper":
     prefix=pathlib.Path(argv[argv.index("-of")+1])
     pathlib.Path(str(prefix)+".txt").write_text("Synthetic test fixture says streets.")
     pathlib.Path(str(prefix)+".srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nSynthetic test fixture says streets.\n")
    else:raise AssertionError(head)
   tools={"yt_dlp":"fake-ytdlp","ffmpeg":"fake-ffmpeg","whisper":"fake-whisper","model":"fake-model"}
   model=root/"ggml-large-v3-turbo-q5_0.bin";model.write_bytes(b"model weights")
   tools["model"]=str(model)
   with mock.patch.object(worker,"run_command",side_effect=fake), mock.patch.object(worker,"authorize",return_value={"owner_verified":True,"proof":"fixture_channel"}):
    result=worker.process_task(VOD,tools,out,work,threads=3)
   self.assertEqual(result["processing_status"],"transcribed")
   self.assertEqual(result["recognition_model"],"whisper.cpp ggml-large-v3-turbo-q5_0")
   self.assertEqual(result["model_sha256"],hashlib.sha256(b"model weights").hexdigest())
   self.assertEqual(result["processed_audio_seconds"],600)
   self.assertGreaterEqual(result["transcription_seconds"],0)
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
   with mock.patch.object(worker,"run_command",side_effect=fake), mock.patch.object(worker,"authorize",return_value={"owner_verified":True,"proof":"fixture_channel"}):
    with self.assertRaisesRegex(RuntimeError,"synthetic failure"):
     worker.process_task(TASK,tools,out,work)
   self.assertFalse(list((work/"tmp").iterdir()))
   self.assertFalse(worker.completed(TASK,out))
 def test_source_rejected_before_downloading_any_media(self):
  with tempfile.TemporaryDirectory() as td:
   root=pathlib.Path(td);out=root/"out";work=root/"work"
   with mock.patch.object(worker,"authorize",side_effect=worker.SourceRejected("youtube_foreign_channel")):
    with mock.patch.object(worker,"run_command") as downloader:
     with self.assertRaisesRegex(worker.SourceRejected,"youtube_foreign_channel"):
      worker.process_task(VOD,{"yt_dlp":"fake-yt-dlp"},out,work)
     downloader.assert_not_called()
   self.assertFalse(list((work/"tmp").iterdir()))
   self.assertFalse(list(out.iterdir()))
 def test_journal_blocks_rejected_source_permanently(self):
  with tempfile.TemporaryDirectory() as td:
   out=pathlib.Path(td)
   state={VOD["task_id"]:{"source_rejected":True,"reason":"twitch_foreign_vod"}}
   self.assertEqual(worker.eligible(VOD,state,out),(False,"source_rejected"))
 def test_space_guard_reports_real_disk_space(self):
  with tempfile.TemporaryDirectory() as td:
   self.assertGreater(worker.free_disk_bytes(pathlib.Path(td)),0)
 def test_rejects_unwindowed_long_form(self):
  with tempfile.TemporaryDirectory() as td:
   p=pathlib.Path(td)/"manifest.jsonl"
   for kind in ("vod","youtube"):
    p.write_text(json.dumps({**TASK,"kind":kind}))
    with self.assertRaisesRegex(RuntimeError,"complete time window"):
     worker.load_manifest(p)
 def run_fixture(self, root, outcomes, min_successes=0, push=False):
  tasks=[{**TASK,"task_id":f"clip:fixture-{i}"} for i in range(len(outcomes))]
  manifest=root/"manifest.jsonl";manifest.write_text("".join(json.dumps(t)+"\n" for t in tasks))
  args=worker.parser().parse_args(["run","--manifest",str(manifest),"--work-dir",str(root/"work"),
       "--output",str(root/"out"),"--min-successes",str(min_successes),"--commit-every","1","--min-free-gb","0"])
  args.push=push
  def process(task,*a):
   outcome=outcomes.pop(0)
   if isinstance(outcome,BaseException):raise outcome
   return {"recognized_words":7}
  with mock.patch.object(worker,"OUTPUT",root/"out"), mock.patch.object(worker,"find_tools",return_value={}), \
       mock.patch.object(worker,"process_task",side_effect=process):
   return worker.execute(args)
 def test_min_successes_and_ordinary_unavailable_batch(self):
  for required,expected in ((1,1),(0,0)):
   with tempfile.TemporaryDirectory() as td:
    self.assertEqual(self.run_fixture(pathlib.Path(td),[RuntimeError("source unavailable")],required),expected)
  with tempfile.TemporaryDirectory() as td:
   self.assertEqual(self.run_fixture(pathlib.Path(td),[True],1),0)
 def test_local_checkpoints_continue_after_upload_failure(self):
  with tempfile.TemporaryDirectory() as td, \
       mock.patch.object(worker,"checkpoint") as checkpoint, \
       mock.patch.object(worker,"push_commits",side_effect=RuntimeError("upload offline")) as push:
   self.assertEqual(self.run_fixture(pathlib.Path(td),[True,True,True],push=True),0)
   self.assertEqual(push.call_count,1)
   self.assertGreaterEqual(checkpoint.call_count,3)
 def test_interrupt_checkpoints_completed_results(self):
  with tempfile.TemporaryDirectory() as td, \
       mock.patch.object(worker,"checkpoint") as checkpoint, \
       mock.patch.object(worker,"push_commits",return_value="test upload"):
   self.assertEqual(self.run_fixture(pathlib.Path(td),[True,KeyboardInterrupt()],push=True),130)
   self.assertGreaterEqual(checkpoint.call_count,2)
 def test_explicit_model_path_overrides_name(self):
  with tempfile.TemporaryDirectory() as td:
   model=pathlib.Path(td)/"ggml-custom.bin";model.write_bytes(b"real weights fixture")
   tools=worker.find_tools(model, __file__, __file__, __file__, "small.en")
   self.assertEqual(tools["model"],str(model))
   self.assertEqual(tools["model_sha256"],hashlib.sha256(model.read_bytes()).hexdigest())
 def test_short_download_cannot_complete_a_full_window(self):
  with tempfile.TemporaryDirectory() as td:
   root=pathlib.Path(td)
   def fake(argv,timeout,cwd=None):
    if str(argv[0])=="fake-ytdlp":
     pathlib.Path(argv[argv.index("-o")+1].replace("%(ext)s","mp4")).write_bytes(b"partial")
    elif str(argv[0])=="fake-ffmpeg":
     with wave.open(str(argv[-1]),"wb") as audio:
      audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(1);audio.writeframes(b"\0"*20)
    else:self.fail("Incomplete audio must never reach transcription")
   tools={"yt_dlp":"fake-ytdlp","ffmpeg":"fake-ffmpeg"}
   with mock.patch.object(worker,"run_command",side_effect=fake), mock.patch.object(worker,"authorize",return_value={"owner_verified":True,"proof":"fixture_channel"}):
    with self.assertRaisesRegex(RuntimeError,"incomplete window"):
     worker.process_task(VOD,tools,root/"out",root/"work")
   self.assertFalse(worker.completed(VOD,root/"out"))
   self.assertFalse(list((root/"work/tmp").iterdir()))
 def test_real_git_checkpoint_recovers_after_upload_failure(self):
  with tempfile.TemporaryDirectory() as td:
   root=pathlib.Path(td);out=root/"corpus/local_transcripts";out.mkdir(parents=True)
   def git(*args):
    return subprocess.check_output(["git","-C",str(root),*args],text=True,stderr=subprocess.DEVNULL).strip()
   git("init","-b","main")
   git("config","user.name","test");git("config","user.email","test@example.invalid")
   (root/"README").write_text("fixture");git("add","README");git("commit","-m","initial")
   with mock.patch.object(worker,"REPO",root),mock.patch.object(worker,"OUTPUT",out):
    (out/"first.txt").write_text("first result")
    with self.assertRaisesRegex(RuntimeError,"fetch"):
     worker.publish() # no origin: the commit must still exist
    self.assertEqual(git("rev-list","--count","HEAD"),"2")
    (out/"second.txt").write_text("second result")
    worker.checkpoint()
    self.assertEqual(git("rev-list","--count","HEAD"),"3")
    self.assertEqual(git("status","--porcelain"),"")
if __name__=="__main__":
 unittest.main()
