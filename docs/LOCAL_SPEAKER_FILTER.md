# Harmon — unattended local target-speaker filtering

## What is implemented

An **optional, local, no-LLM** audio prefilter between the existing yt-dlp
source-owner check and the independently selected Whisper model:

1. yt-dlp metadata must identify Harmon's **actual channel/broadcaster**.
2. Download the existing frozen source+time window to a *temporary* media file.
3. FFmpeg produces an ephemeral mono 16-kHz WAV (same as the existing worker).
4. WebRTC VAD detects speech in 30ms frames. Silences and <1.8s voiced islands
   do not reach the speaker encoder.
5. A cached CPU Resemblyzer encoder compares 1.8–12s voice spans to a
   privately enrolled profile calibrated using known target and other speakers.
6. Speech above the conservative threshold is compacted with 300ms separators;
   dissimilar or ambiguous speech is discarded **before Whisper**.
7. Whisper uses **the model selected by the other local agent**; this work does
   not install, replace, or choose a transcription model.
8. TXT, source-relative approximate SRT, and a compact-to-source interval map
   are committed as the worker already does. **Original media, WAV and compacted
   WAV are deleted** on success/failure, with crash cleanup.
9. If no likely target speech remains, the worker commits an empty TXT/SRT and
   status = `no_target_speech`, **never running Whisper for that window**.

This is *not* speaker identity proof or a calibrated posterior probability.
Mixed voices, voice acting, vocal effects and overlaps can still be wrong.
Use conservative references/thresholds and measure errors on an independent
holdout. Reference files and embeddings are **private local biometric material**
and must not go in this public repository.

## One-time setup (no LLM in the loop)

Keep the current local agent's model-optimization branch and artifacts intact.
This work is on a separate feature branch until integration is approved.

First install the existing transcription/bootstrap environment, then the
optional CPU-only speaker model:

```bash
bash scripts/bootstrap_speaker.sh
```

The bootstrap installs `resemblyzer==0.1.4` and its dependencies, including
the speaker encoder and WebRTC VAD. It does not alter the Whisper model or
the Vulkan build. PyTorch CPU binaries can be substantial; install once.

Prepare a small set of *candidate* speech excerpts from the already-frozen
worklist (owner-verified, and no new discovery):

```bash
.venv/bin/python collector/prepare_voice_references.py --count 16 --seconds 10
```

Temporary original media is removed immediately. The review WAVs are in
`.harmon-work/reference_review/` (ignored by Git); these short clips temporarily
remain until they are labeled.

The output `unlabelled_references.json` is **not an enrollment manifest**.
Listen to/inspect these samples once. Mark **at least five unambiguous Harmon
speaking excerpts spanning three separate recordings**, plus **at least three
examples of *other* voices from at least two sources**. If you don't have
enough genuine negative samples, collect and review more reference candidates.

For each chosen WAV, create a JSON array entry:

```json
[
  {"path": "confirmed_harmon_01.wav", "source_id": "youtube:recording_1",
   "label": "harmon", "reviewer": "human-reviewed"},
  {"path": "confirmed_other_01.wav", "source_id": "youtube:recording_9",
   "label": "other", "reviewer": "human-reviewed"}
]
```

Use *real existing filenames*; the example above is schematic. Do not assign
positive labels solely because the recording was uploaded to the channel.
Use at least eight reviewed references; this is the one-time human step,
not an LLM call for each of 3,404 work units.

Save the manifest as
`.harmon-work/reference_review/confirmed_labels.json`, then:

```bash
.venv/bin/python collector/speaker_filter.py enroll \
  --references .harmon-work/reference_review/confirmed_labels.json \
  --out .harmon-work/speaker_profile.json
```

Enrollment fails closed if reference distributions overlap or evidence is
insufficient. Adjust *reference examples* rather than guessing a global
similarity threshold. The profile is private (ignored by Git).

## Bound pilot before any overnight run

Use a small test manifest of video windows **not used during enrollment**.
Do not overwrite local model-comparison artifacts.

```bash
HARMON_MODEL=MODEL_CHOSEN_BY_AGENT bash scripts/run_limited.sh \
  bash scripts/run_archive.sh \
  --manifest .harmon-work/speaker_pilot.jsonl \
  --speaker-filter --speaker-profile .harmon-work/speaker_profile.json \
  --max-items 10 --no-push
```

`MODEL_CHOSEN_BY_AGENT` is a placeholder: replace with the already
benchmarked local model name. A speaker filter does *not* invoke any text
generation model or hosted API.

Check the pilot manually once to validate target voice retention, false
acceptances, timing map and elapsed runtime. A regular full archive run now
**fails before any downloading** unless `--speaker-filter` is enabled with a
calibrated profile. Bounded model-quality pilots can stay unfiltered.
Do not use `--allow-unfiltered` for production.

### Speed accountability

```bash
.venv/bin/python collector/report_gate_cost.py
```

The report measures speaker-filter walltime, actual Whisper walltime on accepted
speech, rejected-region counts and fraction of audio submitted to Whisper.
Compare the **same source windows** against the established unfiltered model
benchmark. The earlier 1.5× transcription-model budget is **already spoken for**.
Aim for minimal net added time; the proposed ~10% filtering target remains
**unverified** until real local-host benchmark results exist. If much slower,
tune model invocation and selection before a bulk run.

### Unattended use when validation passes

The existing `collector/local_worker.py` retains all retry, checkpoint,
resume, media cleanup, disk-space and Git push behavior. The voice encoder is
cached **once per Python process** to amortize loading. The agent should run
the same finite worklist, now with `--speaker-filter` and the existing
transcription-model selection; no LLM is needed for routine processing.
The model does not run on GitHub-hosted Actions.

After enrolling and validating, remove labeled reference WAVs if no longer
needed; keep the reviewed-source provenance manifest and private embedding
profile backed up locally. Never commit the private profile or any audio.

## Safety limitations

- Speaker classification is based on locally enrolled reference speech. It
  is not voice isolation or sound-source separation: overlapping speech
  cannot reliably be assigned without more advanced processing.
- Voice-recognition false positives/negatives can occur. A recording on
  Harmon's account is **not** per-voice verification.
- Short single-word speech is conservatively rejected.
- The SRT remapping is approximate when ASR spans removed audio gaps; precise
  compact-to-source regions are stored in metadata.
- The model score is a similarity, **not** a probabilistic identity claim.
- Model evaluation and rights/permission for training remain separate.
