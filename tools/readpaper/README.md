# readpaper — one-click paper explainer

Open a paper page in Chrome — an arXiv abstract, an OpenReview forum, a PMLR /
JMLR / ACL / NeurIPS / CVF landing page, or any `*.pdf` URL — click the toolbar
button once, and a background job downloads the PDF into `papers/`, runs a coding
agent headless against the pinned `explain-via-html` conventions, rebuilds the mobile
`html-expl/index.html`, and pops a macOS notification when done.

```
browser click ──▶ extension ──▶ native host ──▶ (detached) readpaper_run.sh
                  find pdf +     resolve dest      save/download → %PDF check →
                  fetch bytes    stash bytes       figures → agent → index → notify
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

## Parts
- `extension/` — Chrome MV3 toolbar button (`manifest.json`, `background.js`, icons).
- `host/readpaper_host.py` — native messaging host; resolves the URL → (pdf url,
  dest), decodes browser-fetched bytes, spawns the job.
- `host/readpaper_run.sh` — the detached worker (download/adopt + `%PDF-` check +
  figures + headless agent + index + notify).
- `install.sh` — deploys the host, bakes the project path and PATH, writes
  `config.env`, and registers the native messaging host manifest for your extension ID.

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
  `clamping SessionEnd hook timeout to 3s`) arrive as error items on every run. Success is
  judged the same way on both backends: did a new html appear after `START_EPOCH`?
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
- **Editing the extension requires a manual reload** in `chrome://extensions`
  (unpacked). Editing `host/` requires re-running `install.sh` (copy-based deploy).

## Manual smoke test (no Chrome)
```
tools/readpaper/test_host.sh https://arxiv.org/abs/2605.26106v1   # full host round-trip
tools/readpaper/test_host.sh --resolve-only                       # url → (pdf url, dest) battery
tools/readpaper/test_host.sh --resolve-only <url> [<url> ...]
```
The first form feeds a native-messaging frame to the host and prints its reply +
the parsed id; add `--run` to actually execute the full download+claude job.
`--resolve-only` just prints the resolution for a set of urls (arXiv, OpenReview,
PMLR old/new, JMLR, ACL, NeurIPS, raw pdf) without spawning anything.
