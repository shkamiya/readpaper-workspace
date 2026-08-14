#!/usr/bin/env python3
"""Robustly finalize the explainer HTML the worker just generated.

WHY THIS EXISTS
---------------
readpaper_run.sh used to discover the generated file and substitute the session
id with a bash pipeline:

    NEW_HTML="$(ls -t html-expl/*.html | grep -v /index.html | head -1)"
    sed -i '' "s/__CLAUDE_SESSION_ID__/$SID/g" "$NEW_HTML"   # + awk inject + stamp

Under Chrome's flaky macOS-TCC attribution the *bash glob* `html-expl/*.html`
intermittently returns nothing, so NEW_HTML was empty and EVERY post-step
(`[ -n "$NEW_HTML" ]`) silently skipped — shipping a literal
`__CLAUDE_SESSION_ID__` placeholder and no line-1 stamp. Python file IO keeps
Full-Disk-Access reliably in the worker (extract_figures.py / build_index.py
succeed every run), so we do the discovery + edits here instead.

WHAT IT DOES
------------
1. Find the newest non-index *.html in html-expl/ via os.scandir (reliable FDA),
   restricted to files modified at/after <start_epoch> — i.e. produced by THIS
   run. Without that restriction, a run that generated nothing (e.g. claude
   declined a duplicate) stamped and reported whichever unrelated html happened
   to be newest (observed 2026-07-17..21).
2. Replace the literal placeholder __AGENT_SESSION_ID__ (or the legacy
   __CLAUDE_SESSION_ID__, still emitted by older prompts and present in ~100
   already-generated files) with the real SID.
3. If the visible session line is absent entirely, inject a <div class="meta">
   right after the first </h1> as a fallback.
4. Stamp a `<!-- <agent> session_id: ... -->` comment on line 1 (idempotent).

USAGE
-----
    finalize_html.py <project_dir> <sid> <pdf> <timestamp> [start_epoch] [agent]
Prints the resolved html path (or "NONE") so the worker can log / notify.
start_epoch (unix seconds) is optional for backward compatibility; when absent,
any html qualifies (old behavior). <agent> is "claude" (default) or "codex" and
only selects the label and the resume command written into the file — the two
CLIs resume differently (`claude --resume <id>` vs `codex exec resume <id>`), so
a reader who opens the html months later needs to be told which one to run.
"""
from __future__ import annotations

import os
import re
import sys


def newest_explainer(html_dir: str, min_mtime: float = 0.0) -> str | None:
    best, best_mtime = None, -1.0
    try:
        for e in os.scandir(html_dir):
            if not e.is_file() or not e.name.endswith(".html"):
                continue
            if e.name == "index.html":
                continue
            m = e.stat().st_mtime
            if m < min_mtime:
                continue
            if m > best_mtime:
                best, best_mtime = e.path, m
    except OSError as exc:
        print(f"finalize_html: scandir failed: {exc}", file=sys.stderr)
        return None
    return best


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
STAMP_RE = re.compile(r"(?:claude-code|codex|agent) session_id:")


def main() -> int:
    if len(sys.argv) not in (5, 6, 7):
        print("usage: finalize_html.py <project_dir> <sid> <pdf> <ts> [start_epoch] [agent]",
              file=sys.stderr)
        return 2
    project_dir, sid, pdf, ts = sys.argv[1:5]
    # Small slack (-5s) tolerates clock/second truncation between the worker's
    # `date +%s` and the html file's mtime.
    min_mtime = float(sys.argv[5]) - 5.0 if len(sys.argv) >= 6 else 0.0
    agent = sys.argv[6] if len(sys.argv) == 7 else "claude"
    label, resume_fmt = AGENTS.get(agent, AGENTS["claude"])
    resume_cmd = resume_fmt.format(sid=sid)
    html_dir = os.path.join(project_dir, "html-expl")
    path = newest_explainer(html_dir, min_mtime)
    if not path:
        print("NONE")
        return 0

    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    # 2. placeholder -> real id
    hit = next((p for p in PLACEHOLDERS if p in src), None)
    if hit:
        src = src.replace(hit, sid)
        print(f"finalize_html: filled placeholder {hit} in {path}", file=sys.stderr)
    elif VISIBLE_RE.search(src):
        print(f"finalize_html: visible id already present in {path}", file=sys.stderr)
    else:
        # 3. fallback: inject a visible meta line after the first </h1>
        inj = f'\n  <div class="meta">{label} session: <code>{sid}</code></div>'
        src, n = re.subn(r"</h1>", "</h1>" + inj, src, count=1)
        if n:
            print(f"finalize_html: injected fallback id after </h1> in {path}", file=sys.stderr)
        else:
            print(f"finalize_html: WARN no </h1> to inject after in {path}", file=sys.stderr)

    # 4. line-1 stamp (idempotent)
    first_line = src.split("\n", 1)[0]
    if not STAMP_RE.search(first_line):
        stamp = (f"<!-- {label} session_id: {sid}  source: {pdf}  generated: {ts}\n"
                 f"     resume: cd {project_dir} && {resume_cmd} -->\n")
        src = stamp + src

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
