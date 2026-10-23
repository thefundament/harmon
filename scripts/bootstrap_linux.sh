#!/usr/bin/env bash
# Idempotent setup on Ubuntu/Debian/Linux or Ubuntu under WSL2. CPU by default.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for tool in git python3 cmake c++ make curl ffmpeg; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing $tool. On Ubuntu/Debian: sudo apt-get update && sudo apt-get install -y git python3 python3-venv python3-pip cmake build-essential curl ffmpeg" >&2
    exit 2
  fi
done
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --upgrade 'yt-dlp>=2026.10.21'
mkdir -p .harmon-tools
if [[ ! -d .harmon-tools/whisper.cpp/.git ]]; then
  git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp.git .harmon-tools/whisper.cpp
fi
if [[ ! -x .harmon-tools/whisper.cpp/build/bin/whisper-cli ]]; then
  cmake -S .harmon-tools/whisper.cpp -B .harmon-tools/whisper.cpp/build \
    -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF
  cmake --build .harmon-tools/whisper.cpp/build --target whisper-cli --parallel "${HARMON_BUILD_JOBS:-4}"
fi
MODEL=".harmon-tools/whisper.cpp/models/ggml-small.en.bin"
mkdir -p "$(dirname "$MODEL")"
if [[ ! -s "$MODEL" ]]; then
  echo "Downloading official Whisper small.en model (~466 MB); resumable..."
  curl --fail --location --retry 3 --connect-timeout 30 --continue-at - \
    --output "${MODEL}.part" \
    'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin'
  mv "${MODEL}.part" "$MODEL"
fi
SIZE="$(stat -c '%s' "$MODEL")"
if (( SIZE < 400000000 )); then
  echo "Model too small ($SIZE bytes); delete incomplete model and retry setup" >&2
  exit 2
fi
test -x .harmon-tools/whisper.cpp/build/bin/whisper-cli
.venv/bin/yt-dlp --version
echo "READY: model $MODEL ($SIZE bytes)"
echo "DRY RUN: .venv/bin/python collector/local_worker.py status"
echo "EXECUTE: .venv/bin/python collector/local_worker.py run --threads 6 --commit-every 10"
