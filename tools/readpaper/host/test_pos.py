#!/usr/bin/env python3
"""Regression tests for pos_store.py (reading position / しおり). stdlib only:

    python3 tools/readpaper/host/test_pos.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pos_store as ps  # noqa: E402


class PosStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_missing_is_empty(self):
        rec = ps.read(self.root, "20260909-foo")
        self.assertEqual(rec["stem"], "20260909-foo")
        self.assertIsNone(rec["updated_at"])
        self.assertIsNone(rec["anchor"])
        self.assertEqual(rec["ratio"], 0.0)

    def test_save_roundtrip_and_all(self):
        rec = ps.save(self.root, "a", {"i": 12, "fp": "P:hello", "frac": 0.25}, 0.6, "iPhone")
        self.assertEqual(rec["anchor"], {"i": 12, "fp": "P:hello", "frac": 0.25})
        self.assertEqual(rec["ratio"], 0.6)
        self.assertEqual(rec["device"], "iPhone")
        self.assertTrue(rec["updated_at"])
        again = ps.read(self.root, "a")
        self.assertEqual(again["anchor"], rec["anchor"])
        with open(ps.all_path(self.root), encoding="utf-8") as fh:
            allrec = json.load(fh)
        self.assertEqual(allrec["items"]["a"]["ratio"], 0.6)
        self.assertEqual(ps.load_all(self.root)["a"]["updated_at"], rec["updated_at"])

    def test_last_writer_wins(self):
        ps.save(self.root, "a", {"i": 1, "fp": "P:x", "frac": 0.0}, 0.1, "Mac")
        rec = ps.save(self.root, "a", {"i": 40, "fp": "H2:y", "frac": 0.5}, 0.9, "iPad")
        self.assertEqual(ps.read(self.root, "a")["anchor"]["i"], 40)
        self.assertEqual(rec["device"], "iPad")

    def test_anchor_optional(self):
        rec = ps.save(self.root, "a", None, 0.3)
        self.assertIsNone(rec["anchor"])
        self.assertEqual(rec["ratio"], 0.3)
        rec = ps.save(self.root, "a", {}, 0.3)
        self.assertIsNone(rec["anchor"])

    def test_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            ps.save(self.root, "a", None, 1.5)
        with self.assertRaises(ValueError):
            ps.save(self.root, "a", None, "nope")
        with self.assertRaises(ValueError):
            ps.save(self.root, "a", {"i": -1, "fp": "P:x", "frac": 0}, 0.5)
        with self.assertRaises(ValueError):
            ps.save(self.root, "a", {"i": 3, "fp": "", "frac": 0}, 0.5)
        with self.assertRaises(ValueError):
            ps.save(self.root, "a", {"i": 3, "fp": "P:x", "frac": 2}, 0.5)
        with self.assertRaises(ValueError):
            ps.save(self.root, "a", [1, 2], 0.5)
        with self.assertRaises(ValueError):
            ps.save(self.root, "a", None, 0.5, device=7)
        self.assertFalse(os.path.exists(ps.record_path(self.root, "a")))

    def test_truncation_and_rounding(self):
        rec = ps.save(self.root, "a", {"i": 0, "fp": "P:" + "x" * 500, "frac": 0.123456}, 0.98765, "d" * 100)
        self.assertEqual(len(rec["anchor"]["fp"]), ps.MAX_FP)
        self.assertEqual(rec["anchor"]["frac"], 0.1235)
        self.assertEqual(rec["ratio"], 0.9877)
        self.assertEqual(len(rec["device"]), ps.MAX_DEVICE)

    def test_clear(self):
        ps.save(self.root, "a", None, 0.5)
        ps.save(self.root, "b", None, 0.7)
        rec = ps.clear(self.root, "a")
        self.assertIsNone(rec["updated_at"])
        self.assertFalse(os.path.exists(ps.record_path(self.root, "a")))
        self.assertEqual(set(ps.load_all(self.root)), {"b"})
        ps.clear(self.root, "zzz")  # missing: no error

    def test_corrupt_file_reads_empty(self):
        with open(ps.record_path(self.root, "a"), "w") as fh:
            fh.write("{not json")
        self.assertIsNone(ps.read(self.root, "a")["updated_at"])


if __name__ == "__main__":
    unittest.main()
