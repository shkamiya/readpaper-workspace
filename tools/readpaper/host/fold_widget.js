/* Fold toggles for explainer pages.  Injected by ask_server.py as the FIRST
 * script, before ask/mark/ink/pos, so the page is already folded when pos_widget
 * restores the reading position and ink_widget lays out strokes.
 *
 * What it folds (html is never modified; this works on all existing explainers):
 *   - the glossary sidebar (aside.terms, or a bare <aside> / <div class="terms">
 *     whose first heading reads 用語/記号/glossary/terms). Closed by default; the
 *     reader's choice is remembered in localStorage for every page.
 *   - every aside.concept (and the rare aside.example) box (最小例 / 数値例 / 直感 ...). Always closed on load;
 *     the label stays visible as the toggle. Alt/Option-click toggles all of them.
 *
 * How, and why this way: only attributes are added (data-rp-fold, role,
 * tabindex, aria-expanded) and the toggle text is CSS ::after content. No
 * element is inserted or removed and no textContent changes, because
 * ink_widget.js / pos_widget.js anchor to (index in a block-element list, text
 * fingerprint) and an inserted <button> or wrapper would shift both. Closed
 * content is display:none, so ink strokes anchored inside it are skipped by
 * ink_widget.js and pos_widget.js calls readpaperFold.reveal() before jumping.
 */
(function () {
  "use strict";

  var SELF = document.currentScript;
  var FILE = SELF && SELF.getAttribute("data-ask-file");
  if (!FILE || FILE === "index.html") return;

  var LS_TERMS = "readpaper-fold-terms";     // "open" | "closed"
  var TERMS_RE = /用語|記号|glossary|terms?\b/i;
  var HEAD_MAX = 90;                         // a longer first child is body text, not a label

  var css = [
    "[data-rp-fold-head]{cursor:pointer;-webkit-user-select:none;user-select:none}",
    "[data-rp-fold-head]::after{content:'\\00a0\\00a0\\25BE\\00a0閉じる';font-size:.78em;font-weight:400;",
    "  opacity:.55;white-space:nowrap;letter-spacing:0}",
    "[data-rp-fold=closed]>[data-rp-fold-head]::after{content:'\\00a0\\00a0\\25B8\\00a0開く'}",
    "[data-rp-fold=closed]>:not([data-rp-fold-head]){display:none!important}",
    "[data-rp-fold=closed]>[data-rp-fold-head]{margin-top:0!important;margin-bottom:0!important}",
    "aside[data-rp-fold=closed].concept,[data-rp-fold=closed][data-rp-fold-kind=concept]{",
    "  padding-top:9px!important;padding-bottom:9px!important;margin-top:16px!important;margin-bottom:16px!important}",
    "[data-rp-fold=closed][data-rp-fold-kind=terms]{padding-top:9px!important;padding-bottom:9px!important;",
    "  max-height:none!important;overflow:visible!important}",
    "[data-rp-fold-nohead]{cursor:pointer}",
    "[data-rp-fold-nohead]::before{content:'補足\\00a0\\00a0\\25BE\\00a0閉じる';display:block;font-weight:700;",
    "  font-size:.9rem;opacity:.75;margin-bottom:6px}",
    "[data-rp-fold=closed][data-rp-fold-nohead]::before{content:'補足\\00a0\\00a0\\25B8\\00a0開く';margin:0}",
    "[data-rp-fold=open][data-rp-fold-nohead]{cursor:auto}",
    "[data-rp-fold-head]:focus-visible{outline:2px solid currentColor;outline-offset:2px}",
    "@media print{[data-rp-fold=closed]>:not([data-rp-fold-head]){display:revert!important}",
    "  [data-rp-fold-head]::after,[data-rp-fold-nohead]::before{content:none!important}}",
  ].join("\n");
  var st = document.createElement("style");
  st.id = "readpaper-fold-style";
  st.textContent = css;
  document.head.appendChild(st);

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  function text(el) { return (el.textContent || "").replace(/\s+/g, " ").trim(); }

  // The first element child, if it reads like a label rather than body text.
  function labelOf(box) {
    var h = box.firstElementChild;
    if (!h) return null;
    var tag = h.tagName;
    if (/^(H[2-6]|SPAN|STRONG|B)$/.test(tag)) return h;
    if (/label|lbl|lab|title/i.test(h.className || "")) return h;
    if ((tag === "DIV" || tag === "P") && text(h).length <= HEAD_MAX &&
        !h.querySelector("p,table,figure,ul,ol,pre,.katex-display,img,svg")) return h;
    return null;
  }

  var boxes = [];
  function setState(box, open) {
    box.setAttribute("data-rp-fold", open ? "open" : "closed");
    var h = box.__rpHead;
    if (h) h.setAttribute("aria-expanded", open ? "true" : "false");
  }
  function isOpen(box) { return box.getAttribute("data-rp-fold") === "open"; }

  function register(box, kind, open) {
    if (box.hasAttribute("data-rp-fold")) return;
    var h = labelOf(box);
    if (h && kind === "terms" && !/^H[2-6]$/.test(h.tagName) && !TERMS_RE.test(text(h))) h = null;
    box.__rpHead = h;
    box.setAttribute("data-rp-fold-kind", kind);
    if (h) {
      h.setAttribute("data-rp-fold-head", "");
      h.setAttribute("role", "button");
      h.setAttribute("tabindex", "0");
    } else {
      box.setAttribute("data-rp-fold-nohead", "");
    }
    setState(box, open);
    boxes.push(box);
  }

  /* ---------- find the boxes ---------- */
  var termsOpen = lsGet(LS_TERMS) === "open";
  var asides = document.querySelectorAll("aside");
  for (var i = 0; i < asides.length; i++) {
    var a = asides[i];
    if (a.classList.contains("concept") || a.classList.contains("example")) { register(a, "concept", false); continue; }
    if (a.classList.contains("why") || a.classList.contains("caveat")) continue;
    if (a.classList.contains("terms")) { register(a, "terms", termsOpen); continue; }
    var inner = a.querySelector(".terms");
    if (inner) { register(inner, "terms", termsOpen); continue; }
    var f = labelOf(a);
    if (f && TERMS_RE.test(text(f))) register(a, "terms", termsOpen);
  }
  if (!boxes.length) return;

  /* ---------- toggling ---------- */
  function toggle(box, e) {
    var open = !isOpen(box);
    if (e && e.altKey && box.getAttribute("data-rp-fold-kind") === "concept") {
      for (var k = 0; k < boxes.length; k++)
        if (boxes[k].getAttribute("data-rp-fold-kind") === "concept") setState(boxes[k], open);
    } else {
      setState(box, open);
    }
    if (box.getAttribute("data-rp-fold-kind") === "terms") lsSet(LS_TERMS, open ? "open" : "closed");
    // Keep the label where the reader's eye is when a box above it shrinks.
    if (!open) {
      var r = (box.__rpHead || box).getBoundingClientRect();
      if (r.top < 0) box.scrollIntoView({ block: "start" });
    }
  }

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    if (t.closest("a")) return;
    var h = t.closest("[data-rp-fold-head]");
    if (h && h.parentElement && h.parentElement.hasAttribute("data-rp-fold")) {
      e.preventDefault(); toggle(h.parentElement, e); return;
    }
    // Head-less box: the ::before line belongs to the box itself, so a click on
    // it has the box as target. When closed, anywhere on the box opens it.
    if (t.hasAttribute && t.hasAttribute("data-rp-fold-nohead")) {
      var r = t.getBoundingClientRect();
      if (!isOpen(t) || e.clientY - r.top < 34) { toggle(t, e); }
    }
  }, true);

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var h = e.target && e.target.closest && e.target.closest("[data-rp-fold-head]");
    if (!h) return;
    e.preventDefault(); toggle(h.parentElement, e);
  });

  /* ---------- reveal: open every closed box around el ---------- */
  function reveal(el) {
    var changed = false;
    for (var n = el; n && n !== document.body; n = n.parentElement) {
      if (n.getAttribute && n.getAttribute("data-rp-fold") === "closed") { setState(n, true); changed = true; }
    }
    return changed;
  }
  window.readpaperFold = { reveal: reveal };

  function revealHash() {
    if (!location.hash) return;
    var id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (e) { return; }
    var el = document.getElementById(id);
    if (el && reveal(el)) el.scrollIntoView({ block: "start" });
  }
  window.addEventListener("hashchange", revealHash);
  if (document.readyState === "complete") revealHash();
  else window.addEventListener("load", revealHash, { once: true });

  // Browser find-in-page cannot see display:none text; Chrome fires
  // beforematch only for hidden=until-found, which we do not use. Ctrl/Cmd+F
  // therefore opens everything once, so a search never silently misses.
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && (e.key === "f" || e.key === "F"))
      for (var k = 0; k < boxes.length; k++) setState(boxes[k], true);
  });
})();
