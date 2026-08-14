# Vendored skills

This directory holds a **pinned, unmodified copy** of a third-party agent skill that the
readpaper ("DP") pipeline depends on. The worker prompt in
`tools/readpaper/host/readpaper_run.sh` tells the agent to read
`skills/explain-via-html/SKILL.md` by path, so the pipeline works on any machine without
installing a plugin marketplace, and produces identical output under both Claude Code and
Codex.

## `explain-via-html/`

| | |
|---|---|
| Upstream | https://github.com/mathbullet/skills |
| Upstream path | `plugins/explain-via-html/` |
| Pinned commit | `336ff3acaa14a408a338adce8ab969311a292a80` (2026-06-08) |
| Version | 1.0.0 |
| Retrieved | 2026-08-14 |
| `SKILL.md` sha256 | `2804d7f31bdcff445f48e9bd173405ddacf9c554578d74ac1e11d4658e55894c` |
| License | MIT — Copyright (c) 2026 mathbullet (see `explain-via-html/LICENSE`) |
| Modified? | **No.** Byte-for-byte identical to upstream at the pinned commit. |

### Why this is pinned rather than installed from the marketplace

Upstream **deleted this skill on 2026-08-01** in commit `5dc38549`
("feat: replace explain-via-html with html skill", PR #7). It was replaced by a differently
named and substantially different skill, `html`. A fresh
`mathbullet/skills` marketplace install today cannot obtain `explain-via-html` at all.

The replacement is not a drop-in. Among the incompatibilities:

- `html` mandates MathJax 3 and forbids the page from redefining `window.MathJax`; the
  project house rules (`CLAUDE.md` §1) require KaTeX with all four delimiter forms
  registered.
- `html` ships an external `design-system/document.css` + `math-copy.js` alongside the
  page; `explain-via-html` requires a single self-contained `.html`.
- Different palette (`#FAF9F6` / blue `#2990DA` / red `#D63A2F` vs. white / black /
  cyan `#00A4AF`), different term-list markup, and a `.mb-*` class system.

Adopting `html` would break visual and structural consistency with the ~100 explainers
already in `html-expl/`. Pinning keeps every machine on the exact skill those files were
written against.

### Updating

Do not edit `explain-via-html/SKILL.md` in place — project-specific rules belong in
`CLAUDE.md` / `AGENTS.md`, which take precedence over the skill on conflict. If you ever
intend to move to upstream's `html` skill, that is a deliberate migration: the house rules
and the existing explainers have to be reconciled with it first.
