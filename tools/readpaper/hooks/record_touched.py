#!/usr/bin/env python3
"""Record an html-expl/*.html file the current session just wrote.

Driven by explainer_touch_hook.sh (a PostToolUse hook on Write|Edit|MultiEdit).
Reads the hook JSON on stdin and appends the target path to a per-session queue in
$TMPDIR; explainer_stop_hook.sh consumes that queue at the end of the turn.

Why a queue at all: "which explainers did THIS session write" cannot be recovered by
scanning html-expl/, because a concurrent headless worker's output sits in the same
directory with the same `__AGENT_SESSION_ID__` placeholder still in it. The harness
tells us the answer one tool call at a time, so we write it down instead of guessing.

Never raises: a hook that fails must not be able to fail the turn.
"""
import json
import os
import sys
import tempfile


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:                              # noqa: BLE001
        return 0
    sid = str(payload.get("session_id") or "").strip()
    if not sid or "/" in sid or os.sep in sid:     # keep it usable as a filename
        return 0

    ti = payload.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("notebook_path") or ""
    if not raw:
        return 0

    path = os.path.abspath(raw)
    if (not path.endswith(".html")
            or os.path.basename(os.path.dirname(path)) != "html-expl"
            or os.path.basename(path) == "index.html"):
        return 0

    qdir = os.environ.get("TMPDIR") or tempfile.gettempdir()
    try:
        with open(os.path.join(qdir, f"readpaper-expl-queue-{sid}.txt"), "a",
                  encoding="utf-8") as fh:
            fh.write(path + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
