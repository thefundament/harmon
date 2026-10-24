#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python || ! -x .harmon-tools/whisper.cpp/build/bin/whisper-cli ]]; then
  bash scripts/bootstrap_linux.sh
fi
export HARMON_MODEL="${HARMON_MODEL:-large-v3-turbo-q5_0}"
exec .venv/bin/python -m collector.production_candidate run --threads "${HARMON_THREADS:-6}" \
  --max-hours 2 --commit-every "${HARMON_COMMIT_EVERY:-25}" "$@"
