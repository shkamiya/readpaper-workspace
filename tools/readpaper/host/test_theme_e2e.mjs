// Isolated theme UI/persistence regression test. Requires an external process-group
// watchdog. CHROME, PROFILE and SHOTS are supplied by the invoking runner.
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const theme = fs.readFileSync(new URL('./theme_widget.js', import.meta.url),'utf8');
const repo = new URL('../../../',import.meta.url);
const palettes=JSON.parse(fs.readFileSync(new URL('reports/assets/20261018-readable-palettes/palettes.json',repo)));
const ids=['current','graphite','soft-charcoal','warm-charcoal','espresso','navy','steel','deep-teal','forest'];
// Reuse actual index layout without its scripts, content, or reading records.
const index=fs.readFileSync(new URL('html-expl/index.html',repo),'utf8');
const indexCSS=index.match(/<style[^>]*>([\s\S]*?)<\/style>/i)[1];
const header=index.match(/<header>[\s\S]*?<\/header>/i)[0];
const sample='<main><h2>本文の見出し</h2><p id="text">同じ文章で、背景と文字の読みやすさを確かめます。</p><aside class="concept">補足と数式 <span class="katex"><span>x + y</span></span></aside><a href="#text">本文リンク</a><small>補助文字</small><svg width="100" height="40"><rect width="100" height="40" fill="#f00"/></svg></main>';
const shadow=`<script>var h=document.createElement('div');h.id='readpaper-ask-host';document.body.append(h);h.attachShadow({mode:'open'}).innerHTML='<style>:host{all:initial}.panel{background:#fff;color:#111;border:1px solid #ddd}.meta{color:#888}</style><div class="panel">チャット表示 <span class="meta">注記</span><button>送信</button></div>';</script>`;
let unknown=[];
const server=createServer((req,res)=>{
 const path=new URL(req.url,'http://localhost').pathname;
 if(path==='/theme.js'){res.setHeader('Content-Type','application/javascript');res.end(theme);return;}
 if(!['/','/index.html','/article.html','/blocked.html','/favicon.ico'].includes(path)){unknown.push(path);res.writeHead(404);res.end();return;}
 if(path==='/favicon.ico'){res.writeHead(204);res.end();return;}
 const isIndex=path==='/'||path==='/index.html';
 const block=path==='/blocked.html'?`<script>Object.defineProperty(window,'localStorage',{get(){throw new Error('blocked')}});</script>`:'';
 res.setHeader('Content-Type','text/html; charset=utf-8');
 res.end(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${block}<script src="/theme.js"></script><style>${isIndex?indexCSS:'body{background:#fff;color:#111;padding:24px;font:18px sans-serif}'} body{min-height:1800px}</style></head><body>${isIndex?header:''}${sample}${shadow}</body></html>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const chrome=spawn(process.env.CHROME,['--headless','--no-sandbox','--remote-debugging-port=0','--user-data-dir='+process.env.PROFILE,'about:blank'],{stdio:['ignore','ignore','inherit']});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let sockets=[],checks=0,errors=[];
const check=(name,v)=>{assert.ok(v,name);console.log('PASS',name);checks++;};
try{
 let port;
 for(let i=0;i<100;i++){try{port=fs.readFileSync(process.env.PROFILE+'/DevToolsActivePort','utf8').split('\n')[0];break;}catch{await pause(100);}}
 assert.ok(port);
 async function connect(target){
  const ws=new WebSocket(target.webSocketDebuggerUrl);sockets.push(ws);await new Promise(r=>ws.onopen=r);
  let id=0;const pending=new Map();
  ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);}};
  const send=(method,params={})=>new Promise(r=>{pending.set(++id,r);ws.send(JSON.stringify({id,method,params}));});
  const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert.ok(!r.error,JSON.stringify(r));assert.ok(!r.result.exceptionDetails,JSON.stringify(r.result));return r.result.result.value;};
  await send('Page.enable');await send('Runtime.enable');return {send,ev};
 }
 const tabs=await(await fetch('http://127.0.0.1:'+port+'/json')).json();
 const {send,ev}=await connect(tabs.find(t=>t.type==='page'));
 const nav=async path=>{await send('Page.navigate',{url:'about:blank'});await pause(30);await send('Page.navigate',{url:base+path});for(let i=0;i<80;i++){if(await ev('document.readyState==="complete"&&!!window.readpaperTheme'))return;await pause(50);}throw Error('navigation timed out');};
 const choose=async (id,value)=>ev(`(()=>{const s=document.getElementById('readpaper-theme-host').shadowRoot.querySelector(${JSON.stringify('#'+id)});s.value=${JSON.stringify(value)};s.dispatchEvent(new Event('change'));})()`);
 const shot=async name=>{const r=await send('Page.captureScreenshot',{format:'png'}),buf=Buffer.from(r.result.data,'base64');assert.ok(buf.length>5000);fs.writeFileSync(process.env.SHOTS+'/'+name+'.png',buf);};
 await send('Emulation.setDeviceMetricsOverride',{width:834,height:1194,deviceScaleFactor:1,mobile:false});
 await nav('/');
 check('initial original mode and disabled palette select',await ev(`!document.documentElement.hasAttribute('data-rp-theme')&&document.getElementById('readpaper-theme-host').shadowRoot.querySelector('#palette-choice').disabled`));
 check('only eight adopted palettes plus legacy',JSON.stringify(await ev(`[...document.getElementById('readpaper-theme-host').shadowRoot.querySelector('#palette-choice').options].map(x=>x.value)`))===JSON.stringify(ids));
 await ev(`document.getElementById('readpaper-theme-host').shadowRoot.querySelector('.trigger').click()`);
 await choose('theme-choice','dark');
 for(const id of ids){
  await choose('palette-choice',id);
  const p=palettes.find(x=>x.id===id),expected=[p.bg,p.text,p.panel,p.muted,p.accent,p.border,p.panel,p.text].map(h=>'rgb('+[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)).join(', ')+')');
  const actual=await ev(`(()=>{const css=(e,k)=>getComputedStyle(e)[k],h=document.getElementById('readpaper-theme-host'),chat=document.getElementById('readpaper-ask-host').shadowRoot.querySelector('.panel');return [css(document.body,'backgroundColor'),css(document.getElementById('text'),'color'),css(document.querySelector('.concept'),'backgroundColor'),css(document.querySelector('small'),'color'),css(document.querySelector('h2'),'color'),css(document.querySelector('.concept'),'borderTopColor'),css(h.shadowRoot.querySelector('.panel'),'backgroundColor'),css(chat,'color')]})()`);
  assert.deepEqual(actual,expected,id);check('exact palette and settings/shadow colors: '+id,true);
 }
 await choose('palette-choice','warm-charcoal');
 await shot('ipad-settings');
 await nav('/article.html');
 check('article inherits saved palette without a settings button',await ev(`!document.getElementById('readpaper-theme-host')&&document.documentElement.dataset.rpPalette==='warm-charcoal'&&document.documentElement.dataset.rpTheme==='dark'`));
 check('figures keep original colors with no inversion',await ev(`getComputedStyle(document.querySelector('rect')).fill==='rgb(255, 0, 0)'&&getComputedStyle(document.body).filter==='none'&&getComputedStyle(document.querySelector('svg')).backgroundColor==='rgb(255, 255, 255)'`));
 await send('Emulation.setEmulatedMedia',{media:'print'});
 check('print restores article colors',await ev(`getComputedStyle(document.body).backgroundColor==='rgb(255, 255, 255)'&&getComputedStyle(document.body).color==='rgb(17, 17, 17)'`));
 await send('Emulation.setEmulatedMedia',{media:'screen',features:[{name:'prefers-color-scheme',value:'light'}]});
 await nav('/index.html');
 check('reload restores mode and palette selectors',await ev(`document.getElementById('readpaper-theme-host').shadowRoot.querySelector('#theme-choice').value==='dark'&&document.getElementById('readpaper-theme-host').shadowRoot.querySelector('#palette-choice').value==='warm-charcoal'`));
 await choose('theme-choice','system');
 check('system light restores original',await ev(`!document.documentElement.hasAttribute('data-rp-theme')`));
 await send('Emulation.setEmulatedMedia',{media:'screen',features:[{name:'prefers-color-scheme',value:'dark'}]});await pause(150);
 check('system dark uses saved warm palette',await ev(`document.documentElement.dataset.rpTheme==='dark'&&getComputedStyle(document.body).backgroundColor==='rgb(33, 30, 26)'`));
 const other=await(await fetch('http://127.0.0.1:'+port+'/json/new?'+encodeURIComponent(base+'/article.html'),{method:'PUT'})).json();
 const tab2=await connect(other);for(let i=0;i<50;i++){if(await tab2.ev('!!window.readpaperTheme'))break;await pause(50);}
 await tab2.ev(`readpaperTheme.setPalette('deep-teal');readpaperTheme.set('dark')`);await pause(100);
 check('real cross-tab storage updates palette and mode',await ev(`document.getElementById('readpaper-theme-host').shadowRoot.querySelector('#palette-choice').value==='deep-teal'&&document.getElementById('readpaper-theme-host').shadowRoot.querySelector('#theme-choice').value==='dark'&&getComputedStyle(document.body).backgroundColor==='rgb(20, 36, 38)'`));
 for(const [w,h] of [[390,844],[834,1194],[1194,834]]){
  await send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:false});
  await ev(`(()=>{scrollTo(0,0);const r=document.getElementById('readpaper-theme-host').shadowRoot;if(r.querySelector('.panel').hidden)r.querySelector('.trigger').click()})()`);
  check('settings beside filters and within viewport '+w,await ev(`(()=>{const host=document.getElementById('readpaper-theme-host'),r=host.shadowRoot.querySelector('.panel').getBoundingClientRect(),seg=document.querySelector('.seg').getBoundingClientRect(),hr=host.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&hr.left>=seg.right-1&&document.documentElement.scrollWidth<=innerWidth})()`));
  if(w===390)await shot('phone-settings');
  await ev(`scrollTo(0,600)`);
  check('settings scroll away '+w,await ev(`document.getElementById('readpaper-theme-host').getBoundingClientRect().bottom<0`));
 }
 await ev(`scrollTo(0,0);document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
 check('Escape closes panel',await ev(`document.getElementById('readpaper-theme-host').shadowRoot.querySelector('.panel').hidden`));
 await ev(`localStorage.removeItem('readpaper-palette');localStorage.setItem('readpaper-theme','dark')`);await nav('/');
 check('old dark setting keeps legacy colors',await ev(`document.documentElement.dataset.rpPalette==='current'&&getComputedStyle(document.body).backgroundColor==='rgb(20, 24, 31)'&&getComputedStyle(document.querySelector('.concept')).borderTopColor==='rgb(72, 85, 104)'`));
 await ev(`localStorage.setItem('readpaper-palette','__proto__');localStorage.setItem('readpaper-theme','invalid')`);await nav('/');
 check('invalid stored values fall back safely',await ev(`document.documentElement.dataset.rpPalette==='current'&&!document.documentElement.hasAttribute('data-rp-theme')`));
 await nav('/blocked.html');await ev(`readpaperTheme.set('dark');readpaperTheme.setPalette('forest')`);
 check('storage unavailable still allows switching',await ev(`getComputedStyle(document.body).backgroundColor==='rgb(28, 36, 31)'`));
 check('no page JavaScript exceptions',errors.length===0);check('no reading-data or API requests',unknown.length===0);
 console.log('ALL '+checks+' CHECKS PASSED');
}finally{
 sockets.forEach(s=>s.close());server.close();
 if(chrome.exitCode===null&&chrome.signalCode===null){chrome.kill('SIGTERM');await new Promise(r=>chrome.once('exit',r));}
}
