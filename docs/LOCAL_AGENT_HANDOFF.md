# Harmon finite local transcription handoff

The inventory is frozen. Do not discover new sources or start recurring jobs. This session authorizes setup, a sequential model comparison and a pilot capped at 20 source attempts. **Leave the ten-hour batch unstarted.** Do not use hosted Actions for transcription.

`corpus/history/manifest.jsonl` contains 1,134 tasks, initially 994 eligible and 140 legacy clips skipped. VOD and YouTube tasks cover their complete listed durations in contiguous ten-minute windows (with shorter final windows). Existing clip and VOD task IDs are preserved. Unwindowed long-form tasks are rejected. The inventory lists 127.79 hours including clips; duplicates and unavailable material can reduce actual work.

## Setup on this host

Ubuntu Linux needs Python 3 with venv, Git, CMake, C++, Make, curl, FFmpeg, Vulkan headers/loader and `glslc`. The host Radeon 8060S supports Vulkan. Bootstrap pins whisper.cpp `v1.9.5`, builds with `GGML_VULKAN=ON` and at most two Make jobs, and caps the upstream shader compiler helper to one subprocess (otherwise it bypasses Make's limit). That build-only throttle does not change inference code. Set `HARMON_VULKAN=OFF` only when deliberately choosing CPU inference.

```bash
HARMON_MODEL=small.en bash scripts/run_limited.sh bash scripts/bootstrap_linux.sh
HARMON_MODEL=large-v3-turbo-q5_0 bash scripts/run_limited.sh bash scripts/bootstrap_linux.sh
.venv/bin/python collector/local_worker.py status
```

Models are verified against the upstream SHA1 and their SHA256 is printed. `HARMON_MODEL` selects the model for setup and launch; `--model-name` selects a downloaded model for the worker, and explicit `--model /path/to/weights.bin` takes precedence. The default remains `small.en` until the live comparison is reviewed. Quantized Turbo is preferred only if it fits the budget, runs faster than real time, takes at most twice Small's measured time on identical audio, and has no obvious output regression. There are no verified reference transcripts: report differences and defects, never WER or measured accuracy.

## Memory and logging

`run_limited.sh` creates a transient user systemd service with `MemoryMax=6G`, `MemorySwapMax=0`. Its guard samples every 250 ms and stops our workload if cgroup usage plus positive GPU VRAM/GTT growth exceeds 8 GiB, or host `MemAvailable` falls below 2 GiB. Summing both can count some pages twice; GPU counters are global and other GPU workloads can cause a conservative stop. The guard requires readable counters and checks that the cgroup limits are active. Logs and peak metrics go to `.harmon-work/logs/`; set an absolute `HARMON_LOG` for a chosen log filename. No swap is permitted for this job even when other host jobs use swap.

## Validation and batch commands

For a validation run, choose a frozen subset manifest under `.harmon-work/` and use `--min-successes`. `--max-items` counts source attempts, including failures. Success requirements count new durable TXT/SRT/metadata outputs, including explicit no-speech results; inspect the requested speech outputs separately.

```bash
HARMON_MODEL=small.en bash scripts/run_limited.sh bash scripts/run_archive.sh \
  --manifest .harmon-work/pilot.jsonl --max-items 20 --min-successes 7 --commit-every 1
```

Pilot acceptance requires five successful clips, one complete VOD window and one YouTube window, using the selected model. Verify hashes, timestamps, durations, cleanup, publishing and resumption. Do not exceed 20 total download attempts across comparison and acceptance. Account for unavailable sources explicitly.

**Prepared ten-hour command; do not execute as part of this session:**

```bash
HARMON_MODEL=small.en HARMON_LOG="$PWD/.harmon-work/logs/ten-hour.log" \
  bash scripts/run_limited.sh bash scripts/run_archive.sh --max-hours 10 --commit-every 10
```

Use the final validation report's model selection when running later. The wall-time cap prevents starting another task after ten hours; a task already running can finish and checkpoint afterward.

## Completion, recovery and publishing

One worker may run per checkout, enforced by an advisory lock. A hash-valid TXT/SRT/metadata trio is the durable completion marker and is skipped on resumption even without a journal. Metadata records the actual model filename and SHA256, processed audio duration, transcription time, requested source window and hashes. Audio shorter than a requested long-form window by more than two seconds fails instead of marking the window complete. Source cuts and SRT timestamps remain relative to downloaded sections; keyframe cuts can be imprecise.

Media and WAVs live only in `.harmon-work/tmp/`, are removed on success/failure/handled interruption, and orphaned task directories are reclaimed on resumption. No media/model binaries are committed. Downloads are capped at 384 MiB, free disk must stay above 2 GiB, and failures receive backoff and at most three attempts. `--no-push` leaves results as local files without automatic commits. Custom output paths require `--no-push`.

With publishing enabled, completed results are committed every `--commit-every` successes and on graceful SIGINT/SIGTERM. An upload failure disables further network attempts for that run but **local checkpoint commits continue**. The worker refuses to commit unrelated staged files. Commit tested code and handoff changes before validating publishing: dirty tracked files can prevent rebasing. Only `corpus/local_transcripts/` is staged by the worker.

```bash
.venv/bin/python collector/local_worker.py publish
```

`publish` retries upload of preserved local commits through the configured `origin` to `main`, fetching/rebasing first. The journal `.harmon-work/state.json` retains unavailable-source errors; ordinary batch source failures do not make the command fail, whereas insufficient `--min-successes` returns 1. A handled interruption returns 130 after checkpointing. CI updates only repository indexes.

Keep `speaker_verified=false`, `training_permission_verified=false`, `training_ready=false`. Channel identity is not speaker verification. Stop after the frozen inventory; no scheduler or new discovery.
