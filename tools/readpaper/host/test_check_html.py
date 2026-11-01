#!/usr/bin/env python3
"""Regression tests for check_html.py and finalize_html.py.

    python3 tools/readpaper/host/test_check_html.py

No test framework: this has to run from a bare worker environment where only the
standard library is guaranteed. Each case builds a throwaway html in a temp dir,
runs the checker (or the finalizer), and asserts on the findings.

The cases are the defects actually measured across html-expl/ on 2026-08-16, plus
the two "must NOT fire" cases that earlier drafts got wrong (a benign `a < b`, and
a concurrent worker's file that the Stop hook must not claim).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = _load("check_html")
F = _load("finalize_html")

KATEX_HEAD_FULL = """
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body, {
  delimiters: [
    {left: '$$', right: '$$', display: true},
    {left: '$',  right: '$',  display: false},
    {left: '\\\\[', right: '\\\\]', display: true},
    {left: '\\\\(', right: '\\\\)', display: false}
  ],
  throwOnError: false
});"></script>
"""
KATEX_HEAD_POOR = """
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body, {
  delimiters: [{left: '$$', right: '$$', display: true}]
});"></script>
"""
MATHJAX_HEAD_POOR = """
<script>window.MathJax = {tex: {inlineMath: [['\\\\(','\\\\)']], displayMath: [['$$','$$']]}};</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" async></script>
"""

FAILS: list[str] = []


def page(head: str, body: str, sid: str = "__AGENT_SESSION_ID__") -> str:
    return (f'<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
            f"<title>t</title>{head}</head><body><h1>t</h1>"
            f'<div class="meta">claude-code session: <code>{sid}</code></div>'
            f"{body}</body></html>")


def write(tmp: str, name: str, src: str) -> str:
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(src)
    return p


def ids(path: str) -> set[str]:
    return {f["id"] for f in C.analyse(path)["findings"]}


def check(label: str, cond: bool, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"   {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def case_raw_lt(tmp):
    print("raw `<` inside math")
    p = write(tmp, "a.html", page(KATEX_HEAD_FULL, r"<p>$c_{<t} := (c_1)$ と $a < b$。</p>"))
    check("detected", "raw-lt-in-math" in ids(p))
    C.apply_fixes(p, C.analyse(p))
    src = open(p, encoding="utf-8").read()
    check("harmful `<t` escaped", "$c_{&lt;t}" in src)
    check("benign `a < b` untouched", "$a < b$" in src, repr(src[src.find("$a"):src.find("$a") + 8]))
    check("clean after fix", "raw-lt-in-math" not in ids(p))


def case_benign_lt(tmp):
    print("`<` followed by a non-letter must NOT fire")
    p = write(tmp, "b.html", page(KATEX_HEAD_FULL,
                                  r"<p>$0 < \eta < 1$、$\rho(A)<1$、$i < j$。</p>"))
    check("no finding", "raw-lt-in-math" not in ids(p), str(ids(p)))


def case_delims(tmp):
    print("delimiter registered vs used")
    p = write(tmp, "c.html", page(KATEX_HEAD_POOR, r"<p>$x$ と \(y\) と $$z$$</p>"))
    check("detected", "delim-not-registered" in ids(p))
    C.apply_fixes(p, C.analyse(p))
    src = open(p, encoding="utf-8").read()
    check("all four registered now", src.count("left:") == 4, str(src.count("left:")))
    check("throwOnError: false added", "throwOnError: false" in src)
    check("clean after fix", not (ids(p) & {"delim-not-registered", "throw-on-error"}), str(ids(p)))


def case_mathjax(tmp):
    print("MathJax missing \\[ and $")
    p = write(tmp, "d.html", page(MATHJAX_HEAD_POOR, r"<p>\(x\) と \[y\] と $z$</p>"))
    check("detected", "delim-not-registered" in ids(p))
    C.apply_fixes(p, C.analyse(p))
    src = open(p, encoding="utf-8").read()
    check("inline $ registered", "['$','$']" in src)
    check("display \\[ registered", "\\\\[" in src)
    check("clean after fix", "delim-not-registered" not in ids(p), str(ids(p)))


def case_figcaption(tmp):
    print("block math inside <figcaption>")
    p = write(tmp, "e.html", page(KATEX_HEAD_FULL,
                                  r"<figure><img src=x><figcaption>図1. $$\alpha$$ の図。</figcaption></figure>"))
    check("detected", "figcaption-block-math" in ids(p))
    C.apply_fixes(p, C.analyse(p))
    src = open(p, encoding="utf-8").read()
    check("demoted to inline", r"<figcaption>図1. $\alpha$ の図。</figcaption>" in src,
          src[src.find("<figcaption>"):src.find("</figcaption>")])


def case_unsupported(tmp):
    print("KaTeX-unsupported macro / environment")
    p = write(tmp, "f.html", page(KATEX_HEAD_FULL,
                                  r"<p>$$\textsc{Alg}(x)$$ と $W=\begin{psmallmatrix}0&2\\0&0\end{psmallmatrix}$</p>"))
    got = ids(p)
    check("detected", "katex-unsupported" in got, str(got))
    check("not auto-fixed", not any(f["fixable"] for f in C.analyse(p)["findings"]
                                    if f["id"] == "katex-unsupported"))


def case_stray_dollar(tmp):
    print("stray literal $")
    p = write(tmp, "g.html", page(KATEX_HEAD_FULL, "<p>単一 L40S GPU で $2 未満。</p>"))
    check("detected", "stray-dollar" in ids(p), str(ids(p)))


def case_clean(tmp):
    print("a well-formed page produces nothing")
    p = write(tmp, "h.html", page(KATEX_HEAD_FULL,
                                  r"<p>$x_{&lt;t}$ と $$\sum_i a_i$$</p>", sid="dead-beef"))
    check("no findings", ids(p) == set(), str(ids(p)))


def case_session_id(tmp):
    print("session id findings")
    p = write(tmp, "i.html", page(KATEX_HEAD_FULL, "<p>x</p>"))
    check("placeholder detected", "session-id-placeholder" in ids(p))
    q = write(tmp, "j.html", '<!DOCTYPE html><html><head><meta charset="UTF-8">'
                             "<title>t</title></head><body><h1>t</h1><p>x</p></body></html>")
    check("missing id detected", "session-id-missing" in ids(q))


def case_finalize_dest(tmp):
    print("finalize stamps the exact draft, never a citation or the newest file")
    d = os.path.join(tmp, "proj", "html-expl")
    os.makedirs(d, exist_ok=True)
    mine = write(d, "20260101-mine.html",
          page(KATEX_HEAD_FULL, '<img src="figs/2512.12895v1/fig1.png">'))
    # A *newer* file from a concurrent run that merely cites the same paper in prose.
    write(d, "20260101-other.html",
          page(KATEX_HEAD_FULL, "<p>see papers/2512.12895v1.pdf</p>"))
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    cmd = [sys.executable, os.path.join(HERE, "finalize_html.py"), "--html", mine,
           os.path.dirname(d), sid, "papers/2512.12895v1.pdf", "test", "claude"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    check("explicit draft stamped", result.returncode == 0 and sid in F.read(mine))
    other = os.path.join(d, "20260101-other.html")
    check("other file untouched", "__AGENT_SESSION_ID__" in F.read(other))
    with open(mine, "w", encoding="utf-8") as fh:
        fh.write(page(KATEX_HEAD_FULL, "<p>draft</p>", sid="wrong-session"))
    result = subprocess.run(cmd, capture_output=True, text=True)
    check("explicit file corrects an invented id", result.returncode == 0
          and "wrong-session" not in F.read(mine) and sid in F.read(mine))
    os.unlink(mine)
    result = subprocess.run(cmd, capture_output=True, text=True)
    check("missing output is failure", result.returncode != 0)
    check("missing output never falls back", "__AGENT_SESSION_ID__" in F.read(other))


def case_hook_scope(tmp):
    print("Stop hook only stamps the files it was handed")
    proj = os.path.join(tmp, "proj2")
    d = os.path.join(proj, "html-expl")
    os.makedirs(d, exist_ok=True)
    mine = write(d, "mine.html", page(KATEX_HEAD_FULL, "<p>x</p>"))
    worker = write(d, "worker.html", page(KATEX_HEAD_FULL, "<p>x</p>"))
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "finalize_html.py"), "--hook", proj, mine],
        input=json.dumps({"session_id": sid, "cwd": proj}),
        capture_output=True, text=True)
    check("handed file stamped", sid in open(mine, encoding="utf-8").read(), out.stderr.strip())
    check("worker file untouched",
          "__AGENT_SESSION_ID__" in open(worker, encoding="utf-8").read())


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (case_raw_lt, case_benign_lt, case_delims, case_mathjax, case_figcaption,
                   case_unsupported, case_stray_dollar, case_clean, case_session_id,
                   case_finalize_dest, case_hook_scope):
            fn(tmp)
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURE(S): " + "; ".join(FAILS))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
