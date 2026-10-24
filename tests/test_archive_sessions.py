"""Session boundaries and publishing with durable results and real local Git."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from collector import local_worker as worker


class ArchiveSessionTests(unittest.TestCase):
    def setup_session(self, root, count):
        tasks = [{"kind": "clip", "task_id": f"clip:session-{i}",
                  "source_id": f"session-{i}",
                  "source_url": f"https://clips.twitch.tv/session-{i}",
                  "state": "pending"} for i in range(count)]
        manifest = root / "manifest.jsonl"
        manifest.write_text("".join(json.dumps(t) + "\n" for t in tasks))
        args = worker.parser().parse_args([
            "run", "--manifest", str(manifest), "--output", str(root / "corpus/local_transcripts"),
            "--work-dir", str(root / ".harmon-work"), "--min-free-gb", "0"])
        return tasks, args

    def write_result(self, task, tools, output, work, threads, *profile):
        output.mkdir(parents=True, exist_ok=True)
        meta, txt, srt = worker.result_paths(task, output)
        index = int(task["source_id"].rsplit("-", 1)[1])
        status = ("transcribed", "no_speech", "no_target_speech")[index % 3]
        txt.write_text("Synthetic session fixture" if status == "transcribed" else "")
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nSynthetic session fixture\n"
                       if status == "transcribed" else "")
        record = {"task_id": task["task_id"], "processing_status": status,
                  "recognized_words": len(txt.read_text().split()),
                  "text_sha256": hashlib.sha256(txt.read_bytes()).hexdigest(),
                  "srt_sha256": hashlib.sha256(srt.read_bytes()).hexdigest()}
        worker.atomic_json(meta, record)
        return record

    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                       stderr=subprocess.PIPE).strip()

    def setup_git(self, root, remote):
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "session-test")
        self.git(root, "config", "user.email", "session@example.invalid")
        (root / "README").write_text("Session test repository\n")
        (root / ".gitignore").write_text(".harmon-work/\nmanifest.jsonl\n")
        self.git(root, "add", "README", ".gitignore")
        self.git(root, "commit", "-m", "Initial fixture")
        self.git(root, "remote", "add", "origin", str(remote))
        if remote.exists():
            self.git(root, "push", "-u", "origin", "main")

    def test_two_hour_boundary_finishes_section_and_resume_skips_valid_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks, args = self.setup_session(root, 3)
            elapsed = [0]
            seen, checkpoints = [], []

            def process(*params):
                seen.append(params[0]["task_id"])
                record = self.write_result(*params)
                elapsed[0] += 7201  # Current section may run past the deadline.
                return record

            def checkpoint():
                checkpoints.append(sum(worker.completed(t, args.output) for t in tasks))

            with (mock.patch.object(worker, "OUTPUT", args.output),
                  mock.patch.object(worker, "find_tools", return_value={}),
                  mock.patch.object(worker, "process_task", side_effect=process),
                  mock.patch.object(worker.time, "monotonic", side_effect=lambda: elapsed[0]),
                  mock.patch.object(worker, "checkpoint", side_effect=checkpoint),
                  mock.patch.object(worker, "push_commits", return_value="fixture upload"),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(worker.execute(args), 0)
                self.assertEqual(seen, [tasks[0]["task_id"]])
                self.assertEqual(checkpoints, [1])
                self.assertTrue(worker.completed(tasks[0], args.output))
                elapsed[0] = 0
                self.assertEqual(worker.execute(args), 0)
                self.assertEqual(seen, [t["task_id"] for t in tasks[:2]])
                elapsed[0] = 0
                self.assertEqual(worker.execute(args), 0)
                self.assertEqual(seen, [t["task_id"] for t in tasks])
                # An unchanged frozen manifest/journal produces no duplicate work.
                self.assertEqual(worker.execute(args), 0)
                self.assertEqual(len(seen), 3)
                _, txt, _ = worker.result_paths(tasks[0], args.output)
                txt.write_text("corrupt")
                self.assertEqual(worker.execute(args), 0)
                self.assertEqual(seen[-1], tasks[0]["task_id"])
                self.assertEqual(len(seen), 4)

    def test_profile_change_stops_session_and_checkpoints_prior_results(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            tasks,args=self.setup_session(root,2)
            profile=root/"profile.json";profile.write_text("initial private profile")
            initial_hash=worker.sha256_file(profile)
            args.speaker_filter=True;args.speaker_profile=profile
            def process(*params):
                row=self.write_result(*params)
                row["voice_filter"]={"speaker_profile_sha256":initial_hash}
                worker.atomic_json(worker.result_paths(params[0],args.output)[0],row)
                profile.write_text("deliberately changed profile")
                return row
            with (mock.patch.object(worker,"OUTPUT",args.output),
                  mock.patch.object(worker,"find_tools",return_value={}),
                  mock.patch.object(worker,"load_profile",return_value=([],.9,{"_loaded_sha256":initial_hash})),
                  mock.patch.object(worker,"load_speaker_encoder"),
                  mock.patch.dict("sys.modules",{"webrtcvad":mock.Mock()}),
                  mock.patch.object(worker,"process_task",side_effect=process) as process_mock,
                  mock.patch.object(worker,"checkpoint") as checkpoint,
                  mock.patch.object(worker,"push_commits",return_value="fixture upload"),
                  contextlib.redirect_stdout(io.StringIO())):
                with self.assertRaisesRegex(RuntimeError,"profile changed"):
                    worker.execute(args)
                self.assertEqual(process_mock.call_count,1)
                self.assertEqual(checkpoint.call_count,1)
                self.assertTrue(worker.completed(tasks[0],args.output))
                self.assertFalse(worker.completed(tasks[1],args.output))

    def test_missing_profile_fails_before_tools_or_downloads(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _,args=self.setup_session(root,1)
            args.speaker_filter=True;args.speaker_profile=root/"missing-profile.json"
            with mock.patch.object(worker,"find_tools") as tools:
                with self.assertRaisesRegex(RuntimeError,"No calibrated speaker profile"):
                    worker.execute(args)
                tools.assert_not_called()

    def test_uploads_at_25_completed_outputs_and_session_end_with_real_git(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "checkout"
            root.mkdir()
            remote = base / "remote.git"
            subprocess.check_output(["git", "init", "--bare", "-b", "main", str(remote)])
            self.setup_git(root, remote)
            tasks, args = self.setup_session(root, 53)
            failed_id = tasks[-1]["task_id"]
            # Interleave a failed attempt so checkpoints count outputs, not attempts.
            tasks.insert(7, tasks.pop())
            args.manifest.write_text("".join(json.dumps(t) + "\n" for t in tasks))
            uploaded = []
            real_push = worker.push_commits

            def process(task, *params):
                if task["task_id"] == failed_id:
                    raise RuntimeError("fixture unavailable source")
                return self.write_result(task, *params)

            def push():
                result = real_push()
                uploaded.append(sum(worker.completed(t, args.output) for t in tasks))
                return result

            with (mock.patch.object(worker, "REPO", root),
                  mock.patch.object(worker, "OUTPUT", args.output),
                  mock.patch.object(worker, "find_tools", return_value={}),
                  mock.patch.object(worker, "process_task", side_effect=process),
                  mock.patch.object(worker, "push_commits", side_effect=push),
                  contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
                self.assertEqual(worker.execute(args), 0)
            self.assertEqual(uploaded, [25, 50, 52])
            self.assertEqual(self.git(root, "rev-list", "--count", "HEAD"), "4")
            self.assertEqual(self.git(root, "rev-parse", "HEAD"),
                             self.git(remote, "rev-parse", "main"))
            self.assertEqual(self.git(root, "status", "--porcelain"), "")
            journal = worker.load_journal(args.work_dir / "state.json")
            self.assertEqual(journal[failed_id]["attempts"], 1)

    def test_failed_upload_retains_commits_and_retries_next_session_without_new_work(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "checkout"
            root.mkdir()
            remote = base / "offline.git"
            self.setup_git(root, remote)  # Missing local remote simulates an offline upload.
            tasks, args = self.setup_session(root, 27)
            with (mock.patch.object(worker, "REPO", root),
                  mock.patch.object(worker, "OUTPUT", args.output),
                  mock.patch.object(worker, "find_tools", return_value={}),
                  mock.patch.object(worker, "process_task", side_effect=self.write_result) as process,
                  mock.patch.object(worker, "push_commits", wraps=worker.push_commits) as push,
                  contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
                self.assertEqual(worker.execute(args), 0)
                self.assertEqual(push.call_count, 1)
                self.assertEqual(self.git(root, "rev-list", "--count", "HEAD"), "3")
                self.assertTrue(all(worker.completed(t, args.output) for t in tasks))
                local_head = self.git(root, "rev-parse", "HEAD")
                subprocess.check_output(["git", "init", "--bare", "-b", "main", str(remote)])
                # Give the recovered remote its initial shared ancestor.
                self.git(root, "push", "origin", "HEAD~2:refs/heads/main")
                self.assertEqual(worker.execute(args), 0)
                self.assertEqual(process.call_count, 27)
                self.assertEqual(push.call_count, 2)
                self.assertEqual(self.git(remote, "rev-parse", "main"), local_head)
                self.assertEqual(self.git(root, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
