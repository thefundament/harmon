#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python || ! -x .harmon-tools/whisper.cpp/build/bin/whisper-cli ]]; then
  bash scripts/bootstrap_linux.sh
fi
export HARMON_MODEL="${HARMON_MODEL:-small.en}"
exec .venv/bin/python collector/local_worker.py run --threads "${HARMON_THREADS:-6}" \
  --commit-every "${HARMON_COMMIT_EVERY:-10}" "$@"
