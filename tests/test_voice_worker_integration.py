import importlib.util
from array import array
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest import mock

F=Path(__file__).resolve().parents[1]/"collector/local_worker.py"
sys.path.insert(0,str(F.parent))
spec=importlib.util.spec_from_file_location("worker_with_speaker",F)
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)

TASK={"kind":"vod","source_id":"123","task_id":"vod:123:0-600",
      "source_url":"https://www.twitch.tv/videos/123",
      "start_s":0,"end_s":600,"state":"pending"}

class SpeakerWorkerIntegration(unittest.TestCase):
 def run_case(self,accepted):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   work=root/"work";out=root/"out"
   model=root/"weights.bin";model.write_bytes(b"model")
   profile=root/"ref.json";profile.write_text("{}")
   tools={"yt_dlp":"fake-ytdlp","ffmpeg":"fake-ffmpeg",
          "whisper":"fake-whisper","model":str(model),"model_sha256":hashlib.sha256(b"model").hexdigest()}
   calls=[]
   def cmd(argv,timeout,cwd=None):
    calls.append(argv[0])
    if argv[0]=="fake-ytdlp":
     template=argv[argv.index("-o")+1]
     Path(template.replace("%(ext)s","mp4")).write_bytes(b"fake")
    elif argv[0]=="fake-ffmpeg":
     path=Path(argv[-1])
     with wave.open(str(path),"wb") as wav:
      wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
      wav.writeframes(array("h",[500]*16000*600).tobytes())
    elif argv[0]=="fake-whisper":
     prefix=Path(argv[argv.index("-of")+1])
     Path(str(prefix)+".txt").write_text("A synthetic Harmon speech fixture")
     Path(str(prefix)+".srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nsynthetic test\n")
    else:raise AssertionError(argv)
   def apply_filter(wav,out_file,p):
    if accepted:
     out_file.write_bytes(wav.read_bytes())
    return {"no_match":not accepted,"matched_regions":int(accepted),
            "other_or_uncertain_regions":int(not accepted),
            "time_mapping":[],"matched_audio_seconds":600 if accepted else 0,
            "examined_audio_seconds":600}
   with (mock.patch.object(worker,"run_command",side_effect=cmd),
         mock.patch.object(worker,"authorize",return_value={"owner_verified":True,"proof":"test"}),
         mock.patch.object(worker,"filter_wav",side_effect=apply_filter)):
    row=worker.process_task(TASK,tools,out,work,threads=2,speaker_profile=profile)
   self.assertTrue(worker.completed(TASK,out))
   self.assertEqual("fake-whisper" in calls,accepted)
   self.assertEqual(row["processing_status"],"transcribed" if accepted else "no_target_speech")
   self.assertEqual(row["voice_filter"]["no_match"],not accepted)
   self.assertEqual(row["source_owner_verified"],True)
   self.assertFalse(row["speaker_verified"])
   self.assertFalse(row["training_ready"])
   self.assertFalse(list((work/"tmp").iterdir()))
   self.assertFalse(list(out.glob("*.mp4")))
 def test_refilter_when_profile_changes_but_keep_checkpoint_until_replaced(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   row={**TASK,"state":"pending"}
   meta,txt,srt=worker.result_paths(row,root)
   txt.write_text("Legacy text")
   srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nlegacy\n")
   import hashlib
   worker.atomic_json(meta,{"task_id":row["task_id"],"processing_status":"transcribed",
         "text_sha256":hashlib.sha256(txt.read_bytes()).hexdigest(),
         "srt_sha256":hashlib.sha256(srt.read_bytes()).hexdigest()})
   self.assertEqual(worker.eligible(row,{},root),(False,"completed"))
   self.assertEqual(worker.eligible(row,{},root,required_speaker_profile_hash="profile2"),
                    (True,"pending"))
   previous=json.loads(meta.read_text())
   previous["voice_filter"]={"speaker_profile_sha256":"profile2"}
   worker.atomic_json(meta,previous)
   self.assertEqual(worker.eligible(row,{},root,required_speaker_profile_hash="profile2"),
                    (False,"completed_voice_filtered"))
 def test_accepted_speech_uses_whisper_and_cleans_media(self):
  self.run_case(True)
 def test_nonmatching_speech_does_not_call_whisper_and_cleans_media(self):
  self.run_case(False)
if __name__=="__main__":unittest.main()
