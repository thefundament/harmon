#!/usr/bin/env bash
# Optional ONCE per host; does not modify whisper.cpp or the selected ASR model.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  echo "Run scripts/bootstrap_linux.sh first to install the existing collector" >&2
  exit 2
fi
# The speaker encoder runs on CPU. Avoid accidentally installing enormous CUDA
# runtimes on a Strix Halo host whose Vulkan GPU runs Whisper independently.
.venv/bin/python -m pip install --disable-pip-version-check   --index-url https://download.pytorch.org/whl/cpu torch
.venv/bin/python -m pip install --disable-pip-version-check   "resemblyzer==0.1.4"
.venv/bin/python -c 'import webrtcvad; from resemblyzer import VoiceEncoder; print("Speaker VAD + encoder ready")'
echo "Speaker dependency setup complete. No voice profile has been inferred or created."
