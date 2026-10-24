#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077
mkdir -p .harmon-work/quality-trial-30m
export HARMON_LOG="$ROOT/.harmon-work/quality-trial-30m/guard-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
exec bash scripts/run_limited.sh "$ROOT/.venv/bin/python" -m collector.quality_trial run
