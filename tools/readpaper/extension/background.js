// readpaper one-click extension — background service worker (mv3)
//
// On toolbar-button click:
//   1. inject a small extractor into the active tab and read the PDF url out of
//      the *rendered* DOM (citation_pdf_url meta, venue rules, pdf links);
//   2. fetch those bytes here in the service worker, with the browser's cookies;
//   3. hand {url, pdfUrl, title, pdfBase64} to the native host "com.sk.readpaper",
//      which decodes the bytes, resolves a dest name and kicks off a detached job.
//
// WHY THE BROWSER DOES THE FETCH
// Deriving a PDF url is usually easy; getting the bytes is not. Measured
// 2026-08-13 on openreview.net/pdf?id=YicbFdNTTy (ViT, public): plain curl gets
// HTTP 403, and even with full Chrome-like headers the body is a Cloudflare
// Turnstile page, not a PDF. This service worker shares Chrome's cookie jar, so
// a tab that already cleared Turnstile (or is signed in) can read the file.
// The same mechanism covers publisher sites behind an institutional login.
//
// Everything is best effort: if extraction or the fetch fails we still send the
// tab url alone, and the host falls back to scraping + curl in the worker.

const HOST = "com.sk.readpaper";
const MAX_PDF_BYTES = 25 * 1024 * 1024;   // above this, ship the url, not bytes
const FETCH_TIMEOUT_MS = 60_000;

function setBadge(text, color, title) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setTitle({ title: title || "readpaper: explain this paper" });
}

// ---------------------------------------------------------------------------
// Injected into the page (runs in the tab, not here). Must be self-contained.
// ---------------------------------------------------------------------------
function extractPdfInfo() {
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return null; } };
  const metaOf = (n) => {
    const el = document.querySelector(`meta[name="${n}" i], meta[property="${n}" i]`);
    return el ? el.getAttribute("content") : null;
  };

  const out = {
    pageUrl: location.href,
    title: metaOf("citation_title") || document.title || "",
    arxivId: metaOf("citation_arxiv_id") || "",
    pdfUrl: null,
    how: null,
  };

  // 0. the tab already *is* a pdf
  if (document.contentType === "application/pdf" ||
      /\.pdf($|[?#])/i.test(location.href)) {
    out.pdfUrl = location.href; out.how = "self"; return out;
  }

  // 1. OpenReview: forum?id=X -> pdf?id=X (the page is a SPA, so meta tags are
  //    unreliable, but this rewrite is exact).
  if (/(^|\.)openreview\.net$/i.test(location.hostname)) {
    const id = new URLSearchParams(location.search).get("id");
    if (id) { out.pdfUrl = `https://openreview.net/pdf?id=${id}`; out.how = "openreview"; return out; }
  }

  // 2. Highwire Press tag — PMLR (incl. the new volumes whose PDF lives on
  //    raw.githubusercontent.com), JMLR, NeurIPS, ACL, bioRxiv, Springer, ...
  const cite = metaOf("citation_pdf_url");
  if (cite) { out.pdfUrl = abs(cite); out.how = "citation_pdf_url"; return out; }

  // 3. <link rel="alternate" type="application/pdf">
  const link = document.querySelector('link[type="application/pdf" i]');
  if (link && link.getAttribute("href")) {
    out.pdfUrl = abs(link.getAttribute("href")); out.how = "link-alternate"; return out;
  }

  // 4. first plausible <a href="*.pdf"> (skip supplementary / reviews)
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute("href") || "";
    if (!/\.pdf($|[?#])/i.test(href)) continue;
    if (/supp|appendix|-si\b|review|bib/i.test(href)) continue;
    out.pdfUrl = abs(href); out.how = "anchor"; return out;
  }
  return out;
}

// ---------------------------------------------------------------------------

async function extractFromTab(tab) {
  if (!tab || !tab.id) return null;
  // chrome:// and the built-in PDF viewer reject injection; not fatal.
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractPdfInfo,
    });
    return (res && res.result) || null;
  } catch (e) {
    console.warn("[readpaper] executeScript failed:", e.message);
    return null;
  }
}

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CHUNK = 0x8000;           // chunked: fromCharCode.apply blows the stack
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

/** Fetch the PDF with the browser's cookies. Returns {base64} or {error}. */
async function fetchPdf(pdfUrl) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetch(pdfUrl, {
      credentials: "include",
      redirect: "follow",
      signal: ctl.signal,
      headers: { "Accept": "application/pdf,*/*" },
    });
    if (!resp.ok) return { error: `HTTP ${resp.status}` };
    const len = Number(resp.headers.get("content-length") || 0);
    if (len > MAX_PDF_BYTES) return { error: `too large (${len} bytes)` };
    const buf = await resp.arrayBuffer();
    if (buf.byteLength > MAX_PDF_BYTES) return { error: `too large (${buf.byteLength} bytes)` };
    // A bot wall / login page answers 200 with HTML — catch it here rather than
    // shipping HTML that the pipeline would happily treat as a paper.
    const head = new TextDecoder().decode(new Uint8Array(buf.slice(0, 5)));
    if (head !== "%PDF-") return { error: `not a PDF (starts with ${JSON.stringify(head)})` };
    return { base64: toBase64(buf), bytes: buf.byteLength };
  } catch (e) {
    return { error: e.name === "AbortError" ? "fetch timeout" : e.message };
  } finally {
    clearTimeout(timer);
  }
}

function sendToHost(payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendNativeMessage(HOST, payload, (resp) => {
      if (chrome.runtime.lastError) resolve({ error: chrome.runtime.lastError.message });
      else resolve(resp || { error: "empty reply" });
    });
  });
}

chrome.action.onClicked.addListener(async (tab) => {
  setBadge("…", "#6f9bff", "readpaper: working…");
  const url = (tab && tab.url) || "";

  const info = await extractFromTab(tab);
  const payload = {
    url,
    pdfUrl: (info && info.pdfUrl) || "",
    title: (info && info.title) || (tab && tab.title) || "",
    arxivId: (info && info.arxivId) || "",
    how: (info && info.how) || "",
  };
  console.log("[readpaper] extracted:", payload);

  if (payload.pdfUrl) {
    const got = await fetchPdf(payload.pdfUrl);
    if (got.base64) {
      payload.pdfBase64 = got.base64;
      console.log(`[readpaper] fetched ${got.bytes} bytes in-browser`);
    } else {
      // Not fatal: the host/worker will try curl. That works for open venues
      // (PMLR/JMLR/ACL/NeurIPS) and only really fails behind a bot wall.
      console.warn("[readpaper] in-browser fetch failed:", got.error);
    }
  }

  const resp = await sendToHost(payload);
  if (resp.status === "started") {
    console.log("[readpaper] started:", resp.id, "via", resp.how, "local:", resp.local);
    setBadge("OK", "#3fae5a", `readpaper: started ${resp.id}`);
  } else {
    const why = resp.reason || resp.error || JSON.stringify(resp);
    console.error("[readpaper] failed:", why);
    setBadge("ERR", "#d9534f", `readpaper: ${why}`);
  }
  setTimeout(() => setBadge(""), 6000);
});
