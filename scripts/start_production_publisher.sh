#!/usr/bin/env bash
# Attach publishing to the existing session; never restart transcription.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077
SESSION="$(cat .harmon-work/last-production-session)"
UNIT="harmon-publisher-$(basename "$SESSION")"
RUNTIME="$(.venv/bin/python - "$SESSION" <<'PY'
import json, math, sys, time
from pathlib import Path
record=json.loads((Path(sys.argv[1])/'session.json').read_text())
assert record.get('remote_publishing_enabled') and record.get('publication')
print(math.ceil(max(0,record['started_unix']+record['max_seconds']-time.time()))+600)
PY
)"
systemd-run --user --collect --no-block --unit="$UNIT" \
  -p Type=exec -p Restart=no -p "RuntimeMaxSec=$RUNTIME" \
  -p MemoryMax=1G -p MemorySwapMax=0 \
  -p "StandardOutput=append:$SESSION/publishing.log" \
  -p "StandardError=append:$SESSION/publishing.log" \
  --working-directory="$ROOT" \
  "$ROOT/.venv/bin/python" -m collector.production_publisher watch --directory "$SESSION"
systemctl --user show "$UNIT.service" -p ActiveState -p SubState -p RuntimeMaxUSec -p Restart
