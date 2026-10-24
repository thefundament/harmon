#!/usr/bin/env bash
# One detached, guarded production session; no recurring job or automatic restart.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SESSION="$ROOT/.harmon-work/production-8h-$STAMP"
UNIT="harmon-production-8h-$STAMP"
.venv/bin/python -m collector.production_session prepare --directory "$SESSION"
systemd-run --user --collect --no-block --unit="$UNIT" \
  -p Type=exec -p Restart=no -p RuntimeMaxSec=8h -p TimeoutStopSec=180 -p KillMode=mixed \
  -p MemoryMax=6G -p MemorySwapMax=0 -p MemoryAccounting=yes -p OOMPolicy=stop \
  -p "StandardOutput=append:$SESSION/production.log" \
  -p "StandardError=append:$SESSION/production.log" \
  -p "ExecStopPost=$ROOT/.venv/bin/python -m collector.production_session finish --directory $SESSION" \
  --working-directory="$ROOT" \
  --setenv=HARMON_MODEL=large-v3-turbo-q5_0 --setenv=HARMON_THREADS=6 --setenv=HARMON_COMMIT_EVERY=25 \
  /usr/bin/python3 "$ROOT/collector/memory_guard.py" --metrics "$SESSION/memory.json" -- \
  bash "$ROOT/scripts/run_archive.sh" --max-hours 8 --commit-every 25 --min-successes 1 \
  --manifest "$SESSION/manifest.jsonl" --speaker-profile "$ROOT/.harmon-work/speaker_profile.json"
systemctl --user show "$UNIT.service" -p ActiveState -p SubState -p RuntimeMaxUSec -p Restart
printf '%s\n' "$SESSION" > .harmon-work/last-production-session
