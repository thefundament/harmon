"""Exercise competing writers against a real local bare Git remote."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"collector"/"publish_indexes.py"
spec=importlib.util.spec_from_file_location("publish_indexes", SCRIPT)
publisher=importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

def git(cwd,*args):
    p=subprocess.run(["git",*args],cwd=cwd,text=True,capture_output=True)
    if p.returncode:
        raise RuntimeError(f"git {args}: {p.stderr}")
    return p.stdout.strip()

def setup(tmp):
    remote=tmp/"remote.git"
    remote.mkdir()
    git(tmp,"init","--bare",str(remote))
    git(remote,"symbolic-ref","HEAD","refs/heads/main")
    upstream=tmp/"upstream"
    git(tmp,"clone",str(remote),str(upstream))
    git(upstream,"config","user.name","Test User")
    git(upstream,"config","user.email","tester@example.com")
    (upstream/"source").mkdir()
    (upstream/"source"/"input.txt").write_text("one\n")
    git(upstream,"add",".")
    git(upstream,"commit","-m","initial")
    git(upstream,"branch","-M","main")
    git(upstream,"push","-u","origin","main")
    worker=tmp/"worker"
    git(tmp,"clone",str(remote),str(worker))
    git(worker,"config","user.name","Test User")
    git(worker,"config","user.email","tester@example.com")
    return remote,upstream,worker

def generate(root):
    data=(root/"source"/"input.txt").read_text()
    for name in ("quality","history","combined"):
        p=root/"corpus"/name
        p.mkdir(parents=True,exist_ok=True)
        (p/"index.txt").write_text(name+":"+data)

class IndexPublishTests(unittest.TestCase):
    def test_publish_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            remote,upstream,worker=setup(Path(td))
            self.assertEqual(publisher.publish(worker,regenerate_fn=generate,sleep_fn=lambda _:None),"published")
            rev=git(remote,"rev-parse","main")
            self.assertEqual(publisher.publish(worker,regenerate_fn=generate,sleep_fn=lambda _:None),"unchanged")
            self.assertEqual(git(remote,"rev-parse","main"),rev)
    def test_retries_after_new_source_commit_and_uses_latest_data(self):
        with tempfile.TemporaryDirectory() as td:
            remote,upstream,worker=setup(Path(td))
            calls=[]
            def concurrent_write(attempt):
                calls.append(attempt)
                if attempt==1:
                    with (upstream/"source"/"input.txt").open("a") as f:f.write("two\n")
                    git(upstream,"add","source/input.txt")
                    git(upstream,"commit","-m","source update while CI is working")
                    git(upstream,"push","origin","main")
            self.assertEqual(publisher.publish(worker,regenerate_fn=generate,
                before_push=concurrent_write,sleep_fn=lambda _:None),"published")
            self.assertEqual(calls,[1,2])
            self.assertEqual(git(remote,"show","main:corpus/combined/index.txt"),
                             "combined:one\ntwo")
            self.assertEqual(git(remote,"show","main:source/input.txt"),"one\ntwo")
    def test_no_force_push_and_all_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            remote,upstream,worker=setup(Path(td))
            def conflict(attempt):
                with (upstream/"source"/"input.txt").open("a") as f:f.write("update\n")
                git(upstream,"add","source/input.txt")
                git(upstream,"commit","-m",f"race {attempt}")
                git(upstream,"push","origin","main")
            with self.assertRaisesRegex(RuntimeError,"all 2 publishing attempts"):
                publisher.publish(worker,max_attempts=2,regenerate_fn=generate,
                    before_push=conflict,sleep_fn=lambda _:None)
            self.assertIn("update",git(remote,"show","main:source/input.txt"))

if __name__=="__main__":
    unittest.main()
