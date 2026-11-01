/* Reading-mark card for explainer pages.  Injected by ask_server.py as a second
 * script after ask_widget.js.
 *
 * What it is: SK's own 状態 (読み中 / 読了) + 理解度 (ふんわり / だいたい / しっかり)
 * + ひとことまとめ, shown as one card right under the title so "the previous me"
 * is the first thing on the page. Storage is html-expl/meta/<stem>.json via
 * GET/POST /api/meta; the html file itself is never modified.
 *
 * Same two rules as ask_widget.js, for the same reasons:
 *   - everything lives in a shadow root (the explainers ship loose selectors);
 *   - the page is never mutated beyond inserting the host element.
 *
 * Chips save on tap (tap the lit one again to clear it -- "unread" is the
 * absence of a status, never a value). The note has an explicit 保存 button:
 * on the iPhone, blur-to-save fires when the keyboard is dismissed to scroll,
 * which is exactly when the text is half-typed.
 *
 * Math in the note is rendered by the page's own KaTeX when it has one; on the
 * MathJax pages (15 of 157) and the two pages with no renderer, KaTeX is loaded
 * once from the CDN the other pages already use.
 */
(function () {
  "use strict";

  var SELF = document.currentScript;
  var FILE = SELF && SELF.getAttribute("data-ask-file");
  var TOKEN = (SELF && SELF.getAttribute("data-ask-token")) || "";
  if (!FILE || FILE === "index.html") return;

  var STATUS = [["reading", "読み中"], ["done", "読了"]];
  var LEVEL = [["fuzzy", "ふんわり", 1], ["roughly", "だいたい", 2], ["solid", "しっかり", 3]];
  var KATEX_VER = "0.16.9";
  var DELIMS = [
    { left: "$$", right: "$$", display: true },
    { left: "$", right: "$", display: false },
    { left: "\\[", right: "\\]", display: true },
    { left: "\\(", right: "\\)", display: false },
  ];

  /* ---------- where the card goes: under .meta, else under the first h1 ---------- */
  var after = document.querySelector(".meta") || document.querySelector("h1");
  if (!after) return;

  var host = document.createElement("div");
  host.id = "readpaper-mark-host";
  // all:initial shields the host from the page's `div{...}` rules; the inherits
  // after it are re-applied so the card takes the page's text colour and size.
  host.setAttribute("style", "all:initial;display:block;color:inherit;font-size:inherit;line-height:1.6");
  after.parentNode.insertBefore(host, after.nextSibling);
  var root = host.attachShadow({ mode: "open" });

  // The page's KaTeX stylesheet, or the same CDN build when it has none.
  var katexCss = "";
  var links = document.querySelectorAll('link[rel="stylesheet"]');
  for (var i = 0; i < links.length; i++) {
    if (/katex(\.min)?\.css/.test(links[i].href)) { katexCss = links[i].href; break; }
  }
  if (!katexCss) katexCss = "https://cdn.jsdelivr.net/npm/katex@" + KATEX_VER + "/dist/katex.min.css";

  root.innerHTML = [
    '<link rel="stylesheet" href="' + katexCss + '">',
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Zen+Kurenaido&display=swap">',
    "<style>",
    "*{box-sizing:border-box}",
    "[hidden]{display:none!important}",
    ".card{margin:14px 0 20px;padding:11px 14px 9px;border-left:3px solid #6f9bff;border-radius:6px;",
    "  background:rgba(127,127,127,.09);font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif;",
    "  font-size:14px;line-height:1.6}",
    ".row{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}",
    ".grp{display:flex;gap:6px;align-items:center}",
    ".lbl{font-size:11px;opacity:.55;letter-spacing:.04em}",
    "button.chip{font:inherit;font-size:12.5px;line-height:1;padding:6px 10px;border-radius:999px;cursor:pointer;",
    "  color:inherit;background:transparent;border:1px solid rgba(127,127,127,.5);opacity:.75;",
    "  display:inline-flex;align-items:center;gap:6px;-webkit-tap-highlight-color:transparent}",
    "button.chip:hover{opacity:1}",
    "button.chip.on{opacity:1;font-weight:700}",
    'button.chip.on[data-v="reading"]{background:rgba(255,176,32,.22);border-color:#e0a020}',
    'button.chip.on[data-v="done"]{background:rgba(52,199,121,.22);border-color:#2fa86a}',
    'button.chip.on[data-k="level"]{background:rgba(111,155,255,.2);border-color:#6f9bff}',
    ".bars{display:inline-flex;gap:2px}",
    ".bars i{display:block;width:4px;height:10px;border-radius:1px;background:currentColor;opacity:.22}",
    ".bars i.f{opacity:.95}",
    ".note{margin-top:8px;font-family:'Zen Kurenaido','Klee One',cursive;font-size:17px;line-height:1.7;",
    "  white-space:pre-wrap;word-break:break-word;cursor:text;padding:2px 4px;border-radius:4px;min-height:1.7em}",
    ".note:hover{background:rgba(127,127,127,.12)}",
    ".note.empty{opacity:.4}",
    ".note .katex-display{margin:.4em 0}",
    "textarea{width:100%;min-height:84px;resize:vertical;margin-top:8px;padding:8px 10px;border-radius:8px;",
    "  border:1px solid rgba(127,127,127,.5);background:rgba(255,255,255,.06);color:inherit;",
    "  font-family:'Zen Kurenaido','Klee One',cursive;font-size:17px;line-height:1.6}",
    "textarea:focus{outline:2px solid #6f9bff;outline-offset:-1px}",
    ".ed .row{margin-top:6px}",
    ".ed .sp{flex:1}",
    "button.act{font:inherit;font-size:13px;padding:6px 14px;border-radius:8px;cursor:pointer;border:0;",
    "  background:#1f6feb;color:#fff;font-weight:700}",
    "button.act.ghost{background:transparent;color:inherit;opacity:.7;border:1px solid rgba(127,127,127,.5)}",
    "button.act:disabled{opacity:.5;cursor:default}",
    ".ft{display:flex;gap:10px;align-items:baseline;margin-top:6px;font-size:11px;opacity:.55;min-height:1.2em}",
    ".err{color:#e5484d;opacity:1;font-size:12px}",
    "@media(max-width:520px){.card{font-size:13px}.row{gap:6px 10px}}",
    "</style>",
    '<div class="card">',
    '  <div class="row">',
    '    <span class="grp" data-grp="status"><span class="lbl">状態</span></span>',
    '    <span class="grp" data-grp="level"><span class="lbl">理解度</span></span>',
    "  </div>",
    '  <div class="note empty" title="タップして書く"></div>',
    '  <div class="ed" hidden><textarea placeholder="ひとことまとめ（$…$ で数式可）"></textarea>',
    '    <div class="row"><span class="err"></span><span class="sp"></span>',
    '      <button class="act ghost cancel">取消</button><button class="act save">保存</button></div></div>',
    '  <div class="ft"><span class="upd"></span><span class="err"></span></div>',
    "</div>",
  ].join("\n");

  var $ = function (s) { return root.querySelector(s); };
  var noteEl = $(".note"), ed = $(".ed"), ta = $("textarea"), updEl = $(".ft .upd");
  var ftErr = $(".ft .err"), edErr = $(".ed .err"), saveBtn = $(".save");

  var rec = { status: null, level: null, note: "", updated_at: null };

  /* ---------- chips ---------- */
  function chip(k, v, label, bars) {
    var b = document.createElement("button");
    b.className = "chip"; b.type = "button";
    b.dataset.k = k; b.dataset.v = v;
    if (bars) {
      var bx = document.createElement("span"); bx.className = "bars";
      for (var i = 1; i <= 3; i++) { var s = document.createElement("i"); if (i <= bars) s.className = "f"; bx.appendChild(s); }
      b.appendChild(bx);
    }
    b.appendChild(document.createTextNode(label));
    b.addEventListener("click", function () {
      var next = rec[k] === v ? null : v;        // tap the lit chip again -> clear
      var body = { status: rec.status, level: rec.level, note: rec.note };
      body[k] = next;
      post(body, ftErr);
    });
    return b;
  }
  STATUS.forEach(function (s) { $('[data-grp="status"]').appendChild(chip("status", s[0], s[1], 0)); });
  LEVEL.forEach(function (l) { $('[data-grp="level"]').appendChild(chip("level", l[0], l[1], l[2])); });

  function paint() {
    var chips = root.querySelectorAll("button.chip");
    for (var i = 0; i < chips.length; i++) {
      var c = chips[i];
      c.classList.toggle("on", rec[c.dataset.k] === c.dataset.v);
    }
    if (rec.note) {
      noteEl.classList.remove("empty");
      noteEl.textContent = rec.note;      // textContent: the note is data, never markup
      renderMath(noteEl);
    } else {
      noteEl.classList.add("empty");
      noteEl.textContent = "ひとこと…";
    }
    updEl.textContent = rec.updated_at ? "更新 " + rec.updated_at.slice(0, 10) : "";
  }

  /* ---------- note editing ---------- */
  function openEditor() {
    ta.value = rec.note || "";
    edErr.textContent = "";
    noteEl.hidden = true; ed.hidden = false;
    ta.focus();
  }
  function closeEditor() { ed.hidden = true; noteEl.hidden = false; }
  noteEl.addEventListener("click", openEditor);
  $(".cancel").addEventListener("click", closeEditor);
  saveBtn.addEventListener("click", function () {
    post({ status: rec.status, level: rec.level, note: ta.value }, edErr, function () { closeEditor(); });
  });
  ta.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); saveBtn.click(); }
    if (e.key === "Escape") { e.preventDefault(); closeEditor(); }
  });

  /* ---------- server ---------- */
  function apply(j) {
    rec = { status: j.status || null, level: j.level || null, note: j.note || "", updated_at: j.updated_at || null };
    paint();
  }

  function post(body, errEl, ok) {
    errEl.textContent = "";
    saveBtn.disabled = true;
    fetch("/api/meta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: FILE, token: TOKEN, status: body.status, level: body.level, note: body.note }),
    })
      .then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (res) {
        saveBtn.disabled = false;
        if (res.s !== 200) { errEl.textContent = "✗ 保存できません: " + (res.j.error || res.s); return; }
        apply(res.j);
        if (ok) ok();
      })
      .catch(function (e) { saveBtn.disabled = false; errEl.textContent = "✗ 保存できません: " + e.message; });
  }

  fetch("/api/meta?file=" + encodeURIComponent(FILE), { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (j) { if (!j.error) apply(j); })
    .catch(function () { /* offline: an empty card is still the right thing to show */ });
  paint();

  /* ---------- math ----------
   * The page's KaTeX scripts are `defer`red, so at the moment this script runs
   * renderMathInElement does not exist yet even on a KaTeX page. Try after
   * `load`; if it is still missing the page has no KaTeX, so fetch one. */
  var loading = null;
  function ensureKatex(cb) {
    if (window.renderMathInElement) return cb();
    if (document.readyState !== "complete") {
      window.addEventListener("load", function () { ensureKatex(cb); }, { once: true });
      return;
    }
    if (!loading) {
      loading = new Promise(function (resolve, reject) {
        var base = "https://cdn.jsdelivr.net/npm/katex@" + KATEX_VER + "/dist/";
        function add(src, next) {
          var s = document.createElement("script"); s.src = src;
          s.onload = next; s.onerror = function () { reject(new Error("katex load failed")); };
          document.head.appendChild(s);
        }
        if (window.katex) add(base + "contrib/auto-render.min.js", resolve);
        else add(base + "katex.min.js", function () { add(base + "contrib/auto-render.min.js", resolve); });
      });
    }
    loading.then(cb, function () { /* leave the raw TeX visible */ });
  }
  function renderMath(el) {
    if (!/[$\\]/.test(el.textContent)) return;
    ensureKatex(function () {
      try { window.renderMathInElement(el, { delimiters: DELIMS, throwOnError: false }); }
      catch (e) { /* raw TeX stays readable */ }
    });
  }
})();
