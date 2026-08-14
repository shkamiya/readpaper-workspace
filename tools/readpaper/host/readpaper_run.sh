#!/bin/bash
# Detached worker: download the PDF, run claude explain-via-html headless,
# rebuild the mobile index, then fire a macOS notification.
#
#   readpaper_run.sh <pdf_url> <dest_name> [<local_pdf>]
#
# <local_pdf> (optional) is a /tmp file the *browser* already downloaded, handed
# over by readpaper_host.py. Venues behind a bot wall (OpenReview = Cloudflare
# Turnstile) or a login cannot be curl'd from here at all, so the extension
# fetches with Chrome's cookies and we just move the file into place.
#
# Invoked by readpaper_host.py (spawned by Chrome), so it runs with a minimal
# GUI environment — we rebuild PATH explicitly below.
set -uo pipefail

PDF_URL="${1:?pdf_url required}"
DEST="${2:?dest_name required}"
LOCAL_PDF="${3:-}"

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PROJECT_DIR: install.sh rewrites the placeholder below to the absolute repo
# root in the DEPLOYED copy (which lives outside the repo). When this script is
# run straight from the repo, the placeholder is untouched and we fall back to
# the path relative to this file.
PROJECT_DIR="__READPAPER_PROJECT_DIR__"
# Guard token is split across two string literals so the install.sh sed (which
# matches the contiguous placeholder) rewrites ONLY the assignment line above,
# never this comparison. bash concatenates the two literals at runtime.
if [ "$PROJECT_DIR" = "__READPAPER""_PROJECT_DIR__" ] || [ -z "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(cd "$HOST_DIR/../../.." && pwd)"
fi
cd "$PROJECT_DIR" || exit 1

# GUI-spawned processes get a bare PATH, so rebuild it explicitly. This used to hard-code
# SK's machine ("$HOME/.nvm/versions/node/v22.16.0/bin:$HOME/.pyenv/shims:..."), which made
# the worker unusable on any other laptop — a different node version or a non-pyenv python
# meant `claude`/`python3` were simply not found. install.sh now resolves the agent CLI,
# python3 and the homebrew prefix ON THE INSTALLING MACHINE and bakes the result into the
# placeholder below, exactly like PROJECT_DIR. Same guard trick: the comparison token is
# split across two string literals so install.sh's sed (which matches the contiguous
# placeholder) rewrites ONLY the assignment line above, never this comparison.
BAKED_PATH="__READPAPER_PATH__"
if [ "$BAKED_PATH" = "__READPAPER""_PATH__" ] || [ -z "$BAKED_PATH" ]; then
  # Run straight from the repo (not deployed), i.e. by hand from a normal shell. No bake
  # happened, so the inherited PATH is the better guess and goes FIRST — appending the
  # generic guess ahead of it would shadow, say, a pyenv python3 that has pillow with a
  # homebrew python3 that does not.
  export PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
else
  # Deployed under Chrome: the inherited environment is bare, so the baked PATH leads.
  export PATH="$BAKED_PATH:$PATH"
fi

# ------------------------------------------------------------------- agent backend
# Which coding agent writes the explainer: "claude" (Claude Code) or "codex" (Codex CLI).
# Resolution order, most specific wins:
#   1. READPAPER_AGENT from the environment  — lets one machine try the other backend
#      without reinstalling (used by the verification matrix).
#   2. READPAPER_AGENT in $HOST_DIR/config.env — written by install.sh at deploy time.
#   3. autodetect — claude if present, else codex.
# install.sh refuses to install when neither CLI is on PATH, so step 3 normally resolves.
ENV_AGENT="${READPAPER_AGENT:-}"
[ -f "$HOST_DIR/config.env" ] && . "$HOST_DIR/config.env"
AGENT="${ENV_AGENT:-${READPAPER_AGENT:-}}"
if [ -z "$AGENT" ]; then
  if command -v claude >/dev/null 2>&1; then
    AGENT="claude"
  elif command -v codex >/dev/null 2>&1; then
    AGENT="codex"
  fi
fi

TS="$(date +%Y%m%d-%H%M%S)"
START_EPOCH="$(date +%s)"
mkdir -p logs papers
LOG="logs/readpaper-${DEST}-${TS}.log"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"readpaper\" sound name \"Glass\"" >/dev/null 2>&1 || true
}

# Everything past here is logged to the per-job logfile.
exec >>"$LOG" 2>&1
echo "[readpaper] $(date) start  url=$PDF_URL  dest=$DEST  project=$PROJECT_DIR  agent=${AGENT:-<none>}"

# Duplicate guard: if an explainer for this paper ALREADY exists in html-expl/,
# stop here — do not re-run figure extraction (it can clobber manual re-crops)
# and do not burn a 200k+-token headless claude session that will just refuse to
# regenerate. Before this guard existed, duplicate clicks ran claude anyway; it
# declined via memory ("already generated 2026-06-21"), finalize then grabbed the
# NEWEST html in html-expl/ (a different paper!) and the notification announced
# that wrong title as "done" — which looked like "the button silently fails"
# (observed repeatedly: 2605.07772 on 06-30/07-20/07-21, 2601.23236v2 on 07-17).
# Matching is content-based (figs/<id>, papers/<id>, arXiv:<id>) with the version
# suffix stripped, done in python (bash globs are TCC-flaky under Chrome).
# Escape hatch for intentional regeneration: READPAPER_FORCE=1.
if [ "${READPAPER_FORCE:-0}" != "1" ]; then
  EXISTING="$(python3 - "$DEST" <<'PYEOF'
import os, re, sys
dest = sys.argv[1]
base = re.sub(r"v\d+$", "", dest)
pats = [f"figs/{base}", f"papers/{base}", f"arxiv.org/abs/{base}",
        f"arxiv.org/pdf/{base}", f"arXiv:{base}"]
best, best_m = None, -1.0
try:
    for e in os.scandir("html-expl"):
        if not e.is_file() or not e.name.endswith(".html") or e.name == "index.html":
            continue
        try:
            s = open(e.path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if any(p in s for p in pats):
            m = e.stat().st_mtime
            if m > best_m:
                best, best_m = e.path, m
except OSError:
    pass
if best:
    print(best)
PYEOF
)"
  if [ -n "$EXISTING" ]; then
    ETITLE="$(python3 -c "import re,sys; m=re.search(r'<title>([^<]*)</title>', open(sys.argv[1],encoding='utf-8').read()); print(m.group(1) if m else '')" "$EXISTING" 2>/dev/null)"
    [ -n "$ETITLE" ] || ETITLE="$DEST"
    echo "[readpaper] explainer already exists: $EXISTING (skip; READPAPER_FORCE=1 to regenerate)"
    notify "already exists: $ETITLE"
    /usr/bin/open "$EXISTING" >/dev/null 2>&1 || true
    echo "[readpaper] done (duplicate) -> $EXISTING"
    exit 0
  fi
fi

PDF="papers/${DEST}.pdf"
if [ -f "$PDF" ]; then
  echo "[readpaper] PDF already present: $PDF (skip download)"
elif [ -n "$LOCAL_PDF" ] && [ -f "$LOCAL_PDF" ]; then
  # The browser already fetched the bytes (bot wall / login venue). Copy with
  # python, not cp: python IO is the FDA-stable path in this Chrome-spawned
  # worker (same reason extract_figures.py / build_index.py are python).
  echo "[readpaper] using browser-fetched PDF: $LOCAL_PDF -> $PDF"
  if python3 -c "import shutil,sys; shutil.copyfile(sys.argv[1], sys.argv[2])" "$LOCAL_PDF" "$PDF"; then
    rm -f "$LOCAL_PDF"
  else
    echo "[readpaper] ERROR could not move browser-fetched PDF"
    notify "✗ could not save PDF: $DEST"
    exit 1
  fi
else
  echo "[readpaper] downloading -> $PDF"
  if ! curl -fL --retry 3 -A "Mozilla/5.0" "$PDF_URL" -o "$PDF"; then
    echo "[readpaper] ERROR download failed"
    notify "✗ download failed: $DEST"
    exit 1
  fi
fi

# Verify the magic bytes. A bot wall / login page can answer HTTP 200 with HTML
# (measured on openreview.net/pdf?id=... with browser-like headers: 200 + a
# 1,960-byte Cloudflare Turnstile page). Without this check that HTML would be
# saved as papers/<dest>.pdf and the whole figure-extraction + claude pipeline
# would run on it and "succeed" on garbage.
MAGIC="$(python3 -c "import sys
try: sys.stdout.write(open(sys.argv[1],'rb').read(5).decode('latin-1'))
except Exception: pass" "$PDF" 2>/dev/null)"
if [ "$MAGIC" != "%PDF-" ]; then
  echo "[readpaper] ERROR $PDF is not a PDF (starts with '$MAGIC') — login/bot wall or wrong url: $PDF_URL"
  rm -f "$PDF"
  notify "✗ $DEST: not a PDF (login/bot wall?)"
  exit 1
fi

# Extract real figure/table crops HERE, in the worker, BEFORE Claude runs.
# Claude's own Bash/Read tools are spawned under Chrome and intermittently lose
# Full-Disk-Access via macOS TCC, which makes pdftoppm/Read fail and every
# figure silently degrade to a hand-drawn inline SVG. The worker shell keeps FDA
# reliably (its curl/python IO succeeds every run), so doing the pixel work here
# decouples figure quality from Claude's flaky TCC state.
FIGDIR="html-expl/figs/${DEST}"
echo "[readpaper] extracting figures -> $FIGDIR"
python3 "$HOST_DIR/extract_figures.py" "$PDF" "$FIGDIR" || echo "[readpaper] WARN figure extraction failed"
# Count via python: the bash glob here is TCC-flaky under Chrome and logged
# "extracted 0" even when extract_figures had just written 5+ crops.
NFIG="$(python3 -c "import os,sys
try: print(sum(1 for e in os.scandir(sys.argv[1]) if e.name.endswith('.png')))
except OSError: print(0)" "$FIGDIR")"
echo "[readpaper] extracted $NFIG figure crop(s)"

# House-rules file per backend: Claude Code auto-loads CLAUDE.md, Codex auto-loads
# AGENTS.md. Naming it explicitly in the prompt costs nothing and makes the run
# reproducible when invoked by hand with the other backend forced.
if [ "$AGENT" = "codex" ]; then
  RULES_FILE="AGENTS.md"
  AGENT_LABEL="codex"
else
  RULES_FILE="CLAUDE.md"
  AGENT_LABEL="claude-code"
fi
# The explainer conventions are read FROM THE REPO by path, not from an installed
# marketplace plugin. Upstream mathbullet/skills deleted `explain-via-html` on 2026-08-01
# (commit 5dc38549, replaced by a different `html` skill), so a fresh marketplace install
# can no longer provide it. skills/README.md records the pin. Reading it by path also makes
# Claude and Codex follow byte-identical conventions.
SKILL_MD="skills/explain-via-html/SKILL.md"
if [ ! -f "$SKILL_MD" ]; then
  echo "[readpaper] ERROR $SKILL_MD not found under $PROJECT_DIR"
  notify "✗ $DEST: skills/explain-via-html/SKILL.md missing"
  exit 1
fi

PROMPT="まず ${SKILL_MD} を読み、その HTML explainer 規約に従うこと（marketplace plugin ではなく、この repo に pin されたファイルが正）。次に ${RULES_FILE} のハウスルールを読み、スキルと衝突する箇所ではハウスルール側を優先すること。その上で ${PDF} を読んで、ハウスルール（数式中心・未定義記号ゼロ・図表・核心概念の具体例補足・末尾 Q&A 枠）に従って html-expl/ に HTML explainer を生成して。

【図の扱い／最重要・検証してから貼る】worker が論文の図・表を既に切り出し済み。\`${FIGDIR}/manifest.json\`（各エントリ: label/num/page/file/clip/caption/low_conf/needs_review/reason）と PNG が置いてある。本文の該当箇所には、原則この manifest の実図を \`<img src=\"figs/${DEST}/<file>\" ...>\`（html-expl/ からの相対パス）で埋め込み、caption も併記せよ。**ただし機械切り出しは外すことがある。貼る前に必ず検証せよ:**
 (a) needs_review=false の crop → そのまま埋め込んでよい。
 (b) needs_review=true の crop（reason 参照）→ **その PNG を画像として実際に開いて、caption と内容が一致するか必ず目視確認**。一致すれば埋め込む。ズレている（本文テキストを掴んでいる／図の一部しか写っていない／右や下が切れている）場合は、貼る前に本当にその clip でよいか考え直し、\`pdftoppm -png -f <page> -l <page> -r 150 ${PDF} <prefix>\` でページを起こして PIL で正しい領域に切り直し→切り直した PNG を再度画像として開いて確認、を満足するまで繰り返す。表が論文中で実体プローズ（本物の表組み画像が無い）なら、誤クロップは捨てて数値を HTML \`<table>\` に再組版せよ。
 図が実在するのに **inline SVG / canvas で論文図を描き直すことは引き続き禁止**。manifest が空（抽出0件）の論文に限り、最終手段として SVG 模式図を許可する。

【セッション id／必須】header の <div class=\"meta\"> 内に、可視テキストとして '${AGENT_LABEL} session: <code>__AGENT_SESSION_ID__</code>' の1行を必ず含めよ。リテラル文字列 __AGENT_SESSION_ID__ をそのまま書くこと（worker が実セッション id に置換する）。タイトル直下に id が見えるようにするのが目的。

【本文の数式デリミタ／必須】本文のブロック数式は \$\$ … \$\$、インラインは \$ … \$ で書く。\\[ … \\] や \\( … \\) は本文で使うな（自動レンダラのデリミタ登録が \$\$/\$ 前提で、\\[…\\] はリテラル表示に化けることがある）。KaTeX の renderMathInElement の delimiters には保険として \$\$・\$・\\[・\\( の4種すべてを登録すること。

【図・表キャプションの数式／必須】<figcaption> 内の数式は必ずインライン数式（MathJax は \\( … \\)、KaTeX は \$ … \$）で書く。\$\$ … \$\$ や \\[ … \\]（中央寄せブロック数式）をキャプション内で使ってはならない。キャプションの数式が1個ずつ中央寄せの独立行になるのを防ぐため。

完了したら終了してよい。"

echo "[readpaper] running $AGENT headless"
if [ -z "$AGENT" ]; then
  echo "[readpaper] ERROR no agent CLI configured or found (looked for: claude, codex)"
  notify "✗ $DEST: no agent CLI (claude/codex) found"
  exit 1
fi
if ! command -v "$AGENT" >/dev/null 2>&1; then
  echo "[readpaper] ERROR $AGENT not on PATH"
  echo "[readpaper]   PATH=$PATH"
  notify "✗ $AGENT not found: $DEST"
  exit 1
fi

if [ "$AGENT" = "codex" ]; then
# ----------------------------------------------------------------- Codex backend
# Every difference below is forced by the CLI, not a preference:
#  * No `--session-id`. `codex exec` cannot be told which id to use, so the id is
#    RECOVERED AFTER the run from the first JSONL event
#    ({"type":"thread.started","thread_id":"..."}). That is sufficient because the id
#    is only consumed by the finalize step further down, which runs after the agent.
#  * `</dev/null` is REQUIRED. Whenever stdin is not a TTY, `codex exec` reads it and
#    appends it to the prompt as a <stdin> block. Redirecting from /dev/null makes that
#    read hit EOF immediately; leaving the detached worker's inherited stdin attached
#    risks blocking, or worse, silently appending junk to the prompt. (The line
#    "Reading additional input from stdin..." is printed either way — it is not a
#    symptom of a problem, so do not chase it.)
#  * `--dangerously-bypass-approvals-and-sandbox` is the counterpart of Claude's
#    `--dangerously-skip-permissions`; without it a headless run blocks forever on an
#    approval request that nobody can answer.
#  * No pre-compact stage: Codex has no headless equivalent of `/compact`.
# READPAPER_CODEX_ARGS is an escape hatch for per-machine model/effort overrides, e.g.
#   READPAPER_CODEX_ARGS='-m gpt-5.6-sol -c model_reasoning_effort=high'
CODEX_JSONL="logs/readpaper-${DEST}-${TS}.codex.jsonl"
# shellcheck disable=SC2086  # READPAPER_CODEX_ARGS is meant to word-split
codex exec --json \
      --cd "$PROJECT_DIR" \
      --skip-git-repo-check \
      --dangerously-bypass-approvals-and-sandbox \
      ${READPAPER_CODEX_ARGS:-} \
      "$PROMPT" </dev/null | tee "$CODEX_JSONL"
echo "[readpaper] codex exit=${PIPESTATUS[0]}"

# Pull the session id, the token usage and the final message out of the JSONL in one
# pass. NOTE: codex also emits {"type":"item.completed","item":{"type":"error",...}} for
# benign plugin-hook warnings (e.g. "clamping SessionEnd hook timeout to 3s"), so error
# events are COUNTED AND LOGGED but never treated as run failure. Success is decided the
# same way as on the claude path: did this run produce a new html (START_EPOCH check)?
CODEX_SUMMARY="$(python3 - "$CODEX_JSONL" <<'PYEOF'
import json, sys
sid, ctx, last_msg, errors = "", 0, "", 0
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        d = json.loads(line)
    except ValueError:
        continue
    t = d.get("type")
    if t == "thread.started" and not sid:
        sid = d.get("thread_id") or ""
    elif t == "turn.completed":
        u = d.get("usage") or {}
        ctx = (u.get("input_tokens", 0) + u.get("cached_input_tokens", 0)
               + u.get("cache_write_input_tokens", 0))
    elif t == "item.completed":
        it = d.get("item") or {}
        if it.get("type") == "agent_message":
            last_msg = it.get("text") or last_msg
        elif it.get("type") == "error":
            errors += 1
print(sid)
print(ctx)
print(errors)
print(" ".join(str(last_msg)[:300].split()))
PYEOF
)"
SID="$(printf '%s' "$CODEX_SUMMARY" | sed -n 1p)"
CTX="$(printf '%s' "$CODEX_SUMMARY" | sed -n 2p)"
CODEX_ERRS="$(printf '%s' "$CODEX_SUMMARY" | sed -n 3p)"
echo "[readpaper] run agent_message(head)=$(printf '%s' "$CODEX_SUMMARY" | sed -n 4p)"
echo "[readpaper] run error_events=${CODEX_ERRS:-0} (non-fatal; see $CODEX_JSONL)"
if [ -z "$SID" ]; then
  echo "[readpaper] WARN could not recover thread_id from $CODEX_JSONL -> session id will be blank"
fi
echo "[readpaper] session_id=$SID  (resume: codex exec resume $SID)"
echo "[readpaper] context tokens ~= ${CTX:-0}"
echo "[readpaper] codex backend: skip pre-compact (no headless /compact equivalent)"

else
# ---------------------------------------------------------------- Claude backend
# Pre-mint the session id so it is known *before* the run and can be recorded.
# Headless (`-p`) sessions are not listed in the interactive `--resume` picker,
# so without this the id would be unrecoverable. Resume later with:
#   cd "$PROJECT_DIR" && claude --resume "$SID"
SID="$(/usr/bin/uuidgen | tr 'A-Z' 'a-z')"
echo "[readpaper] session_id=$SID  (resume: claude --resume $SID)"
# Capture the run as JSON so we can read the final-turn context size from
# `.usage` (input + cache_read + cache_creation ~= the tokens the transcript will
# replay on the next `--resume`). Side note: with --output-format json the model's
# streaming chatter no longer flows to this log, so we dump is_error + a result
# head below for visibility.
RUN_JSON="$(claude -p "$PROMPT" --session-id "$SID" --output-format json --dangerously-skip-permissions)"
echo "[readpaper] claude exit=$?"
printf '%s' "$RUN_JSON" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print('[readpaper] run is_error=%s subtype=%s' % (d.get('is_error'), d.get('subtype')))
    print('[readpaper] run result(head)=%s' % str(d.get('result'))[:300])
except Exception as e:
    print('[readpaper] WARN could not parse run JSON: %s' % e)"

# Pre-compact heavy sessions so a later interactive `claude --resume $SID` opens
# light and is ready for Q&A immediately. opus 4.8's context window is 1M, so a
# 200k session never hits Claude Code's own auto-compact (which fires near the
# window limit) — but resuming 200k is still slow. /compact runs fine headless and
# writes a `compact_boundary` to the transcript that subsequent --resume honors
# (verified empirically; the codeword survived a real compaction). The full HTML
# explainer lives on disk regardless, so Q&A can still Read it after compaction.
CTX="$(printf '%s' "$RUN_JSON" | python3 -c "import json,sys
try:
    u=json.load(sys.stdin).get('usage',{})
    print(u.get('input_tokens',0)+u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0))
except Exception:
    print(0)")"
echo "[readpaper] context tokens ~= ${CTX}"
if [ "${CTX:-0}" -gt 200000 ]; then
  echo "[readpaper] context > 200k -> pre-compacting session $SID"
  if claude --resume "$SID" -p "/compact" --dangerously-skip-permissions </dev/null; then
    echo "[readpaper] compact done"
  else
    echo "[readpaper] WARN compact failed"
  fi
else
  echo "[readpaper] context <= 200k -> skip compact"
fi

fi  # end agent backend dispatch

# Finalize the generated HTML in PYTHON, not bash. The old bash version
# discovered the file via `ls -t html-expl/*.html | grep | head` and substituted
# the session id with sed/awk. Under Chrome's flaky macOS-TCC attribution that
# bash *glob* intermittently returned empty, so NEW_HTML was blank and the
# session-id substitution + line-1 stamp + fallback inject ALL silently skipped
# (each guarded by `[ -n "$NEW_HTML" ]`) — shipping a literal
# `__CLAUDE_SESSION_ID__` placeholder. Python os.scandir keeps Full-Disk-Access
# reliably here (extract_figures.py / build_index.py succeed every run), so the
# discovery + placeholder substitution + fallback inject + line-1 stamp now all
# happen in finalize_html.py. It prints the resolved html path (or "NONE").
echo "[readpaper] finalizing html (session id + stamp; robust python)"
# Resolve finalize_html.py: prefer the deployed copy next to this worker, but
# fall back to the repo copy under PROJECT_DIR. Rationale: if install.sh was not
# re-run after finalize_html.py was added/changed, "$HOST_DIR/finalize_html.py"
# is absent and the placeholder ships literally (happened 2026-07-01). The worker
# already reads/writes PROJECT_DIR reliably (extract_figures/build_index do), so
# the repo copy is a safe fallback that keeps the session-id substitution running
# even when the deploy dir drifts.
FINALIZE="$HOST_DIR/finalize_html.py"
[ -f "$FINALIZE" ] || FINALIZE="$PROJECT_DIR/tools/readpaper/host/finalize_html.py"
if [ ! -f "$FINALIZE" ]; then
  echo "[readpaper] ERROR finalize_html.py not found (deploy or repo) -> session id will not be filled"
fi
# START_EPOCH restricts discovery to html files created/modified by THIS run —
# without it, a run that generated nothing (e.g. claude refused) stamped and
# announced whichever unrelated html happened to be newest.
# The 6th arg (agent) decides the label and the resume command written into the html —
# `claude --resume <id>` and `codex exec resume <id>` are not interchangeable, so the
# file has to say which CLI reopens it.
NEW_HTML="$(python3 "$FINALIZE" "$PROJECT_DIR" "$SID" "$PDF" "$TS" "$START_EPOCH" "$AGENT")"
echo "[readpaper] finalize_html($FINALIZE) -> ${NEW_HTML:-<none>}"
[ "$NEW_HTML" = "NONE" ] && NEW_HTML=""
# Last-resort guard: if a resolved html still carries a literal placeholder, fill it
# in-place here (python, FDA-stable) so a placeholder can never ship. Both the current
# token and the legacy one are handled — a stale prompt or house-rules file may still
# say __CLAUDE_SESSION_ID__.
if [ -n "$NEW_HTML" ] && [ -f "$NEW_HTML" ]; then
  python3 - "$NEW_HTML" "$SID" <<'PYEOF'
import sys
p, sid = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
hit = next((t for t in ("__AGENT_SESSION_ID__", "__CLAUDE_SESSION_ID__") if t in s), None)
if hit:
    open(p, "w", encoding="utf-8").write(s.replace(hit, sid))
    print("[readpaper] WARN placeholder %s still present -> filled in-place" % hit)
PYEOF
fi

echo "[readpaper] rebuilding index"
python3 build_index.py || echo "[readpaper] WARN build_index.py failed"

# NOTE: the session id is recorded above in THIS per-job log ("session_id=...").
# We deliberately do NOT append to a shared logs/readpaper-sessions.txt here:
# a Chrome-spawned worker cannot modify a shared file once another app (e.g.
# Numbers, if the .tsv is double-clicked) has tagged it via macOS App
# Management → EPERM. The consolidated ledger is instead rebuilt on demand from
# these per-job logs by tools/readpaper/rebuild_sessions.py (run interactively).
if [ -n "$NEW_HTML" ] && [ -f "$NEW_HTML" ]; then
  # Title for the notification: the finalized html's <title> (read by path in
  # python — a known-path read survives TCC better than a bash glob).
  TITLE="$DEST"
  T="$(python3 -c "import re,sys; m=re.search(r'<title>([^<]*)</title>', open(sys.argv[1],encoding='utf-8').read()); print(m.group(1) if m else '')" "$NEW_HTML" 2>/dev/null)"
  [ -n "$T" ] && TITLE="$T"
  echo "[readpaper] done -> $NEW_HTML  session=$SID"
  notify "✓ $TITLE"
else
  # Claude ran but no html newer than START_EPOCH exists — the run produced
  # nothing. Say so instead of faking success with someone else's title.
  echo "[readpaper] ERROR no new html produced by this run (see result head above)  session=$SID"
  notify "✗ $DEST: no html generated (see $LOG)"
fi
