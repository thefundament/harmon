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

`.github/workflows/harvest.yml` is manual-dispatch plus a single bootstrap push trigger. Its first run is bounded to 30 clips and one VOD sample, 12 minutes max, with no schedule. The existing `probe.yml` is a separate earlier pilot (not scheduled).

## Data status labels

- `download_ok`: media bytes actually retrieved.
- `transcription_ok`: Whisper created output; does *not* establish transcript accuracy.
- `speaker_verified: false`: may include other speakers, games, TTS, quoted dialogue.
- `training_permission_verified: false`: not yet cleared for fine-tuning.
- `word_count_nonplaceholder`: estimate excluding ASR markers, **not** verified speaker words.

Avoid compiling training JSONL automatically until speaker/rights checks exist. Early, noisy ASR is for curation, not direct LoRA fine-tuning.

## Provenance

Each `.txt` has a same-stem `.metadata.json` and usually `.srt` timestamp file. Source discovery files and `corpus/summary.json` preserve the extraction report.
