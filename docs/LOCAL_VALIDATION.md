# Local transcription validation — 2026-10-23

**Completed:** setup, worker fixes, model comparison and the bounded live pilot. **The ten-hour batch has not been started.** Recommend **`small.en`**. All fixes and seven output trios were committed and published through the worker to `thefundament/harmon` on `main`.

## Implementation and checks

- Expanded only the frozen YouTube inventory into contiguous ten-minute windows: **1,134 tasks, initially 994 eligible**, 140 existing legacy clips skipped. All prior clip/VOD IDs were compared against the original manifest and preserved. Listed material totals 127.79 hours; overlap/unavailable sources mean this is not unique usable speech.
- Reject unwindowed long-form tasks and downloads more than two seconds short of their requested windows. `--min-successes` fails validation when insufficient new outputs are produced; ordinary unavailable-source handling remains unchanged.
- Setup and launch select models through `HARMON_MODEL`; the worker accepts `--model-name` and gives the existing `--model` path precedence. Outputs record the actual filename and SHA256, processed audio duration, transcription time and content hashes.
- Checkpointing continues locally after an upload failure. Graceful SIGINT/SIGTERM checkpoints completed results and cleans active child processes/media. New guards enforce the process/GPU/host memory limits.
- **24 offline tests passed**, including complete YouTube coverage, stable IDs, actual model metadata, short-window rejection, insufficient successes, interruption and a real local Git repository recovering after failed upload. Shell syntax and Git whitespace checks passed. Code commits `5f6a7c3` and `43ff1f1` preceded the publishing validation.

## Setup and memory

Pinned whisper.cpp **v1.9.5**, commit `d1be6fde11ac6e0407606b4e42fe72d34add8037`, built with Vulkan on the **Radeon 8060S**. Make uses two jobs. Upstream's shader helper independently launches up to 16 compiler processes; bootstrap applies a build-only throttle to one helper subprocess so it cannot bypass the two-job limit. The final throttled rebuild passed. An earlier build before that throttle also stayed inside the memory limit; the final measurements below use the throttled rebuild.

Every build/comparison/pilot used a transient systemd service with **6 GiB process memory maximum and no job swap**, sampled alongside GPU VRAM/GTT growth. A guard stops our workload above 8 GiB combined growth or below 2 GiB host available memory. Process/GPU summation can count some memory twice, and global GPU counters are approximate attribution in the presence of other workloads. All observed GPU baselines were stable. No guard fired and no job OOM occurred.

| Phase | Process peak, cgroup counter | Sampled GPU growth | Sampled combined peak | Minimum host available |
| --- | ---: | ---: | ---: | ---: |
| Throttled build | 3.40 GiB | 0 | 3.37 GiB | 7.90 GiB |
| Model comparison | 1.20 GiB | 0.95 GiB | 2.00 GiB | 10.12 GiB |
| Acceptance pilot | 0.34 GiB | 0.88 GiB | 1.22 GiB | 10.07 GiB |
| Resumption | 0.02 GiB | 0 | 0.02 GiB | 10.32 GiB |

The process peak comes from the kernel's `memory.peak`; combined peaks are simultaneous 250 ms samples, so they need not equal the sum of independent peaks. A 100 ms sampler also measured individual comparison phases. Logs and raw metrics are under `.harmon-work/logs/`; structured results are in [LOCAL_VALIDATION.json](LOCAL_VALIDATION.json).

The model files were verified against the upstream SHA1 and their SHA256 recorded:

| Model | Bytes | SHA256 |
| --- | ---: | --- |
| `small.en` | 487,614,201 | `c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d` |
| `large-v3-turbo-q5_0` | 574,041,195 | `394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2` |

Upstream documentation: [Vulkan build](https://github.com/ggml-org/whisper.cpp/tree/v1.9.5#vulkan-gpu-support), [models](https://github.com/ggml-org/whisper.cpp/blob/v1.9.5/models/README.md).

## Identical-audio model comparison

Three successful frozen-source download attempts supplied 197.941 seconds of representative audio. Each WAV was hashed, both models processed the exact same bytes sequentially with six threads and English recognition, and all media was deleted afterward. Times include CLI startup/model loading. This is a single pass with warming driver/file caches, not a statistical benchmark.

| Sample | Audio seconds | Small seconds | Turbo seconds |
| --- | ---: | ---: | ---: |
| Clip `AbrasiveLivelyGullPraiseIt-QaBGQlP1awQR5kDf` | 17.941 | 2.473 | 1.443 |
| VOD `1633410828`, comparison excerpt | 90 | 4.070 | 4.133 |
| YouTube `-4sAT9-sacM`, comparison excerpt | 90 | 3.144 | 5.260 |
| Total | 197.941 | 9.687 | 10.836 |

Small achieved **20.43× real time**, Turbo **18.27×**. Turbo took 1.12× Small's aggregate time and at most 1.67× on any sample. Both passed performance and memory criteria.

**Quality decision:** Turbo introduced three repetitions of an outro line in the YouTube sample where Small produced music markers. That is an obvious repetition defect, so Turbo fails the plan's output-regression criterion and Small is recommended. Other disagreements included `people` versus `Google` in the clip, `you` versus `I` and `chips` versus `chip` in the VOD, and several VOD lines omitted by Turbo. Small also produced repeated music markers, uncertain phrasing and mixed background dialogue. We have no verified reference transcript or speaker labels and cannot adjudicate every disagreement or report WER/measured accuracy. Raw comparison text/diffs remain local in `.harmon-work/comparison/results.json`.

## Live acceptance and recovery

The recommended Small model produced **five transcribed clips, one full 600-second VOD window and one full 600-second YouTube window**. Comparison used three source attempts and acceptance seven: **10 of the allowed 20 total attempts, zero unavailable sources and zero processing failures**. No further source discovery occurred.

| Source | Audio seconds | ASR seconds |
| --- | ---: | ---: |
| Clip `AbrasiveLivelyGullPraiseIt-QaBGQlP1awQR5kDf` | 17.941 | 0.912 |
| Clip `AbstemiousChillyGaurCoolCat-YrLySB--6n6UqRFw` | 21.803 | 0.597 |
| Clip `AbstemiousMoistGrassRaccAttack-Y0NLdtpz8xozAlBS` | 8.235 | 0.655 |
| Clip `AdorableCulturedDoveFloof-eV2k4pJMheM1xtZc` | 29.269 | 1.457 |
| Clip `AggressiveAggressiveScallionUncleNox-hUlgeP-rAWIkE2gN` | 26.005 | 1.381 |
| VOD `1633410828:0-600` | 600 | 23.478 |
| YouTube `-4sAT9-sacM:0-600` | 600 | 9.761 |

Validated seven TXT/SRT/metadata trios, all output/model hashes, positive audio/ASR durations, source IDs/windows, 124 sequential nonempty subtitle cues, monotonic positive timestamp intervals and unverified-speaker/training flags. No media remained in the worker or comparison temporary directories, and the output directory contains only TXT/SRT/metadata files.

**Remaining output defects:** two final clip cues extend past the audio endpoint: 0.765 seconds for `AbstemiousMoistGrassRaccAttack-Y0NLdtpz8xozAlBS`, and 0.035 seconds for `AggressiveAggressiveScallionUncleNox-hUlgeP-rAWIkE2gN`. They are retained as raw recognizer output and disclosed rather than silently adjusted. Other cue timing is usable relative to downloaded audio, with the existing keyframe-cut alignment limitation. Machine transcripts still require human review.

The pilot completed in **68.129 seconds**, including download, conversion, ASR, Git commits and pushes. It processed **1,303.253 audio seconds**, with **38.241 ASR seconds (34.08× real time)**. Completed-result commit `11e551a` was pushed successfully; earlier checkpoints also pushed and rebased over CI metadata commits. Re-running the same seven-item subset with `--no-push` attempted **zero** tasks, produced zero and recognized all seven as completed. Full-inventory status is **7 completed, 987 pending, 140 legacy clips skipped**. Retry journal has no failures from this pilot.

## Estimate and prepared command

For roughly **127.79 listed hours**, extrapolation gives 3.75 hours of ASR from the warmed pilot, 6.25 hours of ASR from the shorter comparison, and 6.68 hours end to end from the pilot including per-item publishing. Budget **6–10 hours of elapsed processing** at similar host load and source availability; this is a planning range, not a guarantee. Silence/music, overlapping listings, unavailable media and retry backoff can change it. Setup/model downloads are already finished.

**Exact logged, memory-limited ten-hour command — prepared, not executed:**

```bash
cd /home/user/src/harmon
HARMON_MODEL=small.en HARMON_LOG="$PWD/.harmon-work/logs/ten-hour.log" \
  bash scripts/run_limited.sh bash scripts/run_archive.sh --max-hours 10 --commit-every 10
```

It resumes the full frozen manifest and checkpoints results without rediscovery. The ten-hour cap stops new tasks; an in-progress task may finish afterward. See [LOCAL_AGENT_HANDOFF.md](LOCAL_AGENT_HANDOFF.md) for setup, validation and recovery commands.
