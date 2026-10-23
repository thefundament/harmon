# Historical one-pass scope

This is a **finite archive**, not a monitoring product: no scheduled recurrence is needed and none has been enabled.

## Discovery snapshot

Latest checked-in inventory (October 23, 2026): 355 distinct public Twitch clip entries including prior completed sources, 31 Twitch VOD entries, and 12 YouTube candidate entries. The 31 listed VOD durations add to approximately 111.21 hours, but overlap and repeats are not yet excluded.

The repository has 165 historical ASR outputs: 140 clips and 25 VOD samples. These are not 165 full-length videos transcribed. Earlier VOD samples did **not** preserve their precise start and end offsets, so don't mistakenly mark entire VODs (or specific time windows) as done.

## Finite planner

`python collector/history_plan.py` generates:
- `corpus/history/manifest.jsonl`: deterministic source/time-window work IDs (10-minute windows by default)
- `corpus/history/summary.json`: count of completed clips, pending clips and candidate VOD windows

This is planning/QA only, runs automatically as part of the lightweight CI workflow, and does not invoke yt-dlp or Whisper.

A future bounded executor must use these **source plus offset** work IDs as durable checkpoints. It must record each source's actual sampled time range, source file identity, recognition model and errors. Commit only completed work units; resume pending ones without starting over; treat overlaps between distinct VODs separately and avoid counting them as independent dialogue.

## Hosted GitHub runner boundary

No bulk job is scheduled or automatically triggered. A finite archival pass is operationally better than recurring harvesting, but its finite nature alone does not exempt it from GitHub's terms for hosted Actions:

https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features

These rules limit hosted Actions to software production, testing, deployment and publication and prohibit disproportionate server burden. The current plan therefore does **not** authorize proceeding through the 100+ hours of historical recordings on GitHub-hosted runners. GitHub should be asked to confirm the use case before extensive production processing.

## Transcription quality

The existing data is recognized by Whisper base.en. Reprocessing a source with small.en should be an explicit, versioned replacement *candidate*, not a silent overwrite. Speaker and copyright/training permissions are separate from transcript quality.
