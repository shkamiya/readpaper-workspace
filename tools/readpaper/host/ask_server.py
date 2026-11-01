#!/usr/bin/env python3
"""In-page chat server for the explainer fleet.

Serves html-expl/ exactly like `python3 -m http.server` did, and adds three things:

  1. every .html response gets a <script> tag injected before its last </body>,
     which loads the chat widget (ask_widget.js). The files on disk are NEVER
     modified -- html-expl/ is gitignored, so an in-place edit would be
     unrecoverable, and 131 existing explainers would all need patching.
  2. POST /api/ask   -- queue a question/instruction against one explainer.
  3. GET  /api/status?job=<id> -- poll a queued job.
     POST /api/cancel {job} -- stop it (the widget's 中止 button / Ctrl+C).
  4. GET/POST /api/meta -- SK's reading marks (状態 / 理解度 / ひとことまとめ) for one
     explainer, stored as html-expl/meta/<stem>.json by meta_store.py. The mark
     card itself is a second injected script, mark_widget.js. The chat agent is
     shown these marks in its prompt but never writes them; this endpoint is the
     only writer.
     GET/POST /api/ink (handwriting, ink_store.py) and GET/POST /api/pos (reading
     position / しおり, pos_store.py) follow the same pattern with their own
     injected widgets, ink_widget.js and pos_widget.js. fold_widget.js (no API)
     turns the glossary sidebar and aside.concept boxes into click-to-open toggles.

  5. POST /api/readpaper, GET /api/readpaper/status, GET /go -- start the one-click
     explainer pipeline ("DP") from a browser that cannot run the Chrome
     extension. iPadOS Chrome/Safari have no extension mechanism and no native
     messaging, so the iPad gets a bookmarklet/share-sheet that opens
     /go?url=<paper url>; that page shows one button which POSTs to
     /api/readpaper, and the server resolves the url with readpaper_host.resolve()
     and spawns readpaper_run.sh exactly as the native host would. GET /go has no
     side effect on purpose: starting a run needs the token-carrying POST, for the
     same drive-by reason /api/ask does (see "who is allowed" below).

The actual agent run happens in ask_run.py, spawned detached. This process never
blocks on it: a run takes ~5 minutes (measured 2026-08-22).

WHY THE QUESTION TEXT NEVER TOUCHES A SHELL
-------------------------------------------
The question is arbitrary user text that ends up inside an agent prompt. This
repo already has a documented incident where a single apostrophe inside a bash
heredoc broke the readpaper worker (see tools/readpaper/README.md). So the text
travels as JSON: browser -> this process -> the job file -> ask_run.py, which
hands it to the agent as one argv element. subprocess without shell=True does no
word splitting, glob expansion or quote parsing, so there is no point on the
path where quoting can go wrong.

WHO IS ALLOWED TO REACH IT
--------------------------
This endpoint is, in effect, "start `claude --dangerously-skip-permissions` over
HTTP". Phase 1 kept it on 127.0.0.1 for that reason. It now binds 0.0.0.0 so the
iPhone can reach it over Tailscale, which requires the two gates plan note §2
item 4 asked for, in this order of actual strength:

  1. source range (the real boundary) -- every request, not just POST, must come
     from loopback or the Tailnet CGNAT range 100.64.0.0/10. Binding 0.0.0.0 also
     exposes the socket to the home LAN; 192.168.x is rejected here.
  2. shared token -- injected into the page as data-ask-token, echoed back by the
     widget on POST /api/ask, compared with hmac.compare_digest.

Be honest about what (2) buys: anyone who can GET the page can read the token, so
it is not a secret against a Tailnet peer. What it stops is a drive-by POST from
some other site open in the same browser (a cross-origin JSON POST cannot read the
page to learn the token, and its preflight goes unanswered) and blind non-browser
probes. The boundary that matters is (1) plus Tailscale's own authentication.
"""
from __future__ import annotations

import argparse
import glob
import hmac
import html as html_mod
import ipaddress
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import meta_store  # noqa: E402
import ink_store  # noqa: E402
import pos_store  # noqa: E402
# The native-messaging host doubles as the url resolver (arXiv id, venue rules,
# citation_pdf_url scraping). Importing it does not run main(); it only reads
# stdin under `if __name__ == "__main__"`.
import readpaper_host  # noqa: E402

WIDGET = os.path.join(HERE, "ask_widget.js")
MARK_WIDGET = os.path.join(HERE, "mark_widget.js")
INK_WIDGET = os.path.join(HERE, "ink_widget.js")
POS_WIDGET = os.path.join(HERE, "pos_widget.js")
FOLD_WIDGET = os.path.join(HERE, "fold_widget.js")
THEME_WIDGET = os.path.join(HERE, "theme_widget.js")
RUNNER = os.path.join(HERE, "ask_run.py")
GO_PAGE = os.path.join(HERE, "go_page.html")
# The repo copy of the worker, not the deployed one under ~/Library: this server
# already runs from the repo (launchd points at it) with a PATH that carries the
# agent CLI and pyenv python3, and readpaper_run.sh falls back to the inherited
# PATH when its placeholders are unbaked.
WORKER = os.path.join(HERE, "readpaper_run.sh")

# Filled in by main(); module-level so the handler can reach them.
ROOT = ""       # html-expl/
PROJECT = ""    # repo root
JOBS = ""       # <project>/logs/ask-jobs
TOKEN = ""      # shared secret echoed by the widget
ALLOW = ()      # tuple of ip_network the client address must fall in

MAX_QUESTION = 4000
MAX_SELECTION = 2000
LIVE = ("queued", "running")
WORKERS: dict = {}  # job id -> ask_run.py pid, for /api/cancel

# Loopback plus the Tailscale CGNAT range. Tailscale hands out 100.64.0.0/10
# addresses only to nodes in the tailnet, so "source is in 100.64/10" is
# equivalent to "reached us over the tailnet" for a host that is not routing
# CGNAT space itself.
DEFAULT_ALLOW = "127.0.0.0/8,::1/128,100.64.0.0/10"

# install.sh writes this; the server appends ASK_TOKEN to it if it is missing, so
# a fresh checkout bootstraps itself instead of failing closed with no way in.
CONFIG_ENV = os.path.expanduser(
    "~/Library/Application Support/readpaper/config.env")


def parse_allow(spec: str) -> tuple:
    nets = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return tuple(nets)


def client_allowed(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    # A dual-stack listener reports IPv4 peers as ::ffff:100.66.108.47; compare
    # on the mapped-back address so one IPv4 rule covers both.
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip in net for net in ALLOW)


def load_token(path: str) -> str:
    """Read ASK_TOKEN from config.env, generating and persisting one if absent."""
    env_token = os.environ.get("ASK_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("ASK_TOKEN=") and line[10:].strip():
                    return line[10:].strip()
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n# Shared secret for ask_server.py POST /api/ask.\nASK_TOKEN={token}\n")
    return token


def jobs_dir() -> str:
    os.makedirs(JOBS, exist_ok=True)
    return JOBS


def job_path(job_id: str) -> str:
    return os.path.join(jobs_dir(), f"{job_id}.json")


def read_job(job_id: str) -> dict | None:
    try:
        with open(job_path(job_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_job(job: dict) -> None:
    """Atomic write -- the widget polls this file through the worker's updates."""
    p = job_path(job["job"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(job, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def live_job_for(fname: str) -> str | None:
    """Return the id of an in-flight job on this file, if any.

    Serialising per file matters: two agents editing the same html concurrently
    would interleave writes, and the snapshot of the second would already
    contain the first one's edit.
    """
    for name in sorted(os.listdir(jobs_dir())):
        if not name.endswith(".json"):
            continue
        job = read_job(name[:-5])
        if job and job.get("file") == fname and job.get("state") in LIVE:
            # A worker that died without updating its file would block the paper
            # forever, so treat a stale job as finished.
            if time.time() - job.get("created", 0) > 3600:
                continue
            return job["job"]
    return None


def worker_pid(job_id: str, job: dict) -> int | None:
    """The pid of the ask_run.py worker for this job, if it is still running.

    The command line must name this job, so a recycled pid (or a zombie, whose
    command line is empty) is never signalled.
    """
    pid = WORKERS.get(job_id) or job.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return None
    try:
        cmd = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return pid if os.path.basename(RUNNER) in cmd and f"--job {job_id}" in cmd else None


def find_existing(dest: str) -> str | None:
    """Basename of the explainer that already covers <dest>, else None.

    A python port of the duplicate guard in readpaper_run.sh, kept identical on
    purpose: the worker will refuse a paper this says exists, so /go must give the
    same answer up front instead of spawning a worker that exits with "already
    exists". Content-based, version suffix stripped, and header-scoped for the
    legacy id fallback -- see the guard's comment for why a body-wide scan is a
    false-positive storm (every explainer cites other papers by arXiv id).
    """
    base = re.escape(re.sub(r"v\d+$", "", dest))
    ver = r"(?:v\d+)?"
    asset = re.compile(r"(?:figs/" + base + ver + r"/|papers/" + base + ver + r"\.pdf)")
    ident = re.compile(r"(?:arXiv:?\s*|arxiv\.org/(?:abs|pdf)/)" + base + ver + r"\b")

    def header(s: str) -> str:
        i = s.find("<body")
        i = 0 if i < 0 else i
        j = s.find("<h2", i)
        j = min(i + 4000, len(s)) if j < 0 else j
        return s[i:j]

    best, best_m = None, -1.0
    try:
        for e in os.scandir(ROOT):
            if not e.is_file() or not e.name.endswith(".html") or e.name == "index.html":
                continue
            try:
                with open(e.path, encoding="utf-8", errors="replace") as fh:
                    s = fh.read()
            except OSError:
                continue
            if asset.search(s) or ident.search(header(s)):
                mt = e.stat().st_mtime
                if mt > best_m:
                    best, best_m = e.name, mt
    except OSError:
        pass
    return best


def explainer_title(fname: str) -> str:
    try:
        with open(os.path.join(ROOT, fname), encoding="utf-8", errors="replace") as fh:
            m = re.search(r"<title>([^<]*)</title>", fh.read(200_000))
        return html_mod.unescape(m.group(1).strip()) if m else fname
    except OSError:
        return fname


def worker_log_tail(dest: str) -> tuple[str, str]:
    """(log basename, last non-empty line) of the newest readpaper-<dest>-*.log."""
    logs = glob.glob(os.path.join(PROJECT, "logs", f"readpaper-{glob.escape(dest)}-*.log"))
    if not logs:
        return "", ""
    newest = max(logs, key=os.path.getmtime)
    try:
        with open(newest, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            n = fh.tell()
            fh.seek(max(0, n - 4000))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return os.path.basename(newest), ""
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    # The agent's own stdout is interleaved; prefer the worker's tagged lines.
    tagged = [ln for ln in lines if ln.startswith("[readpaper]")]
    return os.path.basename(newest), (tagged[-1] if tagged else (lines[-1] if lines else ""))


def is_safe_dest(dest: str) -> bool:
    return bool(dest) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", dest) is not None


class Handler(SimpleHTTPRequestHandler):
    server_version = "readpaper-ask/0.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    # -- logging: keep the access log quiet but keep errors -------------------
    def log_message(self, fmt, *args):  # noqa: A003
        if not str(args[0] if args else "").startswith(
                ("GET /api/status", "GET /_ask", "GET /api/meta", "GET /meta/",
                 "GET /api/pos", "GET /pos/", "POST /api/pos",
                 "GET /api/readpaper/status")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers -------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    # -- access control ------------------------------------------------------
    def _gate(self) -> bool:
        """Reject anything from outside loopback/tailnet, before it is routed.

        Applied to GET as well as POST: the served html carries the token, so a
        GET-only attacker on the LAN would otherwise walk straight to /api/ask.
        """
        if client_allowed(self.client_address[0]):
            return True
        sys.stderr.write("[ask] refused %s %s\n"
                         % (self.client_address[0], self.path))
        self._json(403, {"error": "forbidden"})
        return False

    # -- GET -----------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if not self._gate():
            return
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._api_get(path)
        if path == "/go":
            return self._go_page()
        if path == "/_ask/widget.js":
            return self._widget(WIDGET)
        if path == "/_ask/mark.js":
            return self._widget(MARK_WIDGET)
        if path == "/_ask/ink.js":
            return self._widget(INK_WIDGET)
        if path == "/_ask/pos.js":
            return self._widget(POS_WIDGET)
        if path == "/_ask/fold.js":
            return self._widget(FOLD_WIDGET)
        if path == "/_ask/theme.js":
            return self._widget(THEME_WIDGET)
        if path.endswith(".html") or path.endswith("/"):
            return self._html(path)
        if path.startswith("/meta/") or path.startswith("/ink/") or path.startswith("/pos/"):
            # The mark card and index.html re-fetch these after every save; a
            # cached copy would show the value from before the tap.
            return self._static_nocache()
        return super().do_GET()

    do_HEAD = do_GET

    def _widget(self, src: str):
        try:
            with open(src, "rb") as fh:
                body = fh.read()
        except OSError:
            return self.send_error(404, "widget not deployed")
        self._send(200, body, "application/javascript; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _static_nocache(self):
        fs = self.translate_path(self.path)
        try:
            with open(fs, "rb") as fh:
                body = fh.read()
        except OSError:
            return super().do_GET()
        self._send(200, body, self.guess_type(fs), {"Cache-Control": "no-store"})

    def _html(self, path: str):
        """Serve an html file with the widget script injected.

        translate_path() is SimpleHTTPRequestHandler's own sandboxing: it
        collapses .. and pins the result under ROOT, so a crafted url cannot
        read outside html-expl/.
        """
        fs = self.translate_path(self.path)
        if os.path.isdir(fs):
            fs = os.path.join(fs, "index.html")
        try:
            with open(fs, "rb") as fh:
                body = fh.read()
        except OSError:
            return super().do_GET()

        def mtime(p: str) -> int:
            return int(os.path.getmtime(p)) if os.path.exists(p) else 0

        fname_b = os.path.basename(fs).encode("utf-8")
        # Apply the saved theme before the body is painted. Keep article files
        # untouched, and leave the fold/position widget ordering unchanged.
        theme = b'<script src="/_ask/theme.js?v=%d"></script>' % mtime(THEME_WIDGET)
        head = re.search(br"<head\b[^>]*>", body, re.IGNORECASE)
        cut_theme = head.end() if head else 0
        body = body[:cut_theme] + theme + body[cut_theme:]
        # fold.js first: it folds the glossary / concept boxes synchronously,
        # before pos.js restores the reading position against the folded layout.
        tag = (b'\n<script src="/_ask/fold.js?v=%d" data-ask-file="%s"></script>'
               b'\n<script src="/_ask/widget.js?v=%d" data-ask-file="%s"'
               b' data-ask-token="%s"></script>\n'
               b'<script src="/_ask/mark.js?v=%d" data-ask-file="%s"'
               b' data-ask-token="%s"></script>\n'
               b'<script src="/_ask/ink.js?v=%d" data-ask-file="%s"'
               b' data-ask-token="%s"></script>\n'
               b'<script src="/_ask/pos.js?v=%d" data-ask-file="%s"'
               b' data-ask-token="%s"></script>\n'
               % (mtime(FOLD_WIDGET), fname_b,
                  mtime(WIDGET), fname_b, TOKEN.encode("utf-8"),
                  mtime(MARK_WIDGET), fname_b, TOKEN.encode("utf-8"),
                  mtime(INK_WIDGET), fname_b, TOKEN.encode("utf-8"),
                  mtime(POS_WIDGET), fname_b, TOKEN.encode("utf-8")))
        cut = body.rfind(b"</body>")
        body = body[:cut] + tag + body[cut:] if cut != -1 else body + tag
        self._send(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def _api_get(self, path: str):
        q = parse_qs(urlparse(self.path).query)
        if path == "/api/status":
            job_id = (q.get("job") or [""])[0]
            job = read_job(job_id) if job_id else None
            if not job:
                return self._json(404, {"error": "no such job"})
            return self._json(200, job)
        if path == "/api/chat":
            # Phase 1: history lives in the job files, not a per-file sidecar.
            fname = (q.get("file") or [""])[0]
            out = []
            for name in sorted(os.listdir(jobs_dir())):
                if not name.endswith(".json"):
                    continue
                job = read_job(name[:-5])
                if job and job.get("file") == fname:
                    out.append(job)
            out.sort(key=lambda j: j.get("created", 0))
            return self._json(200, {"file": fname, "jobs": out})
        if path == "/api/meta":
            fname = (q.get("file") or [""])[0]
            stem = self._explainer_stem(fname)
            if stem is None:
                return self._json(400, {"error": f"no such explainer: {fname}"})
            return self._json(200, meta_store.read(ROOT, stem))
        if path == "/api/ink":
            fname = (q.get("file") or [""])[0]
            stem = self._explainer_stem(fname)
            if stem is None:
                return self._json(400, {"error": f"no such explainer: {fname}"})
            return self._json(200, ink_store.read(ROOT, stem))
        if path == "/api/pos":
            fname = (q.get("file") or [""])[0]
            stem = self._explainer_stem(fname)
            if stem is None:
                return self._json(400, {"error": f"no such explainer: {fname}"})
            return self._json(200, pos_store.read(ROOT, stem))
        if path == "/api/readpaper/status":
            dest = (q.get("id") or [""])[0]
            if not is_safe_dest(dest):
                return self._json(400, {"error": "bad id"})
            fname = find_existing(dest)
            log, line = worker_log_tail(dest)
            failed = line.startswith("[readpaper] ERROR")
            return self._json(200, {
                "id": dest, "exists": bool(fname), "file": fname or "",
                "title": explainer_title(fname) if fname else "",
                "log": log, "phase": line, "failed": failed,
            })
        return self._json(404, {"error": "unknown endpoint"})

    def _go_page(self):
        """The one-button launcher, for browsers without the extension.

        Rendered here rather than served from html-expl/ so the token and the
        host this request arrived on (the bookmarklet has to name it) can be
        filled in per request. `url` is optional: without it the page is the
        setup sheet (bookmarklet, share-sheet shortcut, paste box).
        """
        q = parse_qs(urlparse(self.path).query)
        url = (q.get("url") or [""])[0].strip()
        if url and not url.lower().startswith(("http://", "https://")):
            url = ""
        try:
            with open(GO_PAGE, encoding="utf-8") as fh:
                page = fh.read()
        except OSError:
            return self.send_error(404, "go page not deployed")
        origin = "http://" + (self.headers.get("Host") or "127.0.0.1:8770")
        page = (page.replace("__ORIGIN__", html_mod.escape(origin))
                    .replace("__TOKEN__", html_mod.escape(TOKEN))
                    .replace("__URL__", html_mod.escape(url)))
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _explainer_stem(self, fname: str) -> str | None:
        """stem of an existing html-expl/<fname>, else None."""
        try:
            stem = meta_store.stem_of(fname)
        except ValueError:
            return None
        return stem if os.path.isfile(os.path.join(ROOT, stem + ".html")) else None

    # -- POST ----------------------------------------------------------------
    def do_POST(self):  # noqa: N802
        if not self._gate():
            return
        path = urlparse(self.path).path
        if path not in ("/api/ask", "/api/cancel", "/api/meta", "/api/readpaper",
                        "/api/ink", "/api/pos"):
            return self._json(404, {"error": "unknown endpoint"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "bad json"})

        # compare_digest, not ==, so a wrong token cannot be recovered a byte at
        # a time from response timing.
        if not hmac.compare_digest(str(payload.get("token") or ""), TOKEN):
            sys.stderr.write("[ask] bad token from %s\n" % self.client_address[0])
            return self._json(403, {"error": "bad token -- reload the page"})

        if path == "/api/meta":
            return self._post_meta(payload)
        if path == "/api/readpaper":
            return self._post_readpaper(payload)
        if path == "/api/ink":
            return self._post_ink(payload)
        if path == "/api/pos":
            return self._post_pos(payload)
        if path == "/api/cancel":
            return self._post_cancel(payload)
        return self._post_ask(payload)

    def _post_cancel(self, payload: dict):
        """Stop a running ask job so SK can fix a prompt sent by mistake.

        The worker leads its own process group (start_new_session below), so one
        killpg reaches it and the agent CLI it is waiting on. The worker turns
        that into state "cancelled" and never publishes. It ignores the signal
        once publishing has begun, so that case is refused here up front.
        """
        job_id = str(payload.get("job") or "")
        if not re.fullmatch(r"[0-9a-f]{8}", job_id):
            return self._json(400, {"error": "bad job id"})
        job = read_job(job_id)
        if not job:
            return self._json(404, {"error": "no such job"})
        if job.get("state") not in LIVE:
            return self._json(409, {"error": "already finished", "state": job.get("state")})
        if job.get("stage") == "publish":
            return self._json(409, {"error": "公開処理中のため中止できません", "state": "running"})
        with open(os.path.join(jobs_dir(), f"{job_id}.cancel"), "w") as fh:
            fh.write(str(time.time()))
        pid = worker_pid(job_id, job)
        if pid is None:
            # No worker: it died without writing its file (or never started).
            # Close the job here so the paper is not left looking busy.
            job.update(state="cancelled", phase="中止しました", finished=time.time())
            write_job(job)
            return self._json(200, {"job": job_id, "state": "cancelled"})
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError as exc:
            return self._json(500, {"error": f"signal failed: {exc}"})
        sys.stderr.write(f"[ask] cancel {job_id} (pid {pid})\n")
        return self._json(200, {"job": job_id, "state": "cancelling"})

    def _post_pos(self, payload: dict):
        """Overwrite one page's reading position (しおり). Last writer wins.
        `clear: true` forgets it. The widget also sends this via sendBeacon on
        pagehide; the body is the same JSON, only the transport differs."""
        fname = str(payload.get("file") or "")
        stem = self._explainer_stem(fname)
        if stem is None:
            return self._json(400, {"error": f"no such explainer: {fname}"})
        try:
            if payload.get("clear"):
                rec = pos_store.clear(ROOT, stem)
            else:
                rec = pos_store.save(ROOT, stem, payload.get("anchor"), payload.get("ratio"),
                                     payload.get("device"))
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            sys.stderr.write(f"[ask] pos save failed for {stem}: {exc}\n")
            return self._json(500, {"error": f"save failed: {exc}"})
        return self._json(200, rec)

    def _post_ink(self, payload: dict):
        """Full replace of one page's strokes. 409 carries the current record so
        the widget can union by id and retry (two devices on one page)."""
        fname = str(payload.get("file") or "")
        stem = self._explainer_stem(fname)
        if stem is None:
            return self._json(400, {"error": f"no such explainer: {fname}"})
        base = payload.get("base")
        try:
            rec = ink_store.save(ROOT, stem, payload.get("strokes"),
                                 base=None if base is None else int(base))
        except ink_store.Conflict:
            cur = ink_store.read(ROOT, stem)
            cur["error"] = "conflict"
            return self._json(409, cur)
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            sys.stderr.write(f"[ask] ink save failed for {stem}: {exc}\n")
            return self._json(500, {"error": f"save failed: {exc}"})
        return self._json(200, {"stem": rec["stem"], "updated_at": rec["updated_at"],
                                "rev": rec["rev"], "count": len(rec["strokes"])})

    def _post_readpaper(self, payload: dict):
        """Start the one-click pipeline for a paper url (the iPad path).

        Mirrors readpaper_host.main() minus the browser-fetched PDF bytes: the
        caller only has a url, so venues behind a bot wall (OpenReview's
        Turnstile) will fail in the worker's curl, as documented there.
        resolve() may fetch the landing page (<= 10 s); this handler blocks on
        that, which is fine on a threading server.
        """
        url = str(payload.get("url") or "").strip()[:2000]
        if not url.lower().startswith(("http://", "https://")):
            return self._json(400, {"error": "url must start with http(s)://"})
        try:
            pdf_url, dest, how = readpaper_host.resolve({"url": url})
        except Exception as exc:  # resolver is best-effort; never take the server down
            sys.stderr.write(f"[go] resolve crashed for {url}: {exc!r}\n")
            return self._json(500, {"error": f"resolve failed: {exc}"})
        if not pdf_url:
            return self._json(404, {"error": how or "no pdf found", "url": url})
        if not is_safe_dest(dest):
            return self._json(400, {"error": f"unsafe dest: {dest}"})

        existing = find_existing(dest)
        if existing:
            return self._json(200, {"status": "exists", "id": dest, "file": existing,
                                    "title": explainer_title(existing)})

        log = os.path.join(PROJECT, "logs", "readpaper-spawn.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "ab") as lf:
            proc = subprocess.Popen(
                ["/bin/bash", WORKER, pdf_url, dest],
                stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                start_new_session=True, cwd=PROJECT,
            )
        sys.stderr.write(f"[go] started {dest} via {how} pid={proc.pid} "
                         f"from {self.client_address[0]} url={url}\n")
        return self._json(200, {"status": "started", "id": dest, "how": how,
                                "pdfUrl": pdf_url, "pid": proc.pid})

    def _post_meta(self, payload: dict):
        """Full update of one record. No busy guard: a reading note may be saved
        while the chat agent is editing the page -- they never touch the same file."""
        fname = str(payload.get("file") or "")
        stem = self._explainer_stem(fname)
        if stem is None:
            return self._json(400, {"error": f"no such explainer: {fname}"})
        try:
            rec = meta_store.save(ROOT, stem, payload.get("status"), payload.get("level"),
                                  payload.get("note"),
                                  html_path=os.path.join(ROOT, stem + ".html"))
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            sys.stderr.write(f"[ask] meta save failed for {stem}: {exc}\n")
            return self._json(500, {"error": f"save failed: {exc}"})
        return self._json(200, rec)

    def _post_ask(self, payload: dict):
        fname = os.path.basename(str(payload.get("file") or ""))
        question = str(payload.get("question") or "").strip()[:MAX_QUESTION]
        mode = str(payload.get("mode") or "auto")
        anchor = payload.get("anchor") or {}
        selection = str(payload.get("selection") or "")[:MAX_SELECTION]

        if not fname.endswith(".html") or not os.path.isfile(os.path.join(ROOT, fname)):
            return self._json(400, {"error": f"no such explainer: {fname}"})
        if not question:
            return self._json(400, {"error": "empty question"})
        if mode not in ("auto", "qa", "edit"):
            mode = "auto"

        busy = live_job_for(fname)
        if busy:
            return self._json(409, {"error": "busy", "job": busy})

        job_id = uuid.uuid4().hex[:8]
        job = {
            "job": job_id, "state": "queued", "file": fname, "question": question,
            "mode": mode, "created": time.time(),
            "anchor": {"id": str(anchor.get("id") or ""),
                       "text": str(anchor.get("text") or "")[:300]},
            "selection": selection,
            "phase": "起動中…",
        }
        write_job(job)

        log = os.path.join(PROJECT, "logs", f"ask-{fname[:-5]}-{job_id}.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "ab") as lf:
            proc = subprocess.Popen(
                [sys.executable, RUNNER, "--job", job_id,
                 "--project", PROJECT, "--jobs", JOBS, "--root", ROOT],
                stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                start_new_session=True, cwd=PROJECT,
            )
        WORKERS[job_id] = proc.pid
        return self._json(200, {"job": job_id, "state": "queued"})


def main() -> int:
    global ROOT, PROJECT, JOBS, TOKEN, ALLOW
    project_default = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--project", default=project_default)
    ap.add_argument("--root", default="")
    ap.add_argument("--allow", default=DEFAULT_ALLOW,
                    help="comma-separated CIDRs allowed to reach this server")
    ap.add_argument("--config", default=CONFIG_ENV,
                    help="config.env holding ASK_TOKEN (generated if absent)")
    args = ap.parse_args()

    PROJECT = os.path.abspath(args.project)
    ROOT = os.path.abspath(args.root or os.path.join(PROJECT, "html-expl"))
    JOBS = os.path.join(PROJECT, "logs", "ask-jobs")
    if not os.path.isdir(ROOT):
        sys.stderr.write(f"root not found: {ROOT}\n")
        return 1
    jobs_dir()

    try:
        ALLOW = parse_allow(args.allow)
    except ValueError as exc:
        sys.stderr.write(f"bad --allow: {exc}\n")
        return 1
    if not ALLOW:
        sys.stderr.write("--allow is empty: nothing could reach the server\n")
        return 1
    TOKEN = load_token(args.config)

    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    sys.stderr.write(f"[ask] serving {ROOT} on http://{args.bind}:{args.port}/\n"
                     f"[ask] project={PROJECT}\n"
                     f"[ask] allow={','.join(str(n) for n in ALLOW)}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
