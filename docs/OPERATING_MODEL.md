# Operation, confidence, and GitHub Actions boundaries

## Execution environments

The current archive worker runs locally through `scripts/run_limited.sh`; GitHub performs lightweight offline checks and indexing. See [local sessions](LOCAL_SPEAKER_FILTER.md) and [validation](SESSION_VALIDATION.md). The hosted collection entries below describe the historical acquisition path.

| Activity | Owner / location | Automation status |
| --- | --- | --- |
| Enumerate channel clips and VOD IDs | `collector/harvest.py`, GitHub runner | Automatic within a bounded invocation |
| Download and convert media | yt-dlp and FFmpeg, GitHub runner | Automatic |
| Transcribe and generate SRT | whisper.cpp, GitHub runner | Automatic |
| Record source provenance / deduplicate | `collector/harvest.py` and `rebuild_index.py` | Automatic |
| Commit raw ASR metadata and text | historical harvest workflow | Automatic within an invocation |
| Find short/empty/duplicated transcript candidates | `collector/quality_gate.py` | Automatic, CI on relevant source changes |
| Compare Base and Small ASR on sample clips | `collector/benchmark_asr.py` | Bounded development test |
| Certify words actually spoken by the target | Not implemented | Requires reference speaker validation; quality score is insufficient |
| Export user-facing ZIP | ChatGPT sandbox or future packaging script | Manual in past rounds |

## GitHub Actions limitations

GitHub's Additional Products and Features Terms govern Actions:
https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features

Standard hosted Actions cannot safely be treated as an unlimited free-purpose transcription workstation. Their usage must relate to developing, testing, deploying or publishing this repository's software, and they prohibit workloads whose server burden is disproportionate to user benefit. Public-repo *billing* eligibility does not override those restrictions.

For this reason, `harvest.yml` is currently **manual-only** (no schedule or push trigger). Do not turn it into an unattended bulk downloader or automatic recurring processing job without confirming the use complies with GitHub's terms. `ci.yml` automatically runs lightweight quality tests and refreshes a metadata-only review queue; this is normal repository QA. `model-benchmark.yml` compares two ASR implementations on two tiny samples as a bounded software test.

## Confidence tiers

- **ASR complete** means an ASR engine produced text. It does *not* mean the text is accurate.
- **Review priority score** is a crude heuristic for selecting worthwhile segments; it is *not* accuracy confidence.
- **Speaker match, automatic**: the local worker uses VAD and a private embedding profile. The user selected automatic recurring-voice enrollment without personal review. Its `probable_channel_primary_speaker` label is inferred; cohort separation and withheld-recording recurrence do not establish measured identity accuracy. Optional independently reviewed references remain supported. Multiple speakers, TTS, game dialogue, overlaps and effects remain difficult.
- **High-confidence candidate** (future): strong agreement of independently transcribed speech plus a calibrated speaker match, VAD, reasonable word rate, and source provenance. These remain inferred/automatic rather than human-verified.
- **Manually verified** means an authorized reviewer listened to the source and confirmed the words and identity.
- **Training eligible** separately requires proper permission to use or reproduce the material. Public availability is not proof of that permission.

The existing audio was deliberately deleted after transcription; **speaker verification or model-retranscription of older material requires fetching that audio again**. Future segments should be short, timestamped and associated with their source.

## Model guidance

The current local recognizer is the user-accepted `large-v3-turbo-q5_0`; older hosted outputs used `base.en`. See [the model comparison](stt_benchmark/REPORT.md) for the accepted tradeoff. `small.en` remains an override. Comparing Base to Small can highlight ambiguous words (like 'sheets' versus 'streets'), but disagreement is *not* a measured word-error rate absent a human-labelled gold transcript.

For a true assessment, compare both models on a small manually checked holdout, report WER against that holdout, and avoid silently changing historical wording. Track `recognition_model`, source key, sample offsets and model version in every new output; preserve older text rather than overwriting it.

## Rights and public data

The repository is public. Public video and transcripts may still be protected by copyright, a person's likeness/identity interests, platform terms, or other rights. Before distributing a persona model or its training corpus, establish the creator's authorization. Do not assert this corpus is training-ready, and never invent speech and claim the creator said it.
