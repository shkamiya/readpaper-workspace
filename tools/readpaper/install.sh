#!/bin/bash
# One-time setup for the readpaper extension.
#
#   tools/readpaper/install.sh <chrome-extension-id>
#
# Chrome-spawned native messaging hosts cannot reliably exec a script located
# under TCC-protected folders (~/Desktop, ~/Documents, ~/Downloads): the launch
# fails before Python even runs ("Native host has exited", no host log). So we
# DEPLOY a copy of the host outside those folders, into
#   ~/Library/Application Support/readpaper/
# (Chrome already reads its own manifests from ~/Library — not TCC-protected),
# and bake the absolute project path into the deployed worker.
#
# The worker still writes into the repo (papers/, html-expl/) on the Desktop; if
# that write is blocked, grant "Google Chrome" Full Disk Access and re-test.
set -euo pipefail

EXT_ID="${1:-}"
if [ -z "$EXT_ID" ]; then
  echo "usage: $0 <chrome-extension-id>"
  echo "  (load tools/readpaper/extension/ as an unpacked extension first, then"
  echo "   copy its ID from chrome://extensions)"
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # tools/readpaper
PROJECT_DIR="$(cd "$ROOT/../.." && pwd)"                       # repo root
SRC_HOST="$ROOT/host"

DEPLOY="$HOME/Library/Application Support/readpaper"
mkdir -p "$DEPLOY"

cp "$SRC_HOST/readpaper_host.py" "$DEPLOY/readpaper_host.py"
cp "$SRC_HOST/readpaper_run.sh"  "$DEPLOY/readpaper_run.sh"
# Finalize step: the worker calls it as "$HOST_DIR/finalize_html.py" to fill the
# session-id placeholder + stamp the HTML. It MUST be deployed next to the worker
# — when this cp was missing the deployed worker hit "No such file or directory"
# and every run silently shipped a literal __CLAUDE_SESSION_ID__ (2026-07-01).
cp "$SRC_HOST/finalize_html.py"  "$DEPLOY/finalize_html.py"
# Figure extractor: the worker calls it as "$HOST_DIR/extract_figures.py", so it
# must sit next to the deployed worker. It runs the poppler/PIL crop pipeline in
# the worker's (FDA-stable) context before Claude starts.
cp "$ROOT/extract_figures.py"    "$DEPLOY/extract_figures.py"

# Bake the absolute repo root into the deployed worker (replaces the placeholder).
/usr/bin/sed -i '' "s|__READPAPER_PROJECT_DIR__|$PROJECT_DIR|g" "$DEPLOY/readpaper_run.sh"

# ---------------------------------------------------------------- agent + PATH
# Chrome spawns the native host with a bare environment, so the worker cannot rely on
# the login shell's PATH — it has to carry an absolute one. That PATH used to be
# hard-coded to SK's machine (a specific nvm node version + pyenv shims), which is
# exactly what made the tool non-portable. Resolve it HERE, on the installing machine,
# and bake the result in.
AGENT_BIN="$(command -v claude || true)"
AGENT_NAME="claude"
if [ -z "$AGENT_BIN" ]; then
  AGENT_BIN="$(command -v codex || true)"
  AGENT_NAME="codex"
fi
if [ -z "$AGENT_BIN" ]; then
  echo "ERROR: neither 'claude' nor 'codex' is on PATH." >&2
  echo "  Install one of them first (and log in), then re-run this script:" >&2
  echo "    Codex CLI  : https://developers.openai.com/codex/cli  then 'codex login'" >&2
  echo "    Claude Code: https://claude.com/claude-code" >&2
  echo "  Baking a broken PATH here would fail later, inside a detached Chrome-spawned" >&2
  echo "  worker, where the only evidence is a line in logs/readpaper-*.log." >&2
  exit 1
fi

PY_BIN="$(command -v python3 || true)"
if [ -z "$PY_BIN" ]; then
  echo "ERROR: python3 is not on PATH (needed for figure extraction and finalize)." >&2
  exit 1
fi

BREW_PREFIX="$(brew --prefix 2>/dev/null || echo /opt/homebrew)"
# ORDER MATTERS: python3's directory goes FIRST. The dependency check below verifies
# pillow/numpy against exactly $PY_BIN, so $PY_BIN must be the python3 the worker
# actually runs. Putting the agent's directory first breaks that whenever the agent
# lives somewhere that also ships a python3 — e.g. codex in /opt/homebrew/bin shadows
# a pyenv python3 that has pillow, and figure extraction then dies on `import PIL`
# while install.sh reported everything fine (measured 2026-08-14).
BAKED_PATH="$(dirname "$PY_BIN"):$(dirname "$AGENT_BIN"):$BREW_PREFIX/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
/usr/bin/sed -i '' "s|__READPAPER_PATH__|$BAKED_PATH|g" "$DEPLOY/readpaper_run.sh"

# The worker sources this to learn which backend to drive. Kept as a separate file (not
# baked) so the backend can be switched without re-running install.sh.
cat > "$DEPLOY/config.env" <<EOF
# Written by tools/readpaper/install.sh on $(date +%Y-%m-%d).
# Which agent CLI writes the explainer: claude | codex
# The environment variable READPAPER_AGENT overrides this.
READPAPER_AGENT=$AGENT_NAME
EOF

# Warn about the runtime dependencies the worker cannot supply for itself. These are
# checked but NOT fatal: the host/extension wiring is still worth installing, and the
# messages tell the user exactly what to add.
MISSING=""
command -v pdftotext >/dev/null 2>&1 || MISSING="$MISSING pdftotext"
command -v pdftoppm  >/dev/null 2>&1 || MISSING="$MISSING pdftoppm"
"$PY_BIN" -c "import PIL" 2>/dev/null || MISSING="$MISSING python:pillow"
"$PY_BIN" -c "import numpy" 2>/dev/null || MISSING="$MISSING python:numpy"

chmod +x "$DEPLOY/readpaper_host.py" "$DEPLOY/readpaper_run.sh" "$DEPLOY/finalize_html.py" "$DEPLOY/extract_figures.py"

TARGET_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
mkdir -p "$TARGET_DIR"
MANIFEST="$TARGET_DIR/com.sk.readpaper.json"
cat > "$MANIFEST" <<EOF
{
  "name": "com.sk.readpaper",
  "description": "readpaper one-click host",
  "path": "$DEPLOY/readpaper_host.py",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXT_ID/"]
}
EOF

echo "deployed host : $DEPLOY/readpaper_host.py"
echo "deployed work : $DEPLOY/readpaper_run.sh  (PROJECT_DIR=$PROJECT_DIR)"
echo "agent backend : $AGENT_NAME  ($AGENT_BIN)"
echo "baked PATH    : $BAKED_PATH"
echo "manifest      : $MANIFEST"
echo "allowed_origin: chrome-extension://$EXT_ID/"
echo

if [ -n "$MISSING" ]; then
  echo "WARNING: missing runtime dependencies:$MISSING"
  echo "  Figure extraction will fail (explainers fall back to hand-drawn SVG) until fixed:"
  echo "    brew install poppler"
  echo "    $PY_BIN -m pip install --user pillow numpy"
  echo
fi

echo "No Chrome restart needed (the manifest is re-read per connection)."
echo "Click the toolbar button on an arxiv/PDF page to test."
