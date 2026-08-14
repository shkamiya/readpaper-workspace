#!/usr/bin/env python3
"""Rebuild the readpaper resume ledger from durable sources.

The Chrome-spawned worker intentionally does NOT append to a shared ledger file
(a foreign app tagging the .tsv via macOS App Management makes it unwritable to
the worker). Instead the session id is recorded in each per-job log line
"[readpaper] session_id=<uuid>". This script consolidates those into

    logs/readpaper-sessions.txt   (columns: TS  dest  session_id  html)

The output uses a .txt extension (not .tsv) on purpose: a .tsv double-clicks
open in Apple Numbers, which locks the file and writes dot-hex temp files
(".99bd..."), producing spurious "Not Opened" popups when the file is later
regenerated underneath it. .txt opens harmlessly in TextEdit instead.

Two sources are merged and de-duplicated by session id:
  1. logs/readpaper-<dest>-<TS>.log  -> session_id= / done -> <html>   (new runs)
  2. the Claude transcript bucket    -> first-user prompt "papers/<id>.pdf"
     (back-fills headless runs that predate session_id logging)

Run interactively (so the output file is created by the same app that views it,
avoiding the cross-app provenance lock):  python3 tools/readpaper/rebuild_sessions.py
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import re

PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOGS = os.path.join(PROJECT, "logs")
OUT = os.path.join(LOGS, "readpaper-sessions.txt")


def claude_bucket(project_dir: str) -> str:
    """Transcript directory Claude Code uses for `project_dir`.

    Claude Code slugifies the absolute project path by replacing every '/' and '_' with
    '-', so /Users/sk/Desktop/1_Lab/8_paper-reading becomes
    -Users-sk-Desktop-1-Lab-8-paper-reading. This used to be a hard-coded literal for
    SK's machine, which silently produced an empty source-2 backfill on any other laptop
    (or after moving the project).
    """
    slug = project_dir.replace("/", "-").replace("_", "-")
    return os.path.expanduser(os.path.join("~/.claude/projects", slug))


BUCKET = claude_bucket(PROJECT)
# Codex stores its transcripts elsewhere and in a different layout (dated subdirectories
# under ~/.codex/sessions, one rollout file per thread, not one bucket per project). The
# source-2 backfill below only understands the Claude layout; on a Codex-only machine it
# finds nothing and the ledger is built from the per-job logs alone (source 1), which is
# the authoritative source for every run since 2026-06-17 anyway.
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")

LOGNAME_RE = re.compile(r"readpaper-(.+)-(\d{8}-\d{6})\.log$")
SID_RE = re.compile(r"session_id=([0-9a-f-]{36})")
DONE_RE = re.compile(r"done -> (.*?)\s+session=")
PROMPT_RE = re.compile(r"explain-via-html.*?papers/([^\s]+?)\.pdf")

# session_id -> (ts, dest, sid, html)
rows: dict[str, tuple[str, str, str, str]] = {}

# Source 1: per-job logs (authoritative for new runs).
for path in glob.glob(os.path.join(LOGS, "readpaper-*.log")):
    m = LOGNAME_RE.search(os.path.basename(path))
    if not m:
        continue
    dest, ts = m.group(1), m.group(2)
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    ms = SID_RE.search(text)
    if not ms:
        continue  # predates session_id logging; handled by source 2
    sid = ms.group(1)
    md = DONE_RE.search(text)
    html = md.group(1).strip() if md else ""
    rows[sid] = (ts, dest, sid, html)

# Source 2: transcript bucket, to back-fill older headless runs.
for path in glob.glob(os.path.join(BUCKET, "*.jsonl")):
    sid = os.path.splitext(os.path.basename(path))[0]
    if sid in rows:
        continue
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 6:
                    break
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "user":
                    continue
                content = r.get("message", {}).get("content")
                if isinstance(content, list):
                    content = " ".join(
                        x.get("text", "") for x in content if isinstance(x, dict)
                    )
                pm = PROMPT_RE.search(str(content))
                if pm:
                    ts = datetime.datetime.fromtimestamp(
                        os.path.getmtime(path)
                    ).strftime("%Y%m%d-%H%M%S")
                    rows[sid] = (ts, pm.group(1), sid, "(no per-job log)")
                break
    except OSError:
        continue

ordered = sorted(rows.values(), key=lambda r: r[0])
with open(OUT, "w", encoding="utf-8") as w:
    w.write("# TS\tdest\tsession_id\thtml   (resume: cd <repo> && claude --resume <session_id>)\n")
    for ts, dest, sid, html in ordered:
        w.write(f"{ts}\t{dest}\t{sid}\t{html}\n")

print(f"wrote {OUT} ({len(ordered)} sessions)")
