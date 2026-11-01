/* Shared, opt-in reader theme. No article or reading-record writes. */
(function () {
  "use strict";
  if (window.readpaperTheme) return;
  var KEY = "readpaper-theme";
  var PALETTE_KEY = "readpaper-palette";
  // Approved reader palettes; keep current for existing saved dark/system modes.
  var palettes = {
    "current": {"name": "従来のダーク", "bg": "#14181F", "panel": "#1D2530", "text": "#E2E7EF", "muted": "#B5C0D0", "accent": "#88D8E1", "border": "#485568"},
    "graphite": {"name": "ニュートラル・グラファイト", "bg": "#1B1D20", "panel": "#25282D", "text": "#E1E3E6", "muted": "#B4BAC3", "accent": "#AFC5E5", "border": "#545D6A"},
    "soft-charcoal": {"name": "ソフト・チャコール", "bg": "#242424", "panel": "#303030", "text": "#DDDDDD", "muted": "#BBBBBB", "accent": "#B9CEDA", "border": "#646464"},
    "warm-charcoal": {"name": "ウォーム・チャコール", "bg": "#211E1A", "panel": "#2D2822", "text": "#E5DCCF", "muted": "#C3B5A3", "accent": "#E1C298", "border": "#786650"},
    "espresso": {"name": "エスプレッソ", "bg": "#1B1715", "panel": "#29221E", "text": "#E5D7CF", "muted": "#C4AEA0", "accent": "#DDB6A2", "border": "#7B5D4F"},
    "navy": {"name": "ミッドナイト・ネイビー", "bg": "#111C2B", "panel": "#1C2B3D", "text": "#DFE7F0", "muted": "#B1C0D2", "accent": "#9FC7EF", "border": "#59738F"},
    "steel": {"name": "スチール・ブルー", "bg": "#25303A", "panel": "#303D49", "text": "#E8EDF1", "muted": "#C4CFD8", "accent": "#ACD4E9", "border": "#6A879B"},
    "deep-teal": {"name": "ディープ・ティール", "bg": "#142426", "panel": "#213234", "text": "#DFE9E7", "muted": "#B5CCC8", "accent": "#A0D7CB", "border": "#5E8B86"},
    "forest": {"name": "フォレスト・グレー", "bg": "#1C241F", "panel": "#29342D", "text": "#E1E7DD", "muted": "#BBCABB", "accent": "#B8D5AA", "border": "#71816D"}
  };
  var html = document.documentElement;
  var media = window.matchMedia("(prefers-color-scheme: dark)");
  var select, paletteSelect;
  function validPalette(value) { return Object.prototype.hasOwnProperty.call(palettes, value) ? value : "current"; }
  function savedPalette() { try { return validPalette(localStorage.getItem(PALETTE_KEY)); } catch (_) { return "current"; } }
  var palette = savedPalette();
  function valid(value) { return /^(original|dark|system)$/.test(value) ? value : "original"; }
  function saved() { try { return valid(localStorage.getItem(KEY)); } catch (_) { return "original"; } }
  var choice = saved();
  var style = document.createElement("style");
  style.id = "readpaper-theme-style";
  // Do not invert the whole page: that also changes figure/plot colors and
  // makes fixed-position widgets scroll with the filtered ancestor.
  style.textContent = `
  @media screen {
    html[data-rp-theme=dark] { color-scheme:dark; background:var(--rp-bg)!important; }
    html[data-rp-theme=dark] body { background:var(--rp-bg)!important; color:var(--rp-text)!important; }
    html[data-rp-theme=dark] body :where(*:not(svg, svg *, img, picture, video, canvas, iframe, [id^="readpaper-"])) {
      color:var(--rp-text)!important; background-color:transparent!important; border-color:var(--rp-border)!important;
    }
    html[data-rp-theme=dark] :is(header,main,article,section,aside,nav,footer,div,table,pre,blockquote) {
      background-image:none!important;
    }
    html[data-rp-theme=dark] body :is(aside,pre,blockquote,details,.summary,.card,.concept,.callout,.panel,.box,.eq,.equation) {
      background-color:var(--rp-panel)!important;
    }
    html[data-rp-theme=dark] body :is(th,input,textarea,select,button) { background-color:var(--rp-panel)!important; }
    html[data-rp-theme=dark] body :is(a,a *,h2,h3,h4) { color:var(--rp-accent)!important; }
    html[data-rp-theme=dark] body :is(.meta,.sub,figcaption,caption,small) { color:var(--rp-muted)!important; }
    html[data-rp-theme=dark] body :is(.warn,.warning,.caveat) { border-color:#d9aa67!important; background-color:#30291f!important; }
    html[data-rp-theme=dark] body :is(.good,.success,.positive) { color:#92d6ac!important; }
    html[data-rp-theme=dark] body :is(.bad,.error,.negative,.katex-error) { color:#ffaca6!important; }
    html[data-rp-theme=dark] body :is(button,[role=button],select,input,textarea):focus-visible { outline:2px solid var(--rp-accent)!important; }
    html[data-rp-theme=dark] body :is(input,textarea)::placeholder { color:var(--rp-muted)!important; }
    html[data-rp-theme=dark] body :is(.seg button.on,.st.reading,.st.done) { background-color:var(--rp-panel)!important; box-shadow:inset 0 -2px var(--rp-accent)!important; }
    html[data-rp-theme=dark] body :is(.bars i,.bar i) { background-color:var(--rp-accent)!important; }
    /* Standalone diagrams keep their original palette on a light surface.
       Formula SVG and the transparent handwriting overlay are exceptions. */
    html[data-rp-theme=dark] body svg:not(svg svg, .katex svg, mjx-container svg, #readpaper-ink-svg) {
      background-color:#fff!important; color:#111!important;
    }
    html[data-rp-theme=dark] #readpaper-ink-svg [stroke="#1a1a1a"] { stroke:var(--rp-text); }
  }`;
  style.textContent += "@media screen {" + Object.keys(palettes).map(function (id) {
    var p = palettes[id];
    return 'html[data-rp-theme="dark"][data-rp-palette="' + id + '"] {' +
      ["bg", "panel", "text", "muted", "accent", "border"].map(function (key) {
        return "--rp-" + key + ":" + p[key] + ";";
      }).join("") + "}";
  }).join("\n") + "}";
  document.head.appendChild(style);

  function apply() {
    html.setAttribute("data-rp-palette", palette);
    var dark = choice === "dark" || (choice === "system" && media.matches);
    if (dark) html.setAttribute("data-rp-theme", "dark");
    else html.removeAttribute("data-rp-theme");
    if (select) select.value = choice;
    if (paletteSelect) { paletteSelect.value = palette; paletteSelect.disabled = choice === "original"; }
    var settingsHost = document.getElementById("readpaper-theme-host");
    if (settingsHost) settingsHost.toggleAttribute("data-rp-dark", dark);
    // Existing controls live in open Shadow DOM; avoid touching their content
    // or their stored data. Keep the ink color swatches unchanged.
    document.querySelectorAll('[id^="readpaper-"][id$="-host"]').forEach(function (host) {
      if (host.id === "readpaper-theme-host" || !host.shadowRoot) return;
      var root = host.shadowRoot;
      var sheet = root.querySelector("#rp-theme-shadow");
      if (!sheet) {
        sheet = document.createElement("style"); sheet.id = "rp-theme-shadow";
        sheet.textContent = '@media screen { :host([data-rp-dark]) :is(.panel,.hd,.ft,.card,.ed,.bar,.toast,textarea,input,select,button,.fab,.m,.sel) {background:var(--rp-panel)!important;color:var(--rp-text)!important;border-color:var(--rp-border)!important} :host([data-rp-dark]) :is(.who,.meta,.note) {color:var(--rp-muted)!important} }';
        root.appendChild(sheet);
      }
      host.toggleAttribute("data-rp-dark", dark);
    });
  }
  function set(value) {
    choice = valid(value);
    try { localStorage.setItem(KEY, choice); } catch (_) { /* private/storage-full mode */ }
    apply();
  }
  function setPalette(value) {
    palette = validPalette(value);
    try { localStorage.setItem(PALETTE_KEY, palette); } catch (_) { /* private/storage-full mode */ }
    apply();
  }
  window.readpaperTheme = { set: set, setPalette: setPalette };
  apply();
  if (media.addEventListener) media.addEventListener("change", apply);
  else media.addListener(apply);
  window.addEventListener("storage", function (event) {
    if (event.key === KEY || event.key === PALETTE_KEY || event.key === null) {
      choice = saved(); palette = savedPalette(); apply();
    }
  });
  function mount() {
    // Articles inherit the saved theme without exposing a persistent control.
    // Apply again now that their other widgets have created their shadow roots.
    apply();
    if (!/(?:\/|\/index\.html)$/.test(location.pathname)) return;
    var header = document.querySelector("body > header");
    if (!header || !header.querySelector(".seg")) return;
    header.setAttribute("data-rp-settings-header", "");
    var layout = document.createElement("style");
    layout.textContent = `
      [data-rp-settings-header]{display:flex!important;flex-wrap:wrap;align-items:center;gap:10px 12px}
      [data-rp-settings-header] .seg{margin:0!important;flex-shrink:0}
      @media(max-width:560px){[data-rp-settings-header] .ttl{flex:1 0 100%!important}}
    `;
    document.head.appendChild(layout);
    var host = document.createElement("div"); host.id = "readpaper-theme-host";
    var root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host{all:initial;display:block;position:relative;flex:0 0 auto;margin-left:auto;z-index:30;--surface:#202936;--ink:#eef3fa;--line:#526075;color-scheme:dark}
      :host([data-rp-dark]){--surface:var(--rp-panel);--ink:var(--rp-text);--line:var(--rp-border);color-scheme:dark}
      *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif}
      [hidden]{display:none!important}
      button,select{font-size:16px;color:var(--ink);background:var(--surface);border:1px solid var(--line);cursor:pointer}
      button{min-height:44px;border-radius:10px;padding:8px 12px}
      .trigger{box-shadow:0 2px 8px #0002}
      .panel{position:absolute;right:0;top:calc(100% + 10px);width:280px;max-width:calc(100vw - 28px);max-height:calc(100vh - 200px);max-height:calc(100dvh - 200px);overflow:auto;padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--ink);box-shadow:0 6px 24px #0003}
      .heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;font-size:16px;font-weight:600}
      .close{font-size:18px;min-width:44px;padding:4px}
      label{display:block;font-size:14px;margin:12px 0 8px}
      select{width:100%;min-height:44px;border-radius:8px;padding:8px}
      select:disabled{opacity:.55;cursor:default}
      .hint{font-size:12px;line-height:1.6;margin-top:10px;opacity:.8}
      :is(button,select):focus-visible{outline:3px solid var(--rp-accent,#52aab6);outline-offset:2px}
      @media print{:host{display:none}}
    </style>
    <button type="button" class="trigger" aria-expanded="false" aria-controls="settings-panel" aria-haspopup="dialog">⚙ 設定</button>
    <div class="panel" id="settings-panel" role="dialog" aria-labelledby="settings-title" hidden>
      <div class="heading"><span id="settings-title">表示設定</span><button type="button" class="close" aria-label="設定を閉じる">×</button></div>
      <label for="theme-choice">表示モード</label>
      <select id="theme-choice"><option value="original">元の配色</option><option value="dark">ダーク</option><option value="system">端末に合わせる</option></select>
      <label for="palette-choice">ダークの配色</label>
      <select id="palette-choice"></select>
      <div class="hint">ダーク、または端末に合わせる場合の配色です。このブラウザの一覧とすべての記事に適用されます。</div>
    </div>`;
    header.appendChild(host);
    select = root.querySelector("#theme-choice"); select.addEventListener("change", function () { set(select.value); });
    paletteSelect = root.querySelector("#palette-choice");
    Object.keys(palettes).forEach(function (id) {
      var option = document.createElement("option");
      option.value = id; option.textContent = palettes[id].name;
      paletteSelect.appendChild(option);
    });
    paletteSelect.addEventListener("change", function () { setPalette(paletteSelect.value); });
    var trigger = root.querySelector(".trigger"), panel = root.querySelector(".panel");
    function close(restoreFocus) {
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (restoreFocus) trigger.focus({ preventScroll: true });
    }
    trigger.addEventListener("click", function () {
      if (!panel.hidden) { close(true); return; }
      panel.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      select.focus({ preventScroll: true });
    });
    root.querySelector(".close").addEventListener("click", function () { close(true); });
    document.addEventListener("pointerdown", function (event) {
      if (!panel.hidden && !event.composedPath().includes(host)) close(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !panel.hidden) { event.preventDefault(); close(true); }
    });
    apply();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once:true });
  else mount();
})();
