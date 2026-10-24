#!/usr/bin/env bash
# Private six-source pilot only; uses the existing memory guards, never publishing.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077
mkdir -p .harmon-work/quality-pilot
export HARMON_LOG="$ROOT/.harmon-work/quality-pilot/guard-$(date -u +%Y%m%dT%H%M%SZ).log"
exec bash scripts/run_limited.sh "$ROOT/.venv/bin/python" -m collector.transcription_pilot run
