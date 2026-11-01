// End-to-end check of pos_widget.js (しおり) through headless Chrome + CDP.
// Needs an ask_server on a scratch root with one explainer copied in, e.g.
//
//   ASK_TOKEN=testtoken python3 tools/readpaper/host/ask_server.py --port 8799 --bind 127.0.0.1 //       --root /tmp/posroot --config /tmp/pos.env &
//   PORT=8799 DBG=9350 FILE=<copied>.html UDD=/tmp/pos-udd //       CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" //       node tools/readpaper/host/test_pos_e2e.mjs
//
// Headless gotchas baked in (2026-09-09): frames/timers only advance while an
// evaluate is pending (hence pw()), and the explainers' scroll-behavior:smooth
// turns a plain scrollTo into an animation (hence behavior:'instant').
// No third-party client: Node 22's global WebSocket talks CDP directly.
const PORT = process.env.PORT, FILE = process.env.FILE, CHROME = process.env.CHROME;
const { spawn } = await import("node:child_process");
const base = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=" + process.env.DBG, "--no-first-run",
  "--user-data-dir=" + process.env.UDD, "--window-size=800,900", "about:blank"], { stdio: "ignore" });
process.on("exit", () => { try { chrome.kill(); } catch {} });
let targets;
for (let i = 0; i < 40; i++) { try { targets = await (await fetch("http://127.0.0.1:" + process.env.DBG + "/json")).json(); break; } catch { await sleep(250); } }
const page = targets.find(t => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const j = JSON.parse(m.data); if (j.method === "Runtime.exceptionThrown") console.log("PAGE EXC", JSON.stringify(j.params.exceptionDetails).slice(0, 600)); if (j.method === "Runtime.consoleAPICalled") console.log("PAGE LOG", JSON.stringify(j.params.args.map(a => a.value ?? a.description)).slice(0, 300)); if (j.id && pending.has(j.id)) { pending.get(j.id)(j); pending.delete(j.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); return r.result?.result?.value ?? r; };
const pw = (ms) => ev(`new Promise(r => setTimeout(r, ${ms}))`);   // in-page wait: headless only runs frames/timers while an evaluate is pending
const nav = async (url, settle) => { await send("Page.navigate", { url: "about:blank" }); await sleep(300); await send("Page.navigate", { url }); await sleep(settle ? 4500 : 2500); };
const results = [];
const check = (name, ok, info) => { results.push([ok ? "PASS" : "FAIL", name, info]); console.log(ok ? "PASS" : "FAIL", name, info ?? ""); };

await send("Page.enable");
await send("Runtime.enable");
await fetch(`${base}/api/pos`, { method: "POST", body: JSON.stringify({ file: FILE, token: "testtoken", clear: true }) });
await nav(`${base}/${FILE}`);
check("widget injected", await ev("!!document.getElementById('readpaper-pos-host')"));
check("starts at top, no toast", (await ev("window.scrollY")) === 0 && !(await ev("document.getElementById('readpaper-pos-host').shadowRoot.querySelector('.toast').classList.contains('show')")));
const maxY = await ev("document.documentElement.scrollHeight - document.documentElement.clientHeight");
const target = Math.round(maxY * 0.6);
await sleep(2000);
await ev(`window.scrollTo({top: ${target}, behavior: 'instant'}); 'ok'`);
await pw(1800);   // debounce 1 s + fetch
const rec1 = await ev(`fetch('/api/pos?file=${FILE}',{cache:'no-store'}).then(r=>r.json())`);
check("saved after scroll", !!rec1.updated_at && rec1.anchor && Math.abs(rec1.ratio - 0.6) < 0.02, JSON.stringify(rec1));
const y1 = await ev("window.scrollY");
if (!rec1.anchor) { console.log("no anchor saved; abort"); process.exit(1); }
// reload: should come back to the same place
await nav(`${base}/${FILE}`);
const y2 = await ev("window.scrollY");
check("restored after reload", Math.abs(y2 - y1) < 20, `y1=${y1} y2=${y2}`);
check("toast shown", await ev("document.getElementById('readpaper-pos-host').shadowRoot.querySelector('.toast').classList.contains('show')"),
      await ev("document.getElementById('readpaper-pos-host').shadowRoot.querySelector('.msg').textContent"));
// narrower viewport: anchor (not raw y) must be what we restore to
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await nav(`${base}/${FILE}`);
const y3 = await ev("window.scrollY");
const anchorTopNarrow = await ev(`(function(){var sel="h1,h2,h3,h4,h5,h6,p,li,figure,table,pre,blockquote,aside,dd,dt";var l=[].filter.call(document.querySelectorAll(sel),e=>!e.closest("#readpaper-ask-host,#readpaper-mark-host,#readpaper-ink-host,#readpaper-pos-host"));var e=l[${rec1.anchor.i}];var r=e.getBoundingClientRect();return r.top+window.scrollY+${rec1.anchor.frac}*r.height;})()`);
check("narrow viewport restores to anchor element", Math.abs(y3 - anchorTopNarrow) < 20, `y3=${y3} anchorY=${Math.round(anchorTopNarrow)} (wide y was ${y1})`);
await send("Emulation.clearDeviceMetricsOverride");
// hash url: no restore
// 先頭へ clears the record
await nav(`${base}/${FILE}`);
check("re-restored before 先頭へ", (await ev("window.scrollY")) > 1000);
await ev("document.getElementById('readpaper-pos-host').shadowRoot.querySelector('.top').click(); 'ok'");
await pw(1800);
const rec2 = await ev(`fetch('/api/pos?file=${FILE}',{cache:'no-store'}).then(r=>r.json())`);
check("先頭へ clears the record", (await ev("window.scrollY")) === 0 && !rec2.updated_at, JSON.stringify(rec2));
// pagehide -> sendBeacon
await ev(`window.scrollTo({top: ${Math.round(maxY * 0.3)}, behavior: 'instant'}); 'ok'`);
await sleep(100);
await send("Page.navigate", { url: "about:blank" }); await sleep(800);
const rec3 = await (await fetch(`${base}/api/pos?file=${FILE}`)).json();
check("pagehide beacon saved", !!rec3.updated_at && Math.abs(rec3.ratio - 0.3) < 0.02, JSON.stringify(rec3));
// hash url last: leaving from the top clears the record, by design
await fetch(`${base}/api/pos`, { method: "POST", body: JSON.stringify({ file: FILE, token: "testtoken", anchor: rec1.anchor, ratio: rec1.ratio, device: "test" }) });
await nav(`${base}/${FILE}#conc`);
// the page's scroll-behavior:smooth animates even the fragment scroll; wait until it settles
await ev("new Promise(r => { var last=-1, same=0, n=0; var t=setInterval(() => { var y=window.scrollY; same = (y===last) ? same+1 : 0; last=y; if ((same>=4 && y>0) || ++n>40) { clearInterval(t); r(y); } }, 100); })");
const concY = await ev("document.getElementById('conc').getBoundingClientRect().top + window.scrollY");
// headless does not always run the (smooth) fragment scroll; what matters is that the bookmark did not win
const yh = await ev("window.scrollY");
check("hash url: no bookmark jump", Math.abs(yh - y1) > 100 && (yh === 0 || Math.abs(yh - concY) < 40), `y=${yh} conc=${Math.round(concY)} bookmark=${y1}`);
ws.close(); chrome.kill();
process.exit(results.some(r => r[0] === "FAIL") ? 1 : 0);
