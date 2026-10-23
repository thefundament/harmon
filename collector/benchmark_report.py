"""Render the local comparison report from recorded measurements, without inference."""
from __future__ import annotations

import json

try:
    from . import benchmark_inputs as inputs, benchmark_metrics as metrics
except ImportError:
    import benchmark_inputs as inputs
    import benchmark_metrics as metrics


def render(result, source):
    summary = result["summary"]
    samples = source["samples"]
    corpus = [s for s in samples if s["dataset"] == "corpus"]
    duration = sum(s["audio_seconds"] for s in corpus)
    lines = ["# Additional STT comparison — 2026-10-23", "",
             f"Provisional recommendation: **`{result['selection']['model']}`**. {result['selection']['reason']}", "",
             "The production default remains `small.en`. The ten-hour archive batch has not been started. "
             "Existing corpus transcripts and their verification flags are preserved.", "",
             "The upstream inventory expanded to 465.4 listed hours while this run was in progress. "
             "This comparison retains its original ten frozen sources and the explicitly requested 127.79-hour projection; "
             "it does not sample or start the expanded inventory. [corpus_sources.jsonl](corpus_sources.jsonl) "
             "and the original inventory commit/hash in the lock preserve that boundary.", "",
             "## Measured quality and throughput", "",
             "Reference WER uses the first timed pass, 100 LibriSpeech test-other recordings and 100 FLEURS English test recordings. "
             "Throughput uses the median of three complete passes on the same ten corpus sources, including full 600-second VOD "
             "and YouTube windows. A ratio at most 1.5 is eligible. Lower WER is better.", "",
             "| Configuration | LibriSpeech WER | FLEURS WER | Overall WER | Corpus median s | Time / current Small | Audio / wall | Combined peak GiB | Eligible speed |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    baseline_seconds = summary.get("small.en-unsegmented", {}).get("corpus_median_seconds")
    for name, row in summary.items():
        if not row["complete"]:
            lines.append(f"| `{name}` | incomplete | | | | | | | no |")
            continue
        quality = row["quality"]
        ratio = row["corpus_median_seconds"] / baseline_seconds if baseline_seconds else 0
        lines.append(f"| `{name}` | {quality['librispeech-test-other']['wer']:.2%} | {quality['fleurs-en_us-test']['wer']:.2%} | "
                     f"{quality['overall']['wer']:.2%} | {row['corpus_median_seconds']:.3f} | {ratio:.2f} | "
                     f"{row['corpus_realtime_throughput']:.2f}× | {row['peak_combined_bytes'] / 1024**3:.2f} | {'yes' if ratio <= 1.5 else 'no'} |")
    lines += ["", f"Corpus: {len(corpus)} recordings, {duration:.3f} seconds ({duration / 60:.2f} minutes). "
              "Reference batches amortize one model load per dataset; corpus timings load one model per source, "
              "with every source's VAD regions passed together. Models run sequentially with six CPU threads.", "",
              "## Error counts and uncertainty", "",
              "Counts are additive Levenshtein substitutions (S), deletions (D) and insertions (I); overall WER divides total errors "
              "by total reference words. Empty hypotheses count as deletions. No commentary output has a verified reference.", "",
              "| Configuration | Dataset | Reference words | S | D | I | WER |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in summary.items():
        if row["complete"]:
            for dataset, score in row["quality"].items():
                lines.append(f"| `{name}` | {dataset} | {score['reference_words']} | {score['substitutions']} | {score['deletions']} | {score['insertions']} | {score['wer']:.2%} |")
    lines += ["", "Eligibility requires at least 10% relative overall WER improvement over current unsegmented Small, "
              "no dataset worsening by more than one percentage point, and a positive lower bound in the paired bootstrap "
              "95% interval. The bootstrap resamples 100 paired utterances within each dataset, 2,000 draws with seed 20261023. "
              "Its intervals do not account for shared speakers, repeated sentences, or transfer to gaming commentary.", "",
              "| Configuration | Relative WER improvement | Bootstrap 95% interval | Meets reference threshold | Changed transcripts across repeats |",
              "| --- | ---: | --- | --- | ---: |"]
    for name, row in summary.items():
        if row["complete"] and name != "small.en-unsegmented":
            low, high = row["paired_bootstrap"]["relative_improvement_95_ci"]
            lines.append(f"| `{name}` | {row['relative_wer_improvement']:.1%} | {low:.1%} to {high:.1%} | "
                         f"{'yes' if row['quality_eligible'] and row['uncertainty_eligible'] else 'no'} | {len(row['changed_transcript_ids'])} / {len(samples)} |")
    lines += ["", "Normalization is `harmon-english-v1`: NFKC, lowercase, canonical apostrophes, remove bracketed ASR event markers, "
              "separate hyphens/slashes, remove other punctuation, collapse whitespace. Numbers remain as written. "
              "This policy applies identically to all references and hypotheses. It can count numeral/spelled-number differences as errors. "
              "These are this run's sampled measurements; upstream/public benchmark scores were not used in ranking.", "",
              "## Timing and memory boundaries", "",
              result["timing_policy"], "",
              "Initial model load times below come from each runtime's diagnostics; the separate startup-plus-smoke wall time "
              "also includes one short recognition. Driver and file caches were already warm from setup and the interrupted logging-check "
              "attempt; these are not cold-boot measurements. Each timed repeat includes another CLI load. "
              "Small's unsegmented decoding settings match production; this runner uses JSON plus common TXT/SRT serialization and "
              "retains backend diagnostics instead of production's quiet logging.", "",
              "| Configuration | Initial reported load s | Startup + smoke s | Process/cgroup peak GiB | GPU delta peak GiB | Corpus three-pass range s |",
              "| --- | ---: | ---: | ---: | ---: | --- |"]
    for smoke in result["smoke"]:
        name = smoke["configuration"]
        row = summary.get(name, {})
        if smoke["status"] == "complete" and row.get("complete"):
            load = smoke["timing"].get("reported_model_load_seconds")
            load_text = f"{load:.3f}" if load is not None else "not reported"
            low, high = row["corpus_range_seconds"]
            lines.append(f"| `{name}` | {load_text} | {smoke['initial_startup_and_smoke_seconds']:.3f} | "
                         f"{row['peak_cgroup_bytes'] / 1024**3:.2f} | {row['peak_gpu_delta_bytes'] / 1024**3:.2f} | {low:.3f}–{high:.3f} |")
        else:
            lines += [f"| `{name}` | failed | {smoke.get('elapsed_seconds', 0):.3f} | | | |"]
    lines += ["", "All model runs use the Radeon 8060S through Vulkan. The existing outer guard enforces a 6 GiB aggregate "
              "process cap, no job swap, an 8 GiB process-plus-GPU ceiling and a 2 GiB host-available floor. "
              + result["memory_caveat"] + " Independent peaks are not additive. Raw phase and guard measurements are retained.", "",
              "Shared VAD runs on CPU, six threads, Silero v6.2 F32, threshold 0.5, 300 ms padding, minimum speech 250 ms, "
              "minimum silence 500 ms and maximum speech 30 s. VAD is repeated three times and checked for identical regions; "
              "each candidate is charged the corresponding measured VAD and PCM-slicing cost. "
              "An unvoiced recording produces an explicit empty transcript.", "",
              "## Corpus diagnostics and representative differences", "",
              "These automatic flags and disagreements are unverified. They can reveal omissions, repetition and truncation "
              "for investigation; they cannot establish commentary accuracy, who spoke, or training permission. "
              "`speaker_verified`, `training_permission_verified` and `training_ready` stay false. "
              "All ten sources passed the newly published creator-ownership gate in a retrospective metadata-only check "
              "([evidence](ownership_checks.json)). The gate arrived after acquisition; future benchmark acquisitions "
              "check ownership before any download. Channel ownership does not establish the audible speaker.", "",
              "| Configuration | Repeated 5-gram flags | Empty voiced regions | Timestamp endpoint adjustments |",
              "| --- | ---: | ---: | ---: |"]
    passes = [t for t in result["trials"] if t["repetition"] == 1 and t["status"] == "complete"]
    matched_small = next((t for t in passes if t["configuration"] == "small.en"), None)
    examples = []
    for trial in passes:
        rows = [s for s in trial["samples"] if s["dataset"] == "corpus"]
        lines.append(f"| `{trial['configuration']}` | {sum(bool(s['diagnostics']['repeated_5grams']) for s in rows)} | "
                     f"{sum(s['diagnostics']['empty_voiced_regions'] for s in rows)} | {sum(len(s['timestamp_adjustments']) for s in rows)} |")
        if matched_small and trial["configuration"] not in ("small.en", "small.en-unsegmented"):
            baseline = {s["id"]: s for s in matched_small["samples"] if s["dataset"] == "corpus"}
            for sample in rows:
                diffs = metrics.differences(baseline[sample["id"]]["text"], sample["text"], 4)
                if diffs:
                    examples.append({"id": sample["id"], "candidate": trial["configuration"],
                                     "baseline": "small.en with identical VAD", "differences_unverified": diffs})
    lines += ["", "[Representative token differences](differences.json) compare each candidate with Small using the identical "
              "VAD regions. [Full result records](results.json) retain every timed transcript, subtitle timestamps, content hashes, "
              "per-utterance scores, memory and output-consistency measurements. "
              "A repeated n-gram can be real repeated speech; an empty voiced region can be a VAD false positive.", "",
              "## Runtime limits and provenance", "",
              "- whisper.cpp v1.9.5 uses native segment timestamps. Source commit, build-only shader throttle, linked-library "
              "checksums, build flags and model checksums are in the result provenance.",
              "- speech.cpp v0.8.11 uses the checksum-verified Linux Vulkan release in `.harmon-tools/speech.cpp-0.8.11`. "
              "Its release and all models are pinned in [lock.json](lock.json). The isolated dataset reader is PyArrow 21.0.0; "
              "production `.venv` dependencies are untouched.",
              "- Parakeet TDT v3 F16 uses native segment timestamps. Its runtime may lose sentences in long multi-sentence "
              "inputs; the shared VAD bounds regions but does not guarantee correctness.",
              "- Qwen3-ASR 0.6B Q8_0 lacks native timestamps in this runtime. Its subtitles are labeled "
              "`vad_region_no_word_alignment`, spanning the detected region. They are not word alignment. "
              "Timing includes generating those region cues.",
              "- Native timestamp endpoints are clamped to actual region/audio bounds and every adjustment is recorded. "
              "Source times are relative to the downloaded section; source keyframe cuts can be imprecise.", "",
              "Sources: [speech.cpp CLI](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/cli.md), "
              "[model catalog](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/models.md), "
              "[Qwen ASR](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/models/qwen3-asr.md), "
              "[FastConformer](https://github.com/nyosegawa/speech.cpp/blob/v0.8.11/docs/models/fastconformer.md). "
              "[Frozen references and dataset revisions](reference_samples.json) identify the exact published transcripts; "
              "[inputs](inputs.json) record the WAV hashes used by every model.", "",
              "## Processing estimates and common overhead", "",
              "The 127.79-hour inventory projection scales the corpus panel's processing time per audio second. "
              "It includes segmentation and output writing, but excludes download, conversion, publishing and unavailable-source retries. "
              "This small corpus mixes mostly silent/music YouTube audio with commentary; the full inventory's speech density and "
              "source availability may differ. The range below is the observed three-pass timing range, not a confidence interval.", "",
              "| Configuration | Processing hours for 127.79 h | Observed timing range projected h |",
              "| --- | ---: | --- |"]
    for name, row in summary.items():
        if row["complete"]:
            low, high = row["corpus_range_seconds"]
            lines.append(f"| `{name}` | {row['estimated_127_79_hours_processing_hours']:.2f} | {127.79 * low / duration:.2f}–{127.79 * high / duration:.2f} |")
    download_seconds = sum(s["download_conversion_seconds"] for s in corpus)
    lines += ["", f"Common source download + conversion overhead: **{download_seconds:.3f} s** for the ten successful source attempts "
              f"(ten maximum, no failures), measured once and reused by all configurations. A simple audio-duration projection adds "
              f"{127.79 * download_seconds / duration:.2f} h; platform and clip-heavy workloads can differ substantially. "
              "Pinned model and dataset downloads, runtime installation and reference extraction are setup-only costs recorded in "
              "[execution.json](execution.json). Publishing is separate and recorded there after publication.", "",
              "## Failures and validation", "",
              "An initial preparation check found non-unique FLEURS sentence IDs and stopped before model execution; "
              "unique recording filenames were frozen instead. An initial smoke attempt rejected Whisper because its quiet flag "
              "suppressed backend diagnostics; the run was stopped, its records preserved, and the adapter corrected before this "
              "measurement. These are harness failures, not measured model-quality failures.", ""]
    failures = [s for s in result["smoke"] if s["status"] != "complete"] + [t for t in result["trials"] if t["status"] != "complete"]
    if failures:
        lines += ["Measured-run failures:", ""]
        lines += [f"- `{f['configuration']}`: {f.get('error', 'incomplete')}" for f in failures]
    else:
        lines += ["All configurations completed smoke testing and three timed repetitions. At least two additional models completed, "
                  "so the fixed `large-v3-q5_0` fallback was not needed."]
    lines += ["", "Offline checks cover additive scoring and normalization, deterministic sample selection, strict 1.5× eligibility, "
              "per-dataset quality limits and uncertainty, timing boundaries, GPU confirmation, checksum rejection, offset/clamping, "
              "Qwen region timestamps, failure status, timeout cleanup, memory-cap refusal and a restart-safe ten-attempt ledger. "
              "The full existing suite also passes. [Execution/validation details](execution.json) record final counts and cleanup.", "",
              "To reproduce locally after preserving this report's artifacts:", "", "```bash",
              'HARMON_LOG="$PWD/.harmon-work/logs/stt-comparison.log" \\',
              '  bash scripts/run_limited.sh bash scripts/run_stt_benchmark.sh',
              "python3 collector/benchmark_report.py", "```", "",
              "The launcher is finite, has no discovery or scheduler, and cleans benchmark audio on exit. "
              "The source-attempt ledger prevents an implicit second set of downloads; a new acquisition requires an explicit new ledger. "
              "Existing result files are never overwritten by another run. GitHub's benchmark workflow runs only offline checks.", ""]
    return "\n".join(lines), examples


def main():
    result = json.loads((inputs.REPORT / "results.json").read_text())
    source = json.loads((inputs.REPORT / "inputs.json").read_text())
    report, differences = render(result, source)
    (inputs.REPORT / "REPORT.md").write_text(report)
    inputs.worker.atomic_json(inputs.REPORT / "differences.json", differences)


if __name__ == "__main__":
    main()
