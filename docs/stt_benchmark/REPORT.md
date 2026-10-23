# Additional STT comparison — 2026-10-23

Provisional recommendation: **`small.en`**. No complete candidate meets both speed and reference quality thresholds.

The production default remains `small.en`. The ten-hour archive batch has not been started. Existing corpus transcripts and their verification flags are preserved.

The upstream inventory expanded to 465.4 listed hours while this run was in progress. This comparison retains its original ten frozen sources and the explicitly requested 127.79-hour projection; it does not sample or start the expanded inventory. [corpus_sources.jsonl](corpus_sources.jsonl) and the original inventory commit/hash in the lock preserve that boundary.

## Measured quality and throughput

Reference WER uses the first complete timed repetition, 100 LibriSpeech test-other recordings and 100 FLEURS English test recordings. Throughput uses the median of the recorded complete passes on the same ten corpus sources, including full 600-second VOD and YouTube windows. A ratio at most 1.5 is eligible. Lower WER is better.

| Configuration (completed passes) | LibriSpeech WER | FLEURS WER | Overall WER | Corpus median s | Time / current Small | Audio / wall | Combined peak GiB | Eligible speed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `small.en-unsegmented` (3) | 7.96% | 6.33% | 7.05% | 42.843 | 1.00 | 32.93× | 2.11 | yes |
| `small.en` (3) | 8.24% | 7.92% | 8.06% | 57.758 | 1.35 | 24.42× | 2.10 | yes |
| `large-v3-turbo-q5_0` (3) | 5.15% | 6.50% | 5.91% | 65.918 | 1.54 | 21.40× | 2.12 | no |
| `medium.en-q8_0` (2) | 8.52% | 6.33% | 7.29% | 72.394 | 1.69 | 19.49× | 2.37 | no |
| `parakeet-tdt-0.6b-v3` (1) | 4.31% | 7.57% | 6.13% | 18.823 | 0.44 | 74.94× | 2.24 | yes |
| `qwen3-asr-0.6b` (1) | 5.94% | 7.61% | 6.87% | 21.949 | 0.51 | 64.27× | 2.23 | yes |

Corpus: 10 recordings, 1410.688 seconds (23.51 minutes). Reference batches amortize one model load per dataset; corpus timings load one model per source, with every source's VAD regions passed together. Models run sequentially with six CPU threads.

## Error counts and uncertainty

Counts are additive Levenshtein substitutions (S), deletions (D) and insertions (I); overall WER divides total errors by total reference words. Empty hypotheses count as deletions. No commentary output has a verified reference.

| Configuration | Dataset | Reference words | S | D | I | WER |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `small.en-unsegmented` | librispeech-test-other | 1785 | 112 | 15 | 15 | 7.96% |
| `small.en-unsegmented` | fleurs-en_us-test | 2260 | 98 | 14 | 31 | 6.33% |
| `small.en-unsegmented` | overall | 4045 | 210 | 29 | 46 | 7.05% |
| `small.en` | librispeech-test-other | 1785 | 116 | 16 | 15 | 8.24% |
| `small.en` | fleurs-en_us-test | 2260 | 108 | 41 | 30 | 7.92% |
| `small.en` | overall | 4045 | 224 | 57 | 45 | 8.06% |
| `large-v3-turbo-q5_0` | librispeech-test-other | 1785 | 75 | 9 | 8 | 5.15% |
| `large-v3-turbo-q5_0` | fleurs-en_us-test | 2260 | 92 | 28 | 27 | 6.50% |
| `large-v3-turbo-q5_0` | overall | 4045 | 167 | 37 | 35 | 5.91% |
| `medium.en-q8_0` | librispeech-test-other | 1785 | 110 | 28 | 14 | 8.52% |
| `medium.en-q8_0` | fleurs-en_us-test | 2260 | 87 | 33 | 23 | 6.33% |
| `medium.en-q8_0` | overall | 4045 | 197 | 61 | 37 | 7.29% |
| `parakeet-tdt-0.6b-v3` | librispeech-test-other | 1785 | 68 | 2 | 7 | 4.31% |
| `parakeet-tdt-0.6b-v3` | fleurs-en_us-test | 2260 | 102 | 47 | 22 | 7.57% |
| `parakeet-tdt-0.6b-v3` | overall | 4045 | 170 | 49 | 29 | 6.13% |
| `qwen3-asr-0.6b` | librispeech-test-other | 1785 | 87 | 9 | 10 | 5.94% |
| `qwen3-asr-0.6b` | fleurs-en_us-test | 2260 | 112 | 28 | 32 | 7.61% |
| `qwen3-asr-0.6b` | overall | 4045 | 199 | 37 | 42 | 6.87% |

Eligibility requires at least 10% relative overall WER improvement over current unsegmented Small, no dataset worsening by more than one percentage point, and a positive lower bound in the paired bootstrap 95% interval. The bootstrap resamples 100 paired utterances within each dataset, 2,000 draws with seed 20261023. Its intervals do not account for shared speakers, repeated sentences, or transfer to gaming commentary.

| Configuration | Relative WER improvement | Bootstrap 95% interval | Meets reference threshold | Changed transcripts / subtitles across repeats |
| --- | ---: | --- | --- | ---: |
| `small.en` | -14.4% | -24.6% to -5.2% | no | 0 / 0 (of 210) |
| `large-v3-turbo-q5_0` | 16.1% | 6.3% to 25.4% | yes | 0 / 0 (of 210) |
| `medium.en-q8_0` | -3.5% | -17.9% to 8.2% | no | 0 / 0 (of 210) |
| `parakeet-tdt-0.6b-v3` | 13.0% | -3.8% to 26.4% | no | not measured (one pass) |
| `qwen3-asr-0.6b` | 2.5% | -11.1% to 14.5% | no | not measured (one pass) |

Normalization is `harmon-english-v1`: NFKC, lowercase, canonical apostrophes, remove bracketed ASR event markers, separate hyphens/slashes, remove other punctuation, collapse whitespace. Numbers remain as written. This policy applies identically to all references and hypotheses. It can count numeral/spelled-number differences as errors. These are this run's sampled measurements; upstream/public benchmark scores were not used in ranking.

## Timing and memory boundaries

Initial smoke separate. Each repeat includes CLI startup/load, decoding, native serialization, timestamp offsets, common VAD/extraction and TXT/SRT writing. Downloads, hash verification, scoring, diagnostics and publishing excluded.

Initial model load times below come from each runtime's diagnostics; the separate startup-plus-smoke wall time also includes one short recognition. Driver and file caches were already warm from setup and the interrupted logging-check attempt; these are not cold-boot measurements. Each timed repeat includes another CLI load. Small's unsegmented decoding settings match production; this runner uses JSON plus common TXT/SRT serialization and retains backend diagnostics instead of production's quiet logging.

| Configuration | Initial reported load s | Startup + smoke s | Process/cgroup peak GiB | GPU delta peak GiB | Corpus observed range s |
| --- | ---: | ---: | ---: | ---: | --- |
| `small.en-unsegmented` | 0.177 | 0.503 | 1.29 | 0.88 | 42.612–43.095 |
| `small.en` | 0.128 | 0.477 | 1.28 | 0.87 | 57.560–58.187 |
| `large-v3-turbo-q5_0` | 0.388 | 0.807 | 1.19 | 0.95 | 65.847–66.120 |
| `medium.en-q8_0` | 0.410 | 0.891 | 1.01 | 1.52 | 72.229–72.560 |
| `parakeet-tdt-0.6b-v3` | 0.870 | 0.985 | 0.99 | 1.43 | single pass |
| `qwen3-asr-0.6b` | 0.710 | 1.669 | 1.08 | 1.45 | single pass |

All model runs use the Radeon 8060S through Vulkan. The existing outer guard enforces a 6 GiB aggregate process cap, no job swap, an 8 GiB process-plus-GPU ceiling and a 2 GiB host-available floor. Cgroup includes cache; global GPU deltas may double count shared RAM and other workloads. Phase sampling 100ms, outer guard 250ms. Independent peaks are not additive. Table peaks cover timed phases; separate startup/smoke memory and complete guard measurements are retained in the JSON records.

Shared VAD runs on CPU, six threads, Silero v6.2 F32, threshold 0.5, 300 ms padding, minimum speech 250 ms, minimum silence 500 ms and maximum speech 30 s. VAD is repeated three times and checked for identical regions; each candidate is charged the corresponding measured VAD and PCM-slicing cost. An unvoiced recording produces an explicit empty transcript.

## Corpus diagnostics and representative differences

These automatic flags and disagreements are unverified. They can reveal omissions, repetition and truncation for investigation; they cannot establish commentary accuracy, who spoke, or training permission. `speaker_verified`, `training_permission_verified` and `training_ready` stay false. All ten sources passed the newly published creator-ownership gate in a retrospective metadata-only check ([evidence](ownership_checks.json)). The gate arrived after acquisition; future benchmark acquisitions check ownership before any download. Channel ownership does not establish the audible speaker.

| Configuration | Repeated 5-gram flags | Empty voiced regions | Timestamp endpoint adjustments |
| --- | ---: | ---: | ---: |
| `small.en-unsegmented` | 1 | 0 | 4 |
| `small.en` | 2 | 6 | 24 |
| `large-v3-turbo-q5_0` | 2 | 0 | 74 |
| `medium.en-q8_0` | 2 | 5 | 21 |
| `parakeet-tdt-0.6b-v3` | 1 | 10 | 0 |
| `qwen3-asr-0.6b` | 1 | 0 | 1 |

[Representative token differences](differences.json) compare each candidate with Small using the identical VAD regions. [Full result records](results.json) retain every timed transcript, subtitle timestamps, content hashes, per-utterance scores, memory and output-consistency measurements. [Reference examples](reference_differences.json) include published gold text and the largest per-utterance error reductions/regressions relative to current Small; those examples are illustrative, not a separate score. A repeated n-gram can be real repeated speech; an empty voiced region can be a VAD false positive.

## Runtime limits and provenance

- whisper.cpp v1.9.5 uses native segment timestamps. Source commit, build-only shader throttle, linked-library checksums, build flags and model checksums are in the result provenance.
- speech.cpp v0.8.11 uses the checksum-verified Linux Vulkan release in `.harmon-tools/speech.cpp-0.8.11`. Its release and all models are pinned in [lock.json](lock.json). The isolated dataset reader is PyArrow 21.0.0; production `.venv` dependencies are untouched.
- Parakeet TDT v3 F16 retains native segment and token timestamps. Raw token times in `native_regions` are relative to each explicitly recorded region start; common subtitle segments use whole-recording offsets. Its runtime may lose sentences in long multi-sentence inputs; the shared VAD bounds regions but does not guarantee correctness.
- Qwen3-ASR 0.6B Q8_0 lacks native timestamps in this runtime. Its subtitles are labeled `vad_region_no_word_alignment`, spanning the detected region. They are not word alignment. Timing includes generating those region cues.
- Native timestamp endpoints are clamped to actual region/audio bounds and every adjustment is recorded. Zero-duration native segments retain their text and original times in the adjustment records and full transcript; they are omitted from SRT because they cannot form a positive subtitle interval. Source times are relative to the downloaded section; source keyframe cuts can be imprecise.

Sources: [speech.cpp CLI](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/cli.md), [model catalog](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/models.md), [Qwen ASR](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/models/qwen3-asr.md), [FastConformer](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/models/fastconformer.md). [Frozen references and dataset revisions](reference_samples.json) identify the exact published transcripts; [inputs](inputs.json) record the WAV hashes used by every model.

## Processing estimates and common overhead

The 127.79-hour inventory projection scales the corpus panel's processing time per audio second. It includes segmentation and output writing, but excludes download, conversion, publishing and unavailable-source retries. This small corpus mixes mostly silent/music YouTube audio with commentary; the full inventory's speech density and source availability may differ. The range below is the observed timing range of completed passes, not a confidence interval.

| Configuration | Processing hours for 127.79 h | Observed timing range projected h |
| --- | ---: | --- |
| `small.en-unsegmented` | 3.88 | 3.86–3.90 |
| `small.en` | 5.23 | 5.21–5.27 |
| `large-v3-turbo-q5_0` | 5.97 | 5.96–5.99 |
| `medium.en-q8_0` | 6.56 | 6.54–6.57 |
| `parakeet-tdt-0.6b-v3` | 1.71 | single pass; no range measured |
| `qwen3-asr-0.6b` | 1.99 | single pass; no range measured |

Common source download + conversion overhead: **16.888 s** for the ten successful source attempts (ten maximum, no failures), measured once and reused by all configurations. A simple audio-duration projection adds 1.53 h; platform and clip-heavy workloads can differ substantially. Pinned model and dataset downloads, runtime installation and reference extraction are setup-only costs recorded in [execution.json](execution.json). Publishing is separate and recorded there after publication.

## Failures and validation

An initial preparation check found non-unique FLEURS sentence IDs and stopped before model execution; unique recording filenames were frozen instead. An initial smoke attempt rejected Whisper because its quiet flag suppressed backend diagnostics; the run was stopped, its records preserved, and the adapter corrected before this measurement. A later shared-VAD Small pass exposed zero-duration native cues in the full YouTube window. That incomplete attempt remains in the records; after handling the cues explicitly, only unfinished configurations were resumed. Completed baseline repetitions and the frozen PCM/VAD regions were preserved and verified. These are harness failures, not measured model-quality failures. The user then requested early stopping for conclusively ineligible models; Medium was stopped with two complete passes, and the remaining models use that early-stop policy.

Retained incomplete/failed attempts (excluded from medians and reference scores):

- `small.en`: invalid native timestamp

All three additional models produced evaluable full passes. Medium stopped after two passes, Parakeet and Qwen after one, following the user's early-stop instruction. The fixed Large fallback was not needed. Single-pass timings are indicative; repeat consistency was not measured for those rejected candidates.

Offline checks cover additive scoring and normalization, deterministic sample selection, strict 1.5× eligibility, per-dataset quality limits and uncertainty, timing boundaries, GPU confirmation, checksum rejection, offset/clamping, Qwen region timestamps, failure status, timeout cleanup, memory-cap refusal and a restart-safe ten-attempt ledger. The full existing suite also passes. [Execution/validation details](execution.json) record final counts and cleanup.

To reproduce locally after preserving this report's artifacts:

```bash
HARMON_LOG="$PWD/.harmon-work/logs/stt-comparison.log" \
  bash scripts/run_limited.sh bash scripts/run_stt_benchmark.sh
python3 collector/benchmark_report.py
```

The launcher is finite, has no discovery or scheduler, and cleans benchmark audio on exit. The source-attempt ledger prevents an implicit second set of downloads; a new acquisition requires an explicit new ledger. Existing result files are never overwritten by another run. GitHub's benchmark workflow runs only offline checks.

## Findings from the corpus inspection

- The speed ceiling is 64.264 s for the ten-source corpus. Turbo improves overall reference WER by 16.1% (paired bootstrap 95% interval 6.3% to 25.4%) but takes 65.918 s, or 1.54× Small, so it is excluded.
- Parakeet takes 18.823 s in its one complete pass, but FLEURS WER rises by 1.24 percentage points. Qwen takes 21.949 s, improves overall WER by only 2.5%, and raises FLEURS WER by 1.28 points. Neither meets the default quality thresholds; further repetitions were skipped.
- Segmented Small produces nine repetitions of a phrase beginning “I don’t think I’m going” in the YouTube sample; unsegmented Small emits music markers there. Medium and Turbo also have repeated-phrase flags. These are output defects/flags for investigation, not measured commentary accuracy.
- In the first commentary clip, Medium, Parakeet and Qwen agree on “Hey Google” and the response about not knowing what is playing. Their agreement is unverified; the full reference-free differences are retained rather than scored as WER.
- The game window produces mixed-language/background-dialogue output in Parakeet and Qwen despite English request settings. Qwen retains Japanese text; Parakeet includes phonetic/multilingual fragments. Channel ownership does not establish the speaker or correctness.
- Corpus inspection flags 12 potentially uncovered native-timestamp tails and 10 empty voiced regions for Parakeet; Medium has three such tails and five empty regions. These can reflect silence/VAD false positives or omissions. Qwen’s region-only timestamps cannot support the same tail-alignment test.
- All repeated configurations had identical transcript and subtitle hashes. Repetition consistency is unmeasured for the single-pass rejected Parakeet/Qwen candidates. Retain unsegmented Small; the production default is unchanged.

[Per-source coverage and truncation flags](corpus_inspection.json) retain the automatic inspection. Examples above are unverified transcript differences, not adjudicated commentary accuracy.

## Execution and publication record

- `stt-provision`: 48.798 s wall time, excluded from model timing.
- `stt-inputs`: 13.336 s wall time, excluded from model timing.
- `stt-inputs-retry`: 31.421 s wall time, excluded from model timing.
- `stt-ownership`: 7.297 s wall time, excluded from model timing.

The runtime archive transfer/unpacking and isolated PyArrow installation were not separately timed. The first input phase includes the aborted non-unique-ID check; the retry reuses the pinned dataset downloads.

Publication of the benchmark/report commit took 2.845 s for fetch/rebase/push, separate from inference. A subsequent small verification commit records that observation.

Final validation checked 2730 complete transcript records and 6670 timestamp cues; 65 offline tests passed. Temporary benchmark media were removed.

Dataset attribution: [LibriSpeech / OpenSLR 12](https://www.openslr.org/12), Vassil Panayotov, Guoguo Chen, Daniel Povey and Sanjeev Khudanpur, *LibriSpeech: An ASR corpus based on public domain audio books* (2015), via the [CC-BY-4.0 Hugging Face distribution](https://huggingface.co/datasets/openslr/librispeech_asr); [FLEURS](https://huggingface.co/datasets/google/fleurs), Alexis Conneau et al., *FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech* (2022), CC-BY-4.0. Only sampled reference text and provenance are committed; source audio and dataset archives are temporary.
