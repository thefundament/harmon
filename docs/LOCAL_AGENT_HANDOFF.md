# Harmon historical corpus — deterministic local-agent handoff

**Mission:** Finish the prebuilt **one-time** historical worklist with no media discovery and no human-curated next-task selection. Use a local machine with Internet access. Do **not** use GitHub-hosted Actions for the bulk transcription; hosted Actions run only lightweight repository CI.

## Machine and model

- Recommended environment: **Ubuntu Linux**, including Ubuntu under WSL2 on Windows. Bash, Python 3, Git, FFmpeg, CMake, C++ build tools and curl required.
- Default recognizer: **Whisper \`ggml-small.en.bin\`** (about 466–488 MB; better accuracy than our original Base English model, reasonable on a modern CPU). The bootstrap fetches it automatically. No API key or cloud inference service.
- Use one worker instance per checkout. Default \`6\` CPU threads; can adjust \`HARMON_THREADS\` if host resources permit.
- Source inventory is already frozen in \`corpus/history/manifest.jsonl\`. **Do not run discovery** or replace it with a fresh dynamic channel listing.

## Exact first-run commands

\`\`\`bash
git clone git@github.com:thefundament/harmon.git
cd harmon
bash scripts/bootstrap_linux.sh
.venv/bin/python collector/local_worker.py status
bash scripts/run_archive.sh --max-items 5
\`\`\`

The five-item initial run is a bounded smoke test. If it passes, proceed:

\`\`\`bash
bash scripts/run_archive.sh --max-hours 10
\`\`\`

To run through the whole remaining worklist without the ten-hour limit:

\`\`\`bash
bash scripts/run_archive.sh
\`\`\`

Authentication: ensure the Git SSH key (or credential manager for HTTPS clone) already has push permission to \`thefundament/harmon\`. The worker commits **only** \`corpus/local_transcripts/\` and periodically pushes to \`main\`. If pushing fails, transcription continues, local commits are retained, and \`publish\` can retry later.

If missing Linux dependencies, Ubuntu/Debian setup is:

\`\`\`bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip cmake build-essential curl ffmpeg
bash scripts/bootstrap_linux.sh
\`\`\`

## Agent's instructions (no independent decisions required)

1. Pull the repo, confirm \`main\` and the frozen manifest are present.
2. Run bootstrap once (it is idempotent).
3. Run \`status\`, then \`bash scripts/run_archive.sh --max-items 5\` as acceptance.
4. Inspect that \`corpus/local_transcripts\` contains TXT, SRT, metadata JSON; **no MP4, WAV or partial downloads**. Check \`git log -1\` or the remote branch for successful upload.
5. Run the bounded ten-hour batch command repeatedly while eligible work remains, without redoing completed task IDs. The worker itself selects tasks in manifest order and moves past failed downloads.
6. Report final counts, hard failures, skipped existing clips, and whether Git commits reached remote. **Do not invent transcripts, identify speakers by upload channel, or mark any data training-ready.**
7. After the one-time source list is exhausted, stop. Do not install a recurring scheduler or poll for new videos.

## Resumption and fault handling

- A fully written TXT+SRT+metadata trio with matching hashes is the durable completion marker. Restarting the command skips these tasks even if local journal state was lost.
- Each task gets a unique ID: Twitch clip, YouTube video, or VOD recording **plus start/end seconds**.
- Each temporary download and PCM WAV lives in \`.harmon-work/tmp/<task-hash>-*/\`. **Video and audio are deleted** after transcription, after a failure, and whenever the temporary directory closes. The next run reclaims leftover per-task directories after an interrupted process. No media is committed.
- By default the worker refuses to begin a new task under **2 GiB free disk** and caps individual downloaded files to **384 MB**. Override the free-space floor only with a compelling reason: \`--min-free-gb 4\` is safer on a small drive.
- Each unavailable source is logged in \`.harmon-work/state.json\` with an attempt count and backoff. After three failures it is skipped until \`--retry-exhausted\` is deliberately used. Other sources continue unaffected.
- Completed tasks are not re-downloaded. Earlier short VOD samples are **not** counted as proof that the whole VOD was completed; their precise sample offsets were not recorded.
- Interrupted git pushes retain local files and commits. Retry \` .venv/bin/python collector/local_worker.py publish \` after restoring credentials/network.
- An active worker uses \`.harmon-work/worker.lock\`. If a process was killed and the lock remains, first confirm no worker is still running; only then remove this lock and rerun. The worker cleans orphaned media automatically after obtaining its lock.

## Transcription quality and provenance

The output under \`corpus/local_transcripts/\` includes:
- \`*.txt\` — machine-recognized text
- \`*.srt\` — timestamps relative to the downloaded segment, which may have imprecise cut boundaries
- \`*.metadata.json\` — source URL, exact requested window, recognized word count, SHA256 hashes, Whisper model, and explicit \`speaker_verified=false\`, \`training_ready=false\`

GitHub CI rebuilds \`corpus/combined/\` and \`corpus/quality/\` indexes automatically after new transcripts are pushed. Scores are **review priorities**, not probabilities that words are correct or belong to the primary streamer.

Copyright, platform rights, and permission to train or redistribute a likeness remain separate requirements.

## Checkpoint economics

Each 10 completed work items generates one Git commit/push by default. On interrupted batch, the final partial batch is pushed. This bounds data loss to the item currently running and avoids committing individual tiny files repeatedly.

### Stop conditions

Stop normally when no eligible work remains or the caller's \`--max-hours\`/\`--max-items\` limit is reached. On disk shortage, stop before another download; make space before retrying. On repeated failures, carry on through other sources and leave a final error report for the operator.
