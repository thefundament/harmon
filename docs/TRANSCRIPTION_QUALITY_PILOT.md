# Private transcription quality pilot

Run `python3 -m collector.transcription_pilot freeze` to freeze four specified
problem recordings and two controls. Controls are distinct recordings selected
by sorted task ID from hash-valid completions with at least 200 whitespace
words, no duplicate normalized nonempty line, and no stored or measured timing
defect. The pilot uses each recording's previously processed source window.

Run `bash scripts/run_quality_pilot.sh` for the comparison. It uses the installed
Turbo model, English, six threads, and the existing process/GPU memory guards.
The shared archive lock prevents concurrent archive processing. It never runs
discovery, enrollment, publishing or production-default changes.

The three configurations are current speaker filtering/concatenation/decoding;
the identical filtered PCM decoded independently in 30-second windows with
one-second overlap and `--max-context 0`; and original PCM decoded with those
chunk settings, then annotated with overlap against the unchanged profile's
matched intervals. Annotations retain all text and do not verify identity.

Everything specific to the run stays in ignored, private
`.harmon-work/quality-pilot/`: frozen manifest and protected-input hashes,
six-attempt ledger, native chunk checkpoints, TXT/SRT/JSON results, memory
measurements, `summary.json` and `REPORT.md`. Source attempts are persisted
before network access, with downloader retries disabled. Failed recordings have
no replacements. The first run fixes a 30-minute wall-clock deadline, which
resumption cannot reset.

An identical launcher invocation resumes: completed results and chunks are
verified and reused; no attempted recording is downloaded again. Temporary
audio is deleted after each source and on exit. After an interruption that
cleans up audio, incomplete work on that recording is reported unavailable;
finished outputs remain usable. A hard kill can leave private audio, which can
be reused on a restart within the deadline and is cleaned up on exit.

Repetition uses normalized five-word sequences independently of line breaks.
Counts, empty results/chunks, unique processed-audio coverage, timing defects
and runtime are reported separately. Only exact suffix/prefix matches in cues
wholly inside the shared one-second boundary are deduplicated. All native text
is retained; other repetition and zero-duration cues remain visible. Source
window offsets and the existing approximate compact-to-source map are retained.

These measurements do not establish word accuracy, missing spoken words, or
correct speaker attribution. A lower repetition count is a diagnostic result.
No reference WER or speaker error rate is claimed. Production defaults remain
unchanged pending independent checking.

Offline verification: `python3 -m unittest tests.test_transcription_pilot`.
