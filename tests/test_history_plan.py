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
 def test_youtube_windows_cover_full_duration_and_keep_other_ids(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);d=root/"corpus"/"discovery";d.mkdir(parents=True)
   (d/"youtube.jsonl").write_text(json.dumps({"id":"abcdefghijk","duration":1250.5})+"\n")
   (d/"twitch_vods.jsonl").write_text(json.dumps({"id":"v99","duration":1250})+"\n")
   (d/"twitch_clips.jsonl").write_text(json.dumps({"url":"https://clips.twitch.tv/Test-1"})+"\n")
   rows,_=mod.build(root)
   yt=[r for r in rows if r["kind"]=="youtube"]
   self.assertEqual([(r["start_s"],r["end_s"]) for r in yt],[(0,600),(600,1200),(1200,1250.5)])
   self.assertEqual(yt[-1]["task_id"],"youtube:abcdefghijk:1200-1250.5")
   self.assertEqual([r["task_id"] for r in rows if r["kind"]=="vod"],
                    ["vod:99:0-600","vod:99:600-1200","vod:99:1200-1250.0"])
   self.assertIn("clip:Test-1",{r["task_id"] for r in rows})
 def test_prioritize_youtube_and_round_robin_time_windows(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);d=root/"corpus"/"discovery";d.mkdir(parents=True)
   (d/"youtube.jsonl").write_text("\n".join(json.dumps({"id":id_,"duration":1250}) for id_ in ["abcdefghijk","lmnopqrstuv"])+"\n")
   (d/"twitch_clips.jsonl").write_text(json.dumps({"url":"https://clips.twitch.tv/Test-1"})+"\n")
   rows,_=mod.build(root)
   self.assertEqual([r["kind"] for r in rows[:2]],["youtube","youtube"])
   self.assertEqual([r["start_s"] for r in rows if r["kind"]=="youtube"],[0,0,600,600,1200,1200])
   self.assertEqual(rows[-1]["task_id"],"clip:Test-1")
 def test_expanded_youtube_keeps_old_ids_and_excludes_playlist_foreigners(self):
  root=p.parents[1]
  expanded=root/"corpus"/"discovery"/"youtube_expanded.jsonl"
  if not expanded.exists():
   self.skipTest("Expanded inventory not checked in yet")
  candidates=[json.loads(x) for x in expanded.read_text().splitlines() if x.strip()]
  eligible={x["id"] for x in candidates if not x.get("candidate_only")}
  rejected={x["id"] for x in candidates if x.get("candidate_only")}
  self.assertGreaterEqual(len(eligible),450)
  rows,_=mod.build(root)
  actual={x["source_id"] for x in rows if x["kind"]=="youtube"}
  self.assertEqual(actual,eligible)
  self.assertFalse(actual & rejected)
  self.assertIn("X12hkzBWoTw",actual)
  self.assertEqual(len(rows),len({x["task_id"] for x in rows}))
 def test_frozen_inventory_coverage(self):
  root=p.parents[1]
  rows,_=mod.build(root)
  self.assertGreaterEqual(len(rows),1134)
  self.assertGreaterEqual(sum(r["state"]!="asr_completed_unreviewed" for r in rows),994)
  for source in {r["source_id"] for r in rows if r["kind"]=="youtube"}:
   windows=[r for r in rows if r["kind"]=="youtube" and r["source_id"]==source]
   self.assertEqual(windows[0]["start_s"],0)
   self.assertEqual(windows[-1]["end_s"],windows[0]["duration_s"])
   for i,r in enumerate(windows):
    self.assertLessEqual(r["end_s"]-r["start_s"],600)
    if i:self.assertEqual(windows[i-1]["end_s"],r["start_s"])
if __name__=="__main__":unittest.main()
