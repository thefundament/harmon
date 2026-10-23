"""Reference scoring and diagnostics shared by the local STT comparison.

Normalization v1: Unicode NFKC, lowercase, canonical apostrophes, strip ASR
event markers, separate hyphens, remove other punctuation, collapse whitespace.
Numbers are left as written. This deliberately modest policy is identical for
references and every recognizer; it is not a claim to reproduce a model card WER.
"""
from __future__ import annotations

from collections import Counter
import difflib
import hashlib
import math
import re
import random
import unicodedata

NORMALIZATION = "harmon-english-v1"


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\[[^\]]*\]|<\|[^|]*\|>", " ", text)
    text = re.sub(r"[-–—/]", " ", text)
    text = re.sub(r"[^\w\s']", "", text, flags=re.UNICODE).replace("_", " ")
    return [word.strip("'") for word in text.split() if word.strip("'")]


def score(reference, hypothesis):
    """Levenshtein alignment; ties prefer match, substitution, deletion, insertion.

    Return integer corpus-additive counts, including empty hypotheses. Never
    drop an empty voiced region or average utterance WERs.
    """
    ref, hyp = normalize(reference), normalize(hypothesis)
    previous = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i, word in enumerate(ref, 1):
        current = [(i, 0, i, 0)]
        for j, other in enumerate(hyp, 1):
            if word == other:
                current.append(previous[j - 1])
                continue
            cost, s, d, ins = previous[j - 1]
            substitution = (cost + 1, s + 1, d, ins)
            cost, s, d, ins = previous[j]
            deletion = (cost + 1, s, d + 1, ins)
            cost, s, d, ins = current[j - 1]
            insertion = (cost + 1, s, d, ins + 1)
            current.append(min((substitution, deletion, insertion), key=lambda x: x[0]))
        previous = current
    errors, substitutions, deletions, insertions = previous[-1]
    return dict(reference_words=len(ref), hypothesis_words=len(hyp), errors=errors,
                substitutions=substitutions, deletions=deletions, insertions=insertions,
                wer=errors / len(ref) if ref else None)


def aggregate(scores):
    keys = ("reference_words", "hypothesis_words", "errors", "substitutions", "deletions", "insertions")
    result = {key: sum(s[key] for s in scores) for key in keys}
    result["wer"] = result["errors"] / result["reference_words"] if result["reference_words"] else None
    return result


def diagnostics(text, regions, duration):
    tokens = normalize(text)
    repeated = Counter(tuple(tokens[i:i + 5]) for i in range(max(0, len(tokens) - 4)))
    return {
        "repeated_5grams": [{"text": " ".join(k), "count": v} for k, v in repeated.most_common(8) if v >= 3],
        "empty_voiced_regions": sum(not normalize(r["text"]) for r in regions),
        "last_text_end_s": max((r["end"] for r in regions if normalize(r["text"])), default=None),
        "audio_seconds": duration,
        "note": "Heuristics and disagreement are unverified, not commentary accuracy or speaker attribution.",
    }


def differences(a, b, limit=8):
    aa, bb = normalize(a), normalize(b)
    matcher = difflib.SequenceMatcher(None, aa, bb, autojunk=False)
    return [{"baseline": " ".join(aa[i:j])[:180], "candidate": " ".join(bb[k:l])[:180]}
            for tag, i, j, k, l in matcher.get_opcodes() if tag != "equal"][:limit]


def timestamp(value):
    milliseconds = round(value * 1000)
    hours, rem = divmod(milliseconds, 3600000)
    minutes, rem = divmod(rem, 60000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def offset_segments(segments, start, end, kind):
    """Keep native timings and clamp only to the actual region/audio bounds."""
    result, adjustments = [], []
    for segment in segments:
        a, b = float(segment["start"]), float(segment["end"])
        if not math.isfinite(a + b) or a < 0 or b < a:
            raise ValueError(f"invalid native timestamp {a} -> {b}: {segment['text'][:80]}")
        if b == a:
            adjustments.append({"reason": "zero_duration_native_segment_omitted_from_srt",
                                "native_start": start + a, "native_end": start + b,
                                "text": segment["text"]})
            continue
        clipped_a, clipped_b = min(end, start + a), min(end, start + b)
        if clipped_b != start + b:
            adjustments.append({"native_end": start + b, "clamped_end": clipped_b})
        if clipped_b > clipped_a:
            result.append(dict(start=clipped_a, end=clipped_b, text=segment["text"], timestamp_kind=kind))
    if any(b["start"] < a["start"] for a, b in zip(result, result[1:])):
        raise ValueError("non-monotonic native timestamps")
    return result, adjustments


def write_outputs(prefix, text, segments):
    txt = text.strip() + "\n"
    cues = [s for s in segments if s["text"].strip() and round(s["end"] * 1000) > round(s["start"] * 1000)]
    srt = "\n\n".join(f"{i}\n{timestamp(s['start'])} --> {timestamp(s['end'])}\n{s['text'].strip()}"
                       for i, s in enumerate(cues, 1)) + ("\n" if cues else "")
    prefix.with_suffix(".txt").write_text(txt, encoding="utf-8")
    prefix.with_suffix(".srt").write_text(srt, encoding="utf-8")
    return {"text_sha256": hashlib.sha256(txt.encode()).hexdigest(),
            "srt_sha256": hashlib.sha256(srt.encode()).hexdigest()}


def choose(summary):
    baseline = summary.get("small.en-unsegmented")
    if not baseline or not baseline.get("complete"):
        return {"model": "small.en", "reason": "Fresh baseline incomplete; retain production default."}
    eligible = []
    for name, row in summary.items():
        if not row.get("complete") or name == "small.en-unsegmented":
            continue
        row["time_ratio_to_current_small"] = row["corpus_median_seconds"] / baseline["corpus_median_seconds"]
        row["speed_eligible"] = row["time_ratio_to_current_small"] <= 1.5
        base_wer = baseline["quality"]["overall"]["wer"]
        row["relative_wer_improvement"] = (base_wer - row["quality"]["overall"]["wer"]) / base_wer if base_wer else 0
        row["quality_eligible"] = (row["relative_wer_improvement"] >= .10 and all(
            row["quality"][key]["wer"] <= baseline["quality"][key]["wer"] + .01
            for key in ("librispeech-test-other", "fleurs-en_us-test")))
        row["uncertainty_eligible"] = row.get("paired_bootstrap", {}).get("relative_improvement_95_ci", [0])[0] > 0
        if row["speed_eligible"] and row["quality_eligible"] and row["uncertainty_eligible"]:
            eligible.append(name)
    if not eligible:
        return {"model": "small.en", "reason": "No complete candidate meets both speed and reference quality thresholds."}
    winner = min(eligible, key=lambda n: summary[n]["quality"]["overall"]["wer"])
    return {"model": winner, "provisional": True,
            "reason": "Lowest aggregate reference WER among eligible configurations; commentary accuracy remains unmeasured."}


def paired_bootstrap(baseline, candidate, repetitions=2000):
    """Stratified paired utterance bootstrap; descriptive, not speaker-cluster CI."""
    rng = random.Random(20261023)
    groups = []
    other = {s["id"]: s for s in candidate if "score" in s}
    for dataset in ("librispeech-test-other", "fleurs-en_us-test"):
        group = [(s["score"]["errors"], other[s["id"]]["score"]["errors"], s["score"]["reference_words"])
                 for s in baseline if s["dataset"] == dataset]
        if not group:
            raise ValueError("Incomplete paired reference dataset")
        groups.append(group)
    relative, differences = [], []
    for _ in range(repetitions):
        base = cand = words = 0
        for group in groups:
            for _ in group:
                a, b, n = rng.choice(group)
                base += a
                cand += b
                words += n
        relative.append((base - cand) / base if base else 0)
        differences.append((base - cand) / words)
    def interval(values):
        ordered = sorted(values)
        return [ordered[int(.025 * repetitions)], ordered[min(repetitions - 1, int(.975 * repetitions))]]
    return {"repetitions": repetitions, "seed": 20261023,
            "relative_improvement_95_ci": interval(relative), "wer_reduction_95_ci": interval(differences),
            "limitation": "Resamples paired utterances within each dataset; shared speakers/text and domain shift limit inference."}
