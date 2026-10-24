# Automated speaker attribution

The local worker now supports automatic voice-cohort enrollment and speaker
filtering. The user chose automatic enrollment without personal reference review.
The recurring voice is labelled `probable_channel_primary_speaker`; speaker
identity and training permission remain unverified.

See [LOCAL_SPEAKER_FILTER.md](LOCAL_SPEAKER_FILTER.md) for implemented enrollment,
withheld-recording recurrence checks, bounded pilots, limitations and two-hour
sessions. See [SESSION_VALIDATION.md](SESSION_VALIDATION.md) for measurements.

Automatic cohorts establish recurring acoustic similarity across independent
recordings. Their separation and held-out recurrence do not measure an identity
false-positive rate. A channel-owner match does not establish who spoke any
particular words. Overlapping voices, games, TTS and effects can cause mistakes.

An optional reviewed-reference enrollment path remains available for independently
labelled target and other voices. Without an independent anchor, never promote
an inferred primary voice to `speaker_verified`. Transcript accuracy and rights
for training require separate evidence.
