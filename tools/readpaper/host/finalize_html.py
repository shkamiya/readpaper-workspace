#!/usr/bin/env python3
"""Stamp a worker's explicit draft, or explicitly named interactive files.

Workers MUST pass --html. No file discovery by mtime, citations, or asset paths
is performed. Session provenance is written to a private draft before the common
publisher validates and installs it. --stamp and --hook retain their explicit
interactive file contracts.
"""
from __future__ import annotations

import json
import os
import re
import sys


# Label written into the html, and the command that reopens the session, per backend.
AGENTS = {
    "claude": ("claude-code", "claude --resume {sid}"),
    # --skip-git-repo-check is not decoration: `codex exec resume` refuses to start with
    # "Not inside a trusted directory and --skip-git-repo-check was not specified" unless
    # the cwd is a git repo or is listed as trusted in ~/.codex/config.toml (measured
    # 2026-08-14). The hint has to work for whoever opens this html months later,
    # including on a copy of the project that was never git-cloned.
    "codex": ("codex", "codex exec resume --skip-git-repo-check {sid}"),
}
# Both the current and the legacy placeholder token are honoured, so a prompt (or a
# house-rules file) that still says __CLAUDE_SESSION_ID__ keeps working.
PLACEHOLDERS = ("__AGENT_SESSION_ID__", "__CLAUDE_SESSION_ID__")
# Matches the visible line and the line-1 stamp for ANY backend label, so a second
# finalize pass never double-injects just because the label changed.
VISIBLE_RE = re.compile(r"(?:claude-code|codex|agent) session:")
VISIBLE_ID_RE = re.compile(r"((?:claude-code|codex|agent) session:\s*<code>)([^<]*)(</code>)")
STAMP_RE = re.compile(r"(?:claude-code|codex|agent) session_id:\s*(\S+)")
# The whole leading stamp comment, which spans two lines (id/source/generated, resume).
LEADING_STAMP_RE = re.compile(
    r"\A<!--\s*(?:claude-code|codex|agent) session_id:.*?-->\n", re.S)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def log(msg: str) -> None:
    print(f"finalize_html: {msg}", file=sys.stderr)


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def stamp(path: str, sid: str, label: str, resume_cmd: str, pdf: str, ts: str,
          project_dir: str, authoritative: bool) -> None:
    """Put `sid` into the file: placeholder, then correction, then injection, then line 1."""
    src = read(path)

    hit = next((p for p in PLACEHOLDERS if p in src), None)
    if hit:
        src = src.replace(hit, sid)
        log(f"filled placeholder {hit} in {path}")
    else:
        m = VISIBLE_ID_RE.search(src)
        if m and m.group(2).strip() == sid:
            log(f"visible id already correct in {path}")
        elif m and authoritative:
            # The agent invented an id instead of writing the placeholder (observed
            # 2026-08-16: it wrote a *different, concurrent* session's uuid). Ours is
            # pre-minted by the worker and is the one `claude --resume` accepts.
            log(f"WARN replacing foreign visible id {m.group(2).strip()!r} with {sid} "
                f"in {path}")
            src = VISIBLE_ID_RE.sub(lambda x: f"{label} session: <code>{sid}</code>", src, count=1)
        elif m:
            log(f"visible id {m.group(2).strip()!r} present and correction not authorized; "
                f"leaving it alone")
        elif VISIBLE_RE.search(src):
            log(f"session line present but no <code> id in {path}; leaving it alone")
        else:
            inj = f'\n  <div class="meta">{label} session: <code>{sid}</code></div>'
            src, n = re.subn(r"</h1>", "</h1>" + inj, src, count=1)
            log(f"injected fallback id after </h1> in {path}" if n
                else f"WARN no </h1> to inject after in {path}")

    new_stamp = (f"<!-- {label} session_id: {sid}  source: {pdf}  generated: {ts}\n"
                 f"     resume: cd {project_dir} && {resume_cmd} -->\n")
    old = LEADING_STAMP_RE.match(src)
    if not old:
        src = new_stamp + src
    elif authoritative and STAMP_RE.search(old.group(0)).group(1) != sid:
        # The caller explicitly owns this file and supplies its provenance.
        log(f"WARN correcting line-1 stamp {STAMP_RE.search(old.group(0)).group(1)} "
            f"-> {sid} in {path}")
        src = new_stamp + src[old.end():]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)


# --------------------------------------------------------------------------- modes

def run_hook(argv) -> int:
    """Stop-hook mode: fill the placeholder in THIS session's files with its real id.

    argv is `<project_dir> <file> [<file> ...]` — the explicit list of explainers the
    session wrote, collected by the PostToolUse hook. There is deliberately NO
    directory scan: a headless worker's in-flight output also sits in html-expl/ with
    the placeholder still in it (the worker substitutes only after its agent exits),
    so "every file with a placeholder" is not "this session's files". Scanning stamped
    four of the worker's explainers with an interactive session's id during testing on
    2026-08-16.

    Two further interlocks: the file must be under <project_dir>/html-expl/, and it
    must still contain the literal placeholder.
    """
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                       # noqa: BLE001 - hook must never crash
        log(f"hook: unreadable stdin ({exc})")
        return 0
    sid = str(payload.get("session_id") or "").strip()
    if not UUID_RE.match(sid):
        log(f"hook: no usable session_id in payload ({sid!r})")
        return 0
    if not argv:
        return 0
    # An explicit project dir wins over the payload's cwd: the session may be sitting
    # in a subdirectory, and html-expl/ only exists at the repo root.
    project_dir = os.path.abspath(argv[0] or payload.get("cwd") or os.getcwd())
    html_dir = os.path.join(project_dir, "html-expl")

    label, resume_fmt = AGENTS["claude"]
    n = 0
    for raw in argv[1:]:
        path = os.path.abspath(raw)
        if os.path.dirname(path) != html_dir or not os.path.isfile(path):
            continue
        if not any(p in read(path) for p in PLACEHOLDERS):
            continue
        stamp(path, sid, label, resume_fmt.format(sid=sid), "(interactive)",
              "(interactive)", project_dir, authoritative=True)
        print(path)
        n += 1
    if n:
        log(f"hook: filled the session-id placeholder in {n} file(s) with {sid}")
    return 0


def run_stamp(argv) -> int:
    """Manual mode: finalize_html.py --stamp <html> <sid> [agent]."""
    if len(argv) < 2:
        log("usage: finalize_html.py --stamp <html> <sid> [agent]")
        return 2
    path, sid = argv[0], argv[1]
    agent = argv[2] if len(argv) > 2 else "claude"
    label, resume_fmt = AGENTS.get(agent, AGENTS["claude"])
    if not os.path.isfile(path):
        log(f"no such file: {path}")
        return 2
    stamp(path, sid, label, resume_fmt.format(sid=sid), "(manual)", "(manual)",
          os.path.dirname(os.path.abspath(path)), authoritative=True)
    print(path)
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--hook":
        return run_hook(argv[1:])
    if argv and argv[0] == "--stamp":
        return run_stamp(argv[1:])

    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", required=True, help="exact private HTML draft")
    parser.add_argument("project_dir")
    parser.add_argument("sid")
    parser.add_argument("pdf")
    parser.add_argument("ts")
    parser.add_argument("agent", choices=AGENTS, default="claude", nargs="?")
    args = parser.parse_args(argv)
    if not UUID_RE.fullmatch(args.sid):
        log("no valid session id; refusing to finalize")
        return 1
    path = os.path.abspath(args.html)
    if not os.path.isfile(path) or os.path.islink(path):
        log(f"explicit output missing or symlink: {path}")
        return 1
    label, resume_fmt = AGENTS[args.agent]
    stamp(path, args.sid, label, resume_fmt.format(sid=args.sid), args.pdf,
          args.ts, args.project_dir, authoritative=True)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
