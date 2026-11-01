#!/bin/bash
# Claude Code Stop hook for this repo: finish any explainer written during the turn.
#
# Registered in .claude/settings.json, together with the PostToolUse hook
# explainer_touch_hook.sh that feeds it. It does two things, in order:
#
#   1. finalize_html.py --hook   fill the literal __AGENT_SESSION_ID__ placeholder with
#                                THIS session's real transcript uuid.
#   2. check_html.py --fix       repair the mechanical math defects on those files and
#                                report what is left.
#
# WHY STEP 1 CANNOT BE DONE BY THE MODEL
# --------------------------------------
# The house rules used to tell an interactive session to write its own session id
# straight into the html. That is impossible to do correctly: Claude Code does not
# expose the transcript uuid (the name of ~/.claude/projects/<proj>/<uuid>.jsonl) to
# the model, and the `session_…` string in the system prompt is a claude.ai-side
# identifier that `claude --resume` does not accept. So the model either invented a
# plausible uuid or left the line out — 15 of the 51 interactively-generated
# explainers carry a uuid that exists in no log and no transcript, and one has no id
# at all (audited 2026-08-16).
#
# A hook, unlike the model, is *handed* the real `session_id` on stdin. So the author
# now always writes the placeholder — headless runs get it substituted by the worker,
# interactive ones by this hook — and nobody has to guess.
#
# WHY IT WORKS OFF A QUEUE AND NOT A DIRECTORY SCAN
# -------------------------------------------------
# A concurrent headless worker's output also sits in html-expl/ with the placeholder
# still in it, so "every file that still has a placeholder" is not "the files this
# session wrote". Scanning stamped four of the worker's explainers with the
# interactive session's id during testing. explainer_touch_hook.sh records the actual
# Write/Edit targets instead, and this hook consumes that list.
#
# Always exits 0; a hook must not be able to fail a turn.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # tools/readpaper/hooks
HOST="$DIR/../host"
PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$DIR/../../.." && pwd)}"

command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$HOST/finalize_html.py" ] || exit 0

PAYLOAD="$(cat)"
SID="$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id") or "")
except Exception: print("")')"
[ -n "$SID" ] || exit 0

QUEUE="${TMPDIR:-/tmp}/readpaper-expl-queue-${SID}.txt"
[ -s "$QUEUE" ] || exit 0

FILES="$(sort -u "$QUEUE")"
: > "$QUEUE"

# finalize prints one stamped path per line on stdout and its narration on stderr.
# Capture stdout (step 2 works off that list) and replay stderr afterwards.
ERRLOG="$(mktemp -t readpaper-hook)"
# shellcheck disable=SC2086  # explainer filenames never contain spaces
STAMPED="$(printf '%s' "$PAYLOAD" \
           | python3 "$HOST/finalize_html.py" --hook "$PROJECT" $FILES 2>"$ERRLOG")"
cat "$ERRLOG" >&2
rm -f "$ERRLOG"

if [ -f "$HOST/check_html.py" ]; then
  # Check every file the session touched, not just the ones that needed a session id:
  # a page the model edited to fix a formula still deserves the math check.
  printf '%s\n' "$FILES" | while IFS= read -r f; do
    [ -n "$f" ] && [ -f "$f" ] && python3 "$HOST/check_html.py" --fix "$f" >&2
  done
fi
: "${STAMPED:=}"
exit 0
