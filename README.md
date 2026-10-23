# harmon — simplez4ck public-media collection

A reproducible text-first dataset project, focused on **genuine attributable spoken/written words**, not synthetic imitations.

## Current collection path

1. `collector/harvest.py` discovers publicly listed clips, VODs and YouTube videos.
2. A GitHub-hosted Ubuntu runner retrieves accessible recordings with `yt-dlp`.
3. `ffmpeg` converts media to mono 16-kHz WAV (discarded after transcription).
4. `whisper.cpp` (English base model) emits TXT and timestamped SRT.
5. Results land under `corpus/unreviewed/` with source metadata. Discovery is under `corpus/discovery/`.
6. Nothing is marked as speaker-verified or cleared for model training until independently reviewed.

**Public repository**: transcripts and commit history are visible to everyone. Respect creator permissions and platform policies; no original audio/video is committed.

## Run controls

The historical acquisition pilot is `.github/workflows/harvest.yml`. It has **manual dispatch only**: no scheduled recurrence or push-triggered bulk media collection. The workflow is capped at 12 minutes. This keeps the code reproducible without assuming GitHub-hosted runners can be used for recurring media harvesting.

`.github/workflows/ci.yml` is the lightweight, automated QA workflow. It runs offline tests and updates `corpus/quality/review_queue.jsonl` and `corpus/quality/summary.json`; a score means **review priority**, not proof of speaker or transcription accuracy.

`.github/workflows/model-benchmark.yml` is a two-clip Base-vs-Small development test.

See [docs/OPERATING_MODEL.md](docs/OPERATING_MODEL.md) for the complete execution map, confidence tiers, speaker-verification plan, and GitHub usage limitations.

## Data status labels

- `download_ok`: media bytes actually retrieved.
- `transcription_ok`: Whisper created output; does *not* establish transcript accuracy.
- `speaker_verified: false`: may include other speakers, games, TTS, quoted dialogue.
- `training_permission_verified: false`: not yet cleared for fine-tuning.
- `word_count_nonplaceholder`: estimate excluding ASR markers, **not** verified speaker words.

Avoid compiling training JSONL automatically until speaker/rights checks exist. Early, noisy ASR is for curation, not direct LoRA fine-tuning.

## Provenance

Each `.txt` has a same-stem `.metadata.json` and usually `.srt` timestamp file. Source discovery files and `corpus/summary.json` preserve the extraction report.


## One-time local corpus completion

For a local Linux/WSL agent to process the frozen archive without discovery or human decisions, see [Local Agent Handoff](docs/LOCAL_AGENT_HANDOFF.md). The small.en speech model is downloaded automatically. Original video/WAV are temporary and excluded from Git. Local transcripts are indexed by lightweight CI after push.
