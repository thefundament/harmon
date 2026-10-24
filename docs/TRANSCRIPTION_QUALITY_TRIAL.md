# Private 30-minute candidate trial

The user authorized a private trial after the completed quality follow-up.
Run `bash scripts/run_quality_trial.sh`; repeat the same command only to resume
the same ledger. This does not run the archive worker or publish results.

The trial freezes up to 60 pending windows, one per recording, in the existing
inventory order. It selects only recordings with saved source-ownership
authorization, skips previously failed recordings and all six pilot sources,
and never discovers sources or substitutes failures. The available authorized
pending recordings for this trial are YouTube videos; the results do not test
Twitch acquisition. Existing completed production outputs are skipped.

The installed Turbo model decodes original mono 16kHz PCM in independent
30-second windows, with a one-second overlap, English, six threads, and
`--max-context 0`. The unchanged speaker profile supplies overlap annotations
after decoding; all voices remain in the transcript. A temporary compacted
speaker-filter WAV is deleted before original-audio decoding. The presentation
renderer partitions cue overlaps and retains every active text, with original
timestamps and suspicious text saved separately. No speech-alignment improvement
is claimed. Contextual corrections are not supplied as ASR prompts.

Private files live under ignored `.harmon-work/quality-trial-30m/`: frozen
manifest and preservation hashes, persistent ledger, per-source PCM/WAV hashes,
native chunk checkpoints, original text, native and presentation subtitles,
profile mappings, memory guard metrics, `summary.json`, and `REPORT.md`.

The first run fixes one 30-minute wall-clock deadline across resumptions and
uses the existing process/GPU memory guards and archive lock. The 60-attempt
maximum includes failures and interruptions, and reservations are persisted
before network I/O. Downloader retries are disabled. A consumed source is not
downloaded again; complete results remain durable. A time limit can leave one
incomplete source with native chunk diagnostics but no complete transcript.
Source audio is deleted after every attempt and on exit; orphaned chunk WAVs
are also removed. Native timestamps and source-window offsets remain approximate.

Production transcripts/settings, the speaker profile, completed pilot, and
completed follow-up are protected by hashes. Verification and training flags
remain false. No model downloads, enrollment, production promotion, publication,
archive restart, or recurring job is authorized by this trial.

After the guarded process exits, run
`python3 -m collector.quality_trial report` to include its final memory metrics.
Repetition and timing flags diagnose candidate behavior; they do not establish
word accuracy or speaker identity. The report preserves legitimate repetition
and unusual speech and recommends keeping production defaults unchanged.

Offline checks: `python3 -m unittest tests.test_quality_trial` and
`python3 -m unittest discover -s tests`.
