/* Reading position ("しおり") for explainer pages.  Injected by ask_server.py as
 * the fourth script after ask_widget.js / mark_widget.js / ink_widget.js.
 *
 * What it does: remembers where SK was on the page and, on the next open --
 * on any device -- jumps straight back there, with a small toast offering
 * 先頭へ as the way out.  Storage is html-expl/pos/<stem>.json via GET/POST
 * /api/pos (pos_store.py); localStorage keeps a same-device copy for when the
 * server is unreachable.  The html file itself is never modified.
 *
 * Why not just scrollY: it does not survive a different viewport width (iPhone
 * vs Mac), a font swap, or a regenerated page.  So the position is stored the
 * way ink_widget.js anchors strokes: the block element nearest the viewport top
 * (index in document order + a TAG:text fingerprint to find it again after an
 * edit) plus the fraction of that element already scrolled past.  The overall
 * ratio scrollY / maxScroll is kept as the fallback and as the index's "63%".
 *
 * Save: `scroll` debounced 1 s, and immediately on pagehide / hidden via
 * sendBeacon (a fetch would be killed with the tab on iOS).  Near the top
 * (ratio < 2%) the record is cleared instead: "started over" is the absence of
 * a bookmark.  Nothing is saved before the restore has settled, otherwise the
 * initial scrollY=0 would wipe the bookmark it is about to use.
 */
(function () {
  "use strict";

  var SELF = document.currentScript;
  var FILE = SELF && SELF.getAttribute("data-ask-file");
  var TOKEN = (SELF && SELF.getAttribute("data-ask-token")) || "";
  if (!FILE || FILE === "index.html") return;

  var BLOCK_SEL = "h1,h2,h3,h4,h5,h6,p,li,figure,table,pre,blockquote,aside,dd,dt";
  var OUR_HOSTS = "#readpaper-ask-host,#readpaper-mark-host,#readpaper-ink-host,#readpaper-pos-host";
  var LS_KEY = "readpaper-pos:" + FILE;
  var MIN_RATIO = 0.02;        // below this the bookmark is cleared, not saved
  var DEBOUNCE_MS = 1000;
  var TOAST_MS = 6000;
  var DEVICE = (function () {
    var ua = navigator.userAgent || "";
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
    if (/Macintosh/.test(ua)) return "Mac";
    return "other";
  })();

  /* ---------- anchoring (same scheme as ink_widget.js) ---------- */
  function blocks() {
    var all = document.querySelectorAll(BLOCK_SEL), out = [];
    for (var i = 0; i < all.length; i++) {
      if (all[i].closest(OUR_HOSTS)) continue;
      out.push(all[i]);
    }
    return out;
  }
  function fp(el) {
    var t = (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60);
    return el.tagName + ":" + t;
  }
  function maxScroll() {
    var de = document.documentElement;
    return Math.max(de.scrollHeight - de.clientHeight, 1);
  }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  // The visible block whose top is highest on the page while still at or above
  // the viewport top, and how far into it the viewport top sits.
  function capture() {
    var y = window.scrollY, list = blocks();
    var best = null, bi = -1, bt = 0, bh = 0;
    for (var i = 0; i < list.length; i++) {
      var r = list[i].getBoundingClientRect();
      if (r.height <= 0 && r.width <= 0) continue;      // display:none etc.
      var top = r.top + y;
      if (top <= y + 2 && (best === null || top >= bt)) { best = list[i]; bi = i; bt = top; bh = r.height; }
    }
    return {
      anchor: best ? { i: bi, fp: fp(best), frac: bh > 0 ? clamp01((y - bt) / bh) : 0 } : null,
      ratio: clamp01(y / maxScroll()),
    };
  }
  function resolve(a) {
    if (!a || a.i < 0) return null;
    var list = blocks(), el = list[a.i];
    if (el && fp(el) === a.fp) return el;
    for (var k = 0; k < list.length; k++) if (fp(list[k]) === a.fp) return list[k];
    return null;                                          // let ratio decide
  }
  function targetY(rec) {
    var el = resolve(rec.anchor);
    // The saved block may sit inside a box fold_widget.js has closed.
    if (el && !el.getClientRects().length && window.readpaperFold) window.readpaperFold.reveal(el);
    if (el) {
      var r = el.getBoundingClientRect();
      return r.top + window.scrollY + (rec.anchor.frac || 0) * r.height;
    }
    return (rec.ratio || 0) * maxScroll();
  }
  function jump(y) {
    try { window.scrollTo({ top: y, left: 0, behavior: "instant" }); }
    catch (e) { window.scrollTo(0, y); }
  }

  // A hand on the page cancels the delayed re-corrections after a restore
  // (fonts/KaTeX shifting the layout); our own scrollTo does not set this.
  var touched = false;

  /* ---------- toast ---------- */
  var host = document.createElement("div");
  host.id = "readpaper-pos-host";
  document.body.appendChild(host);
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML = [
    "<style>",
    ":host{all:initial}",
    "*{box-sizing:border-box}",
    ".toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:2147483000;",
    "  display:flex;align-items:center;gap:10px;padding:8px 8px 8px 14px;border-radius:999px;",
    "  background:rgba(30,30,34,.92);color:#f2f2f2;font:13px/1 -apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif;",
    "  box-shadow:0 4px 18px rgba(0,0,0,.25);white-space:nowrap;",
    "  opacity:0;transition:opacity .25s;pointer-events:none}",
    ".toast.show{opacity:1;pointer-events:auto}",
    ".toast b{font-weight:600}",
    ".toast button{font:inherit;line-height:1;padding:7px 11px;border-radius:999px;border:0;cursor:pointer;",
    "  background:rgba(255,255,255,.16);color:inherit;-webkit-tap-highlight-color:transparent}",
    ".toast button:hover{background:rgba(255,255,255,.28)}",
    "</style>",
    '<div class="toast" role="status"><span class="msg"></span><button type="button" class="top">先頭へ</button></div>',
  ].join("\n");
  var toast = root.querySelector(".toast"), msg = root.querySelector(".msg");
  var toastTimer = null;
  function showToast(text) {
    msg.innerHTML = text;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove("show"); }, TOAST_MS);
  }
  root.querySelector(".top").addEventListener("click", function () {
    toast.classList.remove("show");
    touched = true;               // stop any pending re-aim
    jump(0);                      // ratio -> 0 -> the debounced save clears the record
    schedule();
  });

  /* ---------- storage ---------- */
  function lsGet() {
    try { var v = localStorage.getItem(LS_KEY); return v ? JSON.parse(v) : null; } catch (e) { return null; }
  }
  function lsSet(rec) {
    try { rec ? localStorage.setItem(LS_KEY, JSON.stringify(rec)) : localStorage.removeItem(LS_KEY); } catch (e) { /* private mode */ }
  }

  var armed = false;              // no saves until the restore has settled
  var lastKey = null;             // dedupe: what the server already has
  function keyOf(body) {
    return body.clear ? "clear" : [body.anchor ? body.anchor.i : -1, body.anchor ? body.anchor.frac.toFixed(2) : "", body.ratio.toFixed(3)].join("|");
  }
  function bodyNow() {
    var c = capture();
    if (c.ratio < MIN_RATIO) return { file: FILE, token: TOKEN, clear: true };
    return { file: FILE, token: TOKEN, anchor: c.anchor, ratio: c.ratio, device: DEVICE };
  }
  function save(beacon) {
    if (!armed) return;
    var body = bodyNow(), key = keyOf(body);
    if (key === lastKey) return;
    lastKey = key;
    lsSet(body.clear ? null : { anchor: body.anchor, ratio: body.ratio, updated_at: new Date().toISOString() });
    var json = JSON.stringify(body);
    if (beacon && navigator.sendBeacon) {
      try { navigator.sendBeacon("/api/pos", new Blob([json], { type: "application/json" })); return; } catch (e) { /* fall through */ }
    }
    fetch("/api/pos", { method: "POST", headers: { "Content-Type": "application/json" }, body: json, keepalive: true })
      .catch(function () { lastKey = null; /* retry on the next scroll */ });
  }
  var timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () { save(false); }, DEBOUNCE_MS);
  }
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("pagehide", function () { clearTimeout(timer); save(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { clearTimeout(timer); save(true); }
  });

  /* ---------- restore ---------- */
  ["wheel", "touchstart", "keydown", "pointerdown"].forEach(function (ev) {
    window.addEventListener(ev, function () { touched = true; }, { passive: true, once: true });
  });

  function arm() { armed = true; lastKey = null; }

  function restore(rec) {
    if (!rec || !rec.updated_at) { arm(); return; }
    var y = targetY(rec);
    if (y < document.documentElement.clientHeight * 0.8) { arm(); return; }   // less than a screen: not worth a jump
    jump(y);
    arm();                        // from here on, closing the tab saves where we are
    var pct = Math.round((rec.ratio || 0) * 100);
    showToast("前回の続き <b>" + pct + "%</b>" + (rec.device && rec.device !== DEVICE ? " · " + rec.device + "で" : ""));
    // Layout keeps moving for a moment (web fonts, late KaTeX, images without
    // dimensions); re-aim a few times unless the reader has taken over.
    var tries = 0;
    (function again() {
      if (touched || ++tries > 6) return;
      var y2 = targetY(rec);
      if (Math.abs(y2 - window.scrollY) > 8) jump(y2);
      setTimeout(again, tries < 3 ? 250 : 800);
    })();
  }

  function begin() {
    if (location.hash) { arm(); return; }                  // a #link says where to go
    fetch("/api/pos?file=" + encodeURIComponent(FILE), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) { restore(j && !j.error && j.updated_at ? j : lsGet()); })
      .catch(function () { restore(lsGet()); });
  }
  if (document.readyState === "complete") begin();
  else window.addEventListener("load", begin, { once: true });
})();
