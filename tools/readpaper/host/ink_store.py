#!/usr/bin/env python3
"""Handwritten ink strokes on explainer pages, one JSON per explainer.

Sibling of meta_store.py: same layout idea, same ownership rule.

    html-expl/ink/<stem>.json        -- the strokes for html-expl/<stem>.html
    html-expl/ink/<stem>.prev.json   -- the version before the last save (one generation)

The strokes are SK's Apple Pencil writing (ink_widget.js); the html is never
touched and no agent edits this directory. Plan: notes/20260907-ink-handwriting-plan.md.

Record shape:

    {"stem": "...", "updated_at": "2026-09-07T15:00:00+09:00", "rev": 7,
     "strokes": [{"id": "k3f9a1", "tool": "pen" | "hl", "color": "#1a1a1a", "size": 2.4,
                  "anchor": {"i": 37, "fp": "P:...", "w": 640.0}, "vw": 1024,
                  "pts": [[dx, dy, p], ...]}, ...]}

    dx: x normalised by the anchor element's width (0..1, may slightly exceed)
    dy: y in px from the anchor element's top
    p : pointer pressure 0..1

Concurrency: save() takes the `base` rev the client loaded (an integer that grows
by one per save -- updated_at has second resolution and two saves in one second
would pass an updated_at check); when it does not match the stored rev the save
is refused with Conflict, and the widget re-reads,
unions by stroke id and retries. Last writer still wins on identical ids, but two
devices drawing on the same page no longer silently drop each other's strokes.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading

INK_DIR = "ink"

TOOLS = ("pen", "hl")
MAX_STROKES = 3000
MAX_POINTS = 6000
MAX_BYTES = 4_000_000
MAX_FP = 160
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_LOCK = threading.Lock()


class Conflict(Exception):
    """The record changed since the client loaded it."""


def ink_dir(root: str) -> str:
    d = os.path.join(root, INK_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def record_path(root: str, stem: str) -> str:
    return os.path.join(ink_dir(root), f"{stem}.json")


def prev_path(root: str, stem: str) -> str:
    return os.path.join(ink_dir(root), f"{stem}.prev.json")


def empty(stem: str) -> dict:
    return {"stem": stem, "updated_at": None, "rev": 0, "strokes": []}


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def read(root: str, stem: str) -> dict:
    try:
        with open(record_path(root, stem), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return empty(stem)
    out = empty(stem)
    out["updated_at"] = rec.get("updated_at")
    try:
        out["rev"] = int(rec.get("rev") or 0)
    except (TypeError, ValueError):
        out["rev"] = 0
    strokes = rec.get("strokes")
    out["strokes"] = strokes if isinstance(strokes, list) else []
    return out


def _num(v, lo: float, hi: float, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number")
    f = float(v)
    if f != f or f < lo or f > hi:  # NaN or out of range
        raise ValueError(f"{what} out of range: {v!r}")
    return f


def validate_stroke(s) -> dict:
    """One stroke, normalised. Raises ValueError with a user-facing message."""
    if not isinstance(s, dict):
        raise ValueError("stroke must be an object")
    sid = str(s.get("id") or "")
    if not _ID_RE.match(sid):
        raise ValueError(f"bad stroke id: {sid!r}")
    tool = s.get("tool") or "pen"
    if tool not in TOOLS:
        raise ValueError(f"bad tool: {tool!r}")
    color = str(s.get("color") or "#1a1a1a")
    if not _COLOR_RE.match(color):
        raise ValueError(f"bad color: {color!r}")
    size = _num(s.get("size", 2.4), 0.3, 60, "size")
    a = s.get("anchor") or {}
    if not isinstance(a, dict):
        raise ValueError("anchor must be an object")
    anchor = {
        "i": int(_num(a.get("i", -1), -1, 10**7, "anchor.i")),
        "fp": str(a.get("fp") or "")[:MAX_FP],
        "w": _num(a.get("w", 0), 0, 10**5, "anchor.w"),
    }
    vw = _num(s.get("vw", 0), 0, 10**5, "vw")
    pts = s.get("pts")
    if not isinstance(pts, list) or not pts:
        raise ValueError("pts must be a non-empty list")
    if len(pts) > MAX_POINTS:
        raise ValueError(f"too many points ({len(pts)} > {MAX_POINTS})")
    out_pts = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise ValueError("each point must be [dx, dy, p]")
        dx = _num(p[0], -2, 3, "dx")
        dy = _num(p[1], -10**5, 10**6, "dy")
        pr = _num(p[2], 0, 1, "pressure") if len(p) > 2 else 0.5
        out_pts.append([round(dx, 4), round(dy, 1), round(pr, 2)])
    return {"id": sid, "tool": tool, "color": color, "size": round(size, 2),
            "anchor": anchor, "vw": round(vw), "pts": out_pts}


def validate(strokes) -> list:
    if not isinstance(strokes, list):
        raise ValueError("strokes must be a list")
    if len(strokes) > MAX_STROKES:
        raise ValueError(f"too many strokes ({len(strokes)} > {MAX_STROKES})")
    out, seen = [], set()
    for s in strokes:
        v = validate_stroke(s)
        if v["id"] in seen:
            continue           # a client-side merge can double an id; keep the first
        seen.add(v["id"])
        out.append(v)
    return out


def _atomic_dump(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, path)


def save(root: str, stem: str, strokes, base=None) -> dict:
    """Full replace. `base` is the rev the client loaded (None = no check);
    a mismatch raises Conflict and writes nothing."""
    clean = validate(strokes)
    body = json.dumps(clean, separators=(",", ":"))
    if len(body) > MAX_BYTES:
        raise ValueError(f"ink too large ({len(body)} bytes > {MAX_BYTES})")
    with _LOCK:
        cur = read(root, stem)
        if base is not None and int(base) != cur["rev"]:
            raise Conflict(cur["rev"])
        rec = {"stem": stem, "updated_at": now_iso(), "rev": cur["rev"] + 1, "strokes": clean}
        p = record_path(root, stem)
        if os.path.exists(p):
            try:
                os.replace(p, prev_path(root, stem))
            except OSError:
                pass
        _atomic_dump(p, rec)
    return rec
