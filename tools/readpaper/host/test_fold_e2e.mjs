// End-to-end check of fold_widget.js (用語集・補足ボックスの折りたたみ) through
// headless Chrome + CDP. Same setup as test_pos_e2e.mjs:
//
//   ASK_TOKEN=testtoken python3 tools/readpaper/host/ask_server.py --port 8799 --bind 127.0.0.1 \
//       --root /tmp/foldroot --config /tmp/fold.env &
//   PORT=8799 DBG=9351 FILES=<a>.html,<b>.html UDD=/tmp/fold-udd SHOTS=/tmp/foldshots \
//       CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//       node tools/readpaper/host/test_fold_e2e.mjs
const PORT = process.env.PORT, FILES = process.env.FILES.split(","), CHROME = process.env.CHROME;
const SHOTS = process.env.SHOTS;
const { spawn } = await import("node:child_process");
const fs = await import("node:fs");
const base = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=" + process.env.DBG, "--no-first-run",
  "--user-data-dir=" + process.env.UDD, "--window-size=1400,1000", "about:blank"], { stdio: "ignore" });
process.on("exit", () => { try { chrome.kill(); } catch {} });
let targets;
for (let i = 0; i < 40; i++) { try { targets = await (await fetch("http://127.0.0.1:" + process.env.DBG + "/json")).json(); break; } catch { await sleep(250); } }
const page = targets.find(t => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const j = JSON.parse(m.data); if (j.method === "Runtime.exceptionThrown") console.log("PAGE EXC", JSON.stringify(j.params.exceptionDetails).slice(0, 600)); if (j.id && pending.has(j.id)) { pending.get(j.id)(j); pending.delete(j.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); const v = r.result?.result; return v && "value" in v ? v.value : (v?.type === "undefined" ? undefined : r); };
const pw = (ms) => ev(`new Promise(r => setTimeout(r, ${ms}))`);
const nav = async (url) => { await send("Page.navigate", { url: "about:blank" }); await sleep(300); await send("Page.navigate", { url }); await sleep(2500); await pw(500); };
const shot = async (name) => { if (!SHOTS) return; const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(`${SHOTS}/${name}.png`, Buffer.from(r.result.data, "base64")); };
const results = [];
const check = (name, ok, info) => { results.push([ok ? "PASS" : "FAIL", name, info]); console.log(ok ? "PASS" : "FAIL", name, info ?? ""); };

await send("Page.enable");
await send("Runtime.enable");
const stats = `(function(){var b=[].slice.call(document.querySelectorAll('[data-rp-fold]'));
  var c=b.filter(x=>x.dataset.rpFoldKind==='concept'), t=b.filter(x=>x.dataset.rpFoldKind==='terms');
  return {concept:c.length, conceptClosed:c.filter(x=>x.dataset.rpFold==='closed').length,
    nohead:b.filter(x=>x.hasAttribute('data-rp-fold-nohead')).length,
    terms:t.length, termsClosed:t.filter(x=>x.dataset.rpFold==='closed').length,
    asideConcept:document.querySelectorAll('aside.concept,aside.example').length,
    katexErr:document.querySelectorAll('.katex-error').length};})()`;
for (const FILE of FILES) {
  await ev("localStorage.clear(); 'ok'");
  await nav(`${base}/${FILE}`);
  const s = await ev(stats);
  check(`${FILE}: every aside.concept folded and closed`, s.concept === s.asideConcept && s.conceptClosed === s.concept, JSON.stringify(s));
  check(`${FILE}: glossary found and closed`, s.terms === 1 && s.termsClosed === 1, JSON.stringify(s));
  await ev("scrollTo({top:0,behavior:'instant'}); 'ok'"); await pw(200); await shot(FILE.slice(0, 8) + "-desktop-top");
  // open the first concept box by clicking its label (older explainers have none)
  if (s.concept) {
  const opened = await ev(`(function(){var b=document.querySelector('[data-rp-fold-kind=concept]');var h=b.querySelector('[data-rp-fold-head]')||b;
    var before=b.getBoundingClientRect().height; h.click(); var after=b.getBoundingClientRect().height;
    b.scrollIntoView({block:'start',behavior:'instant'}); return {state:b.dataset.rpFold, before:before, after:after};})()`);
  check(`${FILE}: click label opens concept`, opened.state === "open" && opened.after > opened.before + 40, JSON.stringify(opened));
  await pw(200); await shot(FILE.slice(0, 8) + "-desktop-concept-open");
  }
  // glossary choice persists across reload
  await ev(`(function(){var b=document.querySelector('[data-rp-fold-kind=terms]');(b.querySelector('[data-rp-fold-head]')||b).click();return 1})()`);
  await nav(`${base}/${FILE}`);
  check(`${FILE}: glossary open state remembered`, await ev("document.querySelector('[data-rp-fold-kind=terms]').dataset.rpFold") === "open");
  await ev("localStorage.clear(); 'ok'");
  // a #hash pointing inside a closed concept box reveals it
  const inner = await ev(`(function(){var b=document.querySelectorAll('[data-rp-fold-kind=concept]');for(var i=0;i<b.length;i++){var e=b[i].querySelector('[id]');if(e)return e.id;}
    for(var i=0;i<b.length;i++){if(b[i].id)return b[i].id;} return null;})()`);
  if (inner) {
    await nav(`${base}/${FILE}#${inner}`);
    check(`${FILE}: #${inner} reveals its box`, await ev(`(function(){var e=document.getElementById(${JSON.stringify(inner)});return e.getClientRects().length>0 && !e.closest('[data-rp-fold=closed] > :not([data-rp-fold-head])');})()`));
  }
  // phone width: glossary sits on top, must be a single line when closed
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await nav(`${base}/${FILE}`);
  const h = await ev("document.querySelector('[data-rp-fold-kind=terms]').getBoundingClientRect().height");
  check(`${FILE}: phone glossary collapsed to a line`, h < 80, `height=${h}`);
  await ev("scrollTo({top:0,behavior:'instant'}); 'ok'"); await pw(200); await shot(FILE.slice(0, 8) + "-phone-top");
  await send("Emulation.clearDeviceMetricsOverride");
}
ws.close(); chrome.kill();
process.exit(results.some(r => r[0] === "FAIL") ? 1 : 0);
