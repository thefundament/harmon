#!/usr/bin/env bash
# A new user service enforces the aggregate process cap, including child processes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p .harmon-work/logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="${HARMON_LOG:-$ROOT/.harmon-work/logs/$STAMP.log}"
METRICS="${LOG%.log}.memory.json"
# Pass explicit environment values: the user service does not inherit the caller's shell.
set -o pipefail
systemd-run --user --wait --pipe --collect --unit="harmon-$STAMP" \
  -p MemoryMax=6G -p MemorySwapMax=0 -p MemoryAccounting=yes -p OOMPolicy=stop \
  --working-directory="$ROOT" \
  --setenv="HARMON_MODEL=${HARMON_MODEL:-small.en}" \
  --setenv="HARMON_VULKAN=${HARMON_VULKAN:-ON}" \
  --setenv="HARMON_BUILD_JOBS=${HARMON_BUILD_JOBS:-2}" \
  --setenv="HARMON_THREADS=${HARMON_THREADS:-6}" \
  --setenv="HARMON_COMMIT_EVERY=${HARMON_COMMIT_EVERY:-10}" \
  /usr/bin/python3 "$ROOT/collector/memory_guard.py" --metrics "$METRICS" -- "$@" 2>&1 | tee "$LOG"
