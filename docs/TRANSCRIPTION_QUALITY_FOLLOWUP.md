# Private transcription follow-up

The follow-up preserves the completed six-source pilot, production transcripts,
speaker profile, and feedback. Production defaults stay unchanged and the
archive stays stopped. It never downloads models, recalibrates the profile,
publishes, or promotes the candidate.

Run `python3 -m collector.quality_followup render` to render all 18 saved pilot
outputs. The six `chunked_original` presentations are the candidate deliverable.
Overlapping cues become non-overlapping intervals displaying all active text,
including duplicates. TXT is copied verbatim. Sidecars retain every native cue,
its timestamp, and display-to-native indices. Zero/invalid-duration cues remain
in sidecars without fabricated duration. This is a presentation fix, not better
speech alignment; partitioning can display the same text in several intervals.

Run `bash scripts/run_quality_followup.sh` for the bounded investigation. The
existing process/GPU guards and shared archive lock apply. The runner freezes:

| Source | Processed-source passage | Purpose |
| --- | --- | --- |
| fKWZvJmKia8 | 14–44s | Shift the original 29s boss-fight boundary into the window |
| CC9gnJoko50 | 275–305s | Introduction and part-number correction |
| CC9gnJoko50 | 0–30s | Game dialogue |
| HwGaIhx_JW4 | 300–320s | User-confirmed contextual “Paul Rudd” example |
| Ar56zONY1-s | 20–40s | User-confirmed contextual “missions” example |

Only these four sources' original processed windows are reacquired, once each,
with reservations persisted before network I/O and downloader retries disabled.
Failed sources and hash mismatches have no replacements. The pilot saved a
WAV-file SHA-256, not a standalone PCM hash: exact WAV equality proves identical
PCM, and the follow-up additionally records PCM-only SHA-256. If exact equality
fails, the source is excluded from inference and comparison.

Each selected window is decoded independently by installed Turbo, English,
six threads, `--max-context 0`, and `-ojf` full native token diagnostics, then by
installed Parakeet on identical PCM. Native outputs retain all text and timing;
source-offset segments are separate. No text prompt or contextual correction is
supplied to either model. Whisper token probabilities are uncalibrated. Its CLI
does not export segment no-speech probabilities; no values are invented.

The unchanged speaker pass runs on the full processed window and records raw
30ms WebRTC speech decisions, eligible groups, accepted and rejected match
intervals/scores, and spans excluded before matching. Short voiced islands can
be excluded and eligible groups bridge small gaps. Each selected passage also
records 100ms RMS/peak energy. Speech detection, voice matching, and energy do
not establish speaker identity. Profile-match overlap is coverage, not identity
probability. The diagnostic accepted intervals are compared with the pilot.

Results stay private under ignored `.harmon-work/quality-followup/`: frozen
manifest and input hashes, four-attempt ledger, rendered subtitles, native model
checkpoints, diagnostics, `annotations.json`, `summary.json`, `validation.json`,
and one compact `REPORT.md`. `python3 -m collector.quality_followup report`
rebuilds the report and performs an offline integrity audit. The report retains
the user-confirmed contextual examples even when new models disagree. Proposed
readings remain annotations, with verification/training flags false. No further
human listening or text review is needed to complete the follow-up.

The first `run` fixes one 30-minute wall-clock deadline across resumptions.
Re-running the launcher verifies and reuses completed outputs. Attempted sources
are never downloaded again. After graceful cleanup, incomplete work without
audio is unavailable. A hard kill can leave audio; a restart verifies its hash
before reuse. Temporary audio is deleted after every source and at exit,
including signal/deadline failures. A completely finished resume needs no model
initialization or new acquisition.

Disagreement is symmetric normalized word-token Levenshtein distance, divided
by the longer token count (zero for two empty transcripts). It is not reference
WER. Repetition uses the pilot's normalized five-grams appearing at least three
times; legitimate repetitions remain intact. Agreement and fluent alternatives
are supporting evidence, not ground truth or grounds for automatic correction.

Verification: `python3 -m unittest tests.test_quality_followup` and
`python3 -m unittest discover -s tests`. The report audits subtitle coverage,
text/native preservation, offsets, hashes, false verification flags, and audio
cleanup. The suite covers disagreement, attempt caps, resumptions, mismatch
handling, and failure cleanup without network or ML dependencies.
