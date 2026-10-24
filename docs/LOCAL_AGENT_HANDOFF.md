# Harmon finite local transcription handoff

**Latest user authorization:** Promote the original-audio chunked candidate as
an unverified first pass, retain raw repetition for later cleanup, and start one
unattended **eight-hour production session**. This supersedes the earlier
two-hour/no-overnight operating limit for this requested session. Use
`scripts/start_production_8h.sh`; see [production candidate](PRODUCTION_CANDIDATE.md).
There is no recurring schedule or automatic restart. Completed pilot/follow-up
artifacts and existing production completions remain preserved; historical
integrity snapshots predate this authorized production/code change.

The inventory is frozen. Do not discover new sources or start recurring jobs.
The current operating mode is manually started **two-hour sessions**, with Turbo,
six threads and publishing every **25 completed outputs** plus session end.
There is no scheduler or overnight batch. The user prefers automatic speaker
enrollment and validation without personal review; inferred voice matches retain
explicitly unverified identity labels. See [Local Speaker Filter](LOCAL_SPEAKER_FILTER.md)
for setup, validation and the standard session command, and
[SESSION_VALIDATION.md](SESSION_VALIDATION.md) for current verification results.

**Historical source expansion completed:** `corpus/history/manifest.jsonl` now contains **3,404 tasks**: 355 Twitch clips, 31 Twitch VOD entries, and **450 official-channel YouTube videos**. Of these, 140 existing clips were already transcribed, and 682 VOD windows are flagged because old short samples lack exact offsets; these windows are still eligible for full processing. The manifest lists **465.4 hours** of source durations, including **351.87 hours of YouTube**, before overlap, unavailability and non-speech. VOD and YouTube tasks use ten-minute sections (shorter final sections). **Immutable task IDs are preserved.** Newly prioritized YouTube sections are spread across distinct videos before later sections. Unwindowed long-form tasks remain disallowed. The 27 unattributed or duration-unknown playlist candidates are excluded.

The original setup/model-comparison pilots are historical. Preserve their
artifacts and frozen source inventory. Do not start an archive production session
as part of implementation/validation, or launch another session automatically.
Historical source discovery was already completed; do not repeat it locally.

The separately authorized additional-model comparison has its own **ten-source-attempt maximum** and includes 200 frozen reference recordings. Its [report and artifacts](stt_benchmark/REPORT.md) and `scripts/run_stt_benchmark.sh` are separate from archive processing. The benchmark retains ten sources from the original 127.79-hour inventory snapshot; its source ledger counts failed and interrupted attempts across restarts. Neither its launcher nor offline GitHub workflow starts the archive batch.

**Accepted ASR choice:** the user approved provisional use of `large-v3-turbo-q5_0`, now the default in setup and local launchers. Its aggregate reference WER was 5.91% versus Small's 7.05%, with 1.54× processing time. The user accepted that narrow exception to the original 1.50× ceiling. [decision.json](stt_benchmark/decision.json) records the acceptance separately from the unchanged benchmark measurements. Commentary accuracy remains unverified.

**Speaker integration:** the former speaker-gate branch is now integrated in this
checkout while retaining Turbo. Automatic enrollment uses recurring voice cohorts
and a withheld-recording recurrence check; it does not invent human labels or
certify speaker identity. The profile is private and reused across sessions.

## Setup on this host

Ubuntu Linux needs Python 3 with venv, Git, CMake, C++, Make, curl, FFmpeg, Vulkan headers/loader and `glslc`. The host Radeon 8060S supports Vulkan. Bootstrap pins whisper.cpp `v1.9.5`, builds with `GGML_VULKAN=ON` and at most two Make jobs, and caps the upstream shader compiler helper to one subprocess (otherwise it bypasses Make's limit). That build-only throttle does not change inference code. Set `HARMON_VULKAN=OFF` only when deliberately choosing CPU inference.

```bash
HARMON_MODEL=small.en bash scripts/run_limited.sh bash scripts/bootstrap_linux.sh
HARMON_MODEL=large-v3-turbo-q5_0 bash scripts/run_limited.sh bash scripts/bootstrap_linux.sh
.venv/bin/python collector/local_worker.py status
```

Models are verified against the upstream SHA1 and their SHA256 is printed. `HARMON_MODEL` selects the model for setup and launch; `--model-name` selects a downloaded model for the worker, and explicit `--model /path/to/weights.bin` takes precedence. The original live comparison recommended `small.en`; see [LOCAL_VALIDATION.md](LOCAL_VALIDATION.md) for its historical measurements and output defects. The later benchmark retained Small under its strict **1.5×** screen, after which the user accepted Turbo's 1.54× tradeoff. The corpus has no verified reference transcripts: report its differences and defects without WER or measured commentary accuracy. The separate benchmark measures WER only against the published LibriSpeech/FLEURS references.

## Memory and logging

`run_limited.sh` creates a transient user systemd service with `MemoryMax=6G`, `MemorySwapMax=0`. Its guard samples every 250 ms and stops our workload if cgroup usage plus positive GPU VRAM/GTT growth exceeds 8 GiB, or host `MemAvailable` falls below 2 GiB. Summing both can count some pages twice; GPU counters are global and other GPU workloads can cause a conservative stop. The guard requires readable counters and checks that the cgroup limits are active. Logs and peak metrics go to `.harmon-work/logs/`; set an absolute `HARMON_LOG` for a chosen log filename. No swap is permitted for this job even when other host jobs use swap.

## Validation and batch commands

For a validation run, choose a frozen subset manifest under `.harmon-work/` and use `--min-successes`. `--max-items` counts source attempts, including failures. Success requirements count new durable TXT/SRT/metadata outputs, including explicit no-speech/no-target-speech results; inspect the requested speech outputs separately.

```bash
HARMON_MODEL=large-v3-turbo-q5_0 bash scripts/run_limited.sh bash scripts/run_archive.sh \
  --manifest .harmon-work/pilot.jsonl --max-items 20 --min-successes 7 --commit-every 1
```

Pilot acceptance requires five successful clips, one complete VOD window and one YouTube window, using the selected model. Verify hashes, timestamps, durations, cleanup, publishing and resumption. Do not exceed 20 total download attempts across comparison and acceptance. Account for unavailable sources explicitly.

## Standard resumable session

After automatic enrollment and the bounded speaker pilot pass:

```bash
bash scripts/run_limited.sh bash scripts/run_archive.sh \
  --max-hours 2 --commit-every 25 \
  --speaker-filter \
  --speaker-profile .harmon-work/speaker_profile.json
```

Each session is started manually. The limit stops new sections; the current
section can finish and checkpoint afterward. No overnight command is prepared.

## Completion, recovery and publishing

One worker may run per checkout, enforced by an advisory lock. A hash-valid TXT/SRT/metadata trio is the durable completion marker and is skipped on resumption even without a journal. Metadata records the actual model filename and SHA256, processed audio duration, transcription time, requested source window and hashes. Audio shorter than a requested long-form window by more than two seconds fails instead of marking the window complete. Source cuts and SRT timestamps remain relative to downloaded sections; keyframe cuts can be imprecise.

Media and WAVs live only in `.harmon-work/tmp/`, are removed on success/failure/handled interruption, and orphaned task directories are reclaimed on resumption. No media/model binaries are committed. Downloads are capped at 384 MiB, free disk must stay above 2 GiB, and failures receive backoff and at most three attempts. `--no-push` leaves results as local files without automatic commits. Custom output paths require `--no-push`.

With publishing enabled, completed results are committed every `--commit-every` successes (25 by default), at session end and on graceful SIGINT/SIGTERM. An upload failure disables further network attempts for that run but **local checkpoint commits continue**. The worker refuses to commit unrelated staged files. Commit tested code and handoff changes before validating publishing: dirty tracked files can prevent rebasing. Only `corpus/local_transcripts/` is staged by the worker.

```bash
.venv/bin/python collector/local_worker.py publish
```

`publish` retries upload of preserved local commits through the configured `origin` to `main`, fetching/rebasing first. The journal `.harmon-work/state.json` retains unavailable-source errors; ordinary batch source failures do not make the command fail, whereas insufficient `--min-successes` returns 1. A handled interruption returns 130 after checkpointing. CI updates only repository indexes.

Keep `speaker_verified=false`, `training_permission_verified=false`, `training_ready=false`. Channel identity is not speaker verification. Stop after the frozen inventory; no scheduler or new discovery.
