#!/usr/bin/env python3
"""Dump SK's reading marks as a Markdown table on stdout.

    python3 tools/readpaper/export_reading.py                 # everything marked
    python3 tools/readpaper/export_reading.py --status done   # 読了 only
    python3 tools/readpaper/export_reading.py --level solid
    python3 tools/readpaper/export_reading.py --orphans       # records whose html is gone

Reads html-expl/meta/<stem>.json directly (not _all.json) so a record whose html
was regenerated under a new stem still shows up under --orphans.
"""
from __future__ import annotations

import argparse
import html as _html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "host"))
import meta_store  # noqa: E402

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-")


def title_of(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            m = TITLE_RE.search(fh.read(60_000))
    except OSError:
        return ""
    return _html.unescape(m.group(1).strip()) if m else ""


def date_of(stem: str) -> str:
    m = DATE_RE.match(stem)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def rows(root: str, status: str | None, level: str | None, orphans: bool) -> list[list[str]]:
    out = []
    for rec in meta_store.iter_records(root):
        if rec["status"] is None and rec["level"] is None and not rec["note"]:
            continue
        html = os.path.join(root, rec["stem"] + ".html")
        exists = os.path.isfile(html)
        if orphans != (not exists):
            continue
        if status and rec["status"] != status:
            continue
        if level and rec["level"] != level:
            continue
        out.append([date_of(rec["stem"]),
                    title_of(html) if exists else f"(missing) {rec['stem']}",
                    meta_store.STATUS_LABEL.get(rec["status"], ""),
                    meta_store.LEVEL_LABEL.get(rec["level"], ""),
                    rec["note"],
                    rec["stem"]])
    out.sort(key=lambda r: (r[0], r[5]), reverse=True)
    return out


def render(table: list[list[str]]) -> str:
    lines = ["| 日付 | タイトル | 状態 | 理解度 | ひとこと |", "|---|---|---|---|---|"]
    for date, title, st, lv, note, stem in table:
        lines.append(f"| {date} | [{cell(title)}]({stem}.html) | {st} | {lv} | {cell(note)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(HERE, "..", "..", "html-expl"))
    ap.add_argument("--status", choices=meta_store.STATUSES)
    ap.add_argument("--level", choices=meta_store.LEVELS)
    ap.add_argument("--orphans", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        sys.stderr.write(f"no such dir: {root}\n")
        return 1
    sys.stdout.write(render(rows(root, a.status, a.level, a.orphans)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
