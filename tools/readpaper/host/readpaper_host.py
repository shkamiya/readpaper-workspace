#!/usr/bin/python3
"""Native messaging host for the readpaper one-click extension.

NOTE: shebang is the ABSOLUTE system python (/usr/bin/python3), not
`/usr/bin/env python3`. Chrome launches the host with a minimal PATH in which
`env` may fail to locate python3 (exit 127 -> "Native host has exited"). The
host uses only the standard library, so system python 3.9 is sufficient.


Chrome speaks the native-messaging wire protocol over stdin/stdout:
  [4-byte little-endian length][UTF-8 JSON payload].

Flow:
  1. read one message from the extension:
         {"url": "<active tab url>",
          "pdfUrl": "<pdf url the extension found in the page DOM>",   # optional
          "pdfBase64": "<the pdf bytes, fetched by the browser>",      # optional
          "title": "<citation_title>", "arxivId": "...", "how": "..."} # optional
  2. resolve it to (pdf_url, dest_name) -- see resolve() below
  3. if pdfBase64 is present, decode it to /tmp/readpaper-<dest>-<pid>.pdf and
     hand that local path to the worker (the browser already did the download)
  4. reply {"status":"started", "id": dest}  (or an error)
  5. spawn readpaper_run.sh <pdf_url> <dest> [<local_pdf>] detached, then exit.

WHY THE BROWSER SOMETIMES HAS TO DO THE DOWNLOAD (step 3)
---------------------------------------------------------
Some venues sit behind a bot wall. Measured 2026-08-13 on a real public paper
(ViT, openreview.net/forum?id=YicbFdNTTy):
  - `curl -fL -A "Mozilla/5.0" https://openreview.net/pdf?id=...`  -> HTTP 403,
    12,692 bytes of HTML, zero bytes of PDF.
  - the same URL with full Chrome-like headers                     -> HTTP *200*
    but the body is a 1,960-byte Cloudflare **Turnstile** page
    (challenges.cloudflare.com/turnstile/v0/api.js -> POST challenge/verify).
  - api2.openreview.net/notes?id=...  -> {"name":"ChallengeRequiredError",403}
Deriving the URL is trivial (forum?id=X -> pdf?id=X); *fetching* it is what
fails. Only a browser that already cleared Turnstile (or is signed in) can get
the bytes, hence the extension fetches and ships them here. The 200-with-HTML
case is also why readpaper_run.sh verifies the `%PDF-` magic bytes.

Only the two send_message() JSON frames may ever touch stdout -- anything else
on stdout corrupts the native-messaging stream.
"""
from __future__ import annotations

import base64
import binascii
import html as html_mod
import json
import os
import re
import struct
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parent
RUN = HOST_DIR / "readpaper_run.sh"
DEBUG_LOG = Path("/tmp/readpaper_host_debug.log")  # /tmp is not TCC-protected

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT = 10       # seconds; the extension shows "…" while we block here
FETCH_MAX_BYTES = 2_000_000


def dlog(*parts) -> None:
    """Debug log to a file — never stdout (stdout is the native-messaging stream)."""
    try:
        with open(DEBUG_LOG, "a") as f:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts} pid={os.getpid()}] " + " ".join(str(p) for p in parts) + "\n")
    except Exception:
        pass


def notify(text: str) -> None:
    """macOS notification. Used for *resolution* failures — the worker never
    starts in that case, so without this the only feedback is a red badge."""
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e",
             f'display notification "{text}" with title "readpaper" sound name "Basso"'],
            timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def read_message():
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        return None
    length = struct.unpack("<I", raw_len)[0]
    data = sys.stdin.buffer.read(length).decode("utf-8")
    return json.loads(data)


def send_message(obj) -> None:
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# --------------------------------------------------------------------------
# URL -> (pdf_url, dest) resolution
# --------------------------------------------------------------------------
# `dest` is the identity of the paper across the whole pipeline: the PDF is
# papers/<dest>.pdf, crops go to html-expl/figs/<dest>/, and readpaper_run.sh's
# duplicate guard greps generated HTML for "figs/<dest>" / "papers/<dest>". So
# it must be stable, unique and filesystem-safe. arXiv naming is deliberately
# left as the bare id (e.g. 2605.26106v1) — 85 existing explainers depend on it.

ARXIV_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE
)
# The optional `/<segment>` before the extension absorbs the nested layouts
# (proceedings.mlr.press/v235/kim24d/kim24d.pdf, jmlr.org/papers/volume21/19-1015/19-1015.pdf)
# so a raw-PDF tab resolves to the same dest as its landing page.
PMLR_RE = re.compile(
    r"proceedings\.mlr\.press/(v\d+)/([A-Za-z0-9._-]+?)"
    r"(?:/[A-Za-z0-9._-]+?)?(?:\.html|\.pdf)?/?$", re.I)
JMLR_RE = re.compile(
    r"jmlr\.(?:org|csail\.mit\.edu)/papers/(?:v|volume)(\d+)/([A-Za-z0-9._-]+?)"
    r"(?:/[A-Za-z0-9._-]+?)?(?:\.html|\.pdf)?/?$", re.I)
ACL_RE = re.compile(r"aclanthology\.org/([A-Za-z0-9._-]+?)(?:\.pdf|/)?$", re.I)
NEURIPS_RE = re.compile(
    r"(?:proceedings\.neurips\.cc|papers\.nips\.cc)/paper(?:_files)?/paper/(\d{4})/"
    r"(?:hash|file)/([0-9a-f]{8})", re.I
)
CVF_RE = re.compile(r"openaccess\.thecvf\.com/content/([^/]+)/(?:html|papers)/(.+?)(?:_paper)?\.(?:html|pdf)$", re.I)

ATTR_RE = re.compile(r"""([\w:.-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")
TAG_RE = re.compile(r"<(meta|link|a)\b([^>]*)>", re.IGNORECASE)

GENERIC_BASENAMES = {
    "main", "paper", "pdf", "download", "index", "fulltext", "full", "article",
    "manuscript", "preprint", "content", "view", "file",
}


def slug(s: str, maxlen: int = 64) -> str:
    """Filesystem-safe, stable token. Keeps . _ - which carry meaning in venue
    ids (e.g. ACL's 2021.emnlp-main.446)."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "")).strip("-._")
    s = re.sub(r"-{2,}", "-", s)
    return s[:maxlen] or "paper"


def https(u: str) -> str:
    """Upgrade http -> https. citation_pdf_url on JMLR/PMLR is often http, and
    an http subresource fetch from the extension (a secure context) is blocked
    as mixed content; every venue we target serves https fine."""
    return "https://" + u[len("http://"):] if u.lower().startswith("http://") else u


def _attrs(chunk: str) -> dict:
    out = {}
    for k, v in ATTR_RE.findall(chunk):
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            v = v[1:-1]
        out[k.lower()] = html_mod.unescape(v)
    return out


def fetch_page(url: str) -> str:
    """GET an HTML landing page (best effort). Never raises."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            raw = r.read(FETCH_MAX_BYTES)
            charset = (r.headers.get_content_charset() or "utf-8")
        return raw.decode(charset, errors="replace")
    except Exception as exc:
        dlog("fetch_page failed:", url, repr(exc))
        return ""


def scrape_pdf_url(page_url: str, doc: str):
    """Find the PDF for a landing page. Returns (pdf_url, how, meta_dict).

    Priority mirrors what the extension does in the live DOM:
      1. <meta name="citation_pdf_url">  — the Highwire Press tag. Verified
         present on PMLR (both the old flat layout and the new one whose PDF
         actually lives on raw.githubusercontent.com/mlresearch/...), JMLR,
         NeurIPS proceedings, ACL Anthology, bioRxiv, Springer, ...
         This is why we scrape instead of rewriting URLs: for PMLR v235 the PDF
         host is not derivable from the page URL at all.
      2. <link rel="alternate" type="application/pdf">
      3. the first <a href="....pdf"> that does not look like supplementary.
    """
    meta = {}
    cite_pdf = None
    link_pdf = None
    anchor_pdf = None
    for tag, chunk in TAG_RE.findall(doc or ""):
        a = _attrs(chunk)
        t = tag.lower()
        if t == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name.startswith("citation_") and a.get("content"):
                meta.setdefault(name, a["content"])
                if name == "citation_pdf_url" and not cite_pdf:
                    cite_pdf = a["content"]
        elif t == "link":
            if (a.get("type", "").lower() == "application/pdf"
                    and a.get("href") and not link_pdf):
                link_pdf = a["href"]
        elif t == "a":
            href = a.get("href", "")
            if not anchor_pdf and href.lower().split("?")[0].endswith(".pdf"):
                low = href.lower()
                if not any(k in low for k in ("supp", "appendix", "-si", "review")):
                    anchor_pdf = href
    for cand, how in ((cite_pdf, "citation_pdf_url"), (link_pdf, "link-alternate"),
                      (anchor_pdf, "anchor")):
        if cand:
            return https(urllib.parse.urljoin(page_url, cand)), how, meta
    return None, None, meta


def venue_identity(url: str):
    """Per-venue (dest, derived_pdf_url) rules, derived from the page URL alone
    (no network). derived_pdf_url may be None when the PDF location is not
    derivable — the caller then falls back to the extension's DOM hint or to
    scraping citation_pdf_url."""
    bare = url.split("?")[0].split("#")[0]

    m = re.search(r"openreview\.net/(?:forum|pdf|attachment|references)", url, re.I)
    if m:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        oid = (q.get("id") or [""])[0]
        if oid:
            # URL derivation is trivial here; the *download* is the hard part
            # (Cloudflare Turnstile) — see the module docstring.
            return f"openreview-{slug(oid)}", f"https://openreview.net/pdf?id={oid}"

    m = PMLR_RE.search(bare)
    if m:
        vol, pid = m.group(1).lower(), m.group(2)
        if pid.lower() != "index" and not pid.lower().endswith("-supp"):
            return f"pmlr-{vol}-{slug(pid)}", None   # PDF host differs per volume

    m = JMLR_RE.search(bare)
    if m:
        vol, pid = m.group(1), m.group(2)
        return (f"jmlr-v{vol}-{slug(pid)}",
                f"https://www.jmlr.org/papers/volume{vol}/{pid}/{pid}.pdf")

    m = NEURIPS_RE.search(bare)
    if m:
        return f"neurips-{m.group(1)}-{m.group(2)}", None

    m = CVF_RE.search(bare)
    if m:
        return f"cvf-{slug(m.group(2))}", None

    m = ACL_RE.search(bare)
    if m and re.match(r"^[A-Za-z0-9]", m.group(1)) and "." in m.group(1):
        pid = m.group(1)
        return f"acl-{slug(pid)}", f"https://aclanthology.org/{pid}.pdf"

    return None, None


def generic_dest(pdf_url: str, page_url: str, title: str = "") -> str:
    """Fallback naming: <host>-<pdf basename>, or <host>-<title slug> when the
    basename carries no information (main.pdf, download.pdf, ...)."""
    p = urllib.parse.urlparse(pdf_url or page_url)
    host = re.sub(r"^www\.", "", p.netloc.lower())
    host = re.sub(r"\.(org|com|net|edu|gov|io|cc|press|ac\.\w+|co\.\w+)$", "", host)
    host = slug(host.split(".")[0] if "." in host else host, 24)
    base = re.sub(r"\.pdf$", "", p.path.rstrip("/").split("/")[-1], flags=re.I)
    if not base or base.lower() in GENERIC_BASENAMES or len(base) < 3:
        base = slug(title, 48) or "paper"
    return slug(f"{host}-{base}")


def resolve(msg: dict):
    """Return (pdf_url, dest, how) or (None, None, reason)."""
    url = (msg.get("url") or "").strip()
    hint = (msg.get("pdfUrl") or "").strip()
    title = (msg.get("title") or "").strip()
    how = msg.get("how") or "host"

    # 1. arXiv wins over everything so the dest stays the bare id and the
    #    duplicate guard keeps matching the 85 existing explainers.
    for cand in (url, hint, msg.get("arxivId") or ""):
        m = ARXIV_RE.search(cand) or re.match(r"^\s*(\d{4}\.\d{4,5}(?:v\d+)?)\s*$", cand)
        if m:
            aid = m.group(1)
            return f"https://arxiv.org/pdf/{aid}", aid, "arxiv"

    # 2. Venue rules (dest naming, plus a derived PDF url where it is reliable).
    dest, derived = venue_identity(url)

    # 3. PDF url: the extension's DOM hint wins (it saw the rendered page and,
    #    for walled venues, already fetched the bytes with it).
    pdf_url = https(hint) if hint else (derived or "")

    # 4. The tab itself is a PDF.
    if not pdf_url:
        bare = url.split("?")[0].split("#")[0]
        if bare.lower().endswith(".pdf"):
            pdf_url, how = url, "self"

    # 5. Nothing yet -> scrape the landing page for citation_pdf_url.
    meta = {}
    if not pdf_url and url.lower().startswith(("http://", "https://")):
        pdf_url, scraped_how, meta = scrape_pdf_url(url, fetch_page(url))
        if pdf_url:
            how = scraped_how
        # A page may declare its arXiv id even when it is not an arXiv page.
        aid = (meta.get("citation_arxiv_id") or "").strip()
        if aid and re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", aid):
            return f"https://arxiv.org/pdf/{aid}", aid, "citation_arxiv_id"

    if not pdf_url:
        return None, None, "no pdf found on page (no citation_pdf_url / pdf link)"

    if not dest:
        dest, derived2 = venue_identity(pdf_url)
        if not dest:
            dest = generic_dest(pdf_url, url, title or meta.get("citation_title", ""))
    return pdf_url, dest, how


def stash_bytes(b64: str, dest: str):
    """Decode browser-fetched PDF bytes to /tmp (never TCC-protected, unlike the
    repo on ~/Desktop) and let the worker move it into papers/. Returns the path,
    or None if the payload is not a PDF."""
    try:
        blob = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        dlog("b64 decode failed:", repr(exc))
        return None
    if not blob.startswith(b"%PDF"):
        dlog("payload is not a PDF, first bytes:", blob[:16])
        return None
    path = f"/tmp/readpaper-{slug(dest)}-{os.getpid()}.pdf"
    with open(path, "wb") as f:
        f.write(blob)
    dlog("stashed", len(blob), "bytes ->", path)
    return path


def main() -> None:
    dlog("=== launched ===", "exe=", sys.executable, "PATH=", os.environ.get("PATH", ""))
    msg = read_message()
    if not isinstance(msg, dict):
        dlog("no message, exiting")
        return
    dlog("read_message ->", {k: (f"<{len(v)} b64 chars>" if k == "pdfBase64" else v)
                             for k, v in msg.items()})

    pdf_url, dest, how = resolve(msg)
    dlog("resolved ->", pdf_url, dest, how)
    if not pdf_url:
        reason = how or "unrecognized page"
        send_message({"status": "error", "reason": reason, "url": msg.get("url", "")})
        notify(f"✗ no PDF found on this page ({reason})")
        dlog("sent error reply:", reason)
        return

    local_pdf = ""
    if msg.get("pdfBase64"):
        local_pdf = stash_bytes(msg["pdfBase64"], dest) or ""
        if not local_pdf:
            # The browser handed us something that is not a PDF (a login page, a
            # Turnstile challenge). Say so rather than feeding HTML to the pipeline.
            send_message({"status": "error", "reason": "browser payload is not a PDF",
                          "id": dest, "url": msg.get("url", "")})
            notify(f"✗ {dest}: fetched content is not a PDF (login/bot wall?)")
            return

    send_message({"status": "started", "id": dest, "how": how,
                  "pdfUrl": pdf_url, "local": bool(local_pdf)})
    dlog("sent started reply")

    # Detach so the (multi-minute) job outlives this short-lived host process
    # and the Chrome native-messaging connection. Spawn log goes to /tmp (always
    # writable, no TCC); the worker itself writes a per-job log under the project.
    log_fh = open("/tmp/readpaper-spawn.log", "a")
    argv = ["/bin/bash", str(RUN), pdf_url, dest]
    if local_pdf:
        argv.append(local_pdf)
    proc = subprocess.Popen(
        argv,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=log_fh,
    )
    dlog("spawned worker pid=", proc.pid, "argv=", argv)

    # Linger until Chrome closes our stdin (EOF). Exiting immediately after the
    # reply can race with Chrome's teardown and surface as "Native host has
    # exited", even though the reply was sent. This blocks until Chrome is done.
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass
    dlog("stdin EOF, clean exit")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        dlog("EXCEPTION:\n" + traceback.format_exc())
        raise
