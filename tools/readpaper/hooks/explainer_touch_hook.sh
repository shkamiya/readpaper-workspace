#!/bin/bash
# Claude Code PostToolUse hook (matcher: Write|Edit|MultiEdit) for this repo.
#
# Records every html-expl/*.html file the CURRENT session writes into a per-session
# queue under $TMPDIR. The Stop hook (explainer_stop_hook.sh) then stamps exactly
# those files with this session's id — and nothing else.
#
# WHY THE QUEUE EXISTS
# --------------------
# The first cut of the Stop hook simply scanned html-expl/ for any file still holding
# the literal __AGENT_SESSION_ID__ placeholder. That is wrong whenever a headless
# worker run is in flight: its output also sits there with the placeholder still in it
# (the worker substitutes only after its agent exits), so the interactive session
# happily stamped four of the worker's explainers with ITS own id. Caught during
# testing on 2026-08-16 — the same "whose file is this?" confusion that made
# finalize_html.py mis-resolve concurrent runs in the first place.
#
# The work is in record_touched.py rather than an inline heredoc, because
# `python3 - <<'PY'` feeds the *heredoc* to the interpreter as stdin, leaving no stdin
# for the hook payload — the queue silently stayed empty the first time round.
#
# Always exits 0; a hook must not be able to fail a turn.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$DIR/record_touched.py" ] || exit 0
python3 "$DIR/record_touched.py" || true
exit 0
