#!/usr/bin/env python3
"""Detached worker for one in-page-chat request.

Spawned by ask_server.py; never run interactively except for debugging.

    ask_run.py --job <id> --project <repo> --jobs <dir> --root <html-expl>

Pipeline, in order:

    snapshot -> resolve session -> agent -> check_html --fix -> diff -> notify

WHY PYTHON AND NOT BASH
-----------------------
The sibling worker readpaper_run.sh is bash, and tools/readpaper/README.md
records what that costs: a single apostrophe inside a `<<'PYEOF'` heredoc broke
the whole script, inside a detached Chrome-spawned process where nobody sees the
error. Here the input is *arbitrary text typed by SK into a web form*. Building a
shell command around that is not defensible, so the prompt is assembled in Python
and handed to the agent as one argv element -- subprocess without shell=True does
no word splitting, no glob expansion and no quote parsing.

WHY THE SNAPSHOT IS NOT OPTIONAL
--------------------------------
html-expl/ is gitignored (`.gitignore: html-expl/*`), so there is no `git
checkout` to fall back on. If the agent mangles a page, the snapshot taken here
is the only copy of the previous version that exists. A snapshot failure
therefore aborts the run before the agent starts.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meta_store  # noqa: E402
import publish_html  # noqa: E402

AGENT_TIMEOUT = 1800          # 30 min; a measured run is ~5 min
KILL_GRACE = 5                # SIGTERM -> SIGKILL for an agent that ignores it
SNAPSHOT_GENERATIONS = 10     # per explainer

# The line-1 finalize stamp, and the visible id line under the title. Both are
# needed: of 131 explainers, 80 carry the stamp and 51 carry only the visible
# line (measured 2026-08-22).
STAMP_RE = re.compile(r"(claude-code|codex|agent) session_id:\s*(\S+)")
VISIBLE_RE = re.compile(r"(?:claude-code|codex|agent) session:\s*<code>([^<]+)</code>")
SOURCE_RE = re.compile(r"source:\s*(papers/\S+\.pdf)")
JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
ID_RE = re.compile(r'id="([^"]+)"')


def log(msg: str) -> None:
    sys.stderr.write(f"[ask] {msg}\n")
    sys.stderr.flush()


def notify(msg: str) -> None:
    """macOS notification.

    The message is handed to an `on run argv` handler as an argument instead of
    being interpolated into the script source. Interpolating it does not work:
    json.dumps() defaults to ensure_ascii=True and emits \\uXXXX escapes, which
    AppleScript does not understand -- it accepts only \\" \\\\ \\n \\t. Since every
    summary here is Japanese and every mark is a check or warning sign, *every*
    notification failed with `syntax error: Expected quote but found unknown token`
    and the run finished silently (observed on both 2026-08-26 jobs). Passing via
    argv also removes any question of quoting for text that came from a web form.
    """
    try:
        subprocess.run(
            ["/usr/bin/osascript",
             "-e", "on run argv",
             "-e", 'display notification (item 1 of argv) with title (item 2 of argv) '
                   'sound name "Glass"',
             "-e", "end run",
             " ".join(msg.split()), "readpaper ask"],
            check=False, timeout=10, capture_output=True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# cancellation (the widget's 中止 / Ctrl+C -> POST /api/cancel)
# --------------------------------------------------------------------------
# ask_server.py spawns this worker with start_new_session=True, so the worker is
# its process group leader and the agent CLI it runs is in the same group. To
# cancel, the server writes <job>.cancel and sends SIGTERM to the whole group:
# the agent dies, subprocess.run() returns, and the flag set below tells main()
# to stop instead of publishing. The marker covers a cancel that lands before
# the worker installs its handler.
#
# Once publishing has started the cancel is ignored: publish() is the step that
# swaps the public page, and dying half way through it is the one outcome worse
# than letting an unwanted answer land (the snapshot can undo that).
STOP = {"requested": False, "publishing": False}


def _kill_rest_of_group() -> None:
    """Agents that ignore SIGTERM: SIGKILL everything in our group but us."""
    # Only as group leader, i.e. spawned by ask_server.py. Run by hand or under
    # a test runner the group is the caller's shell job, which is not ours.
    if STOP["publishing"] or os.getpgid(0) != os.getpid():
        return
    try:
        out = subprocess.run(["/usr/bin/pgrep", "-g", str(os.getpid())],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return
    for tok in out.split():
        pid = int(tok)
        if pid != os.getpid():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _on_term(signum, frame) -> None:
    if STOP["publishing"] or STOP["requested"]:
        return
    STOP["requested"] = True
    t = threading.Timer(KILL_GRACE, _kill_rest_of_group)
    t.daemon = True
    t.start()


def cancel_requested(jpath: str) -> bool:
    if STOP["publishing"]:
        return False
    if os.path.exists(jpath[:-5] + ".cancel"):
        STOP["requested"] = True
    return STOP["requested"]


def previous_cancelled(jobs: str, job: dict) -> bool:
    """Was the job just before this one on the same paper cancelled?

    A cancelled run that resumed a session leaves the mistaken prompt in that
    session's transcript, so the next resume would see it. Claude Code's own
    Ctrl+C behaves the same way; here the agent is told to disregard it.
    """
    prev = None
    for name in os.listdir(jobs):
        if not name.endswith(".json") or name[:-5] == job["job"]:
            continue
        try:
            with open(os.path.join(jobs, name), encoding="utf-8") as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            continue
        if j.get("file") == job["file"] and j.get("created", 0) < job.get("created", 0):
            if prev is None or j.get("created", 0) > prev.get("created", 0):
                prev = j
    return bool(prev and prev.get("state") == "cancelled" and prev.get("resumed"))


# --------------------------------------------------------------------------
# session resolution
# --------------------------------------------------------------------------
def transcript_dir(project: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]", "-", project)
    return os.path.expanduser(f"~/.claude/projects/{slug}")


def alive(sid: str, backend: str, project: str) -> bool:
    """Is this session still resumable?

    A transcript older than cleanupPeriodDays is deleted and `--resume` on a
    missing id fails, so this is checked before every run rather than discovered
    by burning one. Codex keeps its rollouts elsewhere and no explainer carries a
    codex stamp yet, so its ids are taken on faith.
    """
    if not sid or "__" in sid:          # unfilled placeholder
        return False
    if backend == "codex":
        return True
    return os.path.isfile(os.path.join(transcript_dir(project), f"{sid}.jsonl"))


# -- adopted sessions ------------------------------------------------------
# When the session that wrote a paper is gone, the run falls back to a fresh one.
# That new session then holds the whole exchange -- the html it read, the question,
# its own answer -- so the *next* question should continue it instead of starting
# from nothing again. Without this, every question on an old paper re-read the page
# and had no memory of the previous one (observed 2026-08-26 on the June paper
# 20260623-latent-cot-think-step-by-step.html: two questions, two cold sessions).
#
# The adopted id is kept beside the jobs rather than written back into the html:
# the id printed in the page header means "the session that wrote this page", which
# is provenance, and overwriting it would destroy that.

def store_path(project: str) -> str:
    return os.path.join(project, "logs", "ask-jobs", "sessions.json")


def read_store(project: str) -> dict:
    try:
        with open(store_path(project), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def remember_session(project: str, fname: str, sid: str, backend: str) -> None:
    """Record the live session for this paper, under a lock.

    Different papers run concurrently, so this is a real read-modify-write race.
    The per-file busy guard in ask_server.py only serialises jobs on the *same*
    paper, and they would all write this one file.
    """
    if not sid:
        return
    path = store_path(project)
    lock = path + ".lock"
    fd = None
    for _ in range(50):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # A worker that died holding the lock must not wedge the store.
            try:
                if time.time() - os.path.getmtime(lock) > 30:
                    os.unlink(lock)
                    continue
            except OSError:
                pass
            time.sleep(0.1)
    if fd is None:
        log("WARN could not lock sessions.json; skipping session adoption")
        return
    try:
        store = read_store(project)
        store[fname] = {"sid": sid, "backend": backend, "updated": time.time()}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    finally:
        os.close(fd)
        try:
            os.unlink(lock)
        except OSError:
            pass


def resolve_session(html: str, project: str) -> tuple[str, str, str, str]:
    """-> (session_id, backend, pdf_or_empty, origin).

    origin is "adopted" (a previous ask's session), "stamp" (the session that
    wrote the page) or "" (nothing usable -- the caller starts a fresh session).
    Adopted wins: it is the more recent conversation and it already contains the
    page, whereas the stamped session predates every question asked since.
    """
    src = open(html, encoding="utf-8", errors="replace").read()
    first = src.split("\n", 1)[0]
    pdf = ""
    m = STAMP_RE.search(first)
    if m:
        backend, sid = m.group(1), m.group(2)
        s = SOURCE_RE.search(first)
        pdf = s.group(1) if s else ""
    else:
        v = VISIBLE_RE.search(src[:20000])
        backend, sid = "claude-code", (v.group(1) if v else "")

    entry = read_store(project).get(os.path.basename(html)) or {}
    adopted, abackend = entry.get("sid", ""), entry.get("backend") or backend
    if adopted and adopted != sid and alive(adopted, abackend, project):
        log(f"resuming adopted session {adopted}")
        return adopted, abackend, pdf, "adopted"

    if alive(sid, backend, project):
        return sid, backend, pdf, "stamp"
    if sid:
        log(f"transcript gone for {sid}; falling back to a fresh session")
    return "", backend, pdf, ""


# --------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------
ROUTING_AUTO = """CLAUDE.md のハウスルールを読み、次のどちらかを判断して実行せよ:
  - 単なる質問  → §2 に従い末尾 Q&A 節（タグを問わず id="qa" の要素）に日付つき小見出しで追記する（既存 Q&A は消さない）
  - 本文への指示 → 該当箇所の本文を直接編集する（§1 の数式規約・§1.7 の核心概念補足規約を守る）"""

ROUTING_QA = """CLAUDE.md のハウスルールを読み、§2 に従って末尾 Q&A 節（タグを問わず
id="qa" の要素）に日付つき小見出しで回答を追記せよ。既存 Q&A は消さないこと。
本文は編集しないこと。"""

ROUTING_EDIT = """CLAUDE.md のハウスルールを読み、指定箇所の本文を直接編集せよ
（§1 の数式規約・§1.7 の核心概念補足規約を守ること）。Q&A 節には追記しないこと。"""


def reading_marks_lines(root: str, fname: str) -> list[str]:
    """SK's 理解度 / ひとことまとめ for this paper, as prompt lines. Read-only: the
    agent is told so explicitly. [] when nothing is set (prompt unchanged)."""
    try:
        rec = meta_store.read(root, meta_store.stem_of(fname))
    except ValueError:
        return []
    out = []
    if rec.get("level") in meta_store.LEVEL_LABEL:
        out.append(f"SK の現在の理解度: {meta_store.LEVEL_LABEL[rec['level']]}")
    if rec.get("note"):
        out.append(f"SK のひとことまとめ: 「{rec['note']}」")
    if out:
        out.append("（上の欄は SK 自身の言葉。回答の粒度の参考にし、まとめに誤解があれば指摘してよい。"
                   "html-expl/meta/ 配下は SK の欄なので編集してはならない）")
    return out


def build_prompt(job: dict, html_rel: str, pdf: str, resumed: bool,
                 marks: list[str] | None = None, after_cancel: bool = False) -> str:
    routing = {"qa": ROUTING_QA, "edit": ROUTING_EDIT}.get(job["mode"], ROUTING_AUTO)
    lines = [f"SK からの指示: 「{job['question']}」", f"対象ファイル: {html_rel}",
             "対象は非公開の作業コピー。今回はこのパスだけを編集すること。"
             "以前の会話にある公開用HTMLへ書き込んではならない。",
             "画像URLは公開先html-expl/を基準にした既存の相対パスを保つこと。"
             "画像の確認は公開先の既存画像を読み取り専用で行う。"
             "公開・一覧更新・ブラウザで開く処理はworkerが担当するため実行しないこと。"]
    lines += marks or []
    if after_cancel:
        lines.append("（この会話の直前の指示は SK が途中で中止したもの。誤送信なので無視し、"
                     "それに基づく編集が残っていても今回の作業対象ではない）")
    anchor = job.get("anchor") or {}
    if anchor.get("id"):
        lines.append(f"SK が見ていた場所: id=\"{anchor['id']}\" "
                     f"（見出し: {anchor.get('text', '')}）")
    if job.get("selection"):
        lines.append(f"SK が選択していた文: \"{job['selection']}\"")
    if not resumed:
        # No transcript: the agent has no memory of this paper at all.
        lines.append("")
        lines.append(f"この論文を書いたセッションは既に失われている。まず {html_rel} を読み、"
                     + (f"必要なら {pdf} も参照して" if pdf else "")
                     + "内容を把握してから作業せよ。")
    lines += ["", routing, "",
              "このファイル以外を編集してはならない。ページ全体の再生成もしてはならない。",
              "対象ファイル以外の探索は最小限にすること（ターン数がそのままコストになる）。",
              "完了後に次の JSON を最終出力に含めよ:",
              '  {"mode":"qa"|"edit", "targets":["#id", ...], "summary":"1 行の変更要約"}']
    return "\n".join(lines)


# --------------------------------------------------------------------------
# diff -> changed anchors
# --------------------------------------------------------------------------
def changed_anchors(before: str, after: str) -> tuple[list[str], int, int]:
    """Anchors actually touched, plus (+added, -removed) line counts.

    Measured 2026-08-22: the agent's self-reported `targets` under-reports. It
    said ["#s4"] while also rewriting the glossary in a separate hunk. So the
    highlight list is derived from the diff, not from what the agent claims.
    """
    a, b = before.splitlines(), after.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ids, added, removed = [], 0, 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        added += j2 - j1
        removed += i2 - i1
        for k in range(j1, -1, -1):     # nearest id="" at or above the change
            m = ID_RE.search(b[k]) if k < len(b) else None
            if m:
                if m.group(1) not in ids:
                    ids.append(m.group(1))
                break
    return ids, added, removed


def prune_snapshots(dirpath: str, stem: str) -> None:
    keep = sorted((f for f in os.listdir(dirpath)
                   if f.startswith(stem + ".") and f.endswith(".html")), reverse=True)
    for old in keep[SNAPSHOT_GENERATIONS:]:
        try:
            os.remove(os.path.join(dirpath, old))
        except OSError:
            pass


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--root", required=True)
    args = ap.parse_args()
    args.project = os.path.realpath(args.project)
    args.root = os.path.realpath(args.root)
    args.jobs = os.path.realpath(args.jobs)

    jpath = os.path.join(args.jobs, f"{args.job}.json")
    with open(jpath, encoding="utf-8") as fh:
        job = json.load(fh)

    def save(**kw):
        job.update(kw)
        tmp = jpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(job, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, jpath)

    def fail(msg: str) -> int:
        log("FAIL " + msg)
        save(state="error", phase=msg, finished=time.time())
        notify(f"✗ {msg}")
        return 1

    signal.signal(signal.SIGTERM, _on_term)

    def cancelled() -> int:
        log("cancelled by user")
        _kill_rest_of_group()   # tool processes the agent left behind
        save(state="cancelled", phase="中止しました", finished=time.time())
        try:
            os.unlink(jpath[:-5] + ".cancel")
        except OSError:
            pass
        return 0

    html = os.path.join(args.root, job["file"])
    if cancel_requested(jpath):
        return cancelled()
    save(state="running", phase="スナップショット中…", started=time.time(),
         pid=os.getpid())

    # 1. snapshot -- the only safety net, so a failure here aborts the run.
    stem = job["file"][:-5]
    snapdir = os.path.join(args.project, "logs", "qa-backups")
    try:
        work = publish_html.prepare_edit(Path(args.project), Path(args.root), job["file"])
        save(work_dir=os.path.relpath(work.workdir, args.project))
        os.makedirs(snapdir, exist_ok=True)
        snap = os.path.join(snapdir, f"{stem}.{time.strftime('%Y%m%d-%H%M%S')}.{args.job}.html")
        shutil.copy2(work.baseline, snap)
        before = work.baseline.read_text(encoding="utf-8")
    except (OSError, ValueError, publish_html.PublicationError) as e:
        return fail(f"snapshot failed: {e}")
    prune_snapshots(snapdir, stem)
    log(f"snapshot {os.path.basename(snap)}")

    # 2. session
    sid, backend, pdf, origin = resolve_session(html, args.project)
    resumed = bool(sid)
    prompt = build_prompt(job, str(work.stage), pdf, resumed,
                          reading_marks_lines(args.root, job["file"]),
                          after_cancel=resumed and previous_cancelled(args.jobs, job))

    agent = os.environ.get("READPAPER_CLAUDE") or shutil.which("claude")
    if backend == "codex":
        agent = os.environ.get("READPAPER_CODEX") or shutil.which("codex")
    if not agent:
        return fail(f"agent CLI not found for backend {backend}")

    if backend == "codex":
        cmd = [agent, "exec", "--json", "--dangerously-bypass-approvals-and-sandbox"]
        if resumed:
            cmd = [agent, "exec", "resume", "--skip-git-repo-check", sid,
                   "--json", "--dangerously-bypass-approvals-and-sandbox"]
        cmd.append(prompt)
    else:
        cmd = [agent]
        if resumed:
            # No --fork-session: verified 2026-08-22 that the session id is then
            # preserved, which is what keeps the html's stamp valid forever.
            cmd += ["--resume", sid]
        cmd += ["-p", prompt, "--output-format", "json",
                "--dangerously-skip-permissions"]

    save(phase=("これまでのやりとりを引き継いで考えています…" if origin == "adopted"
                else "セッションを再開して考えています…" if resumed
                else "新規セッションで論文を読んでいます…"),
         resumed=resumed, session_id=sid, backend=backend, session_origin=origin)
    log(f"agent={os.path.basename(agent)} origin={origin or 'fresh'} "
        f"sid={sid or '(none)'}")

    if cancel_requested(jpath):
        return cancelled()
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=args.project, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=AGENT_TIMEOUT)
    except subprocess.TimeoutExpired:
        if cancel_requested(jpath):
            return cancelled()
        return fail(f"agent timed out after {AGENT_TIMEOUT}s")
    except OSError as exc:
        return fail(f"agent could not start: {exc}")
    elapsed = round(time.time() - t0)
    if cancel_requested(jpath):
        # Not adopted either: a fresh run killed mid-way never printed its id,
        # and a resumed one keeps the id it already had.
        return cancelled()
    log(f"agent exit={proc.returncode} elapsed={elapsed}s")
    if proc.stderr.strip():
        log("stderr: " + proc.stderr.strip()[:2000])

    # 3. parse. The agent returns prose followed by a ```json fence -- measured
    #    on both Phase 0 runs -- so the whole result is not valid JSON.
    mode, targets, summary, cost = job["mode"], [], "", None
    ran_sid = ""
    agent_error = False
    try:
        out = json.loads(proc.stdout)
        agent_error = bool(out.get("is_error"))
        result_text = out.get("result", "") or ""
        cost = out.get("total_cost_usd")
        # The id the run actually used. For a resume it equals `sid` (no
        # --fork-session); for a fresh run it is the newly minted session, which is
        # the whole point of adopting it here.
        ran_sid = str(out.get("session_id") or "")
    except ValueError:
        result_text = proc.stdout
    fences = JSON_FENCE_RE.findall(result_text)
    if fences:
        try:
            rep = json.loads(fences[-1])
            mode = rep.get("mode", mode)
            targets = rep.get("targets", []) or []
            summary = (rep.get("summary") or "").strip()
        except ValueError:
            pass
    if not summary:
        summary = " ".join(result_text.split())[-200:] or "(要約なし)"

    # Adopt before the exit check: a run that produced a session but then failed
    # still leaves a session worth continuing, and re-reading the page from cold
    # on the retry would be pure waste.
    if ran_sid:
        remember_session(args.project, job["file"], ran_sid, backend)
        if ran_sid != sid:
            log(f"adopted session {ran_sid} for {job['file']}")

    if proc.returncode != 0 or agent_error:
        return fail(f"agent exit {proc.returncode}: {summary[:120]}")

    # 4. math check. The agent runs this itself per CLAUDE.md §1.4; this is the
    #    second layer, and the one whose verdict the user actually sees.
    STOP["publishing"] = True
    save(phase="作業版を検査して公開しています…", stage="publish")
    try:
        after = work.publish()
    except publish_html.PublicationError as exc:
        save(check="findings")
        return fail(f"公開を中止しました（作業版を保存）: {exc}")
    check = "ok"
    log("draft checked and published atomically")

    # 5. what actually changed
    anchors, added, removed = changed_anchors(before, after)
    if after == before:
        summary = "ファイルに変更はありませんでした。" + (f" {summary}" if summary else "")

    save(state="done", phase="完了", finished=time.time(), elapsed=elapsed,
         mode=mode, summary=summary, cost_usd=cost, check=check,
         reported_targets=targets, anchors=anchors,
         added=added, removed=removed, ran_session_id=ran_sid,
         snapshot=os.path.relpath(snap, args.project))

    mark = "✓" if check == "ok" else "⚠"
    notify(f"{mark} {job['file'][:40]}: {summary[:90]}")
    log(f"done anchors={anchors} +{added}/-{removed} cost={cost}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
