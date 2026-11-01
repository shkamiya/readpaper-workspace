#!/usr/bin/env python3
"""Regression tests for ink_store.py. stdlib only:

    python3 tools/readpaper/host/test_ink.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ink_store as ink  # noqa: E402


def stroke(sid="a1", **kw):
    s = {"id": sid, "tool": "pen", "color": "#1a1a1a", "size": 2.4,
         "anchor": {"i": 3, "fp": "P:hello", "w": 640}, "vw": 1024,
         "pts": [[0.1, 10, 0.5], [0.2, 12.345, 0.8]]}
    s.update(kw)
    return s


class InkStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_missing_is_empty(self):
        self.assertEqual(ink.read(self.root, "x"), {"stem": "x", "updated_at": None, "rev": 0, "strokes": []})

    def test_save_roundtrip_and_normalise(self):
        rec = ink.save(self.root, "s", [stroke()])
        self.assertEqual(len(rec["strokes"]), 1)
        self.assertEqual(rec["strokes"][0]["pts"][1], [0.2, 12.3, 0.8])
        self.assertEqual(rec["rev"], 1)
        self.assertIsNotNone(rec["updated_at"])
        again = ink.read(self.root, "s")
        self.assertEqual(again["strokes"], rec["strokes"])
        with open(ink.record_path(self.root, "s"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["stem"], "s")

    def test_prev_generation_kept(self):
        ink.save(self.root, "s", [stroke("a")])
        ink.save(self.root, "s", [stroke("a"), stroke("b")], base=ink.read(self.root, "s")["rev"])
        with open(ink.prev_path(self.root, "s"), encoding="utf-8") as fh:
            self.assertEqual([x["id"] for x in json.load(fh)["strokes"]], ["a"])
        self.assertEqual([x["id"] for x in ink.read(self.root, "s")["strokes"]], ["a", "b"])

    def test_conflict(self):
        first = ink.save(self.root, "s", [stroke("a")])
        self.assertEqual(first["rev"], 1)
        # a second device saves in between (same second: rev, not updated_at, must catch it)
        ink.save(self.root, "s", [stroke("a"), stroke("c")], base=first["rev"])
        with self.assertRaises(ink.Conflict):
            ink.save(self.root, "s", [stroke("a"), stroke("b")], base=first["rev"])
        # a stale client that thinks the record is empty (rev 0) is refused too
        with self.assertRaises(ink.Conflict):
            ink.save(self.root, "s", [stroke("b")], base=0)
        # nothing was written by the refused save
        self.assertEqual([x["id"] for x in ink.read(self.root, "s")["strokes"]], ["a", "c"])
        # base=None skips the check (first write / explicit overwrite)
        ink.save(self.root, "s", [stroke("z")])
        self.assertEqual([x["id"] for x in ink.read(self.root, "s")["strokes"]], ["z"])

    def test_validate_rejects(self):
        bad = [
            stroke(sid="has space"), stroke(tool="crayon"), stroke(color="red"),
            stroke(size=1000), stroke(pts=[]), stroke(pts=[[0.1]]), stroke(pts=[["x", 1, 1]]),
            stroke(pts=[[0.1, 1, 7]]), stroke(anchor="nope"),
        ]
        for b in bad:
            with self.assertRaises(ValueError, msg=repr(b)):
                ink.validate([b])
        with self.assertRaises(ValueError):
            ink.validate({"not": "a list"})

    def test_duplicate_ids_collapse(self):
        out = ink.validate([stroke("a", color="#ff0000"), stroke("a", color="#00ff00")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["color"], "#ff0000")

    def test_defaults(self):
        s = ink.validate_stroke({"id": "q", "pts": [[0, 0]]})
        self.assertEqual(s["tool"], "pen")
        self.assertEqual(s["pts"][0][2], 0.5)
        self.assertEqual(s["anchor"]["i"], -1)


if __name__ == "__main__":
    unittest.main()
