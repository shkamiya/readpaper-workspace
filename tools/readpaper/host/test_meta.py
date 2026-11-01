#!/usr/bin/env python3
"""Regression tests for meta_store.py and export_reading.py. stdlib only:

    python3 tools/readpaper/host/test_meta.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import meta_store as ms  # noqa: E402
import export_reading as ex  # noqa: E402

STAMPED = ("<!-- claude-code session_id: abc  source: papers/2510.25741v3.pdf  generated: x -->\n"
           "<!DOCTYPE html><html><head><title>Ouro &amp; friends</title></head>"
           "<body><h1>T</h1><div class=\"meta\">m</div></body></html>")
FIGS_ONLY = ("<!DOCTYPE html><html><head><title>Figs</title></head><body>"
             "<img src=\"figs/openreview-klU4737opt/fig1.png\"></body></html>")


class MetaStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        with open(os.path.join(self.root, "20260829-ouro.html"), "w", encoding="utf-8") as fh:
            fh.write(STAMPED)
        with open(os.path.join(self.root, "20260815-jump.html"), "w", encoding="utf-8") as fh:
            fh.write(FIGS_ONLY)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stem_of(self):
        self.assertEqual(ms.stem_of("20260829-ouro.html"), "20260829-ouro")
        self.assertEqual(ms.stem_of("/x/../20260829-ouro.html"), "20260829-ouro")
        for bad in ("index.html", "foo.txt", "", ".hidden.html", "_all.html"):
            with self.assertRaises(ValueError):
                ms.stem_of(bad)

    def test_read_missing_is_empty(self):
        rec = ms.read(self.root, "20260829-ouro")
        self.assertEqual(rec, ms.empty("20260829-ouro"))

    def test_validate(self):
        self.assertEqual(ms.validate("", "", None), (None, None, ""))
        self.assertEqual(ms.validate("done", "solid", " a \r\nb "), ("done", "solid", "a \nb"))
        with self.assertRaises(ValueError):
            ms.validate("skipped", None, "")
        with self.assertRaises(ValueError):
            ms.validate(None, "5", "")
        with self.assertRaises(ValueError):
            ms.validate(None, None, 12)
        with self.assertRaises(ValueError):
            ms.validate(None, None, "x" * (ms.MAX_NOTE + 1))

    def test_save_roundtrip_and_dest(self):
        html = os.path.join(self.root, "20260829-ouro.html")
        rec = ms.save(self.root, "20260829-ouro", "reading", None, "first", html)
        self.assertEqual(rec["status"], "reading")
        self.assertIsNone(rec["level"])
        self.assertEqual(rec["dest"], "2510.25741v3")
        self.assertTrue(rec["updated_at"])
        self.assertEqual(rec["history"], [])
        on_disk = ms.read(self.root, "20260829-ouro")
        self.assertEqual(on_disk, rec)
        # atomic write leaves no tmp file behind
        self.assertFalse(os.path.exists(ms.record_path(self.root, "20260829-ouro") + ".tmp"))

    def test_dest_from_figs(self):
        html = os.path.join(self.root, "20260815-jump.html")
        rec = ms.save(self.root, "20260815-jump", "done", "solid", "", html)
        self.assertEqual(rec["dest"], "openreview-klU4737opt")

    def test_history_only_on_change(self):
        s = "20260829-ouro"
        r1 = ms.save(self.root, s, "reading", "fuzzy", "v1")
        r2 = ms.save(self.root, s, "reading", "fuzzy", "v1")      # no change
        self.assertEqual(r2["history"], [])
        self.assertEqual(r2["updated_at"], r1["updated_at"])
        r3 = ms.save(self.root, s, "done", "roughly", "v2")
        self.assertEqual(len(r3["history"]), 1)
        self.assertEqual(r3["history"][0]["note"], "v1")
        self.assertEqual(r3["history"][0]["at"], r1["updated_at"])

    def test_history_capped(self):
        s = "20260829-ouro"
        for i in range(ms.MAX_HISTORY + 10):
            ms.save(self.root, s, "reading", None, f"n{i}")
        rec = ms.read(self.root, s)
        self.assertEqual(len(rec["history"]), ms.MAX_HISTORY)
        self.assertEqual(rec["history"][-1]["note"], f"n{ms.MAX_HISTORY + 8}")

    def test_all_json_skips_blank(self):
        ms.save(self.root, "20260829-ouro", "done", None, "")
        ms.save(self.root, "20260815-jump", None, None, "")          # blank record
        with open(ms.all_path(self.root), encoding="utf-8") as fh:
            allj = json.load(fh)
        self.assertIn("20260829-ouro", allj["items"])
        self.assertNotIn("20260815-jump", allj["items"])
        self.assertEqual(ms.load_all(self.root)["20260829-ouro"]["status"], "done")

    def test_load_all_absent(self):
        self.assertEqual(ms.load_all(self.root), {})

    def test_corrupt_record_reads_empty(self):
        with open(ms.record_path(self.root, "20260829-ouro"), "w") as fh:
            fh.write("{not json")
        self.assertEqual(ms.read(self.root, "20260829-ouro")["status"], None)

    def test_export(self):
        ms.save(self.root, "20260829-ouro", "done", "solid", "a | b\nsecond line")
        ms.save(self.root, "20260815-jump", "reading", None, "")
        ms.save(self.root, "20260101-gone", "done", None, "orphan")
        table = ex.rows(self.root, None, None, False)
        self.assertEqual([r[5] for r in table], ["20260829-ouro", "20260815-jump"])
        md = ex.render(table)
        self.assertIn("| 2026-08-29 | [Ouro & friends](20260829-ouro.html) | 読了 | しっかり | a \\| b second line |", md)
        self.assertEqual([r[5] for r in ex.rows(self.root, "done", None, False)], ["20260829-ouro"])
        self.assertEqual([r[5] for r in ex.rows(self.root, None, None, True)], ["20260101-gone"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
