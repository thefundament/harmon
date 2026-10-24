import importlib.util
from array import array
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PATH=Path(__file__).resolve().parents[1]/"collector"/"speaker_filter.py"
spec=importlib.util.spec_from_file_location("speaker_filter",PATH)
speaker=importlib.util.module_from_spec(spec);spec.loader.exec_module(speaker)

class SpeakerFilterTests(unittest.TestCase):
    def test_unit_vectors_and_calibration(self):
        positives=[speaker.unit([1,0.001*i]) for i in range(6)]
        negatives=[speaker.unit([-.8,.1*i]) for i in range(5)]
        result=speaker.max_false_positive_threshold(positives,negatives)
        self.assertLess(result["threshold"],1)
        self.assertGreater(result["threshold"],result["negative_max"])
        self.assertEqual(result["training_false_accepts"],0)
    def test_encoder_failure_is_not_a_no_match_result(self):
        pcm=array("h",[500])*speaker.RATE*3
        class VAD:
            def is_speech(self,raw,sr):return True
        with self.assertRaisesRegex(RuntimeError,"encoding failed"):
            speaker.segments_with_match(pcm,[1.,0.],.9,
                 embedder=mock.Mock(side_effect=RuntimeError("encoder error")),vad=VAD())
    def test_wrong_embedding_dimension_cannot_produce_empty_completion(self):
        pcm=array("h",[500])*speaker.RATE*3
        class VAD:
            def is_speech(self,raw,sr):return True
        with self.assertRaises(speaker.UncalibratedProfile):
            speaker.segments_with_match(pcm,[1.,0.],.9,embedder=lambda p:[1.,0.,0.],vad=VAD())
    def test_overlapping_speakers_require_new_reference_samples(self):
        positives=[speaker.unit([1,.01*i]) for i in range(5)]
        negatives=[speaker.unit([1,.01*i]) for i in range(3)]
        with self.assertRaises(speaker.UncalibratedProfile):
            speaker.max_false_positive_threshold(positives,negatives)
    def test_missing_profile_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(speaker.UncalibratedProfile):
                speaker.load_profile(Path(d)/"not_created.json")
    def test_voice_activity_grouping_uses_fast_frames(self):
        pcm=array("h",[0]*speaker.FRAME_SAMPLES*220)
        for i in range(10,110):
            for j in range(i*speaker.FRAME_SAMPLES,(i+1)*speaker.FRAME_SAMPLES):
                pcm[j]=100
        for i in range(145,215):
            for j in range(i*speaker.FRAME_SAMPLES,(i+1)*speaker.FRAME_SAMPLES):
                pcm[j]=-100
        class FakeVAD:
            def is_speech(self,raw,rate):
                f=array("h");f.frombytes(raw)
                return any(x for x in f)
        spans=speaker.speech_spans(pcm,vad=FakeVAD())
        self.assertEqual(len(spans),2)
        self.assertEqual(spans[0],(10*480,110*480))
        self.assertEqual(spans[1],(145*480,215*480))
    def test_audio_compaction_preserves_mapping_and_does_not_include_other_voice(self):
        pcm=array("h",[123]*speaker.RATE*4+[-456]*speaker.RATE*3)
        audio,mapping=speaker.compact_accepted(pcm,[(0,2*speaker.RATE,.99)])
        self.assertTrue(all(x==123 for x in audio))
        self.assertEqual(len(mapping),1)
        self.assertEqual(mapping[0]["source_end_s"],2)
    def test_profile_enrollment_filters_real_pcm_but_no_real_inference(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);refs=[]
            for i in range(10):
                target=i<6
                path=root/("ref%d.wav"%i)
                val=500 if target else -500
                speaker.write_pcm(path,array("h",[val]*speaker.RATE*3))
                refs.append({"path":path.name,"label":"harmon" if target else "other",
                             "source_id":"source%d"%(i%3 if target else i%2),
                             "reviewer":"fixture"})
            manifest=root/"labels.json"
            manifest.write_text(json.dumps(refs))
            def embed(pcm):
                return [1.,0.] if pcm[0]>0 else [-1.,0.]
            profile=root/"out"/"profile.json"
            result=speaker.build_profile(manifest,profile,embedder=embed)
            self.assertEqual(result["positive"],6)
            self.assertTrue(profile.exists())
            centroid,threshold,_=speaker.load_profile(profile)
            self.assertAlmostEqual(centroid[0],1.)
            self.assertGreater(threshold,0)
            wav=root/"mixture.wav"
            pcm=array("h",[500]*speaker.RATE*3+[0]*speaker.RATE+[-500]*speaker.RATE*3)
            speaker.write_pcm(wav,pcm)
            class FakeVAD:
                def is_speech(self,raw,sr):
                    vals=array("h");vals.frombytes(raw)
                    return any(vals)
            filt=root/"accepted.wav"
            stats=speaker.filter_wav(wav,filt,profile,embedder=embed,vad=FakeVAD())
            self.assertEqual(stats["matched_regions"],1)
            self.assertEqual(stats["other_or_uncertain_regions"],1)
            accepted=speaker.read_pcm(filt)
            self.assertTrue(len(accepted)>2*speaker.RATE)
            self.assertTrue(all(x>0 for x in accepted))
            self.assertLess(len(accepted),len(pcm))
    def test_compacted_subtitles_map_back_to_original_source_clock(self):
        mapping=[{"compact_start_s":0.0,"compact_end_s":2.0,
                  "source_start_s":10.0,"source_end_s":12.0},
                 {"compact_start_s":2.3,"compact_end_s":5.3,
                  "source_start_s":80.0,"source_end_s":83.0}]
        srt="1\n00:00:00,500 --> 00:00:01,500\nFirst phrase\n\n2\n00:00:02,500 --> 00:00:04,000\nSecond phrase\n"
        mapped=speaker.remap_srt(srt,mapping)
        self.assertIn("00:00:10,500 --> 00:00:11,500",mapped)
        self.assertIn("00:01:20,200 --> 00:01:21,700",mapped)
    def test_collapsed_gap_cue_is_reported_without_inventing_word_timing(self):
        mapping=[{"compact_start_s":0.,"compact_end_s":2.,"source_start_s":10.,"source_end_s":12.}]
        srt="1\n00:00:02,100 --> 00:00:03,000\nUnsupported recognizer timing\n"
        mapped=speaker.remap_srt(srt,mapping)
        self.assertIn("Unsupported recognizer timing",mapped)
        self.assertEqual(speaker.subtitle_timing_issues(mapped,20)[0]["issues"],["nonpositive_duration"])
    def test_no_matched_voice_skips_compact_wav(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            positives=[speaker.unit([1.,0.]) for _ in range(6)]
            negatives=[speaker.unit([-1.,0.]) for _ in range(3)]
            stats=speaker.max_false_positive_threshold(positives,negatives)
            profile=root/"profile.json"
            profile.write_text(json.dumps({"schema":speaker.PROFILE_SCHEMA,"model":speaker.MODEL,
              "centroid":[1.,0.],"calibration":stats,
              "reference_provenance":[{"label":"harmon" if i<6 else "other",
                                       "reviewer":"fixture", "source_id":f"source{i}"}
                                      for i in range(9)]}))
            wav=root/"different.wav"
            speaker.write_pcm(wav,array("h",[-500]*speaker.RATE*3))
            class FakeVAD:
                def is_speech(self,raw,sr):return True
            out=root/"filtered.wav"
            summary=speaker.filter_wav(wav,out,profile,embedder=lambda p:[-1.,0.],vad=FakeVAD())
            self.assertTrue(summary["no_match"])
            self.assertFalse(out.exists())
if __name__=="__main__":unittest.main()
