# One-time non-Twitch historical media expansion

## Source ownership

The **official YouTube channel ID** is `UCTiP-fGq-Uuzcor4fmp_R2Q`, taken from the creator's own [Linktree](https://linktr.ee/simplez4ck). The existing 12 YouTube entries are preserved in `corpus/discovery/youtube.jsonl`.

A one-off metadata-only GitHub runner inventory probes:
- **Videos:** `https://www.youtube.com/channel/UCTiP-fGq-Uuzcor4fmp_R2Q/videos`
- **Past live streams:** `https://www.youtube.com/channel/UCTiP-fGq-Uuzcor4fmp_R2Q/streams`
- **Shorts:** `https://www.youtube.com/channel/UCTiP-fGq-Uuzcor4fmp_R2Q/shorts`
- **Playlists:** `https://www.youtube.com/channel/UCTiP-fGq-Uuzcor4fmp_R2Q/playlists`; a small bounded selection is inspected

The completed October 23 one-time snapshot found **477 distinct video IDs: 450 eligible creator-channel IDs (351.87 hours of listed recordings) and 27 held-out playlist candidates**. This is metadata, not confirmed successful download. The worklist puts YouTube ahead of Twitch and interleaves the *first* ten-minute section of different YouTube recordings before later sections, improving early language variety.

The result is `corpus/discovery/youtube_expanded.jsonl`, with a separate `youtube_expansion_report.json` documenting blocked surfaces. **The worker does not scrape YouTube.** Its source/offset task list is generated once from the frozen metadata.

## Correctness and safety

1. **Never delete earlier source IDs** when a YouTube surface becomes inaccessible; preserve the original 12.
2. **Never change existing task IDs** for clips, VOD ranges or YouTube ranges. Completed local outputs are keyed by immutable IDs and SHA256.
3. Videos on other creators' channels inside a playlist are candidates only, never executable tasks. Playlist videos without reliable author attribution are candidates only.
4. An entry with no known finite video duration is candidate-only until the duration is established. The local worker requires ten-minute bounded windows.
5. Deduplicate by canonical 11-character YouTube video ID across Videos/Live/Shorts/playlists.
6. Do not assume every discovered video is still downloadable; failures are isolated by the local worker.

## Other creator-linked profiles

`corpus/discovery/linked_media_profiles.json` lists the creator's **Instagram, Facebook, Snapchat, and X** profiles linked from the same Linktree. These are research leads, **not verified video IDs** and thus not automatically added to the executable manifest. The secondary Instagram profile labeled "Tigger's Instagram" is excluded as an unverified speaker source.

A third-party soundboard search mentions approximately eight clips under the streamer's name, but without independently verified original recordings or speaker identities, those cannot be treated as high-confidence conversational training examples.

Instagram Reels, Facebook videos, Snapchat Spotlight/Stories and X-attached videos can be inventoried later **only after actual public item URLs, creator attribution, and supported downloadability are verified**. Do not guess URLs based on a username or add URLs of private/removed content.

## Agent handoff after merge

The local agent is paused. Resume only after this merge completes and the latest `corpus/history/manifest.jsonl` has been generated on `main` by the lightweight indexing workflow.

1. Preserve local, unpushed commits and transcripts; avoid `git reset --hard` or force-push.
2. Fetch and rebase the local checkout against the latest `origin/main` when clean. If local commits conflict, reconcile those commits **without replacing or deleting `corpus/local_transcripts/`**.
3. Verify `python collector/local_worker.py status` succeeds and the task count includes YouTube windows. Do **not** run media discovery on the local agent.
4. Continue the existing local worker. It skips completed source+offset IDs and deletes temporary video and WAV after transcription. Unavailable YouTube videos count as isolated failures, not fatal errors.

No recurring schedule or bulk hosted-runner audio transcription is part of this expansion.
