import importlib.util,json,pathlib,tempfile,unittest
P=pathlib.Path(__file__).resolve().parents[1]/"collector"/"combined_index.py"
spec=importlib.util.spec_from_file_location("combined_index",P);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
class CombinedTest(unittest.TestCase):
 def test_legacy_and_local_both_accounted(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d)
   for name in ["unreviewed","local_transcripts"]:(root/"corpus"/name).mkdir(parents=True)
   (root/"corpus"/"unreviewed"/"a.metadata.json").write_text(json.dumps({"source_key":"old","kind":"clips","transcription_ok":True,"word_count_nonplaceholder":13}))
   (root/"corpus"/"local_transcripts"/"b.metadata.json").write_text(json.dumps({"task_id":"vod:2:0-600","kind":"vod","processing_status":"transcribed","recognized_words":43}))
   s=mod.run(root)
   self.assertEqual(s["legacy_asr_outputs"],1)
   self.assertEqual(s["local_window_asr_outputs"],1)
   self.assertEqual(s["total_asr_outputs"],2)
   self.assertEqual(s["total_recognized_words_not_deduplicated"],56)
   self.assertEqual(s["speaker_verified_words"],0)
if __name__=="__main__":unittest.main()
