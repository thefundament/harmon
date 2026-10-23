# Automated speaker attribution (design, not yet implemented)

## Goal
A statistically high-confidence, machine-*inferred* match to the simplez4ck streamer's voice, without calling it human-verified. Keep uncertainty explicitly represented. An ASR transcript alone cannot identify who spoke it.

## Minimal feasible workflow

1. **Reference building**: collect clear speech from at least 5 independent recordings where the principal streamer's voice is independently attributable. Keep short 3–15s voiced regions and links to the source, not just text. One source alone is unsafe due to music, guests, voice clips or game dialogue. If no independently verified anchor exists, use the label `probable_channel_primary_speaker`, *never* `speaker_verified`.
2. **Voice activity detection**: segment natural speech, discard silence/background music and overlapping speakers, and require enough speech duration per candidate.
3. **Diarization**: cluster speakers within each recording; distinct cluster IDs by themselves do not identify a person. Optionally skip low-value clips shorter than a reliable voice sample.
4. **Voice embeddings**: embed reference and candidate speech using a locally executed, publicly available speaker-recognition model (for example an ECAPA-TDNN system). Keep model name/version and audio pre-processing in the record.
5. **Calibrated similarity**: compare candidates against the reference distribution and negative examples (other commentators, game audio and TTS). Choose accept/reject thresholds using a held-out validation set with a target false-positive rate. Do **not** treat cosine similarity as a directly calibrated probability or adopt an arbitrary fixed threshold.
6. **Dual-ASR review**: evaluate troublesome speech with `base.en` and `small.en`; model agreement is evidence that transcription is *stable*, not correctness. Preserve disagreement spans and avoid silently rewriting words.
7. **Promotion**: output `likely_primary_speaker`, `ambiguous`, `likely_other_speaker`, or `insufficient_speech`, plus speaker-model version, scores and review context. Mark none as `speaker_verified` without a credible independent anchor. `training_ready` remains false absent permission for training.

## Practical first step
Before adding an embeddings dependency, evaluate a few dozen diverse clips for single-speaker vs mixed-game-audio conditions and establish a reliable primary-voice anchor from separate streams. The current corpus did not retain source audio; those sections require re-fetching at attribution time. The project's normal GitHub Actions CI must remain lightweight; do not schedule high-volume audio inference on hosted runners.

## Quality criteria
- Use multiple days/sessions, not several clips of one recording.
- Require more than isolated one-word exclamations for voice matching.
- A strong match to the channel's *primary* voice is still an inference, not identity proof.
- Never treat the video uploader or owner as proof of every spoken word in that video.
- Promote disputed phrases into an audit queue, especially short homophone disagreements such as `streets`/`sheets`.
