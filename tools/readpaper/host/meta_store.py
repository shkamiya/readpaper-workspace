#!/usr/bin/env python3
"""Reading marks: SK's own状態 / 理解度 / ひとことまとめ, one JSON per explainer.

Layout (all under html-expl/, so both the static server on 8770 and ask_server on
8771 serve the files as plain static content; only *writing* goes through
ask_server's POST /api/meta):

    html-expl/meta/<stem>.json   -- the record for html-expl/<stem>.html
    html-expl/meta/_all.json     -- every record, regenerated on each write, for
                                    index.html to fetch in one request

The html files are never touched (see ask_server.py for why), and the records are
SK's words: the in-page chat agent is *shown* them in its prompt (ask_run.py) but is
told not to edit this directory. Plan: notes/20260904-reading-marks-plan.md.

Record shape:

    {"stem": "...", "dest": "2510.25741v3",
     "status": null | "reading" | "done",
     "level":  null | "fuzzy" | "roughly" | "solid",
     "note": "...", "updated_at": "2026-09-04T07:12:00+09:00",
     "history": [{"at": ..., "status": ..., "level": ..., "note": ...}, ...]}

"Unread" is the absence of a status, deliberately: nothing to tick, nothing to count.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading

META_DIR = "meta"
ALL_NAME = "_all.json"

STATUSES = ("reading", "done")
LEVELS = ("fuzzy", "roughly", "solid")
STATUS_LABEL = {"reading": "読み中", "done": "読了"}
LEVEL_LABEL = {"fuzzy": "ふんわり", "roughly": "だいたい", "solid": "しっかり"}

MAX_NOTE = 4000
MAX_HISTORY = 50

# Only the module-level lock: one process (the launchd ask_server) writes.
_LOCK = threading.Lock()

# The finalize stamp on line 1 and the figure dir are the two markers the dupe
# guard in readpaper_run.sh trusts to say "this html *is* the explainer of <dest>".
_STAMP_SRC_RE = re.compile(r"source:\s*papers/([^\s]+?)\.pdf")
_FIGS_RE = re.compile(r"figs/([A-Za-z0-9][A-Za-z0-9._-]*)/")


def meta_dir(root: str) -> str:
    d = os.path.join(root, META_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def record_path(root: str, stem: str) -> str:
    return os.path.join(meta_dir(root), f"{stem}.json")


def all_path(root: str) -> str:
    return os.path.join(meta_dir(root), ALL_NAME)


def stem_of(fname: str) -> str:
    """'20260829-foo.html' -> '20260829-foo'. Raises ValueError for anything odd."""
    fname = os.path.basename(str(fname or ""))
    if not fname.endswith(".html") or fname == "index.html" or "/" in fname:
        raise ValueError(f"not an explainer file name: {fname!r}")
    stem = fname[:-5]
    if not stem or stem.startswith(".") or stem.startswith("_"):
        raise ValueError(f"bad stem: {stem!r}")
    return stem


def empty(stem: str) -> dict:
    return {"stem": stem, "dest": "", "status": None, "level": None,
            "note": "", "updated_at": None, "history": []}


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def read(root: str, stem: str) -> dict:
    """The record, or an empty default. Never raises on a missing/corrupt file."""
    try:
        with open(record_path(root, stem), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return empty(stem)
    base = empty(stem)
    base.update({k: rec.get(k, base[k]) for k in base})
    base["stem"] = stem
    return base


def validate(status, level, note) -> tuple:
    """-> (status, level, note) normalised. Raises ValueError with a user-facing message."""
    if status in ("", None):
        status = None
    elif status not in STATUSES:
        raise ValueError(f"bad status: {status!r}")
    if level in ("", None):
        level = None
    elif level not in LEVELS:
        raise ValueError(f"bad level: {level!r}")
    if note is None:
        note = ""
    if not isinstance(note, str):
        raise ValueError("note must be a string")
    note = note.replace("\r\n", "\n").strip()
    if len(note) > MAX_NOTE:
        raise ValueError(f"note too long ({len(note)} > {MAX_NOTE})")
    return status, level, note


def extract_dest(html_path: str) -> str:
    """Best-effort paper id for cross-referencing later. '' if nothing is stamped."""
    try:
        with open(html_path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(200_000)
    except OSError:
        return ""
    m = _STAMP_SRC_RE.search(head[:1000])
    if m:
        return m.group(1)
    m = _FIGS_RE.search(head)
    return m.group(1) if m else ""


def _atomic_dump(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def save(root: str, stem: str, status, level, note, html_path: str | None = None) -> dict:
    """Full (not partial) update. Pushes the previous values onto history when
    anything changed, rewrites _all.json, returns the stored record."""
    status, level, note = validate(status, level, note)
    with _LOCK:
        rec = read(root, stem)
        changed = (rec["status"], rec["level"], rec["note"]) != (status, level, note)
        if changed:
            if rec["updated_at"]:
                rec["history"].append({"at": rec["updated_at"], "status": rec["status"],
                                       "level": rec["level"], "note": rec["note"]})
                rec["history"] = rec["history"][-MAX_HISTORY:]
            rec.update(status=status, level=level, note=note, updated_at=now_iso())
        if html_path and not rec.get("dest"):
            rec["dest"] = extract_dest(html_path)
        _atomic_dump(record_path(root, stem), rec)
        rebuild_all(root)
    return rec


def iter_records(root: str):
    d = meta_dir(root)
    for name in sorted(os.listdir(d)):
        if name.endswith(".json") and not name.startswith("_") and not name.endswith(".tmp"):
            yield read(root, name[:-5])


def rebuild_all(root: str) -> dict:
    """index.html reads this one file. Records with nothing set are left out so
    the index never even sees "unread" as a value."""
    items = {}
    for rec in iter_records(root):
        if rec["status"] is None and rec["level"] is None and not rec["note"]:
            continue
        items[rec["stem"]] = {"status": rec["status"], "level": rec["level"],
                              "note": rec["note"], "updated_at": rec["updated_at"]}
    out = {"generated_at": now_iso(), "items": items}
    _atomic_dump(all_path(root), out)
    return out


def load_all(root: str) -> dict:
    """{stem: {...}} from _all.json, or {} when absent. For build_index.py."""
    try:
        with open(all_path(root), encoding="utf-8") as fh:
            return dict(json.load(fh).get("items") or {})
    except (OSError, ValueError, AttributeError):
        return {}
