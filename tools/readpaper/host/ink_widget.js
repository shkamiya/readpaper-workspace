/* Handwriting (Apple Pencil) overlay for explainer pages.  Injected by
 * ask_server.py as the third script, after ask_widget.js and mark_widget.js.
 *
 * What it is: a full-document <svg> laid over the article. In ink mode the
 * pencil draws, a finger still scrolls, and every stroke is saved to
 * html-expl/ink/<stem>.json through POST /api/ink, so the same page opened on
 * the iPhone or the Mac shows the writing too. The html is never modified.
 *
 * Why <svg> and not <canvas>: iOS caps a canvas at roughly 16M pixels, and a
 * long explainer is 1000 x 30000 px. Paths have no such limit and stay crisp.
 *
 * Where a stroke lives: not in page pixels. Line wrapping differs between the
 * iPad, the iPhone and the Mac, so a stroke is stored relative to the block
 * element it was started over (index in a fixed block-element list + a text
 * fingerprint to survive edits), with x normalised by that element's width and
 * y in px from its top. Paragraph-level placement survives reflow; line-level
 * placement does not, and when the element is now much wider or narrower than
 * it was, the stroke is drawn faint to say so.
 *
 * Pencil vs finger: WebKit exposes Touch.touchType === "stylus". A non-passive
 * touchstart calls preventDefault for the stylus only, so touch-action can stay
 * pan-x pan-y and a finger keeps scrolling. Pointer events then carry pressure.
 */
(function () {
  "use strict";

  var SELF = document.currentScript;
  var FILE = SELF && SELF.getAttribute("data-ask-file");
  var TOKEN = (SELF && SELF.getAttribute("data-ask-token")) || "";
  if (!FILE || FILE === "index.html") return;

  var BLOCK_SEL = "h1,h2,h3,h4,h5,h6,p,li,figure,table,pre,blockquote,aside,dd,dt,.katex-display";
  var OUR_HOSTS = "#readpaper-ask-host,#readpaper-mark-host,#readpaper-ink-host,#readpaper-pos-host";
  var FAR = 0.2;               // width mismatch beyond this -> faint
  var ERASE_R = 12;            // px
  var SAVE_DEBOUNCE = 1200;    // ms
  var PENS = [["#1a1a1a", "黒"], ["#d62828", "赤"], ["#1f6feb", "青"]];
  var HL = "#ffe23a";
  var SVGNS = "http://www.w3.org/2000/svg";

  /* ---------- overlay ---------- */
  var svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("id", "readpaper-ink-svg");
  svg.setAttribute("style",
    "position:absolute;left:0;top:0;z-index:2147482000;pointer-events:none;" +
    "touch-action:pan-x pan-y;overflow:visible;display:block");
  var gInk = document.createElementNS(SVGNS, "g");
  svg.appendChild(gInk);
  document.body.appendChild(svg);

  function sizeOverlay() {
    var de = document.documentElement;
    var w = Math.max(de.scrollWidth, document.body.scrollWidth, de.clientWidth);
    var h = Math.max(de.scrollHeight, document.body.scrollHeight, de.clientHeight);
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    svg.style.width = w + "px"; svg.style.height = h + "px";
  }

  /* ---------- anchoring ---------- */
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
  function anchorFor(pageX, pageY) {
    // The overlay is under the pointer; look through it.
    var prev = svg.style.pointerEvents;
    svg.style.pointerEvents = "none";
    var el = document.elementFromPoint(pageX - window.scrollX, pageY - window.scrollY);
    svg.style.pointerEvents = prev;
    el = el && el.closest ? el.closest(BLOCK_SEL) : null;
    if (el && el.closest(OUR_HOSTS)) el = null;
    var list = blocks();
    var i = el ? list.indexOf(el) : -1;
    if (!el || i < 0) return { el: document.body, i: -1, fp: "BODY:", w: docWidth() };
    return { el: el, i: i, fp: fp(el), w: el.getBoundingClientRect().width || 1 };
  }
  var cacheList = null;
  function resolve(a) {
    if (a.i < 0 || a.fp === "BODY:") return document.body;
    if (!cacheList) cacheList = blocks();
    var el = cacheList[a.i];
    if (el && fp(el) === a.fp) return el;
    for (var k = 0; k < cacheList.length; k++) if (fp(cacheList[k]) === a.fp) return cacheList[k];
    return el || document.body;
  }
  function docWidth() { return document.documentElement.clientWidth || 1; }
  function originOf(el) {
    if (el === document.body) return { x: 0, y: 0, w: docWidth() };
    var r = el.getBoundingClientRect();
    return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width || 1 };
  }
  function toPage(stroke) {
    var el = resolve(stroke.anchor), o = originOf(el), pts = [];
    for (var k = 0; k < stroke.pts.length; k++) {
      var p = stroke.pts[k];
      pts.push([o.x + p[0] * o.w, o.y + p[1], p[2]]);
    }
    var far = stroke.anchor.w > 0 && Math.abs(o.w - stroke.anchor.w) / stroke.anchor.w > FAR;
    return { pts: pts, far: far };
  }

  /* ---------- rendering ---------- */
  function pathD(pts) {
    if (!pts.length) return "";
    if (pts.length === 1) return "M" + pts[0][0].toFixed(1) + " " + pts[0][1].toFixed(1) + " l0.1 0";
    var d = "M" + pts[0][0].toFixed(1) + " " + pts[0][1].toFixed(1);
    for (var k = 1; k < pts.length; k++) d += "L" + pts[k][0].toFixed(1) + " " + pts[k][1].toFixed(1);
    return d;
  }
  function widthOf(stroke) {
    if (stroke.tool === "hl") return stroke.size;
    var s = 0; for (var k = 0; k < stroke.pts.length; k++) s += stroke.pts[k][2];
    var avg = stroke.pts.length ? s / stroke.pts.length : 0.5;
    return stroke.size * (0.55 + avg);
  }
  function styleEl(path, stroke, far) {
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", stroke.color);
    path.setAttribute("stroke-width", widthOf(stroke).toFixed(2));
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    var op = stroke.tool === "hl" ? 0.42 : 0.95;
    if (far) op *= 0.45;
    path.setAttribute("opacity", op.toFixed(2));
    if (stroke.tool === "hl") path.style.mixBlendMode = "multiply";
  }
  var strokes = [];         // saved + local, in order
  var rendered = {};        // id -> {path, pts(page)}
  function renderAll() {
    cacheList = null;
    while (gInk.firstChild) gInk.removeChild(gInk.firstChild);
    rendered = {};
    for (var k = 0; k < strokes.length; k++) renderOne(strokes[k]);
  }
  function renderOne(stroke) {
    // Anchored inside a folded box (fold_widget.js): the block has no box, so
    // its origin would be (0,0). Skip it; the ResizeObserver redraws on open.
    var anc = resolve(stroke.anchor);
    if (anc !== document.body && (!anc.getClientRects().length ||
        anc.getAttribute("data-rp-fold") === "closed")) return;
    var pg = toPage(stroke);
    var path = document.createElementNS(SVGNS, "path");
    path.setAttribute("d", pathD(pg.pts));
    styleEl(path, stroke, pg.far);
    path.dataset.id = stroke.id;
    gInk.appendChild(path);
    rendered[stroke.id] = { path: path, pts: pg.pts };
  }

  /* ---------- toolbar (shadow root) ---------- */
  var host = document.createElement("div");
  host.id = "readpaper-ink-host";
  document.body.appendChild(host);
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML = [
    "<style>",
    ":host{all:initial}",
    "*{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif}",
    "[hidden]{display:none!important}",
    ".fab{position:fixed;left:20px;bottom:20px;z-index:2147483000;border:0;border-radius:999px;",
    "  padding:11px 16px;background:#fff;color:#222;box-shadow:0 4px 18px rgba(0,0,0,.22);",
    "  font-size:15px;font-weight:700;cursor:pointer;-webkit-tap-highlight-color:transparent;",
    "  display:flex;align-items:center;gap:8px}",
    ".fab.on{background:#1f6feb;color:#fff}",
    ".bar{position:fixed;left:14px;bottom:70px;z-index:2147483000;background:#fff;color:#222;",
    "  border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.25);padding:8px;display:flex;",
    "  flex-wrap:wrap;gap:6px;align-items:center;max-width:calc(100vw - 28px)}",
    ".t{border:1px solid #d0d5dc;background:#fff;color:#222;border-radius:10px;padding:8px 10px;",
    "  font-size:14px;cursor:pointer;-webkit-tap-highlight-color:transparent;min-width:40px;",
    "  display:inline-flex;align-items:center;justify-content:center;gap:5px}",
    ".t.on{border-color:#1f6feb;box-shadow:0 0 0 2px rgba(31,111,235,.25)}",
    ".sw{display:inline-block;width:14px;height:14px;border-radius:50%;border:1px solid rgba(0,0,0,.2)}",
    ".sw.hl{border-radius:3px;width:18px}",
    ".st{font-size:11px;color:#6a737d;padding:0 4px;min-width:3em}",
    ".sep{width:1px;height:26px;background:#e2e6ea;margin:0 2px}",
    "@media(max-width:520px){.fab{left:14px;bottom:calc(14px + env(safe-area-inset-bottom))}",
    "  .bar{bottom:calc(64px + env(safe-area-inset-bottom))}}",
    "</style>",
    '<button class="fab" title="手書き">✎</button>',
    '<div class="bar" hidden>',
    '  <span class="pens"></span>',
    '  <button class="t hl" data-tool="hl" title="蛍光"><span class="sw hl" style="background:' + HL + '"></span></button>',
    '  <span class="sep"></span>',
    '  <button class="t" data-tool="erase" title="消しゴム">⌫</button>',
    '  <button class="t undo" title="取消">↶</button>',
    '  <span class="sep"></span>',
    '  <button class="t finger" title="指でも描く（Pencil が無いとき）">☝︎</button>',
    '  <span class="st"></span>',
    '  <button class="t done">完了</button>',
    "</div>",
  ].join("\n");
  var $ = function (s) { return root.querySelector(s); };
  var fab = $(".fab"), bar = $(".bar"), stEl = $(".st"), pensEl = $(".pens");
  var mode = false, tool = "pen", color = PENS[0][0], fingerOK = false;

  PENS.forEach(function (p, idx) {
    var b = document.createElement("button");
    b.className = "t pen" + (idx === 0 ? " on" : ""); b.dataset.tool = "pen"; b.dataset.color = p[0]; b.title = p[1];
    b.innerHTML = '<span class="sw" style="background:' + p[0] + '"></span>';
    pensEl.appendChild(b);
  });
  function paintTools() {
    var ts = root.querySelectorAll(".t[data-tool]");
    for (var i = 0; i < ts.length; i++) {
      var t = ts[i];
      t.classList.toggle("on", t.dataset.tool === tool && (tool !== "pen" || t.dataset.color === color));
    }
    $(".finger").classList.toggle("on", fingerOK);
    svg.style.touchAction = fingerOK ? "none" : "pan-x pan-y";
  }
  root.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest(".t[data-tool]") : null;
    if (!t) return;
    tool = t.dataset.tool;
    if (tool === "pen") color = t.dataset.color;
    paintTools();
  });
  $(".finger").addEventListener("click", function () { fingerOK = !fingerOK; paintTools(); });
  $(".undo").addEventListener("click", undo);
  $(".done").addEventListener("click", function () { setMode(false); });
  fab.addEventListener("click", function () { setMode(!mode); });

  function setMode(on) {
    mode = on;
    fab.classList.toggle("on", on);
    bar.hidden = !on;
    svg.style.pointerEvents = on ? "auto" : "none";
    if (on) { sizeOverlay(); renderAll(); paintTools(); }
  }

  /* ---------- drawing ---------- */
  var cur = null;   // {stroke, page pts, path}
  function newId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
  function wantsPointer(e) {
    if (e.pointerType === "touch") return fingerOK;
    return true;    // pen, mouse
  }
  // Stylus only: keep the finger free to scroll. WebKit-specific Touch.touchType.
  svg.addEventListener("touchstart", function (e) {
    if (!mode) return;
    for (var i = 0; i < e.touches.length; i++) {
      if (e.touches[i].touchType === "stylus" || fingerOK) { e.preventDefault(); return; }
    }
  }, { passive: false });
  svg.addEventListener("touchmove", function (e) {
    if (mode && cur) e.preventDefault();
  }, { passive: false });

  svg.addEventListener("pointerdown", function (e) {
    if (!mode || !wantsPointer(e) || (e.button && e.button !== 0)) return;
    e.preventDefault();
    try { svg.setPointerCapture(e.pointerId); } catch (err) {}
    var px = e.clientX + window.scrollX, py = e.clientY + window.scrollY;
    if (tool === "erase") { eraseAt(px, py); cur = { erase: true }; return; }
    var a = anchorFor(px, py), o = originOf(a.el);
    var stroke = {
      id: newId(), tool: tool === "hl" ? "hl" : "pen",
      color: tool === "hl" ? HL : color, size: tool === "hl" ? 16 : 2.4,
      anchor: { i: a.i, fp: a.fp, w: a.w }, vw: docWidth(), pts: [],
    };
    var path = document.createElementNS(SVGNS, "path");
    styleEl(path, stroke, false);
    gInk.appendChild(path);
    cur = { stroke: stroke, o: o, pts: [], path: path };
    addPoint(e);
  });
  function addPoint(e) {
    var evs = (e.getCoalescedEvents && e.getCoalescedEvents()) || [e];
    if (!evs.length) evs = [e];
    for (var k = 0; k < evs.length; k++) {
      var ev = evs[k];
      var px = ev.clientX + window.scrollX, py = ev.clientY + window.scrollY;
      var pr = ev.pressure > 0 ? ev.pressure : 0.5;
      cur.pts.push([px, py, pr]);
      cur.stroke.pts.push([(px - cur.o.x) / cur.o.w, py - cur.o.y, pr]);
    }
    cur.path.setAttribute("d", pathD(cur.pts));
    cur.path.setAttribute("stroke-width", widthOf(cur.stroke).toFixed(2));
  }
  svg.addEventListener("pointermove", function (e) {
    if (!mode || !cur) return;
    if (cur.erase) { eraseAt(e.clientX + window.scrollX, e.clientY + window.scrollY); return; }
    addPoint(e);
  });
  function endStroke(e) {
    if (!cur) return;
    if (!cur.erase) {
      if (cur.stroke.pts.length >= 1) {
        strokes.push(cur.stroke);
        cur.path.dataset.id = cur.stroke.id;
        rendered[cur.stroke.id] = { path: cur.path, pts: cur.pts };
        undoStack.push({ add: cur.stroke });
        scheduleSave();
      } else if (cur.path.parentNode) cur.path.parentNode.removeChild(cur.path);
    } else if (eraseBatch.length) {
      undoStack.push({ del: eraseBatch.slice() });
      eraseBatch = [];
      scheduleSave();
    }
    cur = null;
  }
  svg.addEventListener("pointerup", endStroke);
  svg.addEventListener("pointercancel", endStroke);
  svg.addEventListener("lostpointercapture", function () { if (cur) endStroke(); });

  /* ---------- eraser / undo ---------- */
  var eraseBatch = [];
  var undoStack = [];
  function removeStroke(id) {
    for (var k = 0; k < strokes.length; k++) if (strokes[k].id === id) { strokes.splice(k, 1); break; }
    var r = rendered[id];
    if (r && r.path.parentNode) r.path.parentNode.removeChild(r.path);
    delete rendered[id];
  }
  function eraseAt(px, py) {
    var r2 = ERASE_R * ERASE_R;
    for (var k = strokes.length - 1; k >= 0; k--) {
      var s = strokes[k], r = rendered[s.id];
      if (!r) continue;
      var hit = false;
      for (var j = 0; j < r.pts.length; j++) {
        var dx = r.pts[j][0] - px, dy = r.pts[j][1] - py;
        if (dx * dx + dy * dy <= r2) { hit = true; break; }
      }
      if (hit) { eraseBatch.push(s); removeStroke(s.id); }
    }
  }
  function undo() {
    var a = undoStack.pop();
    if (!a) return;
    if (a.add) removeStroke(a.add.id);
    else if (a.del) { for (var k = 0; k < a.del.length; k++) { strokes.push(a.del[k]); renderOne(a.del[k]); } }
    scheduleSave();
  }

  /* ---------- server ---------- */
  var base = 0, saveTimer = null, saving = false, dirty = false;   // base = server rev we loaded
  function scheduleSave() {
    dirty = true;
    stEl.textContent = "…";
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(save, SAVE_DEBOUNCE);
  }
  function save(retry) {
    if (saving) { dirty = true; return; }
    saving = true; dirty = false;
    fetch("/api/ink", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: FILE, token: TOKEN, strokes: strokes, base: base }),
    })
      .then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (res) {
        saving = false;
        if (res.s === 409 && !retry) {
          // Another device saved since we loaded: union by id, then retry once.
          var mine = strokes.slice(), seen = {};
          strokes = [];
          (res.j.strokes || []).forEach(function (s) { seen[s.id] = 1; strokes.push(s); });
          mine.forEach(function (s) { if (!seen[s.id]) strokes.push(s); });
          base = res.j.rev || 0;
          renderAll();
          return save(true);
        }
        if (res.s !== 200) { stEl.textContent = "✗ " + (res.j.error || res.s); return; }
        base = res.j.rev || 0;
        var at = res.j.updated_at || "";
        stEl.textContent = "保存 " + at.slice(11, 16);
        if (dirty) scheduleSave();
      })
      .catch(function (e) { saving = false; stEl.textContent = "✗ " + e.message; });
  }
  function load() {
    fetch("/api/ink?file=" + encodeURIComponent(FILE), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.error) return;
        strokes = j.strokes || [];
        base = j.rev || 0;
        sizeOverlay(); renderAll();
        if (strokes.length) fab.textContent = "✎ " + strokes.length;
      })
      .catch(function () {});
  }

  /* ---------- relayout ---------- */
  var relayoutTimer = null;
  function relayout() {
    if (relayoutTimer) clearTimeout(relayoutTimer);
    relayoutTimer = setTimeout(function () { sizeOverlay(); renderAll(); }, 150);
  }
  window.addEventListener("resize", relayout);
  window.addEventListener("orientationchange", relayout);
  window.addEventListener("load", relayout);          // KaTeX (deferred) changes heights
  if (window.ResizeObserver) {
    try { new ResizeObserver(relayout).observe(document.body); } catch (e) {}
  }
  // Late layout shifts (fonts, images, MathJax) after `load`.
  setTimeout(relayout, 2500);

  sizeOverlay();
  load();
})();
