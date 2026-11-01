#!/usr/bin/env python3
"""html-expl/ 内の解説 HTML を走査して、モバイル最適化の index.html を生成する。

各 .html（index.html 自身は除く）から <title> を読み、ファイル名先頭の
YYYYMMDD を日付として、新しい順に並べたカード一覧を出力する。
新しい解説を追加したら `python3 build_index.py` を再実行するだけでよい。

SK の読書マーク（html-expl/meta/_all.json, tools/readpaper/host/meta_store.py）は
ビルド時に各カードへ焼き込み、さらにページ側の JS が同じファイルを読み直して
上書きする。記事でマークを付けて一覧に戻ったとき、再ビルド無しで反映させるため。
集計（未読 N 本 等）は意図的に出さない。

しおり（html-expl/pos/_all.json, pos_store.py）も同じ二重描画で、読み中のカードに
「続き 63%」の細い進捗バーを出す。読了のカードには出さない（もう続きは無い）。

一番上の検索窓は、タイトル・著者・所属・掲載先（arXiv id / 会議名）で絞り込む。
著者等は各解説ヘッダの class="meta" / "kicker" 等のブロックから抜き出して
カードの data-info に焼き込み、検索中だけカードに表示する。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML_DIR = ROOT / "html-expl"
OUT = HTML_DIR / "index.html"

sys.path.insert(0, str(ROOT / "tools" / "readpaper" / "host"))
import meta_store  # noqa: E402
import pos_store  # noqa: E402

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-")
# Author / affiliation / venue lines in an explainer header. Layouts vary (generated
# over months by different prompts) but all of them put these in a block with one
# of these classes; nested tags make the non-greedy close imperfect, which is fine
# for a search blob.
INFO_RE = re.compile(r'<(div|p|span)\b[^>]*class="[^"]*\b(?:meta|kicker|authors?|venue|byline)\b[^"]*"[^>]*>'
                     r"(.*?)</\1>", re.IGNORECASE | re.DOTALL)
# Lines in those blocks that are bookkeeping, not paper metadata.
INFO_DROP_RE = re.compile(r"(claude-code session|codex session|ローカル ?(PDF|ファイル)|resume:|explainer 作成日).*$",
                          re.IGNORECASE)


def extract_title(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = TITLE_RE.search(text)
    if not m:
        return path.stem
    # <title> 内は HTML エンティティ（&amp; 等）なので unescape して素のテキストに
    return html.unescape(m.group(1).strip())


def extract_info(path: Path) -> str:
    """Authors, affiliations and venue from the explainer's header, one line,
    parts joined by " · ". Scoped to <body>..</header> (or the first 20k chars)
    so a body that cites other papers does not leak their authors in."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    i = max(text.find("<body"), 0)
    head = text[i:i + 20000]
    j = head.find("</header>")
    if j >= 0:
        head = head[:j]
    parts = []
    for m in INFO_RE.finditer(head):
        frag = re.sub(r"<br\s*/?>", "\n", m.group(2), flags=re.IGNORECASE)
        for line in html.unescape(re.sub(r"<[^>]+>", "", frag)).split("\n"):
            line = INFO_DROP_RE.sub("", re.sub(r"\s+", " ", line)).strip(" ・·／/|（(")
            if line:
                parts.append(line)
    return " · ".join(parts)


def parse_date(name: str) -> str:
    m = DATE_RE.match(name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def mark_html(m: dict | None) -> str:
    """The per-card mark row, as baked at build time. The page JS regenerates the
    same markup from _all.json (keep render() below in sync)."""
    if not m:
        return ""
    parts = []
    st = m.get("status")
    if st in meta_store.STATUS_LABEL:
        parts.append(f'<span class="st {st}">{meta_store.STATUS_LABEL[st]}</span>')
    lv = m.get("level")
    if lv in meta_store.LEVEL_LABEL:
        n = list(meta_store.LEVELS).index(lv) + 1
        bars = "".join(f'<i{" class=f" if i < n else ""}></i>' for i in range(3))
        parts.append(f'<span class="lv"><span class="bars">{bars}</span>{meta_store.LEVEL_LABEL[lv]}</span>')
    note = (m.get("note") or "").strip().split("\n", 1)[0]
    if note:
        parts.append(f'<span class="note">{html.escape(note)}</span>')
    return f'<span class="mark">{"".join(parts)}</span>' if parts else ""


def prog_html(m: dict | None, p: dict | None) -> str:
    """The per-card しおり row (bar + percent). Same rule in render() below: only
    while the paper is not 読了 and the bookmark is past the very top."""
    if not p or (m or {}).get("status") == "done":
        return ""
    try:
        pct = int(round(float(p.get("ratio") or 0) * 100))
    except (TypeError, ValueError):
        return ""
    if pct < 2:
        return ""
    return (f'<span class="prog"><span class="bar"><i style="width:{pct}%"></i></span>'
            f'<span class="pct">続き {pct}%</span></span>')


def main() -> None:
    marks = meta_store.load_all(str(HTML_DIR))
    poss = pos_store.load_all(str(HTML_DIR))
    entries = []
    for p in sorted(HTML_DIR.glob("*.html")):
        if p.name == "index.html":
            continue
        entries.append((parse_date(p.name), p.name, extract_title(p), extract_info(p)))
    # 日付の新しい順（同日はファイル名降順）
    entries.sort(key=lambda e: (e[0], e[1]), reverse=True)

    def card(date: str, name: str, title: str, info: str) -> str:
        stem = name[:-5]
        m = marks.get(stem) or {}
        p = poss.get(stem)
        status = m.get("status") or ""
        return (f'      <a class="card" href="{html.escape(name)}" data-stem="{html.escape(stem)}"'
                f' data-status="{html.escape(status)}">\n'
                f'        <span class="date">{date or "—"}</span>\n'
                f'        <span class="title">{html.escape(title)}</span>\n'
                f'        <span class="info">{html.escape(info)}</span>\n'
                f'        {mark_html(m)}\n'
                f'        {prog_html(m, p)}\n'
                f'      </a>')

    cards = "\n".join(card(*e) for e in entries)
    labels_json = json.dumps({"status": meta_store.STATUS_LABEL, "level": meta_store.LEVEL_LABEL,
                              "levels": list(meta_store.LEVELS)}, ensure_ascii=False)

    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>論文解説 一覧</title>
<link rel="apple-touch-icon" href="icons/icon-helv.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="apple-mobile-web-app-title" content="Daily Papers">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Zen+Kurenaido&display=swap">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: max(env(safe-area-inset-top), 16px) 16px 40px;
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
    background: #0f1115; color: #e6e8ee; line-height: 1.5;
  }}
  header {{ max-width: 760px; margin: 0 auto 20px; display: flex; flex-wrap: wrap;
            align-items: flex-end; gap: 8px 16px; }}
  header .ttl {{ flex: 1; min-width: 200px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .sub {{ color: #8b93a7; font-size: .85rem; }}
  .seg {{ display: inline-flex; border: 1px solid #2c3240; border-radius: 999px; overflow: hidden; }}
  .seg button {{
    font: inherit; font-size: .8rem; padding: 6px 13px; border: 0; cursor: pointer;
    background: transparent; color: #8b93a7; -webkit-tap-highlight-color: transparent;
  }}
  .seg button.on {{ background: #232936; color: #e6e8ee; font-weight: 600; }}
  @media (max-width: 560px) {{
    header {{ display: block; }}
    .seg {{ margin-top: 10px; }}
  }}
  .list {{ max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 10px; }}
  .card {{
    display: flex; flex-direction: column; gap: 6px;
    padding: 16px 18px; border-radius: 14px; text-decoration: none;
    background: #181b22; border: 1px solid #262b36; color: inherit;
    -webkit-tap-highlight-color: transparent; transition: background .15s, border-color .15s;
  }}
  .card:active {{ background: #1f242e; border-color: #3a4255; }}
  .card[hidden] {{ display: none; }}
  .date {{
    font-size: .72rem; letter-spacing: .04em; color: #6f9bff;
    font-variant-numeric: tabular-nums;
  }}
  .title {{ font-size: 1.02rem; font-weight: 600; }}
  .mark {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; margin-top: 2px; min-width: 0; }}
  .mark:empty {{ display: none; }}
  .st {{ font-size: .72rem; padding: 2px 9px; border-radius: 999px; border: 1px solid; line-height: 1.5; }}
  .st.reading {{ color: #f0b84a; border-color: #8a6a24; background: rgba(240,184,74,.12); }}
  .st.done {{ color: #4fd08a; border-color: #2b6e49; background: rgba(79,208,138,.12); }}
  .lv {{ display: inline-flex; align-items: center; gap: 6px; font-size: .74rem; color: #a5adc0; }}
  .bars {{ display: inline-flex; gap: 2px; }}
  .bars i {{ display: block; width: 4px; height: 10px; border-radius: 1px; background: #6f9bff; opacity: .22; }}
  .bars i.f {{ opacity: .95; }}
  .note {{
    flex-basis: 100%; min-width: 0; max-width: 100%;
    font-family: "Zen Kurenaido", "Klee One", cursive; font-size: .98rem;
    color: #c9cfdc; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .prog {{ display: flex; align-items: center; gap: 10px; margin-top: 2px; }}
  .prog:empty {{ display: none; }}
  .bar {{ flex: 1; height: 3px; border-radius: 2px; background: #262b36; overflow: hidden; }}
  .bar i {{ display: block; height: 100%; background: #6f9bff; opacity: .8; }}
  .pct {{ font-size: .72rem; color: #8b93a7; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  footer {{ max-width: 760px; margin: 28px auto 0; color: #59617a; font-size: .75rem; }}
  footer a {{ color: #6f9bff; }}
  /* 検索窓: title / 著者 / 所属 / 掲載先 で絞り込む。状態フィルタとは AND。 */
  .search {{ max-width: 760px; margin: 0 auto 14px; position: relative; }}
  .search input {{
    width: 100%; font: inherit; font-size: 16px; padding: 10px 14px 10px 38px;
    border-radius: 12px; border: 1px solid #262b36; background: #181b22; color: #e6e8ee;
    -webkit-appearance: none; appearance: none;
  }}
  .search input:focus {{ outline: none; border-color: #3a4f80; }}
  .search input::placeholder {{ color: #59617a; }}
  .search svg {{ position: absolute; left: 13px; top: 12px; width: 16px; height: 16px;
                 stroke: #59617a; fill: none; stroke-width: 2; pointer-events: none; }}
  .search .msg {{ font-size: .8rem; margin: 6px 4px 0; min-height: 1.2em; color: #8b93a7; }}
  /* Authors / venue: hidden in the plain list (keeps it compact), shown while a
     query is active so you can see why a card matched. */
  .info {{ font-size: .78rem; color: #8b93a7; line-height: 1.45;
           overflow: hidden; display: none; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
  body.searching .info:not(:empty) {{ display: -webkit-box; }}
  /* iPad landscape / desktop: two columns of cards, wider column. */
  @media (min-width: 900px) {{
    header, .search, .list, footer {{ max-width: 1100px; }}
    /* minmax(0,1fr), not 1fr: a card with a long unbreakable title would otherwise
       widen its column (1fr = minmax(auto,1fr)); measured 720/260 split at 1024px. */
    .list {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; gap: 12px; }}
  }}
</style>
</head>
<body>
  <header>
    <div class="ttl">
      <h1>論文解説 一覧</h1>
      <div class="sub">{len(entries)} 本 · 新しい順</div>
    </div>
    <div class="seg" role="group" aria-label="絞り込み">
      <button data-f="" class="on">すべて</button>
      <button data-f="reading">読み中</button>
      <button data-f="done">読了</button>
    </div>
  </header>
  <section class="search">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input type="search" id="q" placeholder="タイトル・著者・会議 / arXiv id で検索" aria-label="論文を検索"
           autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="search">
    <div class="msg" id="qMsg"></div>
  </section>
  <main class="list">
{cards}
  </main>
  <footer>Tailscale 経由のローカル配信 · build_index.py で再生成 ·
    <a href="/go">iPad / iPhone からポチッと読む設定</a></footer>
<script>
(function () {{
  var L = {labels_json};
  var KEY = "readpaper-index:filter";
  var cards = document.querySelectorAll(".card[data-stem]");

  function esc(s) {{ var d = document.createElement("span"); d.textContent = s; return d.innerHTML; }}
  function render(m) {{
    if (!m) return "";
    var p = [];
    if (L.status[m.status]) p.push('<span class="st ' + m.status + '">' + L.status[m.status] + '</span>');
    if (L.level[m.level]) {{
      var n = L.levels.indexOf(m.level) + 1, b = "";
      for (var i = 0; i < 3; i++) b += "<i" + (i < n ? ' class="f"' : "") + "></i>";
      p.push('<span class="lv"><span class="bars">' + b + "</span>" + L.level[m.level] + "</span>");
    }}
    var note = ((m.note || "").trim().split("\\n")[0]);
    if (note) p.push('<span class="note">' + esc(note) + "</span>");
    return p.length ? '<span class="mark">' + p.join("") + "</span>" : "";
  }}
  function renderProg(m, p) {{
    if (!p || (m && m.status === "done")) return "";
    var pct = Math.round((p.ratio || 0) * 100);
    if (pct < 2) return "";
    return '<span class="prog"><span class="bar"><i style="width:' + pct + '%"></i></span>' +
           '<span class="pct">続き ' + pct + "%</span></span>";
  }}
  var marksNow = null, possNow = null;
  function repaint() {{
    for (var i = 0; i < cards.length; i++) {{
      var c = cards[i], stem = c.dataset.stem;
      var m = marksNow ? marksNow[stem] : null, p = possNow ? possNow[stem] : null;
      if (marksNow) {{
        var old = c.querySelector(".mark");
        if (old) old.remove();
        c.dataset.status = (m && m.status) || "";
        var h = render(m);
        if (h) c.insertAdjacentHTML("beforeend", h);
      }}
      var oldp = c.querySelector(".prog");
      if (possNow) {{
        if (oldp) oldp.remove();
        var hp = renderProg({{ status: c.dataset.status }}, p);
        if (hp) c.insertAdjacentHTML("beforeend", hp);
      }} else if (oldp && c.dataset.status === "done") {{
        oldp.remove();               // marked 読了 since the build: the baked bar is stale
      }}
    }}
    applyFilter();
  }}
  function applyMarks(items) {{ marksNow = items; repaint(); }}
  function applyPos(items) {{ possNow = items; repaint(); }}
  var filter = "";
  try {{ filter = localStorage.getItem(KEY) || ""; }} catch (e) {{}}
  // index.html#done / #reading: a deep link (e.g. an iPhone home-screen icon)
  // beats the remembered choice.
  var h = location.hash.replace("#", "");
  if (h === "reading" || h === "done" || h === "all") filter = h === "all" ? "" : h;
  var segs = document.querySelectorAll(".seg button");

  // Search: every whitespace-separated term must appear somewhere in title +
  // authors/venue + file name. NFKC + accent folding so "sukenik" finds "Súkeník"
  // and full-width input works.
  var q = document.getElementById("q"), qMsg = document.getElementById("qMsg"), terms = [];
  function norm(s) {{
    s = (s || "").normalize("NFKC").toLowerCase();
    return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }}
  var hay = [];
  for (var c = 0; c < cards.length; c++) {{
    var cd = cards[c];
    hay.push(norm(cd.querySelector(".title").textContent + " " +
                  cd.querySelector(".info").textContent + " " + cd.dataset.stem));
    cd.dataset.i = c;
  }}
  function matches(card) {{
    var h = hay[+card.dataset.i];
    for (var t = 0; t < terms.length; t++) if (h.indexOf(terms[t]) < 0) return false;
    return true;
  }}
  q.addEventListener("input", function () {{
    terms = norm(q.value).split(/\s+/).filter(Boolean);
    applyFilter();
  }});
  q.addEventListener("keydown", function (e) {{
    if (e.key === "Escape") {{ q.value = ""; terms = []; applyFilter(); q.blur(); }}
  }});
  document.addEventListener("keydown", function (e) {{
    if (e.key === "/" && document.activeElement !== q) {{ e.preventDefault(); q.focus(); }}
  }});
  function applyFilter() {{
    for (var i = 0; i < segs.length; i++) segs[i].classList.toggle("on", segs[i].dataset.f === filter);
    var n = 0;
    for (var j = 0; j < cards.length; j++) {{
      var hit = !(filter && cards[j].dataset.status !== filter) && matches(cards[j]);
      cards[j].hidden = !hit;
      if (hit) n++;
    }}
    document.body.classList.toggle("searching", terms.length > 0);
    qMsg.textContent = terms.length ? (n ? n + " 件" : "該当なし") : "";
  }}
  for (var k = 0; k < segs.length; k++) segs[k].addEventListener("click", function () {{
    filter = this.dataset.f;
    try {{ localStorage.setItem(KEY, filter); }} catch (e) {{}}
    applyFilter();
  }});
  applyFilter();
  // Re-read the marks on every visit: a status set inside an explainer must show
  // up here on the way back without a rebuild. Missing file (fresh checkout,
  // nothing marked yet) is not an error.
  fetch("meta/_all.json", {{ cache: "no-store" }})
    .then(function (r) {{ return r.ok ? r.json() : null; }})
    .then(function (d) {{ if (d && d.items) applyMarks(d.items); }})
    .catch(function () {{}});
  fetch("pos/_all.json", {{ cache: "no-store" }})
    .then(function (r) {{ return r.ok ? r.json() : null; }})
    .then(function (d) {{ if (d && d.items) applyPos(d.items); }})
    .catch(function () {{}});
  window.addEventListener("pageshow", function (e) {{
    if (e.persisted) location.reload();   // bfcache restore after marking in an explainer
  }});

}})();
</script>
</body>
</html>
"""
    fd, pending = tempfile.mkstemp(prefix=".index-", suffix=".tmp", dir=HTML_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(page)
        os.chmod(pending, 0o644)
        os.replace(pending, OUT)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    print(f"wrote {OUT} ({len(entries)} entries)")

    # Web app manifest, so "ホーム画面に追加" from Chrome for iPad/iPhone (WebKit, but it
    # reads the manifest, not only Safari's apple-* meta tags) also opens standalone.
    # Regenerated with the index so a fresh checkout has it.
    manifest = {
        "name": "Daily Papers",
        "short_name": "Daily Papers",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0f1115",
        "theme_color": "#0f1115",
        "icons": [{"src": "icons/icon-helv.png", "sizes": "512x512", "type": "image/png"}],
    }
    (HTML_DIR / "manifest.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
