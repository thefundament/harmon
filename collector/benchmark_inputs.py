"""Prepare only the frozen, bounded local STT comparison (never the archive batch)."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import wave
import zipfile

try:
    from . import local_worker as worker
except ImportError:
    import local_worker as worker

ROOT = worker.REPO
WORK = ROOT / ".harmon-work/stt-comparison"
REPORT = ROOT / "docs/stt_benchmark"
LOCK = REPORT / "lock.json"
SEED = "harmon-stt-v1"


def command(argv, timeout=600):
    proc = subprocess.Popen([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True,
                            env={**os.environ, "SPEECH_NO_UPDATE_CHECK": "1"})
    try:
        out, err = proc.communicate(timeout=timeout)
        if proc.returncode:
            raise RuntimeError(f"{Path(str(argv[0])).name} exit {proc.returncode}: {err[-1800:]}")
        return out, err
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()


def download(url, path, expected=None, size=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and (not size or path.stat().st_size == size):
        if expected is None or worker.sha256_file(path) == expected:
            return
    partial = path.with_name(path.name + ".part")
    # A bounded transfer; resume only a partial of the same pinned URL.
    command(["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "2",
             "--connect-timeout", "30", "--max-time", "900", "--continue-at", "-",
             "--output", partial, url], timeout=1000)
    if size and partial.stat().st_size != size:
        raise RuntimeError("Downloaded size mismatch: " + str(path))
    if expected and worker.sha256_file(partial) != expected:
        partial.unlink(missing_ok=True)
        raise RuntimeError("Downloaded SHA256 mismatch: " + str(path))
    os.replace(partial, path)


def provision(fallback=False):
    lock = json.loads(LOCK.read_text())
    runtime = lock["runtimes"]["speech.cpp"]
    executable = ROOT / runtime["executable"]
    if not executable.is_file():
        archive = WORK / "setup/speech-0.8.11-linux-x64-vulkan.zip"
        download(runtime["archive_url"], archive, runtime["archive_sha256"])
        executable.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            if any(Path(x).is_absolute() or ".." in Path(x).parts for x in source.namelist()):
                raise RuntimeError("Unsafe release archive")
            source.extractall(executable.parent)
        executable.chmod(0o755)
    version, _ = command([executable, "--version"])
    if "speech.cpp 0.8.11," not in version:
        raise RuntimeError("Unexpected speech.cpp version: " + version)
    if worker.sha256_file(executable) != runtime["executable_sha256"]:
        raise RuntimeError("speech.cpp executable checksum mismatch")
    for model in lock["models"]:
        if model["fallback"] != fallback:
            continue
        print("PROVISION", model["id"], flush=True)
        download(model["url"], ROOT / model["path"], model["sha256"], model["bytes"])


def select_ids(dataset, ids):
    if len(set(ids)) != len(ids) or len(ids) < 100:
        raise RuntimeError("Dataset IDs must be unique and supply at least 100 utterances")
    return sorted(ids, key=lambda x: hashlib.sha256(f"{SEED}\0{dataset}\0{x}".encode()).digest())[:100]


def sample_id(dataset, row):
    value = str(row[dataset["id_column"]])
    return Path(value).name if dataset["id_column"] == "path" else value


def audio_record(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise RuntimeError("Expected mono 16-kHz PCM16")
        seconds = audio.getnframes() / 16000
    return {"path": str(path.relative_to(ROOT)), "audio_seconds": seconds,
            "audio_sha256": worker.sha256_file(path)}


def prepare_references():
    # pyarrow exists only in .harmon-tools/benchmark-venv, never production .venv.
    import pyarrow.parquet as pq
    lock = json.loads(LOCK.read_text())
    frozen_path = REPORT / "reference_samples.json"
    frozen = json.loads(frozen_path.read_text()) if frozen_path.exists() else None
    datasets, plans = [], []
    # Freeze both revisions, all sample IDs and references before invoking any model.
    for ds in lock["datasets"]:
        parquet = WORK / "datasets" / (ds["id"] + ".parquet")
        download(f"https://huggingface.co/datasets/{ds['repository']}/resolve/{ds['revision']}/{ds['file']}", parquet)
        table = pq.read_table(parquet, columns=[ds["id_column"], ds["text_column"]])
        rows = table.to_pylist()
        selected = select_ids(ds["id"], [sample_id(ds, row) for row in rows])
        by_id = {sample_id(ds, row): row for row in rows}
        samples = [{"id": sample, "reference": by_id[sample][ds["text_column"]]} for sample in selected]
        datasets.append({**ds, "parquet_sha256": worker.sha256_file(parquet), "samples": samples})
        plans.append((ds, parquet, set(selected)))
    new_frozen = {"schema_version": 1, "sampling": "100 smallest SHA256(seed NUL dataset NUL id)",
                  "seed": SEED, "datasets": datasets}
    if frozen is not None and frozen != new_frozen:
        raise RuntimeError("Frozen reference sample manifest changed; refusing to replace it")
    worker.atomic_json(frozen_path, new_frozen)
    records = []
    for ds, parquet, selected in plans:
        audio_dir = WORK / "audio" / ds["id"]
        audio_dir.mkdir(parents=True, exist_ok=True)
        # Small batches bound memory even when parquet embeds long recordings.
        for batch in pq.ParquetFile(parquet).iter_batches(batch_size=8):
            for row in batch.to_pylist():
                utterance_id = sample_id(ds, row)
                if utterance_id not in selected:
                    continue
                wav = audio_dir / (hashlib.sha256(utterance_id.encode()).hexdigest()[:20] + ".wav")
                if not wav.exists():
                    with tempfile.TemporaryDirectory(dir=WORK) as temp:
                        source = Path(temp) / "encoded-audio"
                        data = row["audio"]["bytes"]
                        if not data:
                            raise RuntimeError("Dataset row lacks embedded audio")
                        source.write_bytes(data)
                        command(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                                 "-i", source, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], 60)
                records.append({"id": ds["id"] + ":" + utterance_id, "dataset": ds["id"],
                                "reference": row[ds["text_column"]], **audio_record(wav)})
        print("REFERENCES", ds["id"], len(selected), flush=True)
    if len(records) != 200:
        raise RuntimeError("Reference preparation did not produce exactly 200 utterances")
    return sorted(records, key=lambda r: r["id"])


def corpus_tasks():
    frozen = json.loads(LOCK.read_text())["corpus"]
    path = ROOT / frozen["manifest"]
    if worker.sha256_file(path) != frozen["manifest_sha256"]:
        raise RuntimeError("Frozen comparison source manifest changed")
    return worker.load_manifest(path)


def authorize_source(task, yt_dlp):
    try:
        from .source_gate import authorize
    except ImportError:
        from source_gate import authorize
    return authorize(task, yt_dlp, WORK / "source_checks")


def prepare_corpus():
    ledger_path = REPORT / "source_attempts.json"
    original_inventory = json.loads(LOCK.read_text())["corpus"]["original_inventory_sha256"]
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {
        "manifest_sha256": original_inventory, "maximum_source_attempts": 10, "attempts": []}
    if ledger["manifest_sha256"] != original_inventory:
        raise RuntimeError("Frozen inventory changed")
    done = {r["id"]: r for r in ledger["attempts"]}
    yt = ROOT / ".venv/bin/yt-dlp"
    for task in corpus_tasks():
        if task["task_id"] in done:
            continue
        if len(ledger["attempts"]) >= 10:
            break
        record = {"id": task["task_id"], "dataset": "corpus", "task": task,
                  "status": "started", "speaker_verified": False,
                  "training_permission_verified": False, "training_ready": False}
        ledger["attempts"].append(record)
        worker.atomic_json(ledger_path, ledger)  # Count failures and interrupted attempts too.
        tick = time.monotonic()
        try:
            record["source_owner_evidence"] = authorize_source(task, yt)
            with tempfile.TemporaryDirectory(prefix="source-", dir=WORK) as temp:
                temp = Path(temp)
                argv = [yt, "--no-playlist", "--no-progress", "--no-part", "--socket-timeout", "20",
                        "--retries", "2", "--fragment-retries", "2", "--concurrent-fragments", "1",
                        "--max-filesize", "384M", "-f", "worstaudio/worst/bestaudio/best",
                        "-o", temp / "source.%(ext)s"]
                start, end = task.get("start_s"), task.get("end_s")
                if start is not None:
                    argv += ["--download-sections", f"*{start:g}-{end:g}"]
                command([*argv, task["source_url"]], 300 if start is not None else 150)
                media = next(temp.glob("source.*"))
                wav = WORK / "audio/corpus" / (worker.task_key(task) + ".wav")
                wav.parent.mkdir(parents=True, exist_ok=True)
                # Full ten-minute long-form windows; no short comparison excerpts.
                limit = end - start if start is not None else 180
                command(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", media,
                         "-vn", "-t", str(limit), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], 120)
                info = audio_record(wav)
                if start is not None and info["audio_seconds"] < limit - 2:
                    wav.unlink()
                    raise RuntimeError("Incomplete long-form window")
                record.update(status="ok", media_sha256=worker.sha256_file(media), **info)
        except Exception as exc:
            record.update(status="failed", error=str(exc))
        record["download_conversion_seconds"] = time.monotonic() - tick
        worker.atomic_json(ledger_path, ledger)
        print("SOURCE", len(ledger["attempts"]), record["id"], record["status"], flush=True)
    return [r for r in ledger["attempts"] if r["status"] == "ok"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("provision", "fallback", "prepare", "cleanup"))
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    # Share the comparison lock, so setup/cleanup cannot race a live benchmark.
    with (WORK / "comparison.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command in ("provision", "fallback"):
            provision(args.command == "fallback")
        elif args.command == "prepare":
            tick = time.monotonic()
            references = prepare_references()
            corpus = prepare_corpus()
            worker.atomic_json(REPORT / "inputs.json", {"schema_version": 1, "samples": references + corpus,
                               "preparation_seconds_excluded_from_model_timing": time.monotonic() - tick})
        else:
            import shutil
            for folder in (WORK / "audio", WORK / "regions", WORK / "datasets"):
                if folder.is_symlink():
                    raise RuntimeError("Refusing cleanup through symlink")
                if folder.exists():
                    shutil.rmtree(folder)


if __name__ == "__main__":
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    main()
