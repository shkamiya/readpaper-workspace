#!/usr/bin/env python3
"""Publication regression tests; temporary files only, no agent or live server."""
import pathlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import publish_html as publication
import ask_run


def page(text="original", image=""):
    return ('<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
            '<title>Paper</title></head><body><h1>Paper</h1>'
            '<div class="meta">claude-code session: <code>old-session</code></div>'
            f'<p>{text}</p>{image}</body></html>')


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = pathlib.Path(self.tmp.name)
        self.root = self.project / "html-expl"
        self.root.mkdir()
        self.target = self.root / "paper.html"
        self.target.write_text(page(), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self):
        return publication.prepare_edit(self.project, self.root, "paper.html")

    def test_edit_is_private_until_publish(self):
        work = self.edit()
        work.stage.write_text(page("updated"), encoding="utf-8")
        self.assertEqual(self.target.read_text(), page())
        self.assertNotIn(self.root, work.stage.parents)
        self.assertEqual(work.publish(), page("updated"))
        self.assertEqual(self.target.read_text(), page("updated"))

    def test_invalid_or_missing_draft_keeps_old_page(self):
        for draft in ("", "<h1>unfinished", page("$x$"),
                      page(image='<img src="figs/missing.png">')):
            with self.subTest(draft=draft):
                work = self.edit()
                work.stage.write_text(draft, encoding="utf-8")
                with self.assertRaises(publication.PublicationError):
                    work.publish()
                self.assertEqual(self.target.read_text(), page())
                self.assertTrue(work.stage.exists())
        work = self.edit()
        work.stage.unlink()
        with self.assertRaises(publication.PublicationError):
            work.publish()
        self.assertEqual(self.target.read_text(), page())

    def test_conflicting_edit_keeps_newer_public_version(self):
        first, second = self.edit(), self.edit()
        first.stage.write_text(page("first"))
        second.stage.write_text(page("second"))
        first.publish()
        with self.assertRaises(publication.PublicationError):
            second.publish()
        self.assertEqual(self.target.read_text(), page("first"))

    def test_generation_does_not_find_other_papers(self):
        other = self.root / "other.html"
        other.write_text(page('citation https://arxiv.org/abs/2601.12345'))
        before = other.read_bytes()
        work = publication.prepare_generation(self.project, "2601.12345")
        with self.assertRaises(publication.PublicationError):
            work.publish()
        self.assertFalse(work.target.exists())
        self.assertEqual(other.read_bytes(), before)
        work.stage.write_text(page("new"))
        work.publish()
        self.assertTrue(work.target.exists())
        self.assertEqual(other.read_bytes(), before)

    def test_generation_assets_use_new_paths_and_survive_publish(self):
        old = self.root / "figs/2601.12345/fig1.png"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old figure")
        work = publication.prepare_generation(self.project, "2601.12345")
        work.assets.mkdir(parents=True)
        (work.assets / "fig1.png").write_bytes(b"new figure")
        ref = work.asset_rel + "/fig1.png"
        work.stage.write_text(page("new", f'<img src="{ref}">'))
        work.publish()
        self.assertEqual(old.read_bytes(), b"old figure")
        self.assertEqual((self.root / ref).read_bytes(), b"new figure")

    def test_generation_name_comes_from_agent_slug(self):
        work = publication.prepare_generation(self.project, "2601.12345")
        work.slug_file.write_text("Dissecting HRM: mechanistic study\n")
        work.stage.write_text(page("new"))
        work.publish()
        date = work.record["date"]
        self.assertEqual(work.target.name, f"{date}-dissecting-hrm-mechanistic-study.html")
        self.assertTrue(work.target.exists())
        self.assertFalse(list(self.root.glob("*readpaper*")))

    def test_generation_name_falls_back_to_title_and_never_overwrites(self):
        date = None
        for expected in ("paper", "paper-2"):
            work = publication.prepare_generation(self.project, "2601.12345")
            date = work.record["date"]
            work.stage.write_text(page("new"))
            work.publish()
            self.assertEqual(work.target.name, f"{date}-{expected}.html")
        self.assertEqual(self.target.read_text(), page())

    def test_rejected_draft_does_not_publish_assets(self):
        work = publication.prepare_generation(self.project, "2601.12345")
        work.assets.mkdir(parents=True)
        (work.assets / "fig1.png").write_bytes(b"new figure")
        work.stage.write_text("<h1>unfinished")
        with self.assertRaises(publication.PublicationError):
            work.publish()
        self.assertFalse((self.root / work.asset_rel).exists())

    def test_existing_image_references_work_for_edit(self):
        asset = self.root / "figs/existing.png"
        asset.parent.mkdir()
        asset.write_bytes(b"image")
        work = self.edit()
        work.stage.write_text(page("updated", '<img src="figs/existing.png">'))
        work.publish()


class AskWorkerTest(unittest.TestCase):
    setUp = PublicationTest.setUp
    tearDown = PublicationTest.tearDown
    def test_worker_success_failure_and_timeout(self):
        for outcome in ("success", "exit_error", "invalid", "missing", "timeout", "reported_error", "conflict"):
            with self.subTest(outcome=outcome):
                self.target.write_text(page())
                jobs = self.project / "logs/ask-jobs"
                jobs.mkdir(parents=True, exist_ok=True)
                job_id = outcome
                jpath = jobs / f"{job_id}.json"
                job = {"job": job_id, "file": "paper.html", "mode": "auto",
                       "question": "explain", "state": "queued"}
                jpath.write_text(json.dumps(job))

                def agent(cmd, **kwargs):
                    record = json.loads(jpath.read_text())
                    work = publication.Publication(self.project / record["work_dir"])
                    self.assertIn(str(work.stage), " ".join(cmd))
                    self.assertEqual(self.target.read_text(), page())
                    if outcome == "missing":
                        work.stage.unlink()
                    else:
                        work.stage.write_text("<h1>broken" if outcome == "invalid" else page("answer"))
                    if outcome == "conflict":
                        self.target.write_text(page("newer external edit"))
                    if outcome == "timeout":
                        raise subprocess.TimeoutExpired(cmd, 1)
                    return subprocess.CompletedProcess(cmd, 1 if outcome == "exit_error" else 0,
                        json.dumps({"result": "answer", "is_error": outcome == "reported_error"}), "")

                argv = ["ask_run", "--job", job_id, "--project", str(self.project),
                        "--jobs", str(jobs), "--root", str(self.root)]
                with patch.object(sys, "argv", argv), \
                     patch.object(ask_run, "resolve_session", return_value=("", "claude-code", "", "")), \
                     patch.object(ask_run.shutil, "which", return_value="fixture-agent"), \
                     patch.object(ask_run.subprocess, "run", side_effect=agent), \
                     patch.object(ask_run, "notify"):
                    result = ask_run.main()
                final = json.loads(jpath.read_text())
                self.assertEqual(result, 0 if outcome == "success" else 1)
                self.assertEqual(final["state"], "done" if outcome == "success" else "error")
                expected = "answer" if outcome == "success" else "newer external edit" if outcome == "conflict" else "original"
                self.assertEqual(self.target.read_text(), page(expected))
                self.assertTrue((self.project / final["work_dir"] / "publication.json").exists())


    def test_worker_cancel(self):
        """中止 before the agent starts, and while it runs: never publishes."""
        for when in ("before_agent", "during_agent"):
            with self.subTest(when=when):
                ask_run.STOP.update(requested=False, publishing=False)
                self.target.write_text(page())
                jobs = self.project / "logs/ask-jobs"
                jobs.mkdir(parents=True, exist_ok=True)
                job_id = "cxl-" + when
                jpath = jobs / f"{job_id}.json"
                jpath.write_text(json.dumps({"job": job_id, "file": "paper.html", "mode": "auto",
                                             "question": "explain", "state": "queued"}))
                if when == "before_agent":
                    (jobs / f"{job_id}.cancel").write_text("1")
                calls = []

                def agent(cmd, **kwargs):
                    calls.append(cmd)
                    work = publication.Publication(self.project / json.loads(jpath.read_text())["work_dir"])
                    work.stage.write_text(page("answer"))
                    ask_run._on_term(15, None)          # what the server's killpg delivers
                    return subprocess.CompletedProcess(cmd, -15, "", "")

                argv = ["ask_run", "--job", job_id, "--project", str(self.project),
                        "--jobs", str(jobs), "--root", str(self.root)]
                with patch.object(sys, "argv", argv), \
                     patch.object(ask_run, "resolve_session", return_value=("", "claude-code", "", "")), \
                     patch.object(ask_run.shutil, "which", return_value="fixture-agent"), \
                     patch.object(ask_run.subprocess, "run", side_effect=agent), \
                     patch.object(ask_run, "KILL_GRACE", 0.01), \
                     patch.object(ask_run, "notify"):
                    result = ask_run.main()
                final = json.loads(jpath.read_text())
                self.assertEqual(result, 0)
                self.assertEqual(final["state"], "cancelled")
                self.assertEqual(self.target.read_text(), page("original"))
                self.assertEqual(len(calls), 0 if when == "before_agent" else 1)
                self.assertFalse((jobs / f"{job_id}.cancel").exists())
        ask_run.STOP.update(requested=False, publishing=False)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def test_prompt_after_cancel(self):
        jobs = self.project / "logs/ask-jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        (jobs / "a.json").write_text(json.dumps({"job": "a", "file": "paper.html", "created": 1,
                                                 "state": "cancelled", "resumed": True}))
        job = {"job": "b", "file": "paper.html", "created": 2, "mode": "auto", "question": "q"}
        self.assertTrue(ask_run.previous_cancelled(str(jobs), job))
        self.assertIn("中止", ask_run.build_prompt(job, "x.html", "", True, after_cancel=True))
        (jobs / "c.json").write_text(json.dumps({"job": "c", "file": "paper.html", "created": 1.5,
                                                 "state": "done"}))
        self.assertFalse(ask_run.previous_cancelled(str(jobs), job))


class GenerationWorkerTest(unittest.TestCase):
    def test_worker_backend_and_failure_matrix(self):
        source = pathlib.Path(__file__).resolve().parent
        repo = source.parents[2]
        for backend in ("claude", "codex"):
            for outcome in ("success", "exit_error", "invalid", "missing"):
                with self.subTest(backend=backend, outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                    project = pathlib.Path(tmp)
                    host = project / "tools/readpaper/host"
                    host.mkdir(parents=True)
                    for fname in ("readpaper_run.sh", "publish_html.py", "finalize_html.py", "check_html.py", "meta_store.py", "pos_store.py"):
                        shutil.copy2(source / fname, host / fname)
                    script = host / "readpaper_run.sh"
                    src = script.read_text()
                    start = src.index("notify() {")
                    end = src.index("\n}", start) + 2
                    # Suppress macOS notifications only; execute the real worker flow.
                    script.write_text(src[:start] + "notify() { :; }" + src[end:])
                    shutil.copy2(repo / "build_index.py", project / "build_index.py")
                    skills = project / "skills/explain-via-html"
                    skills.mkdir(parents=True)
                    (skills / "SKILL.md").write_text("fixture")
                    (project / "papers").mkdir()
                    (project / "papers/2601.12345.pdf").write_bytes(b"%PDF-fixture")
                    root = project / "html-expl"
                    root.mkdir()
                    other = root / "other.html"
                    other.write_text(page('citation https://arxiv.org/abs/2601.12345'))
                    other_before = other.read_bytes()
                    (host / "extract_figures.py").write_text(
                        'import pathlib,sys\np=pathlib.Path(sys.argv[2]);p.mkdir(parents=True)\n'
                        '(p/"manifest.json").write_text("[]")\n')
                    binary = project / "bin"
                    binary.mkdir()
                    fixture = binary / backend
                    fixture.write_text(f'#!{sys.executable}\n' + '''import json,os,pathlib,sys
root=pathlib.Path.cwd()
manifest=next((root/'logs/readpaper-work').glob('*/publication.json'))
rec=json.loads(manifest.read_text())
stage=manifest.parent/'html-expl'/rec['name']
assert str(stage) in ' '.join(sys.argv)
mode=os.environ['FIXTURE_OUTCOME']
sid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
if '--session-id' in sys.argv:sid=sys.argv[sys.argv.index('--session-id')+1]
body=os.environ['FIXTURE_PAGE'].replace('old-session','__AGENT_SESSION_ID__')
if mode!='missing':stage.write_text('<h1>unfinished' if mode=='invalid' else body)
if os.environ['READPAPER_AGENT']=='codex':
 print(json.dumps({'type':'thread.started','thread_id':sid}))
 print(json.dumps({'type':'turn.completed','usage':{}}))
else:print(json.dumps({'result':'fixture completed','is_error':False,'usage':{}}))
sys.exit(1 if mode=='exit_error' else 0)
''')
                    fixture.chmod(0o755)
                    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                               READPAPER_AGENT=backend, READPAPER_FORCE="1", READPAPER_CODEX_ARGS="",
                               FIXTURE_OUTCOME=outcome, FIXTURE_PAGE=page("new"))
                    result = subprocess.run(["/bin/bash", str(script), "https://example.test/p.pdf", "2601.12345"],
                                            env=env, capture_output=True, text=True, timeout=30)
                    logs = "\n".join(p.read_text() for p in (project / "logs").glob("readpaper-*.log"))
                    self.assertEqual(result.returncode, 0 if outcome == "success" else 1, logs)
                    self.assertEqual(other.read_bytes(), other_before)
                    generated = [p for p in root.glob("[0-9]*.html")]
                    self.assertEqual(len(generated), 1 if outcome == "success" else 0, logs)
                    if generated:
                        self.assertNotIn("__AGENT_SESSION_ID__", generated[0].read_text())
                        self.assertTrue((root / "index.html").exists())


if __name__ == "__main__":
    unittest.main()
