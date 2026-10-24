# Two-hour session validation — 2026-10-23

Implemented manually started two-hour sessions with Turbo, six threads and
publishing every 25 completed outputs plus session end. Automatic speaker
enrollment and the bounded live pilot completed without personal reference
review. No production archive session, overnight run or scheduler was started.

[Structured measurements](SESSION_VALIDATION.json) retain the results. The
[setup and session command](LOCAL_SPEAKER_FILTER.md) describe ongoing use.

## Session controls and recovery

All **98 offline tests passed**, including the integrated speaker-filter tests.
Shell syntax, Python compilation and Git whitespace checks passed.

A simulated clock proves that the active section can finish after two hours and
that another section is not started at or after the deadline. Resume tests reuse
the frozen manifest and journal, skip hash-valid completions and retry damaged
outputs. Changing a profile stops the session and checkpoints prior completions;
starting with a new profile reassesses older filtered results.

Real temporary Git repositories prove upload checkpoints at **25, 50 and 52
completed outputs**, including explicit empty results and an interleaved failed
source attempt. A separate failed-upload run preserves all 27 results in local
commits, continues checkpointing, then publishes those commits at the next
session's final checkpoint without processing any source again. Interruption,
cleanup and unrelated staged-file protections remain covered by the suite.

## Automatic enrollment

Twenty distinct frozen-inventory YouTube recordings supplied short private
reference candidates within a persistent **20-source-attempt cap**. Sixteen
recordings were available for development; four fresh recordings were reserved
for the final withheld check. The selected primary cohort spans **11 development
recordings**. The frozen profile recurred on **two of four withheld recordings**
and excluded seven other/uncertain spans there.

The profile is labelled `probable_channel_primary_speaker`. All cohort labels
are explicitly machine inferred. No human review, independently labelled
speaker error rate or verified identity is claimed. The rejected clips in the
pilot can include false rejections. Reference audio, embeddings and the profile
remain under ignored `.harmon-work/`; they are not included in this report.

## Live Turbo pilot and resume

Seven sections used recordings outside all 20 enrollment/withheld candidate
recordings. All seven completed in **71.887 seconds**, with no failures or memory
guard stop. Results remain private in `.harmon-work/speaker_pilot/outputs/`.

| Sources | Audio seconds | Matched seconds | Result |
| --- | ---: | ---: | --- |
| Five Twitch clips | 82.432 | 10.440 | One transcript, four explicit no-match completions |
| One complete VOD section | 600.000 | 191.520 | Transcript |
| One complete YouTube section | 600.000 | 263.310 | Transcript |

Audited all seven TXT/SRT/metadata trios: content hashes, model/profile hashes,
source-owner evidence, false speaker/training verification flags, ordered source
interval maps and temporary-media cleanup. Empty no-match outputs skipped ASR.
A guarded resume took **1.769 seconds**, attempted **zero** sources and skipped
all seven completed results using the same profile and journal.

The pilot's kernel process-memory peak was **0.440 GiB**, sampled combined
process/GPU peak **1.391 GiB**, and minimum host available memory **9.149 GiB**.
The existing 6 GiB process cap, zero job swap, 8 GiB combined guard and 2 GiB host
floor remained active. Setup and enrollment also stayed within these guards.

The speaker gate took **3.28 seconds**, and filtered Turbo ASR **31.56 seconds**.
The gate/filtered-ASR ratio was **10.38%**, with **36.28%** of source audio retained.
This is an observational ratio, not net overhead against an identical-audio
unfiltered run and not evidence of an accuracy improvement.

## Retained timing defects

The 120 subtitle cues include **three zero-duration remapped cues**: one in the
VOD at 477.390 seconds and two in YouTube at 469.420 and 487.410 seconds. The
recognizer supplied timing outside retained speech intervals; clamping exposes
that defect. Text is preserved without inventing replacement word timings.
Other cue times are within the processed section and source maps are ordered.

New worker metadata records `subtitle_timing_issues`, and quality checks flag
`invalid_subtitle_timing`. The cost reporter also audits actual SRT files,
including these pilot results. These timing defects do not affect durable
completion, session stopping, publishing or resume. Speaker matching and ASR
word accuracy remain unverified.
