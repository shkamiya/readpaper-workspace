#!/usr/bin/env python3
"""html-expl/ 内の解説 HTML を走査して、モバイル最適化の index.html を生成する。

各 .html（index.html 自身は除く）から <title> を読み、ファイル名先頭の
YYYYMMDD を日付として、新しい順に並べたカード一覧を出力する。
新しい解説を追加したら `python3 build_index.py` を再実行するだけでよい。
"""
from __future__ import annotations

import html
import re
from pathlib import Path

HTML_DIR = Path(__file__).resolve().parent / "html-expl"
OUT = HTML_DIR / "index.html"

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-")


def extract_title(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = TITLE_RE.search(text)
    if not m:
        return path.stem
    # <title> 内は HTML エンティティ（&amp; 等）なので unescape して素のテキストに
    return html.unescape(m.group(1).strip())


def parse_date(name: str) -> str:
    m = DATE_RE.match(name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def main() -> None:
    entries = []
    for p in sorted(HTML_DIR.glob("*.html")):
        if p.name == "index.html":
            continue
        entries.append((parse_date(p.name), p.name, extract_title(p)))
    # 日付の新しい順（同日はファイル名降順）
    entries.sort(key=lambda e: (e[0], e[1]), reverse=True)

    cards = "\n".join(
        f"""      <a class="card" href="{html.escape(name)}">
        <span class="date">{date or '—'}</span>
        <span class="title">{html.escape(title)}</span>
      </a>"""
        for date, name, title in entries
    )

    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>論文解説 一覧</title>
<link rel="apple-touch-icon" href="icons/icon-helv.png">
<meta name="apple-mobile-web-app-title" content="Daily Papers">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: max(env(safe-area-inset-top), 16px) 16px 40px;
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
    background: #0f1115; color: #e6e8ee; line-height: 1.5;
  }}
  header {{ max-width: 760px; margin: 0 auto 20px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .sub {{ color: #8b93a7; font-size: .85rem; }}
  .list {{ max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 10px; }}
  .card {{
    display: flex; flex-direction: column; gap: 6px;
    padding: 16px 18px; border-radius: 14px; text-decoration: none;
    background: #181b22; border: 1px solid #262b36; color: inherit;
    -webkit-tap-highlight-color: transparent; transition: background .15s, border-color .15s;
  }}
  .card:active {{ background: #1f242e; border-color: #3a4255; }}
  .date {{
    font-size: .72rem; letter-spacing: .04em; color: #6f9bff;
    font-variant-numeric: tabular-nums;
  }}
  .title {{ font-size: 1.02rem; font-weight: 600; }}
  footer {{ max-width: 760px; margin: 28px auto 0; color: #59617a; font-size: .75rem; }}
</style>
</head>
<body>
  <header>
    <h1>論文解説 一覧</h1>
    <div class="sub">{len(entries)} 本 · 新しい順</div>
  </header>
  <main class="list">
{cards}
  </main>
  <footer>Tailscale 経由のローカル配信 · build_index.py で再生成</footer>
</body>
</html>
"""
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
