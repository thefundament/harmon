import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH=Path(__file__).resolve().parents[1]/"collector"/"youtube_snapshot.py"
spec=importlib.util.spec_from_file_location("youtube_snapshot",PATH)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HISTORY=Path(__file__).resolve().parents[1]/"collector"/"history_plan.py"
spec2=importlib.util.spec_from_file_location("history_plan",HISTORY)
history=importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(history)

class YoutubeSnapshotTests(unittest.TestCase):
    def test_no_network_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);d=root/"corpus/discovery";d.mkdir(parents=True)
            d.joinpath("youtube.jsonl").write_text(json.dumps({"id":"abcdefghijk","duration":1250,"title":"Legacy video"})+"\n")
            def fail(*a,**kw):
                return [],{"exit_code":1,"records":0,"error_tail":"offline"}
            data=module.create_inventory(root,probe=fail)
            self.assertEqual(data["eligible_creator_video_ids"],1)
            rows=module.read_jsonl(d/"youtube_expanded.jsonl")
            self.assertEqual(rows[0]["id"],"abcdefghijk")
            self.assertEqual(rows[0]["duration"],1250)
            self.assertEqual(len(history.build(root)[0]),3)
    def test_all_four_surfaces_and_playlist_ownership(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            own=module.CHANNEL
            def probe(url,**kwargs):
                response=[]
                if url.endswith("/videos"):
                    response=[{"id":"abcdefghijk","duration":1300,"channel_id":own,"title":"Full video"}]
                if url.endswith("/streams"):
                    response=[{"id":"lmnopqrstuv","duration":3000,"channel_id":own,"title":"Archived live"}]
                if url.endswith("/shorts"):
                    response=[{"id":"12345678901","duration":45,"channel_id":own,"title":"Short clip"}]
                if url.endswith("/playlists"):
                    response=[{"id":"PLtestplaylists12345"}]
                if "playlist?list=" in url:
                    response=[{"id":"ZXCVBNMasdf","duration":1800,"channel_id":own},
                              {"id":"zxcasdQWERT","duration":800,"channel_id":"UCsomebodyelse"},
                              {"id":"QQQQQQQQQQQ","duration":1200}]
                return response,{"exit_code":0,"records":len(response),"error_tail":""}
            report=module.create_inventory(root,probe=probe)
            self.assertEqual(report["eligible_creator_video_ids"],4)
            self.assertEqual(report["candidate_only"],2)
            records=module.read_jsonl(root/"corpus/discovery/youtube_expanded.jsonl")
            self.assertEqual(len(records),6)
            planned,_=history.build(root)
            bykind=[r for r in planned if r["kind"]=="youtube"]
            self.assertEqual(len(bykind),sum((1300+599)//600 for _ in [0])+
                             (3000+599)//600+1+(1800+599)//600)
            self.assertEqual({r["source_id"] for r in bykind},
                             {"abcdefghijk","lmnopqrstuv","12345678901","ZXCVBNMasdf"})
            before=[r["task_id"] for r in bykind]
            module.create_inventory(root,probe=probe)
            after=[r["task_id"] for r in history.build(root)[0] if r["kind"]=="youtube"]
            self.assertEqual(before,after)
    def test_wrong_channel_not_imported_from_direct_tab(self):
        result=module.normalize({"id":"12345678901","channel_id":"UCnotcreator",
                                  "duration":300},"streams")
        self.assertTrue(result["candidate_only"])
    def test_invalid_id_and_unreasonably_long_duration(self):
        self.assertIsNone(module.normalize({"id":"not valid!!!"},"videos"))
        self.assertIsNone(module.normalize({"id":"AAAAAAA"},"videos"))
        result=module.normalize({"id":"12345678901","duration":1000000},"videos")
        self.assertIsNone(result["duration"])
if __name__=="__main__":
    unittest.main()
