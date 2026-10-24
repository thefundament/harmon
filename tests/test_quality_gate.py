import importlib.util, json, pathlib, tempfile, unittest

FILE=pathlib.Path(__file__).resolve().parents[1]/"collector"/"quality_gate.py"
spec=importlib.util.spec_from_file_location("quality_gate",FILE)
quality=importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)

SRT="""1
00:00:00,000 --> 00:00:04,000
I think the streets are empty tonight.

2
00:00:04,100 --> 00:00:08,000
That is not what I expected!
"""
class TestQualityGate(unittest.TestCase):
    def test_timing_and_status(self):
        metadata={"source_key":"sample","source_url":"https://twitch.tv/example","kind":"clips","transcription_ok":True}
        r=quality.evaluate(metadata,"I think the streets are empty tonight. That is not what I expected!",SRT)
        self.assertEqual(r["timestamped_segments"],2)
        self.assertAlmostEqual(r["duration_s"],8.0)
        self.assertFalse(r["training_ready"])
        self.assertEqual(r["speaker_status"],"unknown")
        self.assertNotIn("no_recognized_speech",r["flags"])
    def test_silence_excluded(self):
        r=quality.evaluate({"source_key":"test","transcription_ok":True},"[BLANK_AUDIO]")
        self.assertEqual(r["disposition"],"exclude_pending_review")
        self.assertEqual(r["word_count"],0)
    def test_no_target_completion_is_not_an_asr_failure(self):
        r=quality.evaluate({"processing_status":"no_target_speech",
                            "speaker_status":"probable_channel_primary_speaker"},"")
        self.assertIn("no_target_speech",r["flags"])
        self.assertNotIn("asr_failed",r["flags"])
        self.assertNotIn("missing_timestamps",r["flags"])
        self.assertEqual(r["speaker_status"],"probable_channel_primary_speaker")
        self.assertFalse(r["speaker_verified"])
    def test_invalid_timing_is_exposed_in_the_quality_queue(self):
        r=quality.evaluate({"transcription_ok":True,"subtitle_timing_issues":[{"cue":1}]},
                           "Synthetic fixture words",SRT)
        self.assertIn("invalid_subtitle_timing",r["flags"])
    def test_dedup_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            directory=root/"corpus"/"unreviewed"
            directory.mkdir(parents=True)
            text="One two three four five six seven eight nine ten."
            for name in ("a","b"):
                (directory/(name+".txt")).write_text(text)
                (directory/(name+".srt")).write_text(SRT)
                (directory/(name+".metadata.json")).write_text(json.dumps({"source_key":name,"transcription_ok":True,"source_url":"https://clips.twitch.tv/"+name}))
            summary=quality.run(root)
            self.assertEqual(summary["exact_duplicate_text"],1)
            rows=[json.loads(x) for x in (root/"corpus"/"quality"/"review_queue.jsonl").read_text().splitlines()]
            self.assertEqual(sum(bool(x["duplicate_of"]) for x in rows),1)
            self.assertTrue(all(not x["training_ready"] for x in rows))
    def test_normalize_only_exact(self):
        self.assertEqual(quality.normalized_fingerprint("Street!"),quality.normalized_fingerprint("street"))
        self.assertNotEqual(quality.normalized_fingerprint("sheets"),quality.normalized_fingerprint("streets"))
if __name__=="__main__":
    unittest.main()
