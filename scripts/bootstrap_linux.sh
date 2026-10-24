#!/usr/bin/env bash
# Pinned local setup. Run inside scripts/run_limited.sh on a shared-memory host.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODEL_NAME="${HARMON_MODEL:-large-v3-turbo-q5_0}"
case "$MODEL_NAME" in
  small.en) MODEL_MIN=480000000; MODEL_SHA1=db8a495a91d927739e50b3fc1cc4c6b8f6c2d022 ;;
  large-v3-turbo-q5_0) MODEL_MIN=570000000; MODEL_SHA1=e050f7970618a659205450ad97eb95a18d69c9ee ;;
  *) echo "Unsupported HARMON_MODEL: $MODEL_NAME" >&2; exit 2 ;;
esac
JOBS="${HARMON_BUILD_JOBS:-2}"
[[ "$JOBS" =~ ^[12]$ ]] || { echo 'HARMON_BUILD_JOBS must be 1 or 2' >&2; exit 2; }
for tool in git python3 cmake c++ make curl ffmpeg; do
  command -v "$tool" >/dev/null || { echo "Missing $tool; install Linux build dependencies first" >&2; exit 2; }
done
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --upgrade 'yt-dlp>=2026.10.21'
mkdir -p .harmon-tools
SOURCE=.harmon-tools/whisper.cpp
if [[ ! -d "$SOURCE/.git" ]]; then
  git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp.git "$SOURCE"
fi
[[ "$(git -C "$SOURCE" describe --tags --exact-match)" == v1.9.5 ]] || { echo 'Expected whisper.cpp v1.9.5; use a separate clean tools directory' >&2; exit 2; }
# Upstream's shader helper launches up to 16 glslc processes behind Make's
# jobserver. Cap it to one so that helper plus the other Make job stays at two.
python3 - "$SOURCE/ggml/src/ggml-vulkan/vulkan-shaders/vulkan-shaders-gen.cpp" <<'PYTHON'
import sys
from pathlib import Path
p=Path(sys.argv[1]);s=p.read_text()
s=s.replace('std::min(16u, std::thread::hardware_concurrency())',
            'std::min(1u, std::thread::hardware_concurrency())')
p.write_text(s) if s != p.read_text() else None
PYTHON
cmake -S "$SOURCE" -B "$SOURCE/build" -DCMAKE_BUILD_TYPE=Release \
  -DWHISPER_BUILD_TESTS=OFF -DGGML_VULKAN="${HARMON_VULKAN:-ON}"
cmake --build "$SOURCE/build" --target whisper-cli --parallel "$JOBS"
MODEL="$SOURCE/models/ggml-$MODEL_NAME.bin"
if [[ ! -s "$MODEL" ]]; then
  curl --fail --location --retry 3 --connect-timeout 30 --continue-at - \
    --output "${MODEL}.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL_NAME.bin"
  mv "${MODEL}.part" "$MODEL"
fi
SIZE="$(stat -c '%s' "$MODEL")"
(( SIZE >= MODEL_MIN )) || { echo "Model is incomplete ($SIZE bytes)" >&2; exit 2; }
printf '%s  %s\n' "$MODEL_SHA1" "$MODEL" | sha1sum --check
sha256sum "$MODEL"
.venv/bin/yt-dlp --version
echo "READY: $MODEL ($SIZE bytes); Vulkan=${HARMON_VULKAN:-ON}"
