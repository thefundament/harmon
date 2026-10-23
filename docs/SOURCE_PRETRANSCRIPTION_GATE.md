# Before-download source qualification

## Why this gate exists

**We only want footage belonging to the creator, not arbitrary videos in a playlist.**
The agent must check owner/broadcaster identity *before* downloading or
transcribing a single byte of media.

`collector/source_gate.py` runs on the local agent and uses a metadata-only
`yt-dlp --dump-single-json --skip-download` request. It caches results by
source (not 10-minute window) in `.harmon-work/source_checks/`, then permits
or rejects all of that recording's windows consistently.

The acceptance criteria are strict:

| Source | Required evidence | Never sufficient |
| --- | --- | --- |
| YouTube | Exact video `channel_id = UCTiP-fGq-Uuzcor4fmp_R2Q` | Being in a Harmon-created playlist, matching a title/handle |
| Twitch VOD | Actual VOD `uploader_id = simplez4ck` | A URL appearing in a discovery list |
| Twitch clip | Clip **broadcaster** channel name = `simplez4ck` | The uploader/curator (typically a viewer who clipped it) |

The uploader must match the actual video, and video/clip IDs must match the
frozen worklist. If identity is missing, conflicted, or wrong, the task is
**quarantined without media download or ASR**. This fails closed and may
exclude some genuine material whose metadata is incomplete; it deliberately
prefers false negatives to training on somebody else's uploads. Very obvious
standalone music/video/no-commentary titles are also excluded conservatively.

Download errors/timeouts in a metadata query are *transient* and retain the
worker's usual limited retry/backoff. Confirmed rejects are recorded locally
in `.harmon-work/state.json` as `source_rejected` and are not retried.
Neither full recordings nor probe audio are committed.

## Different concept: Is Harmon actually speaking?

A correct channel ID **does not prove which person is audible**. The video
might include NPC dialogue, other players, clips of a movie, or a guest.

**The new gate solves the wrong-owner/playlist problem before downloading**;
it is **not a voice-identification system** and must never set
`speaker_verified=true`. Before the full historical pass is considered a
Harmon-only speech corpus, we need independently established reference
speech and a short-audio speaker-matching gate, calibrated against
other-speaker examples. The existing transcript flags remain unverified.

Therefore, **do not start the full overnight batch solely on the strength
of this change**. Preserve the paused local agent's pilot and its existing
results. The user can approve additional work once voice attribution is
operational or decide that creator-channel ownership is sufficient for
their intended experiment.

## Local agent update

Fetch and reconcile the latest main without losing unpushed commits or
completed `corpus/local_transcripts/`. Run offline unit tests, inspect
`collector/source_gate.py`, then use the existing capped pilot only.
Each new source incurs at most one successful cached metadata probe; future
ten-minute windows reuse the result. A previously rejected source requires
explicit investigation and deliberate removal of its local journal
quarantine/cache entry before another attempt.

This does not change any existing manifest task IDs or source windows.
