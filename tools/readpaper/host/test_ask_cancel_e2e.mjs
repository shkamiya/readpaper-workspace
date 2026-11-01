// End-to-end check of the in-page chat's 中止 (Ctrl+C) through headless Chrome
// + CDP. Same setup as test_pos_e2e.mjs, but the server needs a fake agent that
// just hangs (ignoring SIGTERM, so the worker's SIGKILL fallback is exercised):
//
//   printf '#!/bin/bash\ntrap "" TERM\nsleep 300 &\nwait\n' > /tmp/hang.sh; chmod +x /tmp/hang.sh
//   READPAPER_CLAUDE=/tmp/hang.sh python3 tools/readpaper/host/ask_server.py --port 8799 \
//       --bind 127.0.0.1 --project <proj with html-expl/paper.html> --config /tmp/ask.env &
//   PORT=8799 DBG=9352 FILE=paper.html UDD=/tmp/cxl-udd SHOTS=/tmp/cxlshots \
//       CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//       node tools/readpaper/host/test_ask_cancel_e2e.mjs
const PORT = process.env.PORT, FILE = process.env.FILE, CHROME = process.env.CHROME;
const SHOTS = process.env.SHOTS;
const { spawn } = await import("node:child_process");
const fs = await import("node:fs");
const base = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=" + process.env.DBG, "--no-first-run",
  "--user-data-dir=" + process.env.UDD, "--window-size=1200,900", "about:blank"], { stdio: "ignore" });
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
// Frames only advance while an evaluate is in flight (see test_pos_e2e.mjs), so wait in-page.
const pw = (ms) => ev(`new Promise(r => setTimeout(r, ${ms}))`);
const shot = async (name) => { if (!SHOTS) return; const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(`${SHOTS}/${name}.png`, Buffer.from(r.result.data, "base64")); };
let failed = 0;
const check = (name, ok, info) => { if (!ok) failed++; console.log(ok ? "PASS" : "FAIL", name, info ?? ""); };

await send("Page.enable");
await send("Runtime.enable");
await send("Page.navigate", { url: `${base}/${FILE}#ask` });
await sleep(1500); await pw(800);

const R = `document.getElementById('readpaper-ask-host').shadowRoot`;
const ui = () => ev(`(function(){var r=${R};var b=r.querySelector('.send');
  return {btn:b.textContent, stop:b.classList.contains('stop'), dis:b.disabled,
    ta:r.querySelector('textarea').value, hint:r.querySelector('.row .hint').textContent,
    log:r.querySelector('.log').textContent};})()`);

const Q = "まちがえて送った質問";
await ev(`(function(){var r=${R};r.querySelector('textarea').value=${JSON.stringify(Q)};r.querySelector('.send').click();})()`);
await pw(2000);
let s = await ui();
check("running: button turns into 中止", s.btn === "中止" && s.stop && !s.dis, JSON.stringify(s));
check("running: textarea cleared", s.ta === "", s.ta);
check("running: hint mentions Ctrl+C", s.hint.includes("Ctrl+C"), s.hint);
await shot("1-running");

// A real key event, so the document-level capture listener is what fires.
await ev(`${R}.querySelector('textarea').focus()`);
await send("Input.dispatchKeyEvent", { type: "keyDown", modifiers: 2, key: "c", code: "KeyC", windowsVirtualKeyCode: 67 });
await send("Input.dispatchKeyEvent", { type: "keyUp", modifiers: 2, key: "c", code: "KeyC", windowsVirtualKeyCode: 67 });
await pw(800);
s = await ui();
check("Ctrl+C: prompt is back in the textarea", s.ta === Q, s.ta);

// SIGTERM is ignored by the fake agent: SIGKILL after 5 s, then one 3 s poll.
let done = false;
for (let i = 0; i < 20 && !done; i++) { await pw(1000); s = await ui(); done = s.log.includes("中止しました"); }
check("cancelled bubble shown", done, s.log.slice(-80));
check("button back to 送信", s.btn === "送信" && !s.stop && !s.dis, JSON.stringify(s));
await shot("2-cancelled");

// Fix the prompt and resend: the paper must not be stuck busy.
await ev(`(function(){var r=${R};r.querySelector('textarea').value='直した質問';r.querySelector('.send').click();})()`);
await pw(1500);
s = await ui();
check("resend accepted (not 409)", s.btn === "中止" && !s.log.includes("まだ処理中"), JSON.stringify(s));
// Leave nothing running: cancel via the button this time.
await ev(`${R}.querySelector('.send').click()`);
done = false;
for (let i = 0; i < 20 && !done; i++) { await pw(1000); s = await ui(); done = s.btn === "送信"; }
check("button cancel works too", done, JSON.stringify(s));

console.log(failed ? `${failed} FAILED` : "ALL PASS");
process.exit(failed ? 1 : 0);
