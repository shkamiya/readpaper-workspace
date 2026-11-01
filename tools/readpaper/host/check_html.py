#!/usr/bin/env python3
"""Check (and mechanically repair) the math + session-id defects an explainer can ship with.

WHY THIS EXISTS
---------------
`readpaper_run.sh` used to finalize the generated HTML (session id + stamp) and
then immediately rebuild the index and fire a "done" notification. Nothing looked
at whether the page's *math actually renders*. A full-scale audit of the 100
explainers in html-expl/ on 2026-08-16 (static analysis + compiling every math
span with real KaTeX 0.16.22 under node + rendering pages in headless Chrome)
found 13 of them visibly broken, in five distinct modes:

  M1  raw `<` inside math          7 files, 31 spots  <- worst, and undocumented
  M2  KaTeX-unsupported macro      2 files,  4 spots
  M3  unpaired `$`                 2 files
  M4  delimiter used but not registered   5 files (all MathJax, missing `\\[`)
  M5  `throwOnError` not false     2 files

M1 is the nasty one and deserves the long explanation. Writing

    <li>$c_{<t} := (c_1,\\dots,c_{t-1})$: ...</li>

makes the *HTML tokenizer* (not KaTeX) read `<t}` as a start tag named `t}`. The
rest of the sentence gets eaten as that tag's attributes and disappears from the
page entirely; KaTeX only ever sees text nodes, so it cannot even try. Confirmed
in a real Chrome DOM dump: elements literally named `t}$`, `t};x)`, `s};x)`.
`x_{<t}` (prefix indexing) is standard notation in language-model papers, so this
recurs on roughly every other paper unless something mechanical stops it.

Modes M1, M4, M5 (plus block math in <figcaption>) are deterministic to repair,
so `--fix` repairs them. M2 and M3 change meaning, so they are only reported —
loudly, into the job log and the macOS notification.

DESIGN NOTE: why a hand-rolled tokenizer instead of html.parser
---------------------------------------------------------------
The defect we most need to see (M1) is precisely a string the standard parser
mis-tokenizes: html.parser happily turns `<t}` into a start tag and hands back
mangled attributes, losing the offsets we need to repair the source. So we split
the raw source ourselves with a regex for *plausible* tags

    </?[A-Za-z][A-Za-z0-9-]*(?:\\s[^<>]*)?/?>

which matches `<strong>` and `<img src="...">` but NOT `<t}` (the name is
followed by `}`, which is neither whitespace nor `>`). Everything the regex does
not claim stays in a prose region — and that asymmetry is exactly the detector.

USAGE
-----
    check_html.py [--fix] [--json] [--quiet] <html-or-dir> [...]

Exit code: 0 when nothing unrepairable is left, 1 when findings remain (after
--fix, if given). The worker logs the report but never fails a run over it.
"""
from __future__ import annotations

import json
import os
import re
import sys

# --------------------------------------------------------------------------- constants

# Regions whose text the math renderer never walks, and which we must not rewrite.
PROTECTED_RE = re.compile(
    r"<!--.*?-->"
    r"|<script\b[^>]*>.*?</script>"
    r"|<style\b[^>]*>.*?</style>"
    r"|<pre\b[^>]*>.*?</pre>"
    r"|<code\b[^>]*>.*?</code>",
    re.S | re.I,
)
# A *plausible* HTML tag. Deliberately strict: see the module docstring.
TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>", re.S)
# A `<` that the HTML tokenizer will treat as opening markup rather than as text.
# Per the HTML5 "tag open state": only `!`, `/`, `?` and an ASCII letter leave the
# data state; anything else (a space, a digit, a backslash, ...) makes the `<` an
# ordinary character. So `$a < b$` and `$\rho(A)<1$` are harmless, while `$x_{<t}$`
# and `$H_{<t}$` are not - and it is exactly the latter shape that TeX subscripts
# produce all the time.
TAG_OPENING_LT_RE = re.compile(r"<(?=[A-Za-z!/?])")

SCRIPT_BODY_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
SCRIPT_OPEN_RE = re.compile(r"<script\b[^>]*>", re.I)
ONLOAD_RE = re.compile(r"""onload\s*=\s*(["'])(.*?)\1""", re.S | re.I)
JSSTR_RE = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""", re.S)
FIGCAPTION_RE = re.compile(r"<figcaption\b[^>]*>(.*?)</figcaption>", re.S | re.I)

# (left, right, display) in the order auto-render tries them: longest left first.
CANON_DELIMS = [("$$", "$$", True), ("\\[", "\\]", True),
                ("\\(", "\\)", False), ("$", "$", False)]

CANON_KATEX_DELIM_JS = """[
    {left: '$$', right: '$$', display: true},
    {left: '$',  right: '$',  display: false},
    {left: '\\\\[', right: '\\\\]', display: true},
    {left: '\\\\(', right: '\\\\)', display: false}
  ]"""

CANON_MJ_INLINE = "[['$','$'], ['\\\\(','\\\\)']]"
CANON_MJ_DISPLAY = "[['$$','$$'], ['\\\\[','\\\\]']]"

# LaTeX that KaTeX does not implement, with the substitute to use instead. Keys are
# matched as whole control sequences (or \begin{env}) inside a math span.
KATEX_UNSUPPORTED = {
    r"\textsc": r"\mathrm{...} or \text{...}",
    r"\bm": r"\boldsymbol",
    r"\mathds": r"\mathbb",
    r"\nicefrac": r"\tfrac",
    r"\mbox": r"\text",
    r"\eqref": "drop it (KaTeX has no cross-references)",
    r"\label": "drop it",
    r"\shortstack": r"\substack",
    r"\stackengine": "rewrite by hand",
    r"\intertext": "split the display",
}
KATEX_UNSUPPORTED_ENVS = {
    "psmallmatrix": "smallmatrix (add \\left( \\right) yourself)",
    "bsmallmatrix": "smallmatrix (add \\left[ \\right] yourself)",
    "align": "aligned",
    "align*": "aligned",
    "eqnarray": "aligned",
    "gather": "gathered",
    "gather*": "gathered",
    "multline": "aligned",
}

PLACEHOLDERS = ("__AGENT_SESSION_ID__", "__CLAUDE_SESSION_ID__")
VISIBLE_SID_RE = re.compile(r"(?:claude-code|codex|agent) session:")

# A math span this long that contains sentence-ending CJK punctuation is not math —
# it is prose swallowed by an unpaired delimiter.
PROSE_SPAN_MIN_LEN = 200
PROSE_MARKERS = "。、！？"


# --------------------------------------------------------------------------- scanning

def protected_spans(src: str):
    return [(m.start(), m.end()) for m in PROTECTED_RE.finditer(src)]


def prose_regions(src: str):
    """(start, end) of every run of raw source the math renderer sees as text.

    Excludes protected elements and everything TAG_RE recognises as a real tag.
    A KaTeX auto-render pass only ever finds delimiter pairs *within one text
    node*, so scanning per region matches the renderer's own behaviour.
    """
    out = []
    cur, allowed = 0, []
    for s, e in protected_spans(src):
        if s > cur:
            allowed.append((cur, s))
        cur = max(cur, e)
    allowed.append((cur, len(src)))
    for a, b in allowed:
        sub = src[a:b]
        last = 0
        for m in TAG_RE.finditer(sub):
            if m.start() > last:
                out.append((a + last, a + m.start()))
            last = m.end()
        if last < len(sub):
            out.append((a + last, b))
    return out


def bracket_span(text: str, start: int) -> tuple[int, int]:
    """(open, close+1) of the [...] literal beginning at/after `start`; (-1,-1) if none."""
    i = text.find("[", start)
    if i < 0:
        return (-1, -1)
    depth, j, instr, q = 0, i, False, ""
    while j < len(text):
        c = text[j]
        if instr:
            if c == "\\":
                j += 2
                continue
            if c == q:
                instr = False
        elif c in "\"'":
            instr, q = True, c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return (i, j + 1)
        j += 1
    return (-1, -1)


def unescape_js(s: str) -> str:
    return (s.replace("\\\\", "\x00").replace("\\'", "'")
             .replace('\\"', '"').replace("\x00", "\\"))


def page_js(src: str) -> str:
    """Inline <script> bodies plus onload= attribute JS (auto-render usually lives there)."""
    parts = SCRIPT_BODY_RE.findall(src)
    for tag in SCRIPT_OPEN_RE.findall(src):
        m = ONLOAD_RE.search(tag)
        if m:
            parts.append(m.group(2).replace("&quot;", '"')
                                   .replace("&#39;", "'").replace("&amp;", "&"))
    return "\n".join(parts)


def detect_renderer(src: str, js: str) -> str:
    if re.search(r"katex(?:@|/|\.min)", src, re.I) or "renderMathInElement" in js:
        return "katex"
    if "MathJax" in src:
        return "mathjax"
    return "none"


def registered_delims(js: str, renderer: str) -> set[str]:
    """The left-delimiters the page actually registers with its renderer."""
    reg: set[str] = set()
    if renderer == "katex":
        for m in re.finditer(r"delimiters\s*:", js):
            a, b = bracket_span(js, m.end())
            if a < 0:
                continue
            arr = js[a:b]
            for lm in re.finditer(r"left\s*:\s*", arr):
                sm = JSSTR_RE.match(arr, lm.end())
                if sm:
                    reg.add(unescape_js(sm.group(2)))
    elif renderer == "mathjax":
        for key, default in (("inlineMath", {"\\("}), ("displayMath", {"$$", "\\["})):
            found = False
            for m in re.finditer(key + r"\s*:", js):
                a, b = bracket_span(js, m.end())
                if a < 0:
                    continue
                found = True
                for sm in JSSTR_RE.finditer(js[a:b]):
                    reg.add(unescape_js(sm.group(2)))
            if not found:
                reg |= default          # MathJax's own defaults still apply
    return reg


def math_spans(src: str, regions):
    """Every (start, end, left, display) math span, greedy longest-left-first.

    Scans with all four canonical delimiter pairs regardless of what the page
    registers: an *unregistered* pair still needs reporting (M4), and the raw-`<`
    defect (M1) is a browser-tokenizer bug that bites no matter which renderer
    (or none) is loaded.
    """
    spans = []
    for a, b in regions:
        text, i = src[a:b], 0
        while i < len(text):
            for left, right, disp in CANON_DELIMS:
                if not text.startswith(left, i):
                    continue
                if left == "$" and i > 0 and text[i - 1] == "\\":
                    continue                      # escaped literal dollar
                end = text.find(right, i + len(left))
                if end < 0:
                    i += len(left)
                    break
                spans.append((a + i, a + end + len(right), left, disp))
                i = end + len(right)
                break
            else:
                i += 1
    return spans


def span_body(src: str, s: int, e: int, left: str) -> str:
    return src[s + len(left):e - len(left)]


# --------------------------------------------------------------------------- checks

def analyse(path: str):
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    js = page_js(src)
    renderer = detect_renderer(src, js)
    reg = registered_delims(js, renderer)
    regions = prose_regions(src)
    spans = math_spans(src, regions)

    findings = []

    def add(fid, msg, fixable, detail=""):
        findings.append({"id": fid, "msg": msg, "fixable": fixable, "detail": detail})

    # --- M1: raw < / > inside a math span -------------------------------------
    raw_lt = []
    for s, e, left, _ in spans:
        body = span_body(src, s, e, left)
        hits = TAG_OPENING_LT_RE.findall(body)
        if hits:
            raw_lt.append((s, e, left, body, len(hits)))
    if raw_lt:
        add("raw-lt-in-math",
            f"{sum(r[4] for r in raw_lt)} raw `<` in {len(raw_lt)} math span(s) start an "
            f"HTML tag; the tokenizer turns e.g. `<t}}` into a start tag and eats the rest "
            f"of the sentence",
            True, f"first: {raw_lt[0][3][:60]!r}")

    # --- M4: delimiter used but not registered --------------------------------
    if renderer != "none":
        used = {}
        for _, _, left, _ in spans:
            used[left] = used.get(left, 0) + 1
        missing = {d: n for d, n in used.items() if d not in reg}
        if missing:
            add("delim-not-registered",
                f"delimiters used but not registered with {renderer}: "
                + ", ".join(f"{d} x{n}" for d, n in sorted(missing.items()))
                + f" (registered: {sorted(reg) or 'none'})",
                True)
    elif spans:
        add("no-renderer", f"{len(spans)} math span(s) but neither KaTeX nor MathJax "
            "is loaded", False)

    # --- M5: throwOnError -----------------------------------------------------
    if renderer == "katex" and not re.search(r"throwOnError\s*:\s*false", js):
        add("throw-on-error",
            "KaTeX options lack `throwOnError: false`; a failing formula is left as "
            "raw TeX source instead of being flagged", True)

    # --- figcaption block math ------------------------------------------------
    figcap = sum(1 for m in FIGCAPTION_RE.finditer(src)
                 if "$$" in m.group(1) or "\\[" in m.group(1))
    if figcap:
        add("figcaption-block-math",
            f"{figcap} <figcaption> use block math; it breaks the caption into "
            "centred stand-alone lines", True)

    # --- M3: unpaired $ -------------------------------------------------------
    prose_like = [(s, e, left, span_body(src, s, e, left)) for s, e, left, _ in spans]
    swallowed = [b for _, _, _, b in prose_like
                 if len(b) > PROSE_SPAN_MIN_LEN and any(c in b for c in PROSE_MARKERS)]
    if swallowed:
        add("swallowed-prose",
            f"{len(swallowed)} math span(s) swallowed prose (>{PROSE_SPAN_MIN_LEN} chars "
            "with sentence punctuation) - a delimiter is left open",
            False, f"first: {swallowed[0][:80]!r}")
    # Parity is counted PER TEXT REGION, because auto-render only pairs delimiters
    # inside a single text node. Counting over the whole document instead makes two
    # unrelated stray `$` cancel out (and makes one stray `$` look like a huge
    # cross-section math span, which is how an earlier draft of this check produced
    # false positives).
    odd = []
    for a, b in regions:
        seg = src[a:b]
        if len(re.findall(r"(?<!\\)\$", seg)) % 2:
            odd.append(seg.strip())
    if odd:
        add("stray-dollar",
            f"{len(odd)} text run(s) hold an odd number of unescaped $ - a literal "
            "dollar sign (prices, shell prompts). Rewrite it without the sign "
            "(\"2 USD\"), because there is no way to escape it: see escaped-dollar",
            False, f"first: {odd[0][:80]!r}")
    # `\$` looks like the obvious escape and is not one. KaTeX's auto-render splits the
    # text on raw delimiters BEFORE any TeX is parsed, so `$\$2$` is cut at the second
    # `$` and KaTeX is handed the body `\`, which fails to parse. (MathJax's FindTeX
    # does honour `\$`, so this is renderer-specific - and every page here is KaTeX by
    # default.) Measured 2026-08-16 while repairing a price in an explainer.
    esc = sum(src[a:b].count("\\$") for a, b in regions)
    if esc:
        add("escaped-dollar",
            f"{esc} occurrence(s) of \\$ in prose; KaTeX auto-render splits on the raw "
            "$ before parsing TeX, so the escape does not work - reword instead",
            False)

    # --- M2: KaTeX-unsupported macros ----------------------------------------
    bad_macros = {}
    for _, _, _, body in prose_like:
        for cs, alt in KATEX_UNSUPPORTED.items():
            if re.search(re.escape(cs) + r"(?![A-Za-z])", body):
                bad_macros[cs] = alt
        for m in re.finditer(r"\\begin\{([A-Za-z*]+)\}", body):
            env = m.group(1)
            if env in KATEX_UNSUPPORTED_ENVS:
                bad_macros["\\begin{%s}" % env] = KATEX_UNSUPPORTED_ENVS[env]
    if bad_macros and renderer == "katex":
        add("katex-unsupported",
            "KaTeX cannot parse: "
            + ", ".join(f"{k} -> use {v}" for k, v in sorted(bad_macros.items())),
            False)

    # --- session id -----------------------------------------------------------
    if any(p in src for p in PLACEHOLDERS):
        add("session-id-placeholder",
            "literal session-id placeholder still in the file (finalize_html.py or the "
            "Stop hook should have replaced it)", False)
    elif not VISIBLE_SID_RE.search(src):
        add("session-id-missing", "no visible `<agent> session: <code>...</code>` line",
            False)

    return {"src": src, "js": js, "renderer": renderer, "reg": reg,
            "spans": spans, "findings": findings}


# --------------------------------------------------------------------------- repairs

def fix_figcaptions(src: str, renderer: str) -> tuple[str, int]:
    """Demote block math inside <figcaption> to inline math."""
    inl_l, inl_r = ("\\(", "\\)") if renderer == "mathjax" else ("$", "$")
    n = 0

    def repl(m):
        nonlocal n
        cap = m.group(1)
        new = re.sub(r"\$\$(.+?)\$\$", lambda x: inl_l + x.group(1).strip() + inl_r,
                     cap, flags=re.S)
        new = re.sub(r"\\\[(.+?)\\\]", lambda x: inl_l + x.group(1).strip() + inl_r,
                     new, flags=re.S)
        if new != cap:
            n += 1
        return m.group(0).replace(cap, new)

    return FIGCAPTION_RE.sub(repl, src), n


def fix_raw_lt(src: str) -> tuple[str, int]:
    """Entity-escape `<` inside math spans so the HTML tokenizer leaves it alone.

    The browser turns `&lt;` back into a `<` character in the text node before
    KaTeX reads it, so the TeX meaning is unchanged - only the tokenizer stops
    mis-reading `<t}` as a start tag.

    Only `<` is touched. A bare `>` in text is legal HTML and renders as itself,
    and `&` must stay raw because TeX uses it as the alignment separator inside
    matrix / aligned environments.
    """
    edits, n = [], 0
    for s, e, left, _ in math_spans(src, prose_regions(src)):
        bs, be = s + len(left), e - len(left)
        body = src[bs:be]
        new = TAG_OPENING_LT_RE.sub("&lt;", body)
        if new == body:
            continue
        edits.append((bs, be, new))
        n += 1
    for bs, be, new in sorted(edits, reverse=True):
        src = src[:bs] + new + src[be:]
    return src, n


def fix_katex_config(src: str) -> tuple[str, int]:
    """Force the canonical 4-delimiter set and throwOnError:false on the auto-render call."""
    n = 0
    for m in list(re.finditer(r"delimiters\s*:", src)):
        a, b = bracket_span(src, m.end())
        if a < 0:
            continue
        if src[a:b].strip() != CANON_KATEX_DELIM_JS.strip():
            src = src[:a] + CANON_KATEX_DELIM_JS + src[b:]
            n += 1
        break                                   # only the first (there is only one call)
    if re.search(r"throwOnError\s*:\s*true", src):
        src = re.sub(r"throwOnError\s*:\s*true", "throwOnError: false", src)
        n += 1
    elif "throwOnError" not in src:
        m = re.search(r"delimiters\s*:", src)
        if m:
            a, b = bracket_span(src, m.end())
            if a >= 0:
                src = src[:b] + ",\n  throwOnError: false" + src[b:]
                n += 1
    return src, n


def fix_mathjax_config(src: str) -> tuple[str, int]:
    """Register $...$ inline and $$...$$ / \\[...\\] display with MathJax."""
    n = 0
    for key, canon in (("inlineMath", CANON_MJ_INLINE), ("displayMath", CANON_MJ_DISPLAY)):
        m = re.search(key + r"\s*:", src)
        if m:
            a, b = bracket_span(src, m.end())
            if a >= 0 and src[a:b] != canon:
                src = src[:a] + canon + src[b:]
                n += 1
        else:
            t = re.search(r"tex\s*:\s*\{", src)
            if t:
                src = src[:t.end()] + f" {key}: {canon}," + src[t.end():]
                n += 1
    return src, n


def apply_fixes(path: str, info) -> tuple[int, list[str]]:
    src, renderer = info["src"], info["renderer"]
    ids = {f["id"] for f in info["findings"] if f["fixable"]}
    total, done = 0, []

    if "figcaption-block-math" in ids:
        src, k = fix_figcaptions(src, renderer)
        total += k
        done.append(f"figcaption-block-math x{k}")
    if "raw-lt-in-math" in ids:
        src, k = fix_raw_lt(src)
        total += k
        done.append(f"raw-lt-in-math x{k}")
    if renderer == "katex" and ids & {"delim-not-registered", "throw-on-error"}:
        src, k = fix_katex_config(src)
        total += k
        done.append(f"katex-config x{k}")
    if renderer == "mathjax" and "delim-not-registered" in ids:
        src, k = fix_mathjax_config(src)
        total += k
        done.append(f"mathjax-config x{k}")

    if total and src != info["src"]:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
    return total, done


# --------------------------------------------------------------------------- cli

def collect(args):
    out = []
    for a in args:
        if os.path.isdir(a):
            for e in sorted(os.scandir(a), key=lambda x: x.name):
                if e.is_file() and e.name.endswith(".html") and e.name != "index.html":
                    out.append(e.path)
        elif os.path.isfile(a):
            out.append(a)
        else:
            print(f"check_html: no such path: {a}", file=sys.stderr)
    return out


def main() -> int:
    argv = sys.argv[1:]
    do_fix = "--fix" in argv
    as_json = "--json" in argv
    quiet = "--quiet" in argv
    targets = collect([a for a in argv if not a.startswith("--")])
    if not targets:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: check_html.py [--fix] [--json] [--quiet] <html-or-dir> [...]",
              file=sys.stderr)
        return 2

    report, n_remaining, n_fixed = [], 0, 0
    for path in targets:
        info = analyse(path)
        fixed, what = (0, [])
        if do_fix and any(f["fixable"] for f in info["findings"]):
            fixed, what = apply_fixes(path, info)
            n_fixed += fixed
            info = analyse(path)                      # re-check the repaired file
        rem = info["findings"]
        n_remaining += len(rem)
        report.append({"file": os.path.basename(path), "renderer": info["renderer"],
                       "fixed": fixed, "fixed_what": what,
                       "findings": [{k: f[k] for k in ("id", "msg", "fixable", "detail")}
                                    for f in rem]})

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    elif not quiet:
        for r in report:
            if not r["findings"] and not r["fixed"]:
                continue
            print(f"{r['file']}  [{r['renderer']}]")
            if r["fixed"]:
                print(f"   FIXED {r['fixed']}: {', '.join(r['fixed_what'])}")
            for f in r["findings"]:
                mark = "FIXABLE" if f["fixable"] else "MANUAL "
                print(f"   {mark} {f['id']}: {f['msg']}")
                if f["detail"]:
                    print(f"           {f['detail']}")
        n_files_bad = sum(1 for r in report if r["findings"])
        print(f"check_html: {len(targets)} file(s), fixed {n_fixed}, "
              f"{n_remaining} finding(s) left in {n_files_bad} file(s)")

    return 1 if n_remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
