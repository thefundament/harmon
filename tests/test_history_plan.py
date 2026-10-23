import importlib.util,json,pathlib,tempfile,unittest
p=pathlib.Path(__file__).resolve().parents[1]/"collector"/"history_plan.py"
spec=importlib.util.spec_from_file_location("history_plan",p)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
class HistoryTests(unittest.TestCase):
 def test_identities(self):
  self.assertEqual(mod.identity("vod",{"id":"v123"})[0],"123")
  self.assertEqual(mod.identity("clip",{"url":"https://www.twitch.tv/simplez4ck/clip/Example-123"})[0],"Example-123")
 def test_resumable_vod_windows(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);d=root/"corpus"/"discovery";d.mkdir(parents=True)
   (d/"twitch_vods.jsonl").write_text(json.dumps({"id":"v99","duration":1250})+"\n")
   m=root/"corpus"/"unreviewed";m.mkdir()
   (m/"vod_99.metadata.json").write_text(json.dumps({"kind":"vods","source_key":"vod_99","url":"https://www.twitch.tv/videos/99","transcription_ok":True}))
   rows,s=mod.build(root)
   self.assertEqual(len(rows),3)
   self.assertEqual(rows[-1]["start_s"],1200)
   self.assertEqual(rows[-1]["end_s"],1250)
   self.assertTrue(all(r["state"]=="legacy_sample_range_unknown" for r in rows))
 def test_completed_clip(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);d=root/"corpus"/"discovery";d.mkdir(parents=True)
   (d/"twitch_clips.jsonl").write_text(json.dumps({"url":"https://www.twitch.tv/simplez4ck/clip/Test-1"})+"\n")
   m=root/"corpus"/"unreviewed";m.mkdir()
   (m/"clip_Test-1.metadata.json").write_text(json.dumps({"kind":"clips","source_key":"clip_Test-1","url":"https://www.twitch.tv/simplez4ck/clip/Test-1","transcription_ok":True}))
   rows,s=mod.build(root)
   self.assertEqual(rows[0]["state"],"asr_completed_unreviewed")
if __name__=="__main__":unittest.main()
