#!/usr/bin/env bash
# Explicit, finite LOCAL comparison; never starts the archive batch or publishes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python3 -c 'from collector.benchmark_adapters import require_guards; require_guards()'
python3 -m venv .harmon-tools/benchmark-venv
.harmon-tools/benchmark-venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir 'pyarrow==21.0.0'
python3 collector/benchmark_inputs.py provision
trap 'python3 collector/benchmark_inputs.py cleanup' EXIT
.harmon-tools/benchmark-venv/bin/python collector/benchmark_inputs.py prepare
python3 collector/benchmark_asr.py run
