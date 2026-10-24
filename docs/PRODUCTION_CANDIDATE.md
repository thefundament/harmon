# Promoted original-audio candidate

The user authorized promotion for an explicitly unverified first-pass archive,
accepting that obvious ASR repetition can be removed later from derived copies.
The 60-window trial completed without runtime failures; nine windows had strong
repetition flags. The promotion accepts that known limitation and does not
claim measured word accuracy or speaker identity.

`scripts/run_archive.sh` now uses installed Turbo, six threads, independent
30-second original-audio chunks with one-second overlap and zero text context.
The unchanged profile supplies post-decode overlap annotations; all voices are
retained. Text and unusual/repeated speech remain intact. Presentation SRTs
partition overlaps and keep the archive's section-relative time coordinates.
Native timestamps, chunk outputs and diagnostic repetition flags remain private
under `.harmon-work/production-candidate/` or the preserved completed trial.
Speaker/training verification flags stay false. No model downloads or profile
recalibration occur. Completed trial windows can be reused without new downloads.

`bash scripts/start_production_8h.sh` starts one detached user service with an
eight-hour hard runtime cap, existing 6 GiB process/zero-swap and combined GPU
memory guards, no restart policy, and no recurring schedule. The shared archive
lock prevents concurrent workers. A frozen pending-only manifest preserves all
existing completed production outputs and the completed private experiments.
The user explicitly authorized publishing to the configured repository remote,
`github.com/thefundament/harmon`. The launcher publishes every 25 completed
outputs and at exit, retaining durable local results if publication fails.
For the already running session launched with `--no-push`, a separate bounded
publisher stages only complete, hash-valid TXT/SRT/metadata sets and publishes
them without interrupting transcription or extending its original deadline.
The source inventory is not expanded.

The service cleans temporary audio and writes its final report automatically.
Its private directory is recorded in `.harmon-work/last-production-session` and
contains `session.json`, the frozen manifest, `production.log`, `memory.json`,
`summary.json` and `REPORT.md`. No agent polling is needed while it runs.
