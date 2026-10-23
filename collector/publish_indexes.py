#!/usr/bin/env python3
"""Safely update derived corpus indexes from the latest main.

Run only in an ephemeral GitHub Actions checkout. Never force-push.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DERIVED_PATHS = ["corpus/quality", "corpus/history", "corpus/combined"]
MAX_ATTEMPTS = 6

def command(repo, *args, check=True):
    p = subprocess.run(list(args), cwd=repo, text=True,
                       capture_output=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {(p.stderr or p.stdout)[-1200:]}")
    return p

def regenerate(repo):
    py=sys.executable
    for script in ("collector/quality_gate.py", "collector/history_plan.py",
                   "collector/combined_index.py"):
        command(repo, py, script)

def publish(repo=ROOT, max_attempts=MAX_ATTEMPTS, regenerate_fn=regenerate,
            before_push=None, sleep_fn=time.sleep):
    """Return 'published' or 'unchanged'; retry only non-fast-forward races.

    Reset is restricted to the ephemeral CI checkout; never call this from
    someone's working repository with uncommitted data.
    """
    repo=Path(repo)
    if os.environ.get("GITHUB_ACTIONS") != "true" and regenerate_fn is regenerate:
        raise RuntimeError("Refusing to reset non-CI checkout")
    for attempt in range(1, max_attempts + 1):
        command(repo, "git", "fetch", "--no-tags", "origin", "main")
        # The CI working directory is disposable. Rebase conflict avoidance:
        # regenerate outputs on the actual latest source files every attempt.
        command(repo, "git", "reset", "--hard", "origin/main")
        regenerate_fn(repo)
        command(repo, "git", "add", "--", *DERIVED_PATHS)
        changes=command(repo, "git", "diff", "--cached", "--quiet", check=False)
        if changes.returncode == 0:
            print("Derived indexes unchanged on latest main; no bot commit needed.")
            return "unchanged"
        if changes.returncode != 1:
            raise RuntimeError("Unable to assess staged changes")
        command(repo, "git", "-c", "user.name=harmon-corpus-bot",
                "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit", "-m", "Refresh derived corpus indexes [skip ci]")
        if before_push is not None:
            before_push(attempt)
        pushed=command(repo, "git", "push", "origin", "HEAD:main", check=False)
        if pushed.returncode == 0:
            print(f"Published deterministic indexes (attempt {attempt}).")
            return "published"
        combined=(pushed.stdout+"\n"+pushed.stderr).lower()
        if any(token in combined for token in ("fetch first", "non-fast-forward", "stale info")):
            print(f"Main moved while publishing (attempt {attempt}); refreshing and recomputing.",
                  flush=True)
            if attempt < max_attempts:
                sleep_fn(min(2*attempt, 8))
            continue
        raise RuntimeError("Index push failed (not a retriable race): " +
                           (pushed.stderr or pushed.stdout)[-900:])
    raise RuntimeError(f"Main changed during all {max_attempts} publishing attempts. "
                       "No force push attempted; the next CI run may retry.")

if __name__ == "__main__":
    try:
        print(publish())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
