#!/usr/bin/env python3
"""Deterministic figure/table extractor for the readpaper one-click worker.

WHY THIS EXISTS
---------------
In the headless (`claude -p`) flow, Claude's own Bash/Read tools are spawned
under Chrome and intermittently lose Full-Disk-Access via macOS TCC
responsible-process attribution. When that happens Claude cannot read the PDF
or run pdftoppm, so every figure silently degrades to a hand-drawn inline SVG.

The *worker* shell (readpaper_run.sh), by contrast, is a plain bash that Chrome
spawns with FDA — its own `curl`/`python3` file IO on ~/Desktop succeeds every
run. So we do all pixel work HERE, before Claude starts, and hand Claude a
folder of already-cropped PNGs plus a manifest. Claude then only needs to emit
`<img src="figs/<id>/figN.png">` by caption — no PDF/vision access required.

METHOD (poppler + PIL + numpy; no PyMuPDF — it is broken in this env)
--------------------------------------------------------------------
1. `pdftotext -bbox` gives every word's bbox in PDF points. We parse it per page.
2. COLUMN MODEL. Two-column papers (ICML/NeurIPS style) glue the left- and
   right-column words of one y-row into a single "line" if grouped by y alone,
   which corrupts caption text and makes crops span both columns. So we first
   detect whether a page is 1- or 2-column (a recurring central whitespace
   gutter) and split each y-row at that gutter. Each resulting line is tagged
   col 0 (left) / 1 (right) / 'full' (a line that crosses the gutter
   *continuously*, e.g. a page-spanning figure caption).
3. CAPTIONS. A line starting with "Figure N" / "Fig. N" / "Table N" (N required,
   ':' or '.' after N) is a caption. Its crop x-range is its column's width, or
   the full content width if the caption line spans the page.
4. FIGURE band (graphics are non-text ink): rasterise the page, build a text
   mask from the word bboxes, and take `graphics = dark & ~text`. The figure is
   the contiguous band of graphics-ink rows directly above the caption, bounded
   by the first wide blank gap (which separates it from the paragraph above).
   This is column-aware and immune to body-text bleed (e.g. an abstract above a
   top-of-page figure no longer leaks in, because text is masked out).
5. TABLE band (a table is text, so step 4 sees nothing): inside the caption's
   column, find the long horizontal rules (booktabs \toprule/\bottomrule = a
   contiguous dark run >= 0.6 * column width). Crop [first rule, last rule].
   If no rules, fall back to a caption->next-boundary window (low_conf).
6. Outputs `<outdir>/fig<N>.png|table<N>.png` and `<outdir>/manifest.json`
   (one entry per crop: label/num/page/file/clip[pts]/px/caption/low_conf/
   needs_review/reason). `needs_review=true` (low_conf fallback, or a
   suspiciously flat figure band) tells the explainer step to VISUALLY VERIFY
   the crop against the caption and re-crop / re-typeset it if it is wrong,
   rather than embedding it blindly.

USAGE
-----
    extract_figures.py <pdf> <outdir> [--dpi 150]
Exit code is 0 on a readable PDF (missing captions => empty manifest);
non-zero only on argument/IO errors so the worker can log a WARN.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from html import unescape

DPI = 150
PT_PER_IN = 72.0

# A real caption line starts with "Figure N:" / "Figure N." / "Table N:" etc.
# Requiring a ':' or '.' immediately after the number separates a caption from
# an in-text reference ("Figure 5 shows ...") whose number is followed by a word.
CAPTION_RE = re.compile(r"^\s*(figure|fig|table)\s*\.?\s*(\d+)\s*[.:]", re.IGNORECASE)

WORD_RE = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
    re.IGNORECASE | re.DOTALL,
)
PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">', re.IGNORECASE)

# Heuristic thresholds (PDF points unless noted).
GUTTER_GAP = 6.0        # min central word-gap (pts) that marks a column gutter
LINE_TOL = 4.0          # words within this y-gap belong to the same line
PAD = 4.0               # crop padding
FIG_BLANK_GAP = 12.0    # blank band (pts) above the figure = its top edge
FIG_WINDOW = 240.0      # fallback figure height when no graphics ink is found
TABLE_WINDOW = 220.0    # fallback table window below the caption
MIN_BAND = 24.0         # reject bands thinner than this
MIN_TALL = 50.0         # figure band thinner than this (pts) is suspiciously flat
                        # (likely a truncated sliver) -> flag needs_review


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def parse_pages(pdf: str):
    """Return [{'w','h','words':[(x0,y0,x1,y1,text)]}] in PDF points per page."""
    xml = run(["pdftotext", "-bbox", pdf, "-"])
    pages = []
    chunks = PAGE_RE.split(xml)  # [pre, w, h, body, w, h, body, ...]
    for i in range(1, len(chunks), 3):
        w, h, body = float(chunks[i]), float(chunks[i + 1]), chunks[i + 2]
        words = []
        for m in WORD_RE.finditer(body):
            x0, y0, x1, y1 = (float(m.group(k)) for k in range(1, 5))
            txt = unescape(m.group(5)).strip()
            if not txt:
                continue
            # Drop pure-margin glyphs (rotated arXiv stamp / line numbers at the
            # far left). Their tall rotated bboxes otherwise merge into adjacent
            # body lines during y-grouping and corrupt line/neighbour detection.
            xc = (x0 + x1) / 2.0
            if xc < 55.0 or xc > w - 18.0:
                continue
            words.append((x0, y0, x1, y1, txt))
        pages.append({"w": w, "h": h, "words": words})
    return pages


def _mkline(ws):
    ws = sorted(ws, key=lambda t: t[0])
    return {
        "words": ws,
        "x0": min(t[0] for t in ws), "y0": min(t[1] for t in ws),
        "x1": max(t[2] for t in ws), "y1": max(t[3] for t in ws),
        "text": " ".join(t[4] for t in ws),
    }


def group_by_y(words):
    """Group words into full-width text lines by vertical position (top->bottom)."""
    lines = []
    for w in sorted(words, key=lambda t: (t[1], t[0])):
        placed = False
        for ln in lines:
            if abs(w[1] - ln["y0"]) <= LINE_TOL or (w[1] < ln["y1"] and w[3] > ln["y0"]):
                ln["_w"].append(w)
                ln["y0"] = min(ln["y0"], w[1]); ln["y1"] = max(ln["y1"], w[3])
                placed = True
                break
        if not placed:
            lines.append({"_w": [w], "y0": w[1], "y1": w[3]})
    out = [_mkline(ln["_w"]) for ln in lines]
    out.sort(key=lambda l: l["y0"])
    return out


def doc_gutter(raw_per_page, pages):
    """Return the column gutter x for a 2-column *document*, else None.

    A single page can have too few clean 2-column body rows to decide reliably
    (e.g. a page dominated by a full-width figure plus a short table). The column
    geometry is fixed across the whole paper, so we pool the evidence: every
    y-row whose words split into a left and a right group across a wide central
    gap contributes its split-x. If enough rows agree document-wide, the paper is
    2-column and the gutter is the median split-x. split_columns() then only
    actually splits a line when it really has a gap at that x, so genuinely
    single-column pages (title, appendix) are left intact.
    """
    centers = []
    for raw, pg in zip(raw_per_page, pages):
        mid = pg["w"] / 2.0
        for ln in raw:
            ws = ln["words"]
            if len(ws) < 4:
                continue
            best = None
            for i in range(len(ws) - 1):
                gap = ws[i + 1][0] - ws[i][2]
                c = (ws[i][2] + ws[i + 1][0]) / 2.0
                if (abs(c - mid) <= 40.0 and gap >= GUTTER_GAP
                        and (i + 1) >= 2 and (len(ws) - i - 1) >= 2):
                    if best is None or gap > best[0]:
                        best = (gap, c)
            if best:
                centers.append(best[1])
    if len(centers) >= 20:
        centers.sort()
        return centers[len(centers) // 2]
    return None


def split_columns(raw_lines, page_w):
    """Split each y-row at the column gutter; tag every line col 0 / 1 / 'full'.

    The gutter x wanders a little row to row (outdented run-in headers, ragged
    column edges), so rather than bracket a fixed x we split at the *largest gap
    in the central band* that also stands out from the line's own word spacing.
    A continuous full-width caption has only ordinary inter-word gaps there, so
    it is left whole ('full'); a glued 2-column row has a gutter gap several
    times wider than its word spacing, so it is split into left + right.
    """
    mid = page_w / 2.0
    band = 0.09 * page_w
    out = []
    for ln in raw_lines:
        ws = ln["words"]
        split_at = None
        if len(ws) >= 4:
            gaps = [ws[i + 1][0] - ws[i][2] for i in range(len(ws) - 1)]
            sg = sorted(g for g in gaps if g > 0)
            med = sg[len(sg) // 2] if sg else 1.0
            best = 0.0
            for i in range(len(ws) - 1):
                c = (ws[i][2] + ws[i + 1][0]) / 2.0
                g = gaps[i]
                if (abs(c - mid) <= band and g >= 8.0 and g >= 2.2 * med
                        and (i + 1) >= 2 and (len(ws) - i - 1) >= 2 and g > best):
                    best = g; split_at = i
        pieces = [ws[: split_at + 1], ws[split_at + 1:]] if split_at is not None else [ws]
        for p in pieces:
            if not p:
                continue
            nl = _mkline(p)
            if nl["x0"] < mid - 30 and nl["x1"] > mid + 30:
                nl["col"] = "full"
            else:
                nl["col"] = 0 if (nl["x0"] + nl["x1"]) / 2.0 < mid else 1
            out.append(nl)
    out.sort(key=lambda l: (l["y0"], l["x0"]))
    return out


def column_bounds(lines, col):
    """Left/right content x of one column (excludes spanning lines)."""
    xs = [ln for ln in lines if ln.get("col") == col]
    if not xs:
        xs = lines
    return min(ln["x0"] for ln in xs), max(ln["x1"] for ln in xs)


# ---- pixel helpers -----------------------------------------------------------

def render_page(pdf, page, dpi, tmp_prefix):
    run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page),
         "-png", "-singlefile", pdf, tmp_prefix])
    return tmp_prefix + ".png"


def page_arrays(img, words, scale):
    """Return (dark, graphics) boolean arrays [H,W]; graphics = ink minus text."""
    import numpy as np
    a = np.asarray(img.convert("L"))
    dark = a < 160
    txt = np.zeros(a.shape, bool)
    H, W = a.shape
    for x0, y0, x1, y1, _ in words:
        X0 = max(0, int(x0 * scale) - 1); Y0 = max(0, int(y0 * scale) - 1)
        X1 = min(W, int(x1 * scale) + 2); Y1 = min(H, int(y1 * scale) + 2)
        if X1 > X0 and Y1 > Y0:
            txt[Y0:Y1, X0:X1] = True
    return dark, (dark & ~txt)


def figure_box_px(graphics, dark, scale, page_w, top_bound_pt, cap_top_pt):
    """Tight (x0,y0,x1,y1) in pts of the figure between top_bound and the caption.

    The vertical search window is [top_bound, caption]. top_bound is the bottom of
    the nearest *wide prose line above the figure* (a body paragraph or another
    float's caption), computed by the caller. We bound by prose rather than by
    graphics sparseness because a plot's upper half is often near-empty (legend +
    blank axes), which would make a graphics-only walk stop inside the plot and
    clip its top. A figure's own legend / axis labels are short, so they are not
    "wide prose" and never act as the bound — they stay inside the crop.

    Within that window the bbox is measured from ALL ink (`dark`), so axis tick
    labels, legends and panel titles are included; leading/trailing whitespace is
    trimmed automatically. x is clipped to the text margins so the vertical arXiv
    margin stamp cannot leak in. `graphics` is used only to confirm the window
    actually contains a figure (else we return None and the caller falls back).
    """
    import numpy as np
    H, W = graphics.shape
    Xl = max(0, int(95 * scale))
    Xr = min(W, int((page_w - 95) * scale))
    capY = min(H, int(cap_top_pt * scale))
    topB = max(0, int(top_bound_pt * scale) + 3)   # +3px clears prose descenders
    topB = max(topB, capY - int(470 * scale))      # clamp pathological windows
    if Xr - Xl < 8 or capY - topB < 8:
        return None
    if graphics[topB:capY, Xl:Xr].sum() < 40:      # no real graphics => not a figure
        return None
    band = dark[topB:capY, Xl:Xr]
    rows = np.flatnonzero(band.sum(axis=1) > 1)
    cols = np.flatnonzero(band.sum(axis=0) > 1)
    if rows.size == 0 or cols.size == 0:
        return None
    y0 = (topB + rows[0]) / scale
    y1 = (topB + rows[-1] + 1) / scale
    x0 = (Xl + cols[0]) / scale
    x1 = (Xl + cols[-1] + 1) / scale
    return x0, y0, x1, y1


RULE_MIN_PX = 110   # a contiguous dark run this long (~0.73in @150dpi) is a rule,
                    # not text: glyph spacing breaks body runs after a few px, so
                    # only ruled lines / underlines reach this length.


def table_band_px(dark, scale, x0p, x1p, y_from_pt, y_to_pt):
    """Tight (x0,y0,x1,y1) pts spanning the first..last horizontal rule.

    A table is text, so the figure ink method sees nothing; instead we locate the
    booktabs rules (\\toprule/\\midrule/\\bottomrule) as long contiguous dark runs
    and crop between the topmost and bottommost rule, with x trimmed to the rules'
    own horizontal extent (the real table width, which is often far narrower than
    the text column it sits in).
    """
    import numpy as np
    H, W = dark.shape
    X0 = max(0, int(x0p * scale)); X1 = min(W, int(x1p * scale))
    Y0 = max(0, int(y_from_pt * scale)); Y1 = min(H, int(y_to_pt * scale))
    if X1 - X0 < 16 or Y1 - Y0 < 8:
        return None
    strip = dark[Y0:Y1, X0:X1]
    rule_rows = []  # (row, run_start, run_end) of the longest run in the row
    for r in range(strip.shape[0]):
        row = strip[r]
        idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        if idx.size < 2:
            continue
        runs = idx[1::2] - idx[::2]
        k = int(runs.argmax())
        if runs[k] >= RULE_MIN_PX:
            rule_rows.append((r, int(idx[2 * k]), int(idx[2 * k + 1])))
    if len(rule_rows) < 2:
        return None
    rtop = rule_rows[0][0]; rbot = rule_rows[-1][0]
    xs0 = min(s for _, s, _ in rule_rows); xs1 = max(e for _, _, e in rule_rows)
    return ((X0 + xs0) / scale, (Y0 + rtop) / scale,
            (X0 + xs1) / scale, (Y0 + rbot) / scale)


# ---- detection ---------------------------------------------------------------

def detect_page(pg, page_idx, raw, gutter):
    """Yield caption records (without pixels) for one page."""
    lines = split_columns(raw, pg["w"]) if gutter else (
        [dict(ln, col="full") for ln in raw])
    caps = [ln for ln in lines if CAPTION_RE.match(ln["text"])]
    # Figure top bounds = lines that sit *above* a figure and mark its top:
    #   - any caption line (another float's caption), and
    #   - body-paragraph lines: wide & word-rich AND part of a multi-line block
    #     (a neighbour prose line within ~18pt). The "neighbour" test is what
    #     rejects a figure's own bold axis title / annotation, which is wide and
    #     word-rich too but sits ALONE between the plot and the caption — without
    #     it the bound would land inside the figure and clip it.
    cand = [ln for ln in lines
            if len(ln["words"]) >= 6 and (ln["x1"] - ln["x0"]) >= 150.0]
    def _neighboured(ln):
        # A paragraph line has another prose line one line-height above/below it
        # (vertical Δy in ~[5,20]pt) that overlaps it horizontally (same column).
        # Same-y mates (two side-by-side panel titles, Δy≈0) do NOT qualify.
        c = (ln["y0"] + ln["y1"]) / 2.0
        for o in cand:
            if o is ln:
                continue
            d = abs(((o["y0"] + o["y1"]) / 2.0) - c)
            if 5.0 <= d <= 20.0 and o["x0"] < ln["x1"] and o["x1"] > ln["x0"]:
                return True
        return False

    def _cap_block_bottom(cap):
        # A caption can wrap to several lines; extend its bottom over the
        # continuation lines (no vertical gap, same column) so a figure below it
        # is bounded under the WHOLE caption, not just its first line.
        bot = cap["y1"]
        while True:
            nxt = [ln for ln in lines
                   if bot - 1 <= ln["y0"] <= bot + 7 and ln is not cap
                   and not CAPTION_RE.match(ln["text"])
                   and ln["x0"] < cap["x1"] and ln["x1"] > cap["x0"]]
            b2 = max((ln["y1"] for ln in nxt), default=bot)
            if b2 <= bot:
                return bot
            bot = b2

    bound_lines = [ln for ln in cand if _neighboured(ln)]
    bound_lines += [{"y1": _cap_block_bottom(c), "x0": c["x0"], "x1": c["x1"]}
                    for c in caps]
    for cap in caps:
        m = CAPTION_RE.match(cap["text"])
        label = "Table" if m.group(1).lower() == "table" else "Figure"
        num = int(m.group(2))
        col = cap.get("col")
        if col in (0, 1) and gutter:
            tx = column_bounds(lines, col)          # table confined to a column
        elif lines:
            # full-width: the caption can be FAR narrower than the table (a short
            # "Table N: foo" over a wide grid), which clips right-hand columns.
            # Use the page's content x-extent instead so wide tables aren't cut.
            tx = (min(ln["x0"] for ln in lines), max(ln["x1"] for ln in lines))
        else:
            tx = (cap["x0"], cap["x1"])
        # Figure top bound: bottom of the nearest bounding line above this caption
        # (the paragraph / other-float caption sitting above the figure).
        top_bound = max((ln["y1"] for ln in bound_lines if ln["y1"] < cap["y0"] - 2),
                        default=PAD)
        # Rule-search / fallback window: from just below this caption down to the
        # next *caption* (i.e. another float — a table caption sits above its
        # grid, which can be a paragraph's height away), else the page bottom.
        below = [ln["y0"] for ln in lines
                 if ln["y0"] > cap["y1"] + 6 and CAPTION_RE.match(ln["text"])]
        next_b = min(below, default=pg["h"] - PAD)
        yield {
            "label": label, "num": num, "page": page_idx,
            "table_x": tx, "cap_x": (cap["x0"], cap["x1"]),
            "cap_y": (cap["y0"], cap["y1"]), "top_bound": top_bound,
            "next_b": next_b, "caption": cap["text"][:300],
        }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dpi = DPI
    for a in sys.argv[1:]:
        if a.startswith("--dpi"):
            dpi = int(a.split("=", 1)[1]) if "=" in a else DPI
    if len(args) < 2:
        print("usage: extract_figures.py <pdf> <outdir> [--dpi N]", file=sys.stderr)
        return 2
    pdf, outdir = args[0], args[1]
    if not os.path.isfile(pdf):
        print(f"no such pdf: {pdf}", file=sys.stderr)
        return 2

    from PIL import Image  # late import: a missing PIL is then a clean error

    os.makedirs(outdir, exist_ok=True)
    pages = parse_pages(pdf)
    scale = dpi / PT_PER_IN

    raw_per_page = [group_by_y(pg["words"]) for pg in pages]
    gutter = doc_gutter(raw_per_page, pages)

    # Collect records, dedup by (label,num) keeping the first occurrence.
    records = []
    seen = set()
    for pidx, (pg, raw) in enumerate(zip(pages, raw_per_page), start=1):
        for rec in detect_page(pg, pidx, raw, gutter):
            key = (rec["label"], rec["num"])
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)

    cache: dict[int, tuple] = {}   # page -> (img, dark, graphics)

    def page_px(p):
        if p not in cache:
            tmp = os.path.join(outdir, f".page-{p}")
            png = render_page(pdf, p, dpi, tmp)
            img = Image.open(png).convert("RGB")
            dark, graphics = page_arrays(img, pages[p - 1]["words"], scale)
            try:
                os.remove(png)
            except OSError:
                pass
            cache[p] = (img, dark, graphics)
        return cache[p]

    manifest = []
    for rec in records:
        p = rec["page"]
        img, dark, graphics = page_px(p)
        page_w = pages[p - 1]["w"]; page_h = pages[p - 1]["h"]
        cy0, cy1 = rec["cap_y"]
        low_conf = False
        reason = ""

        if rec["label"] == "Figure":
            box = figure_box_px(graphics, dark, scale, page_w, rec["top_bound"], cy0)
            if box is None or (box[3] - box[1]) < MIN_BAND:
                # no graphics ink found: fall back to a window at the caption width
                cl, cr = rec["cap_x"]
                top, bot = max(PAD, cy0 - FIG_WINDOW), cy0
                low_conf = True
                reason = "no graphics ink found; fixed-window fallback (verify the crop covers the real figure)"
            else:
                cl, top, cr, bot = box
                if (bot - top) < MIN_TALL:
                    # band detected but very flat: a likely-truncated sliver
                    # (e.g. only the bottom row of a tall multi-panel figure).
                    reason = f"figure band only {bot - top:.0f}pt tall; may be truncated (verify it is the WHOLE figure)"
        else:  # Table
            cl, cr = rec["table_x"]
            search_bot = min(rec["next_b"], cy1 + 400)
            box = table_band_px(dark, scale, cl, cr, cy1, search_bot)
            if box is None or (box[3] - box[1]) < MIN_BAND:
                top = cy1
                bot = min(rec["next_b"], cy1 + TABLE_WINDOW)
                low_conf = True
                reason = "no booktabs rules found; fixed-window fallback often grabs body text, not a table (re-typeset numbers as an HTML table, or re-crop the real table region)"
            else:
                # Take only the VERTICAL extent from the rules; keep x at the
                # column/content width (cl,cr). Booktabs rules — especially a
                # \cmidrule under a spanning header — can be narrower than the
                # widest data cell, so trimming x to the rule extent clips the
                # right-hand columns (observed in 2307.04895 Table 7).
                _, top, _, bot = box

        clip = [round(max(0.0, cl - PAD), 1), round(max(0.0, top - PAD), 1),
                round(min(page_w, cr + PAD), 1),
                round(min(page_h, bot + PAD), 1)]
        x0, y0, x1, y1 = (v * scale for v in clip)
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(img.width, int(x1)), min(img.height, int(y1))
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        crop = img.crop((x0, y0, x1, y1))
        name = f"{'table' if rec['label'] == 'Table' else 'fig'}{rec['num']}.png"
        crop.save(os.path.join(outdir, name))
        manifest.append({
            "label": rec["label"], "num": rec["num"], "page": p,
            "clip": clip, "caption": rec["caption"], "low_conf": low_conf,
            "needs_review": bool(low_conf or reason), "reason": reason,
            "file": name, "px": [crop.width, crop.height],
        })

    manifest.sort(key=lambda r: (r["label"], r["num"]))
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    print(f"extract_figures: {len(manifest)} crop(s) -> {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
