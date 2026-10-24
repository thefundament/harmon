import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest import mock

PATH=Path(__file__).resolve().parents[1]/"collector"/"prepare_voice_references.py"
sys.path.insert(0,str(PATH.parent))
spec=importlib.util.spec_from_file_location("prepare_voice_references",PATH)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ReferencePreparationTests(unittest.TestCase):
    def test_prepares_only_short_candidate_wav_and_cleans_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);plan=root/"manifest.jsonl"
            rows=[{"kind":"youtube","source_id":"abcdefghijk","task_id":"youtube:abcdefghijk:0-600",
                 "start_s":0,"end_s":600,"source_url":"https://www.youtube.com/watch?v=abcdefghijk"},
                  {"kind":"youtube","source_id":"lmnopqrstuv","task_id":"youtube:lmnopqrstuv:0-600",
                 "start_s":0,"end_s":600,"source_url":"https://www.youtube.com/watch?v=lmnopqrstuv"}]
            plan.write_text("".join(json.dumps(x)+"\n" for x in rows))
            out=root/"refs";commands=[];checks=[]
            def owner(task,yt,cache):
                checks.append(task["source_id"]);return {"owner_verified":True}
            def fake(argv,timeout):
                commands.append(argv[0])
                if argv[0]=="fake-ytdlp":
                    p=Path(argv[argv.index("-o")+1].replace("%(ext)s","mp4"))
                    p.write_bytes(b"temporary synthetic media")
                else:
                    wav=Path(argv[-1])
                    with wave.open(str(wav),"wb") as f:
                        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
                        f.writeframes(b"\x00\x00"*16000*10)
            count=module.prepare(plan,out,max_samples=2,seconds=10,yt_dlp="fake-ytdlp",
                                 ffmpeg="fake-ffmpeg",checker=owner,runner=fake)
            self.assertEqual(count,2)
            self.assertEqual(checks,["abcdefghijk","lmnopqrstuv"])
            refs=json.loads((out/"unlabelled_references.json").read_text())
            self.assertTrue(all(r["label"]=="UNREVIEWED" and not r["reviewer"] for r in refs))
            self.assertEqual(len(list(out.glob("*.wav"))),2)
            self.assertEqual(list(out.glob("ref-media-*")),[])
            self.assertEqual(list(out.glob("*.mp4")),[])
            # Repeated preparation never redownloads already reviewed candidates.
            self.assertEqual(module.prepare(plan,out,max_samples=2,seconds=10,
              yt_dlp="fake-ytdlp",ffmpeg="fake-ffmpeg",checker=owner,runner=fake),2)
            self.assertEqual(len(checks),2)
    def test_manifest_selection_deduplicates_windows_by_recording(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"m.jsonl"
            lines=[{"kind":"youtube","source_id":"a","task_id":f"youtube:a:{i}-{i+600}"} for i in (0,600)]
            lines.append({"kind":"youtube","source_id":"b","task_id":"youtube:b:0-600"})
            p.write_text("".join(json.dumps(x)+"\n" for x in lines))
            self.assertEqual([t["source_id"] for t in module.choose_sources(p,3)],["a","b"])
    def test_failed_attempts_remain_bounded_across_restarts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);plan=root/"manifest.jsonl";out=root/"refs"
            plan.write_text("".join(json.dumps({"kind":"youtube","source_id":str(i),
                "task_id":f"youtube:{i}:0-600","source_url":f"https://www.youtube.com/watch?v={i}"})+"\n" for i in range(5)))
            checker=mock.Mock(side_effect=RuntimeError("unavailable"))
            for _ in range(2):
                self.assertEqual(module.prepare(plan,out,max_samples=3,max_attempts=2,checker=checker),0)
            self.assertEqual(checker.call_count,2)
            self.assertEqual(len(json.loads((out/"reference_attempts.json").read_text())),2)
    def test_existing_candidate_is_skipped_when_sample_count_grows(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);plan=root/"manifest.jsonl";out=root/"refs";out.mkdir()
            plan.write_text(json.dumps({"kind":"youtube","source_id":"existing","task_id":"youtube:existing:0-600"})+"\n")
            (out/"unlabelled_references.json").write_text(json.dumps([
                {"source_id":"youtube:existing","path":"existing.wav","label":"UNREVIEWED","reviewer":""}]))
            checker=mock.Mock()
            self.assertEqual(module.prepare(plan,out,max_samples=2,checker=checker),1)
            checker.assert_not_called()
if __name__=="__main__":
    unittest.main()
