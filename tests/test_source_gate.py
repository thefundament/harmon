import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

F=Path(__file__).resolve().parents[1]/"collector/source_gate.py"
spec=importlib.util.spec_from_file_location("source_gate",F)
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

YT={"kind":"youtube","source_id":"abcdefghijk",
    "source_url":"https://www.youtube.com/watch?v=abcdefghijk",
    "task_id":"youtube:abcdefghijk:0-600","title":"Harmon plays Elden Ring"}
VOD={"kind":"vod","source_id":"123","source_url":"https://www.twitch.tv/videos/123",
     "task_id":"vod:123:0-600","title":"A stream"}
CLIP={"kind":"clip","source_id":"Nice-123","source_url":"https://clips.twitch.tv/Nice-123",
      "task_id":"clip:Nice-123","title":"Reaction"}

class SourceGateTests(unittest.TestCase):
    def test_youtube_channel_owner_exact(self):
        ok=gate.assess(YT,{"id":"abcdefghijk","channel_id":gate.YOUTUBE_CHANNEL,
                           "title":"Harmon plays Elden Ring"})
        self.assertEqual(ok["proof"],"video_channel_id")
        for info,reason in [
            ({"id":"abcdefghijk","channel_id":"UCsomeone_else"},"youtube_foreign_channel"),
            ({"id":"abcdefghijk"},"youtube_owner_unknown"),
            ({"id":"someotherid","channel_id":gate.YOUTUBE_CHANNEL},"youtube_video_id_mismatch")]:
            with self.subTest(info=info),self.assertRaisesRegex(gate.SourceRejected,reason):
                gate.assess(YT,info)
    def test_playlist_membership_never_grants_ownership(self):
        # Channel playlist entries from other uploaders do not pass the owner gate.
        with self.assertRaisesRegex(gate.SourceRejected,"youtube_foreign_channel"):
            gate.assess({**YT,"playlist_id":"PLharmon"},{
                "id":"abcdefghijk","channel_id":"UCmusic_label","title":"Example music"})
    def test_twitch_clip_broadcaster_not_viewer_clipper(self):
        valid=gate.assess(CLIP,{"display_id":"Nice-123","channel":"SimpleZ4ck",
                                      "uploader":"SomeOtherPerson","uploader_id":"54321"})
        self.assertEqual(valid["proof"],"clip_broadcaster")
        with self.assertRaisesRegex(gate.SourceRejected,"twitch_foreign_broadcaster"):
            gate.assess(CLIP,{"display_id":"Nice-123","channel":"SomeOtherStreamer",
                             "uploader":"simplez4ck"})
    def test_vod_uploader_login(self):
        self.assertTrue(gate.assess(VOD,{"id":"v123","uploader_id":"SimpleZ4ck"})["owner_verified"])
        with self.assertRaisesRegex(gate.SourceRejected,"twitch_foreign_vod"):
            gate.assess(VOD,{"id":"v123","uploader_id":"somebody_else"})
    def test_obvious_unrelated_video(self):
        with self.assertRaisesRegex(gate.SourceRejected,"non_commentary_title"):
            gate.assess(YT,{"id":"abcdefghijk","channel_id":gate.YOUTUBE_CHANNEL,
                 "title":"Artist - Example (Official Music Video)"})
        self.assertIsNone(gate.disallowed_title("Harmon gaming and fighting a boss"))
    def test_cache_avoids_repeated_lookups_across_windows(self):
        with tempfile.TemporaryDirectory() as td:
            seen=[]
            def fetch(url,command):
                seen.append(url)
                return {"id":"abcdefghijk","channel_id":gate.YOUTUBE_CHANNEL,"title":"Gamestream"}
            a=gate.authorize(YT,"fake-yt-dlp",td,fetcher=fetch)
            b=gate.authorize({**YT,"task_id":"youtube:abcdefghijk:600-1200"},"fake-yt-dlp",td,fetcher=fetch)
            self.assertEqual(len(seen),1)
            self.assertEqual(a,b)
            self.assertFalse(any(x.suffix in (".wav",".mp4") for x in Path(td).iterdir()))
    def test_rejected_source_cached_before_download(self):
        with tempfile.TemporaryDirectory() as td:
            called=[]
            def fetch(url,command):
                called.append(url)
                return {"id":"abcdefghijk","channel_id":"UCforeign"}
            for _ in range(2):
                with self.assertRaises(gate.SourceRejected):
                    gate.authorize(YT,"fake",td,fetcher=fetch)
            self.assertEqual(len(called),1)
    def test_transient_fetch_failure_is_not_cached(self):
        with tempfile.TemporaryDirectory() as td:
            count=[]
            def offline(url,command):
                count.append(1);raise RuntimeError("network unavailable")
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError,"network unavailable"):
                    gate.authorize(YT,"fake",td,fetcher=offline)
            self.assertEqual(len(count),2)
            self.assertFalse(list(Path(td).glob("*.json")))
if __name__=="__main__":
    unittest.main()
