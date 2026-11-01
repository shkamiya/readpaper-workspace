/* In-page chat widget for explainer pages.  Injected by ask_server.py.
 *
 * Everything lives inside a shadow root. That is not stylistic: the 131
 * explainers each ship their own stylesheet, several with selectors as loose as
 * `div{...}`, so anything in the light DOM would be restyled unpredictably by
 * whichever page it landed on. A shadow root gives the widget its own scope.
 *
 * The page is never mutated. When a job finishes the user presses 反映する and
 * we reload -- the explainers run inline scripts (KaTeX auto-render, and in
 * some pages interactive demos), so swapping innerHTML would leave the page in
 * a half-initialised state. A reload re-runs them exactly as the author meant.
 */
(function () {
  "use strict";

  var SELF = document.currentScript;
  var FILE = SELF && SELF.getAttribute("data-ask-file");
  // Shared secret the server injected into this tag. It is echoed on POST so a
  // cross-origin drive-by, which cannot read this page to learn it, gets a 403.
  var TOKEN = (SELF && SELF.getAttribute("data-ask-token")) || "";
  // index.html has no paper of its own to ask about.
  if (!FILE || FILE === "index.html") return;

  var POLL_MS = 3000;
  // On macOS Ctrl+C is not copy (Cmd+C is), so it is free to mean 中止 as it does
  // in a terminal. Elsewhere it only cancels when nothing is selected.
  var MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
  var SCROLL_KEY = "readpaper-ask:scroll:" + FILE;

  /* ---------- shadow root ---------- */
  var host = document.createElement("div");
  host.id = "readpaper-ask-host";
  document.body.appendChild(host);
  var root = host.attachShadow({ mode: "open" });

  root.innerHTML = [
    "<style>",
    ":host{all:initial}",
    "*{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif}",
    // The UA rule [hidden]{display:none} is an author-vs-UA-origin loser against
    // our own .panel{display:flex} / .chip{display:flex}, so the panel and the
    // selection chip both rendered open on load. Restate it as an author rule.
    "[hidden]{display:none!important}",
    ".fab{position:fixed;right:20px;bottom:20px;z-index:2147483000;",
    "  background:#1f6feb;color:#fff;border:0;border-radius:999px;padding:12px 18px;",
    "  font-size:14px;font-weight:600;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.28)}",
    ".fab:hover{background:#1a5fd0}",
    ".fab .dot{display:inline-block;width:8px;height:8px;border-radius:50%;",
    "  background:#ffd33d;margin-right:7px;vertical-align:middle;animation:p 1.2s infinite}",
    "@keyframes p{0%,100%{opacity:.35}50%{opacity:1}}",
    ".panel{position:fixed;right:20px;bottom:20px;z-index:2147483000;width:380px;",
    "  max-height:72vh;display:flex;flex-direction:column;background:#fff;color:#1a1a1a;",
    "  border:1px solid #d6dae0;border-radius:14px;box-shadow:0 10px 34px rgba(0,0,0,.24);overflow:hidden}",
    ".hd{display:flex;align-items:center;gap:8px;padding:10px 14px;background:#f4f6f8;",
    "  border-bottom:1px solid #e2e6ea;font-size:13px;font-weight:700}",
    ".hd .sp{flex:1}",
    ".hd button{border:0;background:transparent;font-size:16px;cursor:pointer;color:#57606a;padding:2px 6px}",
    ".log{flex:1;overflow-y:auto;padding:12px 14px;font-size:13px;line-height:1.65}",
    ".log .empty{color:#6a737d;font-size:12px;line-height:1.7}",
    ".m{margin:0 0 14px}",
    ".m .who{font-size:11px;font-weight:700;color:#6a737d;margin-bottom:3px}",
    ".m.q .bd{background:#eef4ff;border:1px solid #d6e2fb;border-radius:9px;padding:8px 10px;white-space:pre-wrap;word-break:break-word}",
    ".m.a .bd{background:#f6f8fa;border:1px solid #e2e6ea;border-radius:9px;padding:8px 10px;word-break:break-word}",
    ".m.a.err .bd{background:#fff2f0;border-color:#f3c2bb}",
    ".m.a.warn .bd{background:#fff8e6;border-color:#f2dfa8}",
    ".m.a.cxl .bd{color:#6a737d;border-style:dashed}",
    ".meta{font-size:11px;color:#6a737d;margin-top:5px}",
    ".apply{margin-top:8px;background:#1f6feb;color:#fff;border:0;border-radius:7px;",
    "  padding:6px 12px;font-size:12px;font-weight:700;cursor:pointer}",
    ".ft{border-top:1px solid #e2e6ea;padding:10px 12px;background:#fafbfc}",
    ".chip{display:flex;gap:6px;align-items:flex-start;font-size:11px;color:#57606a;",
    "  background:#eef4ff;border:1px solid #d6e2fb;border-radius:7px;padding:5px 8px;margin-bottom:7px}",
    ".chip .tx{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".chip button{border:0;background:transparent;cursor:pointer;color:#57606a;padding:0 2px}",
    "textarea{width:100%;min-height:62px;max-height:180px;resize:vertical;padding:8px 10px;",
    "  border:1px solid #d6dae0;border-radius:8px;font-size:13px;line-height:1.5;background:#fff;color:#1a1a1a}",
    "textarea:focus{outline:2px solid #a9c7ff;outline-offset:-1px}",
    ".row{display:flex;align-items:center;gap:8px;margin-top:7px}",
    ".row .hint{flex:1;font-size:11px;color:#6a737d}",
    ".send{background:#1f6feb;color:#fff;border:0;border-radius:8px;padding:7px 15px;",
    "  font-size:13px;font-weight:700;cursor:pointer}",
    ".send:disabled{background:#b9c3ce;cursor:default}",
    ".send.stop{background:#cf222e}",
    ".send.stop:hover{background:#a40e26}",
    "@media(max-width:520px){",
    "  .panel{right:0;left:0;bottom:0;width:auto;max-height:82vh;border-radius:14px 14px 0 0;",
    "    padding-bottom:env(safe-area-inset-bottom)}",
    "  .fab{right:14px;bottom:calc(14px + env(safe-area-inset-bottom))}}",
    "</style>",
    '<button class="fab" part="fab"><span class="dot" hidden></span>💬 聞く</button>',
    '<div class="panel" hidden>',
    '  <div class="hd"><span>この論文に聞く</span><span class="sp"></span>',
    '    <button class="min" title="閉じる">✕</button></div>',
    '  <div class="log"></div>',
    '  <div class="ft">',
    '    <div class="chip" hidden><span class="tx"></span><button class="unpin">✕</button></div>',
    '    <textarea placeholder="質問 or 指示を入力…&#10;例: この部分の説明を厚くして"></textarea>',
    '    <div class="row"><span class="hint">目安 5 分</span>',
    '      <button class="send">送信</button></div>',
    "  </div>",
    "</div>",
  ].join("");

  var $ = function (s) { return root.querySelector(s); };
  var fab = $(".fab"), dot = $(".fab .dot"), panel = $(".panel"), logEl = $(".log");
  var ta = $("textarea"), send = $(".send"), chip = $(".chip"), chipTx = $(".chip .tx");
  var hint = $(".row .hint");

  /* ---------- restore scroll after a 反映する reload ---------- */
  try {
    var saved = sessionStorage.getItem(SCROLL_KEY);
    if (saved !== null) {
      sessionStorage.removeItem(SCROLL_KEY);
      var st = JSON.parse(saved);
      window.addEventListener("load", function () {
        window.scrollTo(0, st.y);
        (st.anchors || []).forEach(function (id) { flash(id); });
        open();
      });
    }
  } catch (e) { /* sessionStorage can be unavailable; not worth failing over */ }

  function flash(id) {
    var el = document.getElementById(id);
    if (!el) return;
    var prev = el.style.transition;
    el.style.transition = "background-color .4s";
    el.style.backgroundColor = "rgba(255,214,10,.38)";
    setTimeout(function () {
      el.style.backgroundColor = "";
      setTimeout(function () { el.style.transition = prev; }, 500);
    }, 1500);
  }

  /* ---------- what SK is looking at ----------
   * Phase 0 (2026-08-22) showed this is the load-bearing part: "この部分の説明を
   * 厚くして" carries no target at all, and the agent resolved it correctly from
   * the anchor alone.  Rule: the last element with an id whose top edge is above
   * the middle of the viewport -- i.e. the section being read, not the one just
   * scrolling into view. */
  function currentAnchor() {
    var mid = window.innerHeight * 0.5, best = null;
    var els = document.querySelectorAll("[id]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.id === "readpaper-ask-host") continue;
      var top = el.getBoundingClientRect().top;
      if (top < mid) best = el; else break;
    }
    if (!best) return { id: "", text: "" };
    var t = (best.textContent || "").trim().replace(/\s+/g, " ").slice(0, 120);
    return { id: best.id, text: t };
  }

  /* ---------- selection chip ---------- */
  var pinned = "";
  document.addEventListener("selectionchange", function () {
    if (pinned) return;
    var s = String(window.getSelection ? window.getSelection() : "").trim();
    if (s.length < 8) return;
    chipTx.textContent = "選択中: 「" + s.slice(0, 80) + (s.length > 80 ? "…" : "") + "」";
    chip.hidden = false;
    chip.dataset.text = s.slice(0, 2000);
  });
  $(".unpin").addEventListener("click", function () {
    chip.hidden = true; chip.dataset.text = ""; pinned = "";
  });

  /* ---------- rendering ---------- */
  function el(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }

  function renderJob(job) {
    var id = "job-" + job.job;
    var old = root.getElementById ? root.getElementById(id) : root.querySelector("#" + id);
    var box = el("div", "m a"); box.id = id;
    var done = job.state === "done", bad = job.state === "error";
    if (job.state === "cancelled") {
      box.className = "m a cxl";
      box.appendChild(el("div", "who", "Claude"));
      box.appendChild(el("div", "bd", "⏹ 中止しました（ページは変更していません）"));
      if (old) old.replaceWith(box); else logEl.appendChild(box);
      logEl.scrollTop = logEl.scrollHeight;
      return;
    }
    if (bad) box.className = "m a err";
    else if (done && job.check === "findings") box.className = "m a warn";

    box.appendChild(el("div", "who", "Claude"));
    var bd = el("div", "bd");
    if (!done && !bad) {
      bd.appendChild(el("span", null, "⌁ " + (job.phase || "考えています…")));
    } else if (bad) {
      bd.appendChild(el("span", null, "✗ " + (job.phase || "失敗しました")));
    } else {
      bd.appendChild(el("span", null,
        (job.check === "findings" ? "⚠ " : "⌁ ") + (job.summary || "完了")));
      var bits = [];
      if (job.mode) bits.push(job.mode === "qa" ? "Q&A に追記" : "本文を編集");
      if (job.added != null) bits.push("+" + job.added + "/-" + job.removed + " 行");
      if (job.elapsed) bits.push(job.elapsed + " 秒");
      if (job.cost_usd) bits.push("$" + Number(job.cost_usd).toFixed(2));
      if (job.session_origin === "adopted") bits.push("会話を継続");
      else if (job.resumed === false) bits.push("新規セッション");
      if (job.check === "findings") bits.push("数式チェックに指摘あり");
      if (bits.length) bd.appendChild(el("div", "meta", bits.join(" ・ ")));
      if (job.added || job.removed) {
        var btn = el("button", "apply", "反映する");
        btn.addEventListener("click", function () {
          try {
            sessionStorage.setItem(SCROLL_KEY, JSON.stringify(
              { y: window.scrollY, anchors: job.anchors || [] }));
          } catch (e) { /* proceed without the scroll restore */ }
          location.reload();
        });
        bd.appendChild(btn);
      }
    }
    box.appendChild(bd);
    if (old) old.replaceWith(box); else logEl.appendChild(box);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function renderQuestion(job) {
    var id = "q-" + job.job;
    if (root.querySelector("#" + id)) return;
    var box = el("div", "m q"); box.id = id;
    box.appendChild(el("div", "who", "SK"));
    box.appendChild(el("div", "bd", job.question));
    logEl.appendChild(box);
  }

  /* ---------- polling ---------- */
  var watching = {};
  function watch(jobId) {
    if (watching[jobId]) return;
    watching[jobId] = setInterval(function () {
      fetch("/api/status?job=" + encodeURIComponent(jobId), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (job) {
          if (job.error) throw new Error(job.error);
          renderJob(job);
          if (job.state === "done" || job.state === "error" || job.state === "cancelled") {
            clearInterval(watching[jobId]); delete watching[jobId];
            busy(false);
          }
        })
        .catch(function () { /* transient: the server may be restarting */ });
    }, POLL_MS);
  }

  // The job in flight, kept so 中止 can put its prompt back into the textarea.
  var live = null;

  function busy(on) {
    if (!on) live = null;
    send.disabled = false;
    send.classList.toggle("stop", on);
    send.textContent = on ? "中止" : "送信";
    hint.textContent = on ? (MAC ? "Ctrl+C で中止" : "Ctrl+C / 中止ボタンで中止") : "目安 5 分";
    dot.hidden = !on;
  }

  /* ---------- cancel (Ctrl+C) ----------
   * Like Claude Code's Ctrl+C: stop the run and hand the prompt back for
   * editing. The prompt is restored at once rather than after the worker
   * confirms, since fixing it is the whole point; the textarea is only filled
   * when empty so nothing typed meanwhile is overwritten. */
  function cancel() {
    if (!live || send.disabled) return;
    var job = live;
    send.disabled = true; send.textContent = "中止中…";
    fetch("/api/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job: job.job, token: TOKEN }),
    })
      .then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (res) {
        if (res.s !== 200) {
          send.disabled = false; send.textContent = "中止";
          if (res.s === 409 && res.j.state !== "running") return;   // already over
          alertLine("✗ 中止できません: " + (res.j.error || res.s));
          return;
        }
        if (job.question && !ta.value.trim()) {
          ta.value = job.question;
          if (job.selection) {
            chipTx.textContent = "選択中: 「" + job.selection.slice(0, 80)
              + (job.selection.length > 80 ? "…" : "") + "」";
            chip.dataset.text = job.selection; chip.hidden = false;
          }
        }
        if (res.j.state === "cancelled") {
          renderJob({ job: job.job, state: "cancelled" });
          if (watching[job.job]) { clearInterval(watching[job.job]); delete watching[job.job]; }
          busy(false);
        }
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
      })
      .catch(function (e) {
        send.disabled = false; send.textContent = "中止";
        alertLine("✗ 中止できません: " + e.message);
      });
  }

  document.addEventListener("keydown", function (e) {
    if (!live || !e.ctrlKey || e.metaKey || e.altKey || e.key !== "c") return;
    if (!MAC) {
      var sel = String(window.getSelection ? window.getSelection() : "");
      if (sel || ta.selectionStart !== ta.selectionEnd) return;   // that is a copy
    }
    e.preventDefault();
    cancel();
  }, true);

  /* ---------- submit ---------- */
  function submit() {
    var q = ta.value.trim();
    if (!q) return;
    var body = {
      file: FILE, question: q, mode: "auto",
      token: TOKEN,
      anchor: currentAnchor(),
      selection: chip.hidden ? "" : (chip.dataset.text || ""),
    };
    send.disabled = true; send.textContent = "送信中…";
    fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (res) {
        if (res.s === 409) {
          alertLine("前の質問をまだ処理中です。終わるまで待つか、中止してください。");
          busy(true); live = { job: res.j.job }; watch(res.j.job); return;
        }
        if (res.s !== 200) { alertLine("✗ " + (res.j.error || res.s)); busy(false); return; }
        ta.value = "";
        chip.hidden = true; chip.dataset.text = "";
        busy(true);
        live = { job: res.j.job, question: q, selection: body.selection };
        renderQuestion({ job: res.j.job, question: q });
        renderJob({ job: res.j.job, state: "queued", phase: "起動中…" });
        watch(res.j.job);
      })
      .catch(function (e) { alertLine("✗ 送信できません: " + e.message); busy(false); });
  }

  function alertLine(msg) {
    var box = el("div", "m a err");
    box.appendChild(el("div", "who", "Claude"));
    box.appendChild(el("div", "bd", msg));
    logEl.appendChild(box);
    logEl.scrollTop = logEl.scrollHeight;
  }

  send.addEventListener("click", function () { if (live) cancel(); else submit(); });
  ta.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); if (!live) submit(); }
  });

  /* ---------- open / close ---------- */
  function open() {
    panel.hidden = false; fab.hidden = true;
    if (!logEl.dataset.loaded) { logEl.dataset.loaded = "1"; loadHistory(); }
    ta.focus();
  }
  fab.addEventListener("click", open);
  $(".min").addEventListener("click", function () { panel.hidden = true; fab.hidden = false; });
  // Deep link: .../paper.html#ask opens straight into the chat. Also the only
  // way to screenshot the expanded panel in headless Chrome.
  if (location.hash === "#ask") open();

  function loadHistory() {
    fetch("/api/chat?file=" + encodeURIComponent(FILE), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var jobs = d.jobs || [];
        if (!jobs.length) {
          logEl.appendChild(el("div", "empty",
            "本文で気になった箇所を選択してから質問すると、その場所についての質問として扱われます。"
            + "「この部分の説明を厚くして」のような指示を出すと、Q&A ではなく本文が書き換わります。"));
          return;
        }
        jobs.forEach(function (job) {
          renderQuestion(job); renderJob(job);
          if (job.state === "queued" || job.state === "running") {
            watch(job.job); busy(true);
            live = { job: job.job, question: job.question, selection: job.selection || "" };
          }
        });
      })
      .catch(function () { /* offline: start with an empty log */ });
  }
})();
