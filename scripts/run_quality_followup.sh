#!/usr/bin/env bash
# Private follow-up only; keep the archive stopped and reuse existing guards.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077
mkdir -p .harmon-work/quality-followup
export HARMON_LOG="$ROOT/.harmon-work/quality-followup/guard-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
exec bash scripts/run_limited.sh "$ROOT/.venv/bin/python" -m collector.quality_followup run
