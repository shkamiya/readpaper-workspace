# readpaper — one-click paper explainer

Open a paper page in Chrome — an arXiv abstract, an OpenReview forum, a PMLR /
JMLR / ACL / NeurIPS / CVF landing page, or any `*.pdf` URL — click the toolbar
button once, and a background job downloads the PDF into `papers/`, runs a coding
agent headless against the pinned `explain-via-html` conventions, rebuilds the mobile
`html-expl/index.html`, and pops a macOS notification when done.

```
browser click ──▶ extension ──▶ native host ──▶ (detached) readpaper_run.sh
                  find pdf +     resolve dest      save/download → %PDF check → figures →
                  fetch bytes    stash bytes       agent → finalize → check → index → notify
```

First-time setup on a fresh Mac is in `SETUP.md` at the repo root.

## Finding the PDF on a landing page
The extension injects a tiny extractor into the active tab and reads the PDF url
out of the **rendered** DOM, in this order:

1. the tab already is a PDF (`document.contentType`, `*.pdf`);
2. OpenReview rule: `forum?id=X` → `pdf?id=X`;
3. `<meta name="citation_pdf_url">` — the Highwire tag, present on PMLR, JMLR,
   NeurIPS, ACL, bioRxiv, Springer, …;
4. `<link rel="alternate" type="application/pdf">`;
5. the first plausible `<a href="*.pdf">`.

The host repeats the same ladder server-side (it GETs the page and scrapes
`citation_pdf_url`) so `test_host.sh` and non-extension callers work too.

Why scrape rather than rewrite urls: for PMLR volume 235 the landing page is
`proceedings.mlr.press/v235/kim24d.html` but the PDF actually lives at
`raw.githubusercontent.com/mlresearch/v235/main/assets/kim24d/kim24d.pdf` —
not derivable from the page url. Older volumes are flat
(`proceedings.mlr.press/v37/ioffe15.pdf`). `citation_pdf_url` covers both.

**Why the browser downloads the bytes.** Some venues sit behind a bot wall.
Measured 2026-08-13 on `openreview.net/pdf?id=YicbFdNTTy` (ViT, public):
plain `curl` → **HTTP 403** (12,692 bytes of HTML); the same url with full
Chrome-like headers → **HTTP 200 whose body is a Cloudflare Turnstile page**,
not a PDF; `api2.openreview.net` → `ChallengeRequiredError`. Deriving the url is
trivial, *fetching* it is not — so the service worker fetches with Chrome's
cookies (a tab that cleared Turnstile, or a signed-in session) and ships the
bytes to the host, which stashes them in `/tmp` for the worker. Open venues
(PMLR/JMLR/ACL/NeurIPS) still work without this, via `curl` in the worker.

## `dest` naming
`dest` is the paper's identity everywhere: `papers/<dest>.pdf`,
`html-expl/figs/<dest>/`, and the worker's duplicate guard. A landing page and
its raw PDF url resolve to the same `dest`.

| venue | dest |
|---|---|
| arXiv | `2605.26106v1` (bare id — **unchanged**; existing explainers depend on it) |
| OpenReview | `openreview-YicbFdNTTy` |
| PMLR | `pmlr-v235-kim24d` |
| JMLR | `jmlr-v21-19-1015` |
| ACL Anthology | `acl-2021.emnlp-main.446` |
| NeurIPS | `neurips-2017-3f5ee243` |
| CVF | `cvf-<paper slug>` |
| anything else | `<host>-<pdf basename>` (or the title slug if the basename is generic) |

## Duplicate guard
Before downloading anything, the worker scans `html-expl/*.html` for an explainer
that already covers this `dest` (version suffix ignored), and if it finds one it
opens that file, notifies `already exists: <title>`, and exits 0 — no figure
re-extraction (which would clobber manual re-crops), no 200k-token agent session.
`READPAPER_FORCE=1` skips the guard.

An html counts as "the explainer *of* this paper" only via markers that the page
owns:
- `figs/<dest>/…` (embedded crops) or `papers/<dest>.pdf` (the finalize stamp
  `<!-- … source: papers/<dest>.pdf … -->`);
- failing that, the id printed in the page header — everything from `<body>` to
  the first `<h2>` — which is the only marker the ~26 pre-2026-07 explainers have.

It deliberately does **not** substring-match `arxiv.org/abs/<id>` over the whole
body: every explainer cites a dozen other papers in exactly that form, in its
先行研究 and Q&A sections. Doing so made the button answer "already exists" for
papers that had never been read (2026-08-17: clicking 2306.11167 and 2509.23808
both matched `20260814-long-distance-exploration-insight.html`, which cites both
in its reference list). Regression test: `test_host.sh --dupe-guard`.

## Parts
- `extension/` — Chrome MV3 toolbar button (`manifest.json`, `background.js`, icons).
- `host/readpaper_host.py` — native messaging host; resolves the URL → (pdf url,
  dest), decodes browser-fetched bytes, spawns the job.
- `host/readpaper_run.sh` — the detached worker (download/adopt + `%PDF-` check +
  figures + headless agent + finalize + math check + index + notify).
- `host/finalize_html.py` — puts the real session id into the explicitly named draft.
- `host/publish_html.py` — private drafts, validation, conflict detection and atomic publication.
- `host/check_html.py` — checks and mechanically repairs the page's math.
- `host/test_check_html.py` — regression tests for the two above (`python3 …`, no deps).
- `host/ask_server.py` — serves `html-expl/`, injects the chat widget into every html
  response, and queues questions (see "In-page chat" below).
- `host/ask_run.py` — the detached worker for one question (snapshot + agent + math
  check + diff + notify).
- `host/ask_widget.js` — the widget itself; lives in a shadow root so no explainer's
  stylesheet can reach it.
- `host/meta_store.py` — SK's reading marks (状態 / 理解度 / ひとことまとめ), one JSON per
  explainer under `html-expl/meta/` (see "Reading marks" below).
- `host/mark_widget.js` — the mark card injected under every explainer's title.
- `host/test_meta.py` — regression tests for the store and the exporter (`python3 …`, no deps).
- `export_reading.py` — dumps the marks as a Markdown table.
- `hooks/` — Claude Code hooks that do for an *interactive* session what the worker
  does for a headless one (see "Session id" below). Registered in `.claude/settings.json`.
- `install.sh` — deploys the host, bakes the project path and PATH, writes
  `config.env`, and registers the native messaging host manifest for your extension ID.

## Explicit drafts, session ids, and publication (2026-09-07)

A worker fixes its output **before** starting the agent. The private draft is
named `YYYYMMDD-readpaper-<dest>.html`, but that is only a placeholder. At publish
time (2026-10-02 fix) the public file is `YYYYMMDD-<slug>.html`. The agent writes
`<slug>` to `logs/readpaper-work/<run-id>/slug.txt` (English kebab-case, 3-6 words,
e.g. `dissecting-hrm-mechanistic-study`). If slug.txt is missing, the slug comes
from the English words of `<title>`, and the placeholder name is used only if both
are empty. An existing file is never overwritten: a clash gets `-2`, `-3`, and so on.
The index title still comes from the document's `<title>`. The private run directory is `logs/readpaper-work/<run-id>/`:

```
publication.json       exact public filename + starting content hash
before.html            prior public version, if one exists
html-expl/<name>.html   the only HTML the agent is instructed to edit
html-expl/figs/<dest>/<run-id>/   this generation's private figure crops
```

`finalize_html.py --html <exact-draft> <project> <sid> <pdf> <ts> <agent>`
stamps only that file. It never searches citations, asset paths, or modification
times. Missing output and missing/invalid worker session ids are failures.
Interactive `--stamp` and `--hook` still use their explicit file lists.

`publish_html.py` takes a frozen copy of the draft, runs the mandatory math
checker and safe automatic fixes, checks UTF-8 and closed document/title/body/h1
structure, and verifies local image references. Findings abort publication.
Under a short process lock it checks that the public page has the same hash as
when the run began. A competing edit aborts publication instead of losing work.
New figure files go to `figs/<dest>/<run-id>/`, preserving earlier figure
versions; the validated HTML then replaces the public file with `os.replace`.
The index also uses an atomic replacement.

Agent failure, timeout on the ask path, missing output, failed checks, and stale
edits leave the public HTML untouched. The private draft, baseline and manifest
remain under `logs/readpaper-work/` for inspection. These directories currently
have no automatic retention policy. A failure during asset copy can leave an
unreferenced new asset directory; it never overwrites an old figure directory.

The no-output discovery bug previously let concurrent workers stamp another
paper's HTML. This explicit-path contract replaces that heuristic entirely.
`READPAPER_FORCE=1` now regenerates into the deterministic filename for today's
date; it does not guess which differently named legacy HTML should be replaced.
Existing files and their reading marks keep their names.

The agent is instructed to edit only the private draft. This is the worker's
publication boundary, **not** an OS sandbox against an unrestricted agent that
ignores its target. Direct edits in ordinary interactive sessions still follow
that session's own workflow. Static checks do not prove semantic completeness
or fidelity to the paper.

Regression tests (stdlib only, no paid agent calls):

```
python3 tools/readpaper/host/test_publication.py
python3 tools/readpaper/host/test_check_html.py
```

Deploy `publish_html.py`, `check_html.py`, `finalize_html.py` and the worker as
one update with `install.sh`; don't deploy only the new finalizer beside an old
worker. The installer preserves existing `config.env`, including `ASK_TOKEN`.
The ask server starts the repository's `ask_run.py` for each new question, so
that worker update requires no server restart.

## Agent backends

The worker drives either **Claude Code** (`claude`) or **Codex CLI** (`codex`). It picks
one in this order: `READPAPER_AGENT` in the environment → `READPAPER_AGENT` in the
deployed `config.env` (written by `install.sh`) → autodetect (`claude`, else `codex`).

| | claude | codex |
|---|---|---|
| invocation | `claude -p "$PROMPT" --session-id <SID> --output-format json --dangerously-skip-permissions` | `codex exec --json --dangerously-bypass-approvals-and-sandbox "$PROMPT" </dev/null` |
| session id | pre-minted with `uuidgen`, passed in | recovered *after* the run from the first `thread.started` JSONL event |
| resume | `claude --resume <id>` | `codex exec resume <id>` / `codex resume <id>` |
| pre-compact >200k | yes (`-p "/compact"`) | **not available** — skipped, logged |
| extra args | — | `READPAPER_CODEX_ARGS` (e.g. `-m gpt-5.6-sol -c model_reasoning_effort=high`) |

Three Codex-specific traps, all load-bearing:
- **`</dev/null` is required.** Whenever stdin is not a TTY, `codex exec` reads it and
  appends it to the prompt as a `<stdin>` block. The banner
  `Reading additional input from stdin...` prints either way and is *not* a symptom.
- **`item.type == "error"` events are not failures.** Benign plugin-hook warnings (e.g.
  `clamping SessionEnd hook timeout to 3s`) arrive as error items on every run. Success requires a zero process exit, the explicit draft, valid session metadata,
  and the mandatory publication checks on both backends.
- **`codex exec resume` needs `--skip-git-repo-check`** unless the cwd is a git repo or is
  marked trusted in `~/.codex/config.toml`; otherwise it refuses with "Not inside a trusted
  directory". `finalize_html.py` writes the flag into the resume hint it stamps into each
  html for exactly this reason.

## Explainer conventions are pinned in-repo

The worker prompt tells the agent to read `skills/explain-via-html/SKILL.md` **by path**.
Upstream `mathbullet/skills` deleted that skill on 2026-08-01 (commit `5dc38549`,
replaced by a different `html` skill), so a marketplace install can no longer supply it —
and reading it by path makes both backends follow byte-identical conventions with no
plugin install at all. See `skills/README.md`.

## In-page chat (ask): asking a question from inside the explainer

`ask_server.py` replaces `python3 -m http.server` for `html-expl/`. It serves the
same files, and injects a chat widget into every `.html` response:

```
browser (widget) ──POST /api/ask──▶ ask_server.py ──detach──▶ ask_run.py
   FAB bottom-right                  job table +               snapshot → agent →
   selection + anchor                per-file lock             check → atomic publish → diff → notify
   ◀──GET /api/status (3s poll)──                              writes logs/ask-jobs/<id>.json
```

Typing a question appends to the page's Q&A section; typing an instruction such as
"この部分の説明を厚くして" edits the body instead. The agent decides which, from the
same input box. What makes the second form work is that the widget also sends **where
you were looking**: the current selection, plus the id of the last `[id]` element above
the middle of the viewport. Measured 2026-08-22: with that anchor alone, an instruction
carrying no target at all was routed to the right section.

**The files on disk are never modified by the server.** The `<script>` tag is inserted
into the response bytes before the last `</body>`. `html-expl/` is gitignored, so an
in-place edit would be unrecoverable, and all 131 explainers would need patching.
The cost of that choice: a page opened as `file://` gets no widget. Read via
`http://127.0.0.1:8770/` on the Mac, or `http://100.66.108.47:8770/` from the iPhone
over Tailscale.

**中止 (Ctrl+C).** While a job runs, the send button becomes 中止 and Ctrl+C triggers
it too (on macOS always, since Cmd+C is copy; elsewhere only when nothing is selected).
The widget puts the prompt back into the input box so it can be fixed and resent.
`POST /api/cancel` writes `logs/ask-jobs/<id>.cancel` and `killpg`s the worker, which
leads its own process group (`start_new_session=True`), so the agent CLI dies with it;
anything that ignores SIGTERM is SIGKILLed after 5 s. The worker then records
`state: "cancelled"` and **never publishes** -- the public page is untouched. Once
publishing has started the cancel is refused (409), because dying inside `publish()` is
worse than letting the answer land. If the cancelled run had resumed a session, the
mistaken prompt stays in that transcript (same as Claude Code's own Ctrl+C), so the next
prompt on that paper tells the agent to disregard it. `test_ask_cancel_e2e.mjs` covers
the browser side.

The worker resumes the session named in the page's line-1 stamp
(`claude --resume <sid>`, no `--fork-session`, so the id survives and the stamp stays
valid). A transcript older than `cleanupPeriodDays` is gone, so `resolve_session()`
checks for the jsonl first and falls back to a fresh session that reads the html and
the PDF.

**Adopted sessions.** After a fallback, the new session holds the whole exchange -- the
page it read, the question, its answer -- so the next question should continue it rather
than start cold again. `ask_run.py` records the session the run actually used in
`logs/ask-jobs/sessions.json` (file -> session), and `resolve_session()` prefers it over
the stamp. Without this, every question on a pre-`cleanupPeriodDays` paper re-read the
page and remembered nothing (observed 2026-08-26: two questions on a June paper, two cold
sessions). The adopted id lives beside the jobs and is never written back into the html --
the id printed in the page header means "the session that wrote this page", which is
provenance worth keeping. An adopted id is liveness-checked like any other, so it degrades
to a fresh session once it too expires. The store is written under an `O_EXCL` lock: the
per-file busy guard only serialises jobs on the *same* paper, and jobs on different papers
run concurrently against this one file.

**The snapshot is not optional.** `html-expl/` being gitignored means there is no
`git checkout` to undo a bad edit; `logs/qa-backups/<stem>.<ts>.html` is the only prior
copy that exists, so a snapshot failure aborts the run before the agent starts.
Ten generations are kept per explainer.

The worker is Python, not bash, unlike `readpaper_run.sh`. The input is arbitrary text
typed into a web form, and this README already documents what one apostrophe did to a
heredoc in the bash worker. `subprocess` with a list argument never invokes a shell.

Two things measured on real runs, both of which shaped the code:

- the agent's final output is **prose followed by a ` ```json ` fence**, not bare JSON,
  so `ask_run.py` extracts the last fence rather than parsing the whole result;
- the agent's self-reported `targets` **under-reports** what it changed (it said
  `["#s4"]` while also rewriting the glossary), so the highlight list is computed by
  diffing against the snapshot.

### Running it: launchd, not nohup

It is a LaunchAgent, `~/Library/LaunchAgents/com.sk.paper-reading-ask.plist`
(`RunAtLoad` + `KeepAlive`, port **8770**, `--bind 0.0.0.0`).

```
launchctl kickstart -k gui/$(id -u)/com.sk.paper-reading-ask   # restart (after editing the script)
launchctl print       gui/$(id -u)/com.sk.paper-reading-ask    # state / pid / last exit code
launchctl bootout     gui/$(id -u)/com.sk.paper-reading-ask    # stop and unregister
tail -f ~/Library/Logs/readpaper/ask-server.log
```

**Ports, and why they are the way they are.**

| port | agent | what it is |
|---|---|---|
| **8770** | `com.sk.paper-reading-ask` | `ask_server.py` — the real one. Read *and* ask. |
| 8772 | `com.sk.paper-reading-serve` | plain `http.server` — read-only escape hatch, no widget |

`ask_server.py` first went live on 8771 with the plain server left on 8770, so that a
crash-looping ask server could never cost read access. That was the right instinct and
the wrong port: the iPhone made **zero** requests to 8771, ever. SK reads through a
"Daily Papers" home-screen web app, and **iOS bakes a web app's start URL in when you
add it and gives you no way to edit it** — the icon points at `…:8770/` forever, so every
tap landed on the widget-less plain server and the chat simply was not there. The port
you can actually reach has to be the port with the feature. On 2026-09-04 ask_server took
8770 and the plain server moved to 8772, which keeps the escape hatch without putting it
in front of the door.

The plist is `KeepAlive`, so a server that fails at startup crash-loops. If that happens,
`http://100.66.108.47:8772/` still serves every explainer, read-only.

Two things this plist must carry, both learned the hard way on 2026-09-04:

- **`EnvironmentVariables.PATH`.** launchd hands over `/usr/bin:/bin:/usr/sbin:/sbin`,
  not the login shell's PATH. `ask_run.py:329` locates the agent with
  `shutil.which("claude")`, which returns `None` under that PATH — every question would
  be accepted and then die on dequeue.
- **`Standard{Out,Error}Path` outside `~/Desktop`.** launchd opens those files itself,
  before the process exists, and `~/Desktop` is TCC-protected. Pointing them at
  `<project>/logs/ask-server.log` made every spawn fail with `last exit code = 78:
  EX_CONFIG` and no log saying why. They now go to `~/Library/Logs/readpaper/`.
  (`com.sk.paper-reading-serve` still writes to `logs/serve.log` on the Desktop only
  because it was granted that before the check applied to it — do not copy the pattern.)

Before it was a LaunchAgent it was started by hand, which is why it silently vanished at
the 2026-08-27 reboot and stayed dead for a week (`nohup` survives a closed terminal, not
a restart). The equivalent manual command, still useful for testing on a spare port:

```
python3 tools/readpaper/host/ask_server.py --port 8783 --bind 127.0.0.1
```

Changing which port serves the widget means the home-screen icon has to be re-added by
hand (Safari → 共有 → ホーム画面に追加, then delete the old icon), which is exactly the
cost 8770 was chosen to avoid. Keep it on 8770.

### Who can reach it

`--bind 0.0.0.0` is what lets the iPhone in, and it also exposes the socket to the home
LAN, so two gates guard the endpoint that starts `claude --dangerously-skip-permissions`:

1. **Source range** — every request, GET included, must come from `127.0.0.0/8`, `::1`,
   or the Tailnet CGNAT range `100.64.0.0/10`; anything else gets 403 and a
   `[ask] refused <ip> <path>` line. Override with `--allow <cidr,cidr>`.
   GET is gated too because the served page carries the token: a LAN client allowed to
   read would otherwise just read the token off the page.
2. **Shared token** — `ASK_TOKEN` in
   `~/Library/Application Support/readpaper/config.env` (the server generates and appends
   one if it is missing). It is injected into each page as `data-ask-token`, echoed by the
   widget on `POST /api/ask`, and compared with `hmac.compare_digest`.

The token is not a secret against someone who can already fetch the page, so gate 1 plus
Tailscale's own authentication is the real boundary. What gate 2 stops is a cross-origin
drive-by POST from another site open in the same browser (it cannot read the page to learn
the token, and its preflight goes unanswered) and blind non-browser probes. Rotate it by
deleting the `ASK_TOKEN=` line and running `launchctl kickstart -k`.

Per job: `logs/ask-<dest>-<job>.log` and `logs/ask-jobs/<job>.json`. A run takes about
five minutes.

## Starting DP from a browser without the extension (iPad / iPhone): `/go`

iPadOS Chrome and Safari have no extension mechanism and no native messaging, so the
toolbar button cannot exist there. `ask_server.py` therefore exposes the same pipeline
over HTTP (added 2026-09-07):

```
bookmarklet / share-sheet shortcut ──▶ GET /go?url=<paper url>   (no side effect)
   "この論文を読む" button           ──▶ POST /api/readpaper {url, token}
                                          └─ readpaper_host.resolve() → find_existing()
                                             → spawn readpaper_run.sh <pdf_url> <dest>
   progress                          ◀── GET /api/readpaper/status?id=<dest>  (15 s poll)
```

- `GET /go` without `url` is the setup sheet: the bookmarklet (host filled in from the
  request's `Host` header), the iOS Shortcut recipe (share sheet → POST with the token),
  and a paste box. The index page links to `/go` from its footer (its own paste box was
  replaced by a title/author/venue search box on 2026-10-02).
- `POST /api/readpaper` reuses `readpaper_host.resolve()` unchanged (arXiv id, venue
  rules, `citation_pdf_url` scraping), then runs the same content-based duplicate check
  as the worker's guard (`find_existing()`, a port of the guard's regexes) so a paper that
  already has an explainer answers `{"status":"exists","file":…}` immediately instead of
  spawning a worker that would exit with "already exists". The worker spawned is the
  **repo** copy of `readpaper_run.sh` (this server runs from the repo under launchd with a
  PATH that carries `claude` and pyenv `python3`), not the deployed copy.
- `GET /go` has no side effect on purpose. Starting a run needs the token-carrying POST,
  for the same reason `/api/ask` does: otherwise `<img src="http://…:8770/go?url=…">` on
  any page open in the same browser would start a `claude --dangerously-skip-permissions`
  run. The token is the one in `config.env` (`ASK_TOKEN`), also shown on `/go` for the
  Shortcut recipe -- it is already readable on every served page, so this is no new
  exposure.
- What the iPad path cannot do: ship PDF bytes. The extension fetches with Chrome's
  cookies (OpenReview's Turnstile, publisher logins); from `/go` only the url reaches the
  Mac, so those venues fail in the worker's curl with "download failed". Open venues
  (arXiv, PMLR, JMLR, ACL, NeurIPS, raw `*.pdf`) are unaffected.
- Status: `GET /api/readpaper/status?id=<dest>` returns `exists`/`file`/`title` (via
  `find_existing`) and the last `[readpaper]` line of the newest `logs/readpaper-<dest>-*.log`
  as `phase`; `failed` is true when that line starts with `[readpaper] ERROR`.
- Smoke test without burning an agent run: POST a url that ends in `.pdf` but 404s
  (`https://example.com/x.pdf`). `resolve()` accepts it (`how: self`), the worker spawns,
  curl fails, status reports `failed: true` within seconds, and the Mac shows the
  "✗ download failed" notification. Verified 2026-09-07.

## Handwriting (Apple Pencil): `ink_widget.js` + `/api/ink`

Third injected script (2026-09-07). A full-document `<svg>` overlay; the ✎ button
bottom-left switches ink mode on, the pencil draws, a finger still scrolls, and the
strokes are saved to `html-expl/ink/<stem>.json` (`ink_store.py`), so the same page on
the iPhone or the Mac shows them. Plan: `notes/20260907-ink-handwriting-plan.md`.

- **SVG, not canvas.** iOS caps a canvas at about 16M pixels; an explainer is
  1000 × 30000 px. One `<path>` per stroke.
- **Anchored to block elements, not page pixels.** Explainers carry `id`s only on `h2`, so
  a stroke records the index of the block element it started over (`h1-h6, p, li, figure,
  table, pre, blockquote, aside, dd, dt, .katex-display`) plus a text fingerprint
  (tag + first 60 chars) to survive edits by the chat agent, with `x` normalised by the
  element's width and `y` in px from its top. Paragraph-level placement survives reflow;
  line-level does not. When the element is now more than 20 % wider or narrower than
  when the stroke was written (iPad → iPhone), the stroke is drawn faint.
- **Pencil vs finger.** WebKit's `Touch.touchType === "stylus"`: a non-passive
  `touchstart` calls `preventDefault` for the stylus only, so `touch-action: pan-x pan-y`
  stays and a finger scrolls. Pointer events then give pressure (stroke width =
  `size × (0.55 + mean pressure)`). ☝︎ toggles finger drawing for a device without a
  pencil. On the Mac the mouse/trackpad draws.
- **Concurrency.** Records carry an integer `rev`; `POST /api/ink {strokes, base}` is
  refused with 409 (and the current record) when `base != rev`, and the widget unions
  by stroke id and retries once. `updated_at` alone was not enough: two saves inside one
  second are indistinguishable at second resolution (caught by `test_ink.py`). The prior
  version is kept as `<stem>.prev.json`.
- Tools: 3 pen colours, highlighter (`mix-blend-mode: multiply`), stroke eraser (12 px),
  undo, 完了. Saves 1.2 s after the last change; status next to the toolbar.
- Never shown to the chat agent; like `meta/`, this directory is SK's and no agent edits it.

Verified 2026-09-07 with curl (GET / POST / 409 / 403 / validation) and a headless Chrome
`--dump-dom` of a page with one saved stroke, which contained the `<path data-id>` at the
anchor element's position. Pencil behaviour itself (stylus detection, pressure) needs the
iPad.

## Reading marks: 読み中 / 読了, 理解度, ひとことまとめ

Every explainer gets a card right under its title holding SK's own state for that
paper: a status chip (読み中 / 読了 — "unread" is the *absence* of a status, never a
value), a three-step understanding chip (ふんわり / だいたい / しっかり, independent of
the status), and a free-text one-liner in a handwriting face (Zen Kurenaido, `$…$` math
rendered by KaTeX). Design and the decisions behind it: `notes/20260904-reading-marks-plan.md`.

```
mark_widget.js (card under <h1>)  ──POST /api/meta──▶  ask_server.py ──▶ meta_store.py
      ◀──GET  /api/meta──                                        writes html-expl/meta/<stem>.json
                                                                 rewrites html-expl/meta/_all.json
index.html  ──GET meta/_all.json (static, no-store)──▶  badges + 読み中/読了 filter
ask_run.py  ──meta_store.read()──▶  prompt: "SK の現在の理解度: …", read-only
```

- **Storage is a sidecar, never the html.** Same reason as the chat widget: the html
  files are not the server's to modify, and the chat agent edits them — a note embedded
  in the page would be one agent rewrite away from vanishing. `html-expl/meta/` is
  gitignored with the rest of `html-expl/` (personal content; Time Machine is the backup).
- **Key = html stem** (`20260829-ouro-looped-language-models`), the same identity
  `build_index.py` and `/api/ask` use. `dest` (arXiv id etc.) is recorded inside the
  record for later cross-referencing, from the line-1 finalize stamp or the `figs/<dest>/`
  path. `export_reading.py --orphans` lists records whose html has gone (regenerated
  under a new stem).
- **Writes are full, not partial, and go through `POST /api/meta` only** — the same
  source-range + token gates as `/api/ask`, but no per-file busy guard: a note may be
  saved while the agent is editing the page; they never touch the same file. The previous
  values are pushed onto `history` (cap 50) whenever anything changed. Writes are
  `tmp` + `os.replace` under a process-level lock (one process writes).
- **`_all.json` is what the index reads** — one request for all 150+ papers, regenerated
  on every save, served `no-store`. Records with nothing set are left out of it, so
  the index never even sees "unread" as a value, and it shows **no counts** by design.
  `build_index.py` bakes the same badges at build time; the page JS re-fetches and
  overwrites them on every visit (and reloads on a bfcache restore), so a status set inside
  an explainer shows up on the way back without a rebuild. `index.html#done` / `#reading`
  deep-links a filter.
- **The agent sees the marks but never writes them.** `ask_run.py` prepends the
  understanding level and the note to the prompt with an explicit "do not edit
  `html-expl/meta/`" line; the house rule is `CLAUDE.md` / `AGENTS.md` §3.
- **Math in the note.** The card uses the page's own KaTeX when present (134 pages); on
  the MathJax pages (15) and the two with no renderer it loads KaTeX 0.16.9 from the same
  CDN once, after `load`. Verified 2026-09-04 on one page of each kind.
- **Read-only fallbacks.** On 8772 (plain `http.server`) or `file://` there is no
  injection, so no card — but the index badges still work there, since `_all.json` is a
  static file.

```
python3 tools/readpaper/host/test_meta.py              # store + exporter tests
python3 tools/readpaper/export_reading.py --status done  # md table on stdout
launchctl kickstart -k gui/$(id -u)/com.sk.paper-reading-ask   # after editing the server/widget
```

## Adding a venue
1. `extension/manifest.json` → add the PDF host to `host_permissions` (the list
   is deliberately enumerated, not `<all_urls>`). Only needed when the browser
   must do the fetch, i.e. behind a bot wall or login.
2. `host/readpaper_host.py` → add a `dest` rule in `venue_identity()` if the
   generic `<host>-<basename>` name is not good enough. The PDF url itself
   usually needs no rule (`citation_pdf_url` finds it).
3. `tools/readpaper/install.sh <EXT_ID>` + reload the extension in Chrome.

## Setup (once)
1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select `tools/readpaper/extension/`.
2. Copy the generated **extension ID**.
3. `tools/readpaper/install.sh <EXT_ID>`.
4. Reload the extension. Click the button on an arxiv page to test.

## Notes / caveats
- **Figures are auto-extract rough.** Headless runs skip the manual figure
  clip-coordinate step; refine figures later in an interactive session if needed.
- **`--dangerously-skip-permissions`** is used so the headless run can write
  files / run python without prompts. Only point this at papers you trust — a
  malicious PDF could attempt prompt injection. Personal-research use only.
- **PATH** is baked into the *deployed* `readpaper_run.sh` by `install.sh`, because
  Chrome spawns the host with a bare env. `install.sh` resolves the agent CLI, `python3`
  and the homebrew prefix on the installing machine. If you switch node/python versions
  (so `claude`/`python3` move), **re-run `install.sh`** — do not edit the deployed copy.
- **Logs** per job: `logs/readpaper-<dest>-<timestamp>.log`.
- Concurrent clicks launch concurrent headless runs (no queue in v0.1).
- **A landing page that needs JS to show its PDF link** only works through the
  extension (the host's server-side GET sees the unrendered HTML).
- **No apostrophes inside the `<<'PYEOF'` heredocs in `readpaper_run.sh`.** They sit inside
  `$( ... )` command substitutions, and bash keeps tracking quotes through the heredoc body
  there, so one unpaired `'` — even inside a Python comment — breaks the whole script with
  `unexpected EOF while looking for matching`. Not hypothetical: writing Claude+`'`+s in a
  comment did exactly this. Always run `bash -n tools/readpaper/host/readpaper_run.sh`
  after editing, and note that the failure would otherwise first surface inside a detached
  Chrome-spawned worker.
- **Editing the extension requires a manual reload** in `chrome://extensions`
  (unpacked). The generation worker/helper changes require re-running `install.sh` (copy-based deploy);
  the ask worker runs from the repository on each request.

## Manual smoke test (no Chrome)
```
tools/readpaper/test_host.sh https://arxiv.org/abs/2605.26106v1   # full host round-trip
tools/readpaper/test_host.sh --resolve-only                       # url → (pdf url, dest) battery
tools/readpaper/test_host.sh --resolve-only <url> [<url> ...]
tools/readpaper/test_host.sh --dupe-guard                         # duplicate-guard fixtures
```
The first form feeds a native-messaging frame to the host and prints its reply +
the parsed id; add `--run` to actually execute the full download+claude job.
`--resolve-only` just prints the resolution for a set of urls (arXiv, OpenReview,
PMLR old/new, JMLR, ACL, NeurIPS, raw pdf) without spawning anything.
`--dupe-guard` lifts the duplicate-guard snippet out of `readpaper_run.sh` and
runs it over synthetic `html-expl/` fixtures in a tempdir: a page that merely
*cites* an id must not match it, while asset/stamp/legacy-header pages must.


### 配色切り替え（2026-10-19更新）

通常の readpaper サーバー（8770）の一覧ページで、絞り込みボタン右側の「⚙ 設定」を押すと表示設定が開き、
表示モードで「元の配色」「ダーク」「端末に合わせる」を選べます。ダークの配色では、グラファイト、ソフト・チャコール、ウォーム・チャコール、エスプレッソ、ミッドナイト・ネイビー、スチール・ブルー、ディープ・ティール、フォレスト・グレーの8案と「従来のダーク」を選べます。元の配色では暗色パレット選択は無効です。上部の帯や画面に追従する固定ボタンはありません。設定ボタンは一覧だけに表示し、各論文ページには保存された配色だけを適用します。初期値は元の配色です。
表示モードは `readpaper-theme`、暗色パレットは `readpaper-palette` として、同じブラウザ・同じ配信元のlocalStorageに保存され、
別の記事と再読み込みにも適用されます。端末設定の変更と別タブの設定変更にも追従します。
「端末に合わせる」では、端末がダークのときに選択した暗色パレットを使います。旧設定でパレット未保存の場合は従来のダークを維持します。
保存が禁止されたブラウザでも、そのページ上での切り替えはできます。

`theme_widget.js` を HTML 応答の head に挿入して、本文描画前に保存設定を反映します。
操作UIは Shadow DOM に置き、本文アンカーの対象ブロックを増やしません。
図・画像を反転せず、通常SVGは白地に元色を保ち、数式は本文色に追従します。
黒い手書き線はダーク時の表示だけ明るくし、保存された線は変更しません。
印刷は元の配色です。HTMLファイルを直接開く場合と素の静的サーバーには適用されません。
テーマ自体のAPIや端末間同期はありません。iPhone/iPad実機の確認は未実施です。
