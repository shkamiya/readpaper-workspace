#!/usr/bin/env python3
"""Reading position ("しおり") for explainer pages, one JSON per explainer.

Sibling of meta_store.py / ink_store.py: same layout, same ownership rule.

    html-expl/pos/<stem>.json   -- where SK last was on html-expl/<stem>.html
    html-expl/pos/_all.json     -- every record, regenerated on each write, for
                                   index.html to show a progress figure

Written only through ask_server's POST /api/pos (pos_widget.js); the html is never
touched and no agent edits this directory. Plan: notes/20260909-scroll-bookmark-plan.md.

Record shape:

    {"stem": "...", "updated_at": "2026-09-09T10:00:00+09:00",
     "anchor": {"i": 137, "fp": "P:本節では…", "frac": 0.35},
     "ratio": 0.63, "device": "iPhone"}

    anchor.i    : index of the block element nearest the viewport top, in the same
                  document order ink_widget.js uses (BLOCK_SEL minus our own hosts)
    anchor.fp   : TAG + ":" + first 60 chars of its text, so the element can be
                  found again after the page is regenerated with a shifted index
    anchor.frac : (scrollY - top(el)) / height(el), 0..1
    ratio       : scrollY / (scrollHeight - clientHeight), 0..1 -- the fallback
                  when the anchor cannot be resolved, and the index's "63%"

Concurrency: last writer wins. A position has nothing to merge, so unlike ink
there is no rev/409 dance; two devices simply overwrite each other, which is the
behaviour one wants from a bookmark.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading

POS_DIR = "pos"
ALL_NAME = "_all.json"

MAX_FP = 80
MAX_DEVICE = 40

_LOCK = threading.Lock()


def pos_dir(root: str) -> str:
    d = os.path.join(root, POS_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def record_path(root: str, stem: str) -> str:
    return os.path.join(pos_dir(root), f"{stem}.json")


def all_path(root: str) -> str:
    return os.path.join(pos_dir(root), ALL_NAME)


def empty(stem: str) -> dict:
    return {"stem": stem, "updated_at": None, "anchor": None, "ratio": 0.0, "device": ""}


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


def _unit(v, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number")
    if f != f or f < 0.0 or f > 1.0:
        raise ValueError(f"{what} out of range: {v!r}")
    return round(f, 4)


def validate(anchor, ratio, device) -> tuple:
    """-> (anchor | None, ratio, device) normalised. Raises ValueError."""
    ratio = _unit(ratio, "ratio")
    if anchor in (None, "", {}):
        anchor = None
    else:
        if not isinstance(anchor, dict):
            raise ValueError("anchor must be an object")
        try:
            i = int(anchor.get("i"))
        except (TypeError, ValueError):
            raise ValueError("anchor.i must be an integer")
        if i < 0:
            raise ValueError("anchor.i must be >= 0")
        fp = anchor.get("fp")
        if not isinstance(fp, str) or not fp:
            raise ValueError("anchor.fp must be a non-empty string")
        anchor = {"i": i, "fp": fp[:MAX_FP], "frac": _unit(anchor.get("frac", 0.0), "anchor.frac")}
    if device is None:
        device = ""
    if not isinstance(device, str):
        raise ValueError("device must be a string")
    return anchor, ratio, device.strip()[:MAX_DEVICE]


def _atomic_dump(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def save(root: str, stem: str, anchor, ratio, device=None) -> dict:
    """Overwrite one record, rewrite _all.json, return the stored record."""
    anchor, ratio, device = validate(anchor, ratio, device)
    with _LOCK:
        rec = empty(stem)
        rec.update(anchor=anchor, ratio=ratio, device=device, updated_at=now_iso())
        _atomic_dump(record_path(root, stem), rec)
        rebuild_all(root)
    return rec


def clear(root: str, stem: str) -> dict:
    """Forget the position (reading restarted from the top)."""
    with _LOCK:
        try:
            os.remove(record_path(root, stem))
        except FileNotFoundError:
            pass
        rebuild_all(root)
    return empty(stem)


def iter_records(root: str):
    d = pos_dir(root)
    for name in sorted(os.listdir(d)):
        if name.endswith(".json") and not name.startswith("_") and not name.endswith(".tmp"):
            yield read(root, name[:-5])


def rebuild_all(root: str) -> dict:
    """index.html reads this one file: {stem: {ratio, updated_at}}."""
    items = {}
    for rec in iter_records(root):
        if not rec["updated_at"]:
            continue
        items[rec["stem"]] = {"ratio": rec["ratio"], "updated_at": rec["updated_at"]}
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
