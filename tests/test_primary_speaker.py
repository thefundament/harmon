from pathlib import Path
import tempfile
import unittest

from collector import enroll_primary_speaker as primary, speaker_filter as speaker


def example(source,vector):
    return {"source_id":source,"audio_sha256":"fixture", "start_s":0.,"end_s":3.,
            "embedding":speaker.unit(vector)}


class AutomaticPrimarySpeakerTests(unittest.TestCase):
    def examples(self):
        train=[example(f"youtube:primary-{i}",[1,.005*i,0]) for i in range(6)]
        train += [example("youtube:other-1",[0,1,0]),example("youtube:other-1",[0,1,.01]),
                  example("youtube:other-2",[0,0,1]),example("youtube:other-2",[0,.01,1])]
        hold=[example("youtube:withheld-1",[1,.01,0]),
              example("youtube:withheld-2",[1,.02,0]),example("youtube:withheld-3",[0,-1,0])]
        return train+hold,{e["source_id"] for e in hold}

    def test_inferred_profile_preserves_machine_provenance_and_passes_recurrence(self):
        examples,heldout=self.examples()
        profile=primary.calibrate(examples,heldout)
        self.assertEqual(profile["speaker"],"probable_channel_primary_speaker")
        self.assertTrue(profile["automatic_validation"]["passed"])
        self.assertFalse(profile["automatic_validation"]["identity_accuracy_measured"])
        refs=profile["reference_provenance"]
        self.assertTrue(all(r["label_method"]=="automatic_voice_cohort" and "reviewer" not in r for r in refs))
        self.assertTrue(heldout.isdisjoint({r["source_id"] for r in refs}))
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"profile.json"
            speaker.save_profile(path,profile)
            _,_,loaded=speaker.load_profile(path)
            self.assertEqual(loaded["speaker"],"probable_channel_primary_speaker")
            profile["automatic_validation"]["passed"]=False
            speaker.save_profile(path,profile)
            with self.assertRaises(speaker.UncalibratedProfile):speaker.load_profile(path)

    def test_single_recording_cannot_establish_recurring_primary_voice(self):
        examples,heldout=self.examples()
        for e in examples:
            if e["source_id"] not in heldout:e["source_id"]="youtube:one-recording"
        with self.assertRaises(speaker.UncalibratedProfile):primary.calibrate(examples,heldout)

    def test_withheld_failure_cannot_be_promoted(self):
        examples,heldout=self.examples()
        for e in examples:
            if e["source_id"] in heldout:e["embedding"]=[-1,0,0]
        with self.assertRaisesRegex(speaker.UncalibratedProfile,"withheld"):
            primary.calibrate(examples,heldout)

    def test_no_distinct_negative_cohort_refuses_calibration(self):
        vectors=[[1,0,0]]*12
        with self.assertRaises(speaker.UncalibratedProfile):primary.cohort_cutoff(vectors)

    def test_comparable_recurring_voices_cannot_be_named_primary(self):
        examples=[example(f"youtube:a-{i}",[1,.001*i,0]) for i in range(6)]
        examples += [example(f"youtube:b-{i}",[0,.001*i,1]) for i in range(6)]
        with self.assertRaisesRegex(speaker.UncalibratedProfile,"comparable"):
            primary.infer_cohorts(examples)

    def test_malformed_or_unreviewed_profile_cannot_claim_reviewed_enrollment(self):
        examples,heldout=self.examples()
        profile=primary.calibrate(examples,heldout)
        profile.pop("calibration_basis")
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"profile.json"
            speaker.save_profile(path,profile)
            with self.assertRaises(speaker.UncalibratedProfile):speaker.load_profile(path)


if __name__=="__main__":unittest.main()
