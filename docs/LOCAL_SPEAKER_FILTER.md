# Local speaker filtering and resumable sessions

The CPU speaker filter is integrated with the Turbo archive worker. Per the
user's preference, enrollment and validation run automatically; personal review
is not required. The recurring voice is labelled
`probable_channel_primary_speaker`. This is an inference about the channel's
primary voice, not verified identity or measured speaker accuracy.

## One-time automatic enrollment

Run setup and audio processing through the existing resource guard:

```bash
bash scripts/run_limited.sh bash scripts/bootstrap_speaker.sh
bash scripts/run_limited.sh .venv/bin/python collector/prepare_voice_references.py \
  --count 16 --seconds 10 --max-attempts 20
bash scripts/run_limited.sh .venv/bin/python collector/enroll_primary_speaker.py
```

Reference preparation uses the frozen inventory and checks source ownership
before downloading. It prefers separate YouTube/VOD recordings, retains only
short private WAV excerpts, and records attempts before starting them. Its
20-attempt limit includes failed/interrupted attempts across restarts. Growing
the requested sample count resumes the same set without redownloading earlier
samples. Candidate labels remain `UNREVIEWED`.

Automatic enrollment withholds complete recordings before fitting. WebRTC VAD
and a CPU Resemblyzer encoder extract short voice spans. A split learned from
pairwise similarities identifies voice cohorts. A recurring core must span at
least five separate recordings and dominate distinct competing cohorts; its
training examples include matching acoustic variants. Ambiguous examples are
excluded. Cohort separation sets the acceptance threshold. The frozen profile
must then recur on at least two withheld recordings, with at least three
withheld recordings examined and some other/uncertain speech excluded.

The held-out check measures recurrence, not false-positive rate against known
speaker labels. Machine cohort labels never claim human review. No threshold is
selected using held-out scores. `--heldout-sources private.json` can specify a
preselected JSON array of recording IDs; use fresh withheld recordings after
development changes informed by an earlier check.

The profile, reference WAVs, provenance and embedding diagnostics stay under
`.harmon-work/`, ignored by Git. Profiles have mode 0600. A failed enrollment
preserves an existing profile. The optional reviewed-reference path remains:

```bash
bash scripts/run_limited.sh .venv/bin/python collector/speaker_filter.py enroll \
  --references .harmon-work/reference_review/confirmed_labels.json \
  --out .harmon-work/speaker_profile.json
```

That path requires real `harmon`/`other` labels, source IDs and reviewer fields;
those labels are never generated from channel ownership.

## Bounded validation

Before production, freeze a small pilot manifest under `.harmon-work/` using
recordings excluded from all enrollment and withheld-reference recordings.
Keep the existing ASR comparison artifacts intact. For a seven-section pilot:

```bash
bash scripts/run_limited.sh bash scripts/run_archive.sh \
  --manifest .harmon-work/speaker_pilot.jsonl \
  --work-dir .harmon-work/speaker_pilot \
  --output .harmon-work/speaker_pilot/outputs \
  --speaker-filter --speaker-profile .harmon-work/speaker_profile.json \
  --max-items 7 --min-successes 7 --no-push
.venv/bin/python collector/report_gate_cost.py \
  --directory .harmon-work/speaker_pilot/outputs
```

Verify durable hashes, source/voice provenance, subtitle remapping, explicit
empty completions, temporary-media cleanup and resumption. Inspect timing
and resource metrics. These checks establish operational behavior and observed
cost, not measured identity or transcript accuracy. Gate time divided by filtered
ASR time is observational; it cannot establish net savings or a 10% overhead
without an identical-audio unfiltered comparison.

## Manually started two-hour sessions

After enrollment and bounded validation, start each session explicitly:

```bash
bash scripts/run_limited.sh bash scripts/run_archive.sh \
  --max-hours 2 --commit-every 25 \
  --speaker-filter \
  --speaker-profile .harmon-work/speaker_profile.json
```

Defaults are Turbo `large-v3-turbo-q5_0`, six ASR threads, two hours and a
25-output checkpoint interval. No scheduler, overnight batch or subsequent
session is launched automatically. The worker stops starting sections at the
time limit and lets the active section finish; checkpoint upload may also
extend elapsed time.

The same frozen inventory and `.harmon-work/state.json` are reused. Hash-valid
outputs made with the same profile are skipped. Deliberately changing the
profile makes previously filtered/unfiltered outputs eligible for reassessment;
legacy clips remain excluded unless `--include-existing` is supplied. A profile
change during a session stops work and checkpoints prior completions.

Every 25 completed outputs, including `no_speech` and `no_target_speech`, are
committed and pushed. Session end and handled interruption checkpoint the
remainder. Failed uploads retain files and commits; local commits continue,
and the next session's checkpoint retries publishing, even if no new work was
needed. `collector/local_worker.py publish` also retries preserved commits.

The source-owner check, retry journal, worker lock, disk floor, temporary-media
cleanup and memory guards remain active. Process memory is capped at 6 GiB,
job swap at zero, combined process/GPU growth at 8 GiB, and host available
memory must remain above 2 GiB. Git receives transcripts and metadata only.

## Attribution limits

Voice matching does not isolate overlapping voices. Mixed speech, game dialogue,
TTS, vocal effects and short interjections remain difficult. Accepted regions
are compacted before ASR; approximate source-relative SRT and the interval map
are retained. No matching voice produces empty TXT/SRT with `no_target_speech`
and skips Whisper. An encoder error fails the section instead of producing an
empty completion. `speaker_verified`, `training_permission_verified` and
`training_ready` remain false.
