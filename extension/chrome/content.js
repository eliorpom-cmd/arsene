// Arsène content script (isolated world): protects what goes to the chat, and shows the
// real names in the conversation without ever handing them to the site. The site's own
// scripts share the DOM: a real name written into its nodes could be read and sent back
// (analytics, error reports, session replay). So each block that holds a surrogate gets a
// closed shadow root: the browser renders our copy of the block, with the real names, in
// the normal flow of the page, while the block's own nodes (all the site can read) keep
// the surrogates.
(() => {
  const DOSSIER = "navigateur";
  // Per site, from the toolbar popup: on unless paused there (claude.ai/code is never
  // touched: excluded in the manifest). Paused: no paste, send, copy or display is touched.
  const SITE = location.hostname;
  let on = false;
  chrome.storage.local.get({ sites: {} }, ({ sites }) => { on = sites[SITE] !== false; if (on) { collect(document.body || document.documentElement); schedule(); } });
  chrome.storage.onChanged.addListener((c) => { if (c.sites) on = c.sites.newValue?.[SITE] !== false; });
  chrome.runtime.onMessage.addListener((m, _sender, reply) => {
    if (m.op === "reload") location.reload();
    if (m.op === "aiView") { setAiView(m.on); reply({ aiView }); }
    if (m.op === "summary") { summary().then(reply); return true; }
    if (m.op === "toast" && typeof m.text === "string") toast(m.text);   // a download restored (background)
  });
  // DESIGN.md tokens: real = Riso Sunflower, double = Riso Blue
  const T = {
    light: { ink: "#16181D", ink2: "#555A63", ink3: "#8C9098", surface: "#FBFBF8", line: "#DDDDD6",
             page: "#F3F3EF", surface2: "#E9E9E4", ok: "#00AA93", thumb: "#FBFBF8",
             real: "#FFB511", realTint: "#FFE7B0", double: "#0078BF", doubleTint: "#D3E6F3" },
    dark: { ink: "#F3F3EF", ink2: "#A7ABB2", ink3: "#6E737B", surface: "#1A1C21", line: "#2E3138",
            page: "#111216", surface2: "#23262C", ok: "#2CC2AB", thumb: "#2E3138",   // thumb: lighter than its track
            real: "#FFB511", realTint: "#4A3A12", double: "#3A9BD9", doubleTint: "#15324A" },
  };
  const FONT = '"Momo Trust Sans", -apple-system, system-ui, sans-serif';

  // Momo Trust Sans (OFL) for Arsène's own surfaces (bubble, toasts, review); the names in the
  // thread keep the site's font.
  (async () => {
    try {
      for (const f of ["latin", "latin-ext"]) {
        const buf = await (await fetch(chrome.runtime.getURL(`fonts/momo-trust-sans-${f}-wght-normal.woff2`))).arrayBuffer();
        document.fonts.add(await new FontFace("Momo Trust Sans", buf, { weight: "200 800" }).load());
      }
      // identifiers in the review sheet: 0/O and 1/l/I never confused
      const mono = await (await fetch(chrome.runtime.getURL("fonts/atkinson-hyperlegible-mono-latin-wght-normal.woff2"))).arrayBuffer();
      document.fonts.add(await new FontFace("Atkinson Hyperlegible Mono", mono, { weight: "200 800" }).load());
    } catch {}
  })();

  // ---------- characters (DESIGN.md « Personnage », port of design/peeps-gen/avatar.py) ----------
  // Open Peeps (CC0) drawn by DiceBear, bundled in peeps.js: no name ever leaves the Mac.
  // Sex from the first name (engine, INSEE); everything else from a SHA-256 of the full
  // real name, never from its origin; one flat skin for everyone.
  const P = {
    F: ["bangs", "bangs2", "bun", "bun2", "buns", "long", "longBangs", "longCurly", "medium1", "medium2", "medium3",
        "mediumBangs", "mediumBangs2", "mediumStraight", "twists", "longAfro", "bantuKnots"],
    M: ["short1", "short2", "short3", "short4", "short5", "flatTop", "pomp", "shaved1", "shaved2", "noHair1", "noHair2",
        "afro", "dreads1", "cornrows", "mohawk", "hatBeanie"],
    oldF: ["grayBun", "grayMedium"], oldM: ["grayShort", "noHair3"],
    faces: ["calm", "smile", "cute", "cheeky", "lovingGrin1", "serious"],
    beards: ["chin", "full", "full2", "goatee1", "moustache1", "moustache3"],
    glasses: ["glasses", "glasses2", "glasses3", "glasses4"],
    clothes: ["e78276", "78e185", "e279c7", "8c9098"],   // never yellow or blue
  };
  const faces = new Map();   // real full name -> Promise<{ real: svg, double: svg }>
  const faces_ = faces;      // same map, for code where "faces" names a DOM element
  function faceOf(person) {
    if (faces.has(person.who)) return faces.get(person.who);
    const job = (async () => {
      const h = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(person.who)));
      const pick = (i, seq) => seq[h[i] % seq.length];
      const old = h[0] % 6 === 0;
      const heads = person.female ? (old ? P.oldF : P.F) : (old ? P.oldM : P.M);
      const base = { seed: person.who, head: [pick(1, heads)], face: [pick(2, P.faces)], skinColor: ["f7f6f3"],
        clothingColor: [pick(3, P.clothes)], headContrastColor: [old ? "8c9098" : "16181d"],
        maskProbability: 0, facialHairProbability: 0, accessoriesProbability: 0 };
      if (!person.female && h[4] % 3 === 0) Object.assign(base, { facialHair: [pick(5, P.beards)], facialHairProbability: 100 });
      const real = { ...base }, dbl = { ...base, accessories: ["sunglasses"], accessoriesProbability: 100 };
      if (h[6] % 4 === 0) Object.assign(real, { accessories: [pick(7, P.glasses)], accessoriesProbability: 100 });
      const { createAvatar, openPeeps } = globalThis.ArsenePeeps;
      // centred like design/peeps-gen/center.cjs (mean of the per-face offsets)
      const svg = (o) => createAvatar(openPeeps, o).toString().replace(/<metadata[\s\S]*?<\/metadata>/, "")
        .replace(/(<svg[^>]*>)([\s\S]*)<\/svg>$/, '$1<g transform="translate(352 282) scale(1.1176) translate(-381 -289)">$2</g></svg>');
      return { real: svg(real), double: svg(dbl) };
    })();
    faces.set(person.who, job);
    return job;
  }
  const parsed = new Map();
  function disc(svg, cls) {       // a character on its disc, sized by CSS
    if (!parsed.has(svg)) parsed.set(svg, new DOMParser().parseFromString(svg, "image/svg+xml").documentElement);
    const d = document.createElement("span");
    d.className = `disc ${cls}`;
    d.append(document.importNode(parsed.get(svg), true));
    return d;
  }
  const discCss = (t) => `
    .disc { display: inline-block; border-radius: 50%; overflow: hidden; flex: none; }
    .disc svg { width: 100%; height: 100%; display: block; }
    .disc.real { background: ${t.real}; } .disc.double { background: ${t.double}; }`;

  // A slow answer means the engine is starting (the host starts it): say so, once.
  let slowTimer, slowShown = false;
  const ask = (op, texts, extra = {}) => new Promise((resolve) => {
    clearTimeout(slowTimer);
    slowTimer = setTimeout(() => { if (!slowShown) { slowShown = true; toast("Arsène démarre, un instant…"); } }, 2000);
    const done = (r) => { clearTimeout(slowTimer); if (slowShown) { slowShown = false; layer.querySelector(".toast")?.remove(); } resolve(r); };
    try {
      chrome.runtime.sendMessage({ op, dossier: DOSSIER, texts, ...extra }, (r) =>
        done(r || { error: chrome.runtime.lastError?.message || "pas de réponse" }));
    } catch (e) { done({ error: String(e) }); }
  });

  // Page theme from the luminance of its background (ChatGPT themes by class, not media query).
  function theme() {
    const c = getComputedStyle(document.body || document.documentElement).backgroundColor.match(/\d+(\.\d+)?/g);
    if (!c || (c.length > 3 && +c[3] === 0)) return matchMedia("(prefers-color-scheme: dark)").matches ? T.dark : T.light;
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) < 128 ? T.dark : T.light;
  }

  // ---------- top layer: toasts, hover bubble, paste animation ----------
  const top = document.createElement("arsene-layer");
  const layer = top.attachShadow({ mode: "closed" });
  // Isolated from the site: `all: initial` on the host cuts every inherited setting the site
  // puts on <html> (letter-spacing, font-variation-settings, color-scheme…), and inline
  // !important beats any rule of the site that would match the element itself.
  top.style.cssText = "all:initial!important;display:block!important;position:fixed!important;inset:0!important;"
    + "pointer-events:none!important;z-index:2147483647!important;contain:layout style!important;";
  const topStyle = document.createElement("style");
  layer.append(topStyle);
  const mount = () => { if (!top.isConnected) document.documentElement.append(top); };
  function styleTop() {
    const t = theme();
    topStyle.textContent = `
      :host { all: initial; }
      *, *::before, *::after { box-sizing: border-box; margin: 0; letter-spacing: normal; word-spacing: normal; text-transform: none;
        text-indent: 0; text-align: left; font-feature-settings: normal; font-variation-settings: normal; font-kerning: auto;
        -webkit-font-smoothing: antialiased; -webkit-text-fill-color: currentColor; text-shadow: none; }
      :host > * { color-scheme: ${t === T.dark ? "dark" : "light"}; }
      .toast button { padding: 0; border: 0; background: none; color: #F3F3EF; font: 600 14px/1.4 ${FONT};
                      text-decoration: underline 1px; text-underline-offset: 3px; cursor: pointer; }
      .toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); width: max-content; max-width: 520px; pointer-events: auto;
               display: flex; align-items: baseline; gap: 12px; background: #16181D; color: #F3F3EF; font: 500 14px/1.4 ${FONT};
               padding: 12px 16px; border-radius: 14px; box-shadow: 0 18px 40px -22px rgba(20,20,30,.6), 0 0 0 1px rgba(255,255,255,.06);
               animation: rise 220ms cubic-bezier(.2,.8,.2,1); }
      .toast button { flex: none; }
      .bubble { position: fixed; transform: translate(-50%, calc(-100% - 10px)); display: flex; align-items: center;
                gap: 8px; background: ${t.surface}; color: ${t.ink}; border: 1px solid ${t.line};
                font: 400 14px/1.35 ${FONT}; padding: 8px 12px 8px 8px; border-radius: 14px;
                box-shadow: 0 18px 40px -22px rgba(20,20,30,.35); white-space: nowrap;
                opacity: 0; transition: opacity 180ms ease-out, transform 180ms ease-out; }
      .bubble.on { opacity: 1; transform: translate(-50%, calc(-100% - 8px)); }
      .bubble .real-name { color: ${t.ink2}; }
      .bubble .arrow { color: ${t.ink3}; margin: 0 2px; }
      .bubble b { font-weight: 600; }
      .bubble .disc { width: 24px; height: 24px; }
      ${discCss(t)}
      .chip { position: fixed; display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 12px 0 5px;
              background: ${t.surface}; color: ${t.ink}; border: 1px solid ${t.line}; border-radius: 999px;
              font: 600 13px/1 ${FONT}; box-shadow: 0 10px 24px -14px rgba(20,20,30,.45); pointer-events: auto;
              cursor: pointer; opacity: 0; transform: translateY(6px); transition: opacity 220ms ease-out, transform 220ms ease-out; }
      .chip.on { opacity: 1; transform: none; }
      .chip.bump { animation: bump 320ms cubic-bezier(.2,.8,.2,1); }
      .chip.none { padding-left: 12px; color: ${t.ink2}; font-weight: 500; }
      .chip .faces { display: flex; }
      .chip .faces .disc { width: 22px; height: 22px; margin-left: -7px; box-shadow: 0 0 0 2px ${t.surface}; }
      .chip .faces .disc:first-child { margin-left: 0; }
      .chip .ai { width: 8px; height: 8px; border-radius: 50%; background: ${t.double}; }
      .sheet { position: fixed; box-sizing: border-box; display: flex; flex-direction: column; overflow: hidden; background: ${t.surface}; color: ${t.ink};
               border: 1px solid ${t.line}; border-radius: 14px; box-shadow: 0 18px 40px -22px rgba(20,20,30,.35);
               font: 400 14px/1.5 ${FONT}; pointer-events: auto; opacity: 0; transform: translateY(8px);
               transition: opacity 220ms ease-out, transform 260ms cubic-bezier(.2,.8,.2,1); }
      .sheet.on { opacity: 1; transform: none; }
      .sheet:focus { outline: none; }
      .sheet button { font-family: ${FONT}; cursor: pointer; }
      .sheet button:focus-visible { outline: 2px solid ${t.ink}; outline-offset: 2px; }
      .sheet h3 { margin: 0; font: 600 18px/1.25 ${FONT}; letter-spacing: -0.01em; }
      .sheet .sub { margin: 4px 0 0; color: ${t.ink2}; }
      .seg { position: relative; display: grid; grid-template-columns: 1fr 1fr; height: 36px; padding: 3px; box-sizing: border-box;
             border-radius: 999px; background: ${t.surface2}; flex: none; }
      .seg::before { content: ""; position: absolute; top: 3px; bottom: 3px; left: 3px; width: calc(50% - 3px); border-radius: 999px;
                     background: ${t.thumb}; box-shadow: 0 1px 3px rgba(20,20,30,.14);
                     transition: transform 260ms cubic-bezier(.2,.8,.2,1); }
      .seg.right::before { transform: translateX(100%); }
      .seg .tab { position: relative; display: flex; align-items: center; justify-content: center; gap: 8px; border: 0;
                  background: none; border-radius: 999px; color: ${t.ink2}; font-size: 14px; font-weight: 600;
                  transition: color 180ms ease-out; }
      .seg .tab[aria-selected="true"] { color: ${t.ink}; }
      .seg .tab i { width: 8px; height: 8px; border-radius: 50%; }
      .seg .yours i { background: ${t.real}; } .seg .ai i { background: ${t.double}; }
      .doc { background: ${t.page}; border-radius: 12px; padding: 14px 16px; font: 400 15px/1.75 ${FONT}; color: ${t.ink};
             max-height: 240px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; flex: none; }
      .doc.swap { animation: swap 200ms ease-out; }
      .doc.swap.ai .disc svg { animation: drop 450ms cubic-bezier(.3,1.3,.5,1) both; }
      .name { font-weight: 600; padding: 0.05em 0.5em 0.05em 0.18em; border-radius: 999px; white-space: nowrap;
              -webkit-box-decoration-break: clone; box-decoration-break: clone; }
      .name.real { background: ${t.realTint}; } .name.double { background: ${t.doubleTint}; }
      .name .disc { width: 1.15em; height: 1.15em; vertical-align: -0.22em; margin-right: 0.3em; }
      .value { text-decoration: underline 2px; text-underline-offset: 3px; text-decoration-skip-ink: none; }
      .value.real { text-decoration-color: ${t.real}; } .value.double { text-decoration-color: ${t.double}; }
      .doubt { background: ${t.realTint}; box-shadow: inset 0 -2px 0 ${t.real}; padding: 0 2px; }
      .id { font-family: "Atkinson Hyperlegible Mono", ui-monospace, monospace; font-size: 0.9em; }
      .note { margin: -8px 0 0; font: 500 12px/1.35 ${FONT}; color: ${t.ink3}; }
      .doubts .label { margin: 0 0 4px; font: 600 12px/1.35 ${FONT}; color: ${t.ink3}; }
      .doubts .row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0;
                     border-top: 1px solid ${t.line}; }
      .doubts .what { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-width: 0; }
      .doubts .what .doubt { font-size: 15px; line-height: 1.5; overflow-wrap: anywhere; }
      .doubts .why { font: 500 12px/1.35 ${FONT}; color: ${t.ink3}; }
      .pick { display: flex; padding: 3px; border-radius: 999px; background: ${t.surface2}; flex: none; }
      .pick button { height: 30px; padding: 0 12px; border: 0; border-radius: 999px; background: none; color: ${t.ink2};
                     font-size: 13px; font-weight: 600; transition: background-color 180ms ease-out, color 180ms ease-out; }
      .pick button[aria-pressed="true"] { background: ${t.thumb}; color: ${t.ink}; box-shadow: 0 1px 3px rgba(20,20,30,.14); }
      .sheet .body { display: flex; flex-direction: column; gap: 16px; padding: 20px 20px 16px; overflow: auto;
                     overscroll-behavior: contain; min-height: 0; }
      .foot { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; padding: 4px 20px 20px; flex: none;
              border-top: 1px solid transparent; transition: border-color 180ms ease-out; }
      .foot.ruled { padding-top: 16px; border-top-color: ${t.line}; }
      .foot .go { height: 44px; padding: 0 22px; border: 0; border-radius: 999px; background: ${t.ink}; color: ${t.page};
                  font-size: 15px; font-weight: 600; transition: opacity 180ms ease-out; }
      .foot .go:hover { opacity: .9; } .foot .go:active { opacity: .8; }
      .foot .cancel { height: 44px; padding: 0 8px; border: 0; background: none; color: ${t.ink}; font-size: 15px; font-weight: 600; }
      .foot .cancel:hover { text-decoration: underline 1px; text-underline-offset: 3px; }
      .foot .safe { margin-left: auto; display: flex; align-items: center; gap: 8px; font: 500 12px/1.35 ${FONT}; color: ${t.ink2}; }
      .foot .safe i { width: 8px; height: 8px; border-radius: 50%; background: ${t.ok}; }
      @keyframes swap { from { opacity: .35; } }
      @keyframes drop { from { transform: translateY(-8px); opacity: 0; } }
      @keyframes bump { 40% { transform: scale(1.04); } }
      @keyframes rise { from { opacity: 0; transform: translate(-50%, 6px); } }
      @media (prefers-reduced-motion: reduce) { .chip, .bubble, .sheet { transition: opacity 150ms linear; } .chip.bump, .doc.swap, .doc.swap.ai .disc svg, .toast { animation: none; } .sheet { transform: none; } }`;
  }

  let toastTimer;
  function toast(text, action) {
    mount(); styleTop();
    layer.querySelector(".toast")?.remove();
    const t = document.createElement("div");
    t.className = "toast";
    t.textContent = text;
    if (action) {                               // "Voir": opens what was sent
      const b = document.createElement("button");
      b.textContent = action.label;
      b.addEventListener("click", () => { t.remove(); action.run(); });
      t.append(b);
    }
    layer.append(t);
    follow();   // above the field, not over it
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.remove(), 5000);
  }

  // ---------- composer ----------
  const composerOf = (el) => el?.closest?.('textarea, [contenteditable="true"]') || null;
  const findComposer = () => document.querySelector('#prompt-textarea, form textarea, form [contenteditable="true"], [contenteditable="true"]');
  const valueOf = (ed) => ed.tagName === "TEXTAREA" ? ed.value : ed.innerText;
  const isSend = (b) => /envoyer|send/i.test(`${b.getAttribute("aria-label") || ""} ${b.dataset.testid || ""}`);

  function replaceAll(ed, text) {
    ed.focus();
    if (ed.tagName === "TEXTAREA") ed.select();
    else {
      const r = document.createRange();
      r.selectNodeContents(ed);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    }
    // execCommand goes through the editor's own input handling (React, ProseMirror)
    document.execCommand("insertText", false, text);
  }

  // ---------- composer chip: the visible proof ----------
  // The protection is invisible by design (the thread shows real names), so after a paste a
  // chip above the field says what was replaced; a click opens the review sheet below.
  let chip, chipEd, chipItems = [], chipTimer, aiView = false;
  // The composer as the user sees it: the rounded card around the field. Not the <form>:
  // ChatGPT's is a full-width bar fixed at the bottom, and Claude has none (30/09).
  const cards = new WeakMap();
  function anchorOf(ed) {
    if (cards.get(ed)?.isConnected) return cards.get(ed);
    let card = ed;
    for (let el = ed, i = 0; el && el !== document.body && i < 10; el = el.parentElement, i++) {
      const cs = getComputedStyle(el), r = el.getBoundingClientRect();
      if (r.width > innerWidth * 0.95 || r.height > innerHeight * 0.6) break;
      const painted = cs.backgroundColor !== "rgba(0, 0, 0, 0)" || cs.boxShadow !== "none" || parseFloat(cs.borderTopWidth) > 0;
      if (painted && parseFloat(cs.borderTopLeftRadius) >= 8) { card = el; break; }
    }
    cards.set(ed, card);
    return card;
  }
  // Follows the field every frame while the chip or the sheet is up (the sheet may open
  // with no chip: a typed message, a dropped file).
  let placing = false;
  function place() {
    if (placing) return;
    placing = true;
    requestAnimationFrame(() => { placing = false; follow(); });
  }
  function follow() {
    const ed = chipEd?.isConnected ? chipEd : sheet?.state?.ed?.isConnected ? sheet.state.ed : findComposer();
    const tst = layer.querySelector(".toast");
    if (!ed) { if (tst) Object.assign(tst.style, { left: "50%", bottom: "24px" }); return; }
    if (!chip?.isConnected && !sheet && !tst) return;
    const rc = anchorOf(ed).getBoundingClientRect();
    // a field scrolled out of view: everything sits at the bottom of the window
    const top = Math.min(rc.top, innerHeight);
    const chipOn = chip?.isConnected && chip.classList.contains("on");
    if (chip?.isConnected) {       // the sheet takes its place while open
      chip.style.left = `${rc.left + 4}px`; chip.style.bottom = `${innerHeight - top + 8}px`;
      chip.style.visibility = sheet ? "hidden" : "";
    }
    if (sheet) {
      const w = Math.min(Math.max(rc.width, 380), 720, innerWidth - 16);
      sheet.style.width = `${w}px`;
      sheet.style.left = `${Math.max(8, rc.left + (rc.width - w) / 2)}px`;
      sheet.style.bottom = `${innerHeight - top + 12}px`;
      sheet.style.maxHeight = `${Math.min(top - 24, 640)}px`;
    }
    // toasts: centred on the field, above it and above the chip, never over the sheet
    if (tst) {
      tst.style.left = `${rc.left + rc.width / 2}px`;
      tst.style.maxWidth = `${Math.min(Math.max(rc.width - 32, 300), 520)}px`;
      tst.style.bottom = `${innerHeight - top + (chipOn ? 52 : 12)}px`;
      tst.hidden = !!sheet;
    }
    place();
  }
  function people(spans, text) {       // unique people and values, in order of appearance
    const seen = new Map();
    for (const [s, e, real, person] of spans) {
      const key = person ? person.who : real;
      if (!seen.has(key)) seen.set(key, person ? { real: person.who, fake: person.double, person } : { real, fake: text.slice(s, e) });
    }
    return [...seen.values()];
  }
  async function updateChip(ed, pasted) {
    chipEd = ed;
    const text = valueOf(ed);
    const r = text.trim() ? await ask("spans", [text]) : { spans: [[]] };
    if (r.error) return;
    const items = people(r.spans[0], text);
    mount(); styleTop();
    if (!items.length) {
      if (!pasted) return hideChip();
      showChip([], "Rien à masquer dans ce texte");         // proof that it looked
      clearTimeout(chipTimer); chipTimer = setTimeout(hideChip, 2500);
      return;
    }
    clearTimeout(chipTimer);
    const n = items.filter((x) => x.person).length, other = items.length - n;
    const label = [n && `${n} ${n > 1 ? "personnes protégées" : "personne protégée"}`,
                   other && `${other} ${other > 1 ? "autres valeurs" : "autre valeur"}`].filter(Boolean).join(" · ");
    const grew = items.length > chipItems.length;
    chipItems = items;
    showChip(items, label, grew);
  }
  function showChip(items, label, grew) {
    if (!chip) {
      chip = document.createElement("div"); chip.className = "chip";
      chip.addEventListener("click", () => (sheet ? closeSheet() : openSheet(chipEd, "look")));
      layer.append(chip);
    }
    const faces = document.createElement("span"); faces.className = "faces";
    for (const x of items.filter((x) => x.person).slice(0, 3)) {
      const fc = faces_.get(x.person.who)?.done;
      if (fc) faces.append(disc(fc.double, "double"));
      else faceOf(x.person).then((f) => { faces_.get(x.person.who).done = f; showChip(chipItems, label); });
    }
    chip.classList.toggle("none", !items.length);
    chip.replaceChildren(...(faces.childNodes.length ? [faces] : items.length ? [Object.assign(document.createElement("span"), { className: "ai" })] : []), label);
    requestAnimationFrame(() => chip.classList.add("on"));
    if (grew) { chip.classList.remove("bump"); void chip.offsetWidth; chip.classList.add("bump"); }
    place();
  }
  function hideChip() {
    chip?.classList.remove("on");
    chipItems = [];
  }
  // ---------- review before sending: "your text" / "what the AI will read" ----------
  // The protection is invisible by design, so this sheet is where it shows: the message as
  // the user wrote it (real names, yellow) and as the AI will receive it (doubles, blue),
  // plus the doubtful passages the engine left in clear. It opens by itself on sending
  // when there is a doubt, and from the chip at any time. Everything here lives in our
  // closed layer: the site never sees the real values.
  let sheet = null;
  const el = (tag, cls, text) => { const x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; };
  // The message as the user wrote it: the field may already hold doubles (pasted protected).
  async function originalOf(text) {
    const r = await ask("spans", [text]);
    if (r.error) return { error: r.error };
    let out = "", at = 0;
    for (const [s, e, real] of r.spans[0]) { out += text.slice(at, s) + real; at = e; }
    return { text: out + text.slice(at) };
  }
  // What the AI will read, and where the doubles sit in it (people carry their character).
  async function prepare(original) {
    const r = await ask("review", [original]);
    if (r.error) return { error: r.error };
    const s = await ask("spans", [r.review.text]);
    if (s.error) return { error: s.error };
    const spans = s.spans[0];
    await Promise.all(spans.filter((x) => x[3]).map(async ([, , , p]) => { const fc = await faceOf(p); faces.get(p.who).done = fc; }));
    return { review: r.review, spans };
  }
  // identifiers and dates in mono (0/O, 1/l/I), never an address written in words
  const isId = (v) => /\d/.test(v) && !/[a-zà-ÿ]{3,}/.test(v);
  function capsule(person, text, side) {
    const m = el("span", `name ${side}`);
    const fc = faces.get(person.who)?.done;
    if (fc) m.append(disc(side === "real" ? fc.real : fc.double, side));
    m.append(text);
    return m;
  }
  // Paint text with marks [{ s, e, node }] (non-overlapping, sorted).
  function paint(text, marks) {
    const frag = document.createDocumentFragment();
    let at = 0;
    for (const m of marks.sort((a, b) => a.s - b.s)) {
      if (m.s < at) continue;
      frag.append(text.slice(at, m.s), m.node);
      at = m.e;
    }
    frag.append(text.slice(at));
    return frag;
  }
  function yourText(st) {
    const people = st.spans.filter((x) => x[3]).map((x) => x[3]);
    const marks = st.rows.map((d) => ({ s: d.start, e: d.end, node: el("span", `doubt${isId(d.value) ? " id" : ""}`, st.original.slice(d.start, d.end)) }));
    for (const m of st.first.masked) {
      const v = st.original.slice(m.start, m.end);
      const p = m.type === "PER" && people.find((p) => v.toLowerCase().includes(p.who.toLowerCase()) || p.who.toLowerCase().includes(v.toLowerCase()));
      marks.push({ s: m.start, e: m.end, node: p ? capsule(p, v, "real") : el("span", `value real${isId(v) ? " id" : ""}`, v) });
    }
    return paint(st.original, marks);
  }
  function aiText(st) {
    const text = st.review.text;
    const marks = st.spans.map(([s, e, , person]) => ({ s, e, node: person ? capsule(person, text.slice(s, e), "double")
      : el("span", `value double${isId(text.slice(s, e)) ? " id" : ""}`, text.slice(s, e)) }));
    // a passage left in clear leaves as it is: still yellow, the AI reads the real value
    for (const d of st.rows.filter((d) => d.choice === "clear")) {
      for (let i = text.indexOf(d.value); i >= 0; i = text.indexOf(d.value, i + 1)) {
        if (!marks.some((m) => i < m.e && i + d.value.length > m.s)) marks.push({ s: i, e: i + d.value.length, node: el("span", "doubt", d.value) });
      }
    }
    return paint(text, marks);
  }
  async function openSheet(ed, reason) {
    const text = valueOf(ed);
    if (!text.trim()) return;
    const o = await originalOf(text);
    if (o.error) return toast(`Relecture impossible : ${o.error}. Rien n'a été envoyé.`);
    const p = await prepare(o.text);
    if (p.error) return toast(`Relecture impossible : ${p.error}. Rien n'a été envoyé.`);
    if (reason === "send" && !p.review.doubts.length) return sendProtected(ed, p.review.text);
    // doubts start masked: the safe side (a false alarm masked costs little, a leak does not)
    const rows = p.review.doubts.map((d) => ({ ...d, choice: "mask" }));
    const st = { kind: reason, ed, original: o.text, first: p.review, review: p.review, spans: p.spans, rows, view: "yours" };
    if (rows.length) {
      await ask("decide", [], { mask: rows.map((d) => [d.value, d.type]) });
      Object.assign(st, await prepare(o.text));
    }
    showSheet(st);
  }
  async function choose(st, row, choice) {
    if (row.choice === choice) return;
    row.choice = choice;
    await ask("decide", [], choice === "mask" ? { mask: [[row.value, row.type]] } : { unmask: [row.value] });
    const p = await prepare(st.original);
    if (!p.error) Object.assign(st, p);
    showSheet(st);
  }
  function showSheet(st) {
    mount(); styleTop();
    const fresh = !sheet;
    if (fresh) { sheet = el("div", "sheet"); sheet.setAttribute("role", "dialog"); layer.append(sheet); }
    sheet.state = st;
    const n = st.rows.length;
    const people = new Set(st.spans.filter((x) => x[3]).map((x) => x[3].who)).size, others = st.spans.filter((x) => !x[3]).length;
    const head = el("div", "head");
    const file = st.kind === "file" || st.kind === "attached";
    head.append(el("h3", "", st.kind === "file" ? `Vérifiez « ${st.realName} » avant de le joindre`
      : st.kind === "attached" ? `« ${st.realName} » joint en version protégée` : n ? "Vérifiez avant l'envoi" : "Ce que l'IA va lire"),
      el("p", "sub", n ? `${n > 1 ? `${n} passages pourraient` : "Un passage pourrait"} identifier quelqu'un. Arsène les masque, sauf si vous les laissez en clair.`
        : [people && `${people} ${people > 1 ? "personnes remplacées" : "personne remplacée"} par ${people > 1 ? "leurs doublures" : "sa doublure"}`,
           others && `${others} ${others > 1 ? "autres valeurs changées" : "autre valeur changée"}`].filter(Boolean).join(", ").replace(/.$/, "$&.") || "Rien à masquer dans ce message."));
    // two views of the same message, one switch
    const seg = el("div", "seg"); seg.setAttribute("role", "tablist");
    for (const [v, label] of [["yours", "Votre texte"], ["ai", "Ce que l'IA lira"]]) {
      const b = el("button", `tab ${v}`); b.setAttribute("role", "tab"); b.setAttribute("aria-selected", String(st.view === v));
      b.append(el("i"), label);
      b.addEventListener("click", () => { if (st.view !== v) { st.view = v; showSheet(st); sheet.querySelector(".doc").classList.add("swap"); } });
      seg.append(b);
    }
    // the thumb slides from the previous view to this one
    seg.classList.toggle("right", (st.prev || st.view) === "ai");
    requestAnimationFrame(() => seg.classList.toggle("right", st.view === "ai"));
    st.prev = st.view;
    const doc = el("div", `doc ${st.view}`);
    doc.append(st.view === "ai" ? aiText(st) : yourText(st));
    const note = el("p", "note", st.view === "ai"
      ? (file ? `Le fichier que l'IA reçoit, sous le nom « ${st.fileName} ». Les vrais noms restent sur votre Mac.`
        : "Voici le message tel que l'IA le recevra. Les vrais noms restent sur votre Mac.")
      : (file ? (st.kind === "attached" ? "Votre fichier, tel que vous l'avez choisi. Il n'est pas parti sous cette forme."
        : "Votre fichier, tel que vous l'avez choisi. Il ne part pas sous cette forme.")
        : "Votre message, tel que vous l'avez écrit. Il ne part pas sous cette forme."));
    const parts = [head, seg, doc, note];   // scrolls; the actions below always stay in view
    if (n) {
      const list = el("div", "doubts");
      list.append(el("p", "label", "À vérifier"));
      for (const d of st.rows) {
        const row = el("div", "row");
        const what = el("div", "what");
        what.append(el("span", `doubt${isId(d.value) ? " id" : ""}`, d.value), el("span", "why", d.reason));
        const pick = el("div", `pick${d.choice === "clear" ? " right" : ""}`);
        for (const [c, label] of [["mask", "Masquer"], ["clear", "Laisser en clair"]]) {
          const b = el("button", "", label); b.setAttribute("aria-pressed", String(d.choice === c));
          b.addEventListener("click", () => choose(st, d, c));
          pick.append(b);
        }
        row.append(what, pick);
        list.append(row);
      }
      parts.push(list);
    }
    const foot = el("div", "foot");
    const go = el("button", "go", st.kind === "file" ? "Joindre la version protégée" : st.kind === "attached" ? "Fermer" : "Envoyer la version protégée");
    go.addEventListener("click", () => {
      const t = st.review.text;
      if (st.kind === "file") st.done(t);          // before closeSheet, which would answer "cancelled"
      closeSheet();
      if (st.kind === "send" || st.kind === "look") sendProtected(st.ed, t);
    });
    const cancel = el("button", "cancel", "Annuler");
    cancel.addEventListener("click", closeSheet);
    const safe = el("span", "safe"); safe.append(el("i"), "Rien n'est parti");
    foot.append(...(st.kind === "attached" ? [go] : [go, cancel, safe]));
    const body = el("div", "body");
    body.append(...parts);
    sheet.replaceChildren(body, foot);
    requestAnimationFrame(() => foot.classList.toggle("ruled", body.scrollHeight > body.clientHeight));
    // focus on the sheet, not on "send": a second Enter must not send what was not read
    if (fresh) { sheet.tabIndex = -1; requestAnimationFrame(() => { sheet?.classList.add("on"); sheet?.focus({ preventScroll: true }); }); }
    else sheet.classList.add("on");
    place();
  }
  function closeSheet() { const st = sheet?.state; sheet?.remove(); sheet = null; st?.done?.(null); }
  // Every listener below acts only on events the user made (isTrusted). The site's own JS can
  // dispatch a fake copy, paste, drop or Enter: answering it would hand the site the real names
  // (realOf in its DataTransfer) or let it test candidate names. Arsène's own replays are
  // untrusted too and are skipped the same way as with `injecting` / `passing`.
  // a click anywhere else cancels (clicks inside our layer arrive retargeted to it)
  addEventListener("pointerdown", (e) => { if (e.isTrusted && sheet && !e.composedPath().includes(top)) closeSheet(); }, true);
  addEventListener("keydown", (e) => { if (e.isTrusted && sheet && e.key === "Escape") { e.stopImmediatePropagation(); closeSheet(); } }, true);

  // ---------- attached files ----------
  // A file picked, dropped or pasted never reaches the site as it is: Arsène reads it, the
  // review sheet opens when a passage is doubtful, and the site receives a protected text
  // copy under a protected name. A file Arsène cannot read (image, scanned PDF) is held back.
  const FILE_MAX = 20e6;
  let injecting = false;
  const base64Of = (f) => new Promise((ok, ko) => { const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1] || ""); r.onerror = ko; r.readAsDataURL(f); });
  async function fileState(realName, f) {
    const s = await ask("spans", [f.text]);
    if (s.error) return { error: s.error };
    const spans = s.spans[0];
    await Promise.all(spans.filter((x) => x[3]).map(async ([, , , p]) => { const fc = await faceOf(p); faces.get(p.who).done = fc; }));
    return { realName, fileName: f.name, original: f.original, first: f, review: f, spans, view: "yours",
             rows: f.doubts.map((d) => ({ ...d, choice: "mask" })) };
  }
  // Why a file is held back, by the engine's reason (formats.py): what to do about it.
  const HELD = {
    scan: "c'est un PDF scanné, sans texte à lire. Rien n'est parti : copiez son texte dans le champ, ou mettez Arsène en pause sur ce site pour l'envoyer tel quel.",
    encrypted: "il est protégé par un mot de passe. Rien n'est parti : enregistrez-en une copie sans mot de passe, puis déposez-la.",
    image: "c'est une image : Arsène ne lit pas le texte des images. Rien n'est parti ; pour l'envoyer telle quelle, mettez Arsène en pause sur ce site.",
    archive: "c'est une archive. Rien n'est parti : ouvrez-la et déposez les documents eux-mêmes.",
    signature: "c'est une signature seule, sans le document signé. Rien n'est parti : déposez le document.",
    empty: "il ne contient aucun texte. Rien n'est parti.",
    format: "Arsène ne sait pas lire ce format. Rien n'est parti : enregistrez-le en Word ou en PDF, ou mettez Arsène en pause sur ce site pour l'envoyer tel quel.",
  };
  const pages = (l) => l.length === 1 ? `la page ${l[0]}` : `les pages ${l.slice(0, -1).join(", ")} et ${l.at(-1)}`;
  const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  async function protectFile(file) {
    if (file.size > FILE_MAX) { toast(`« ${file.name} » est trop lourd pour Arsène (20 Mo au plus) : il n'a pas été joint.`); return null; }
    let data;
    // a folder dropped from the Finder arrives as a File that cannot be read
    try { data = await base64Of(file); } catch { toast(`« ${file.name} » n'a pas été joint : déposez les fichiers un par un, pas un dossier.`); return null; }
    const r = await ask("file", [], { name: file.name, data });
    if (r.error) { toast(`« ${file.name} » n'a pas été joint : ${r.error}`); return null; }
    if (r.file.unreadable) { toast(`« ${file.name} » n'a pas été joint : ${HELD[r.file.why] || HELD.format}`); return null; }
    const st = await fileState(file.name, r.file);
    if (st.error) { toast(`« ${file.name} » n'a pas été joint : ${st.error}`); return null; }
    let text = r.file.text;
    if (st.rows.length) {
      await ask("decide", [], { mask: st.rows.map((d) => [d.value, d.type]) });
      const p = await prepare(st.original);
      if (p.error) { toast(`« ${file.name} » n'a pas été joint : ${p.error}`); return null; }
      Object.assign(st, p);
      text = await new Promise((done) => showSheet({ ...st, kind: "file", done }));
      if (text == null) return null;
    }
    // A Word file with no image goes as a Word file (layout kept, decisions above applied by
    // the engine); anything else, or a failure there, goes as the protected text.
    let out = new File([text], r.file.name, { type: "text/plain" }), images = false;
    if (r.file.layout === "docx") {
      const w = await ask("protect", [], { name: file.name, data });
      if (!w.error && w.file?.data) {
        const bytes = Uint8Array.from(atob(w.file.data), (c) => c.charCodeAt(0));
        out = new File([bytes], w.file.name, { type: DOCX });
        images = (w.file.warnings || []).some((x) => /image/.test(x));
      }
    }
    const people = new Set(st.spans.filter((x) => x[3]).map((x) => x[3].who)).size;
    const missing = (r.file.notes || []).find((n) => n[0] === "pages-sans-texte");
    const how = out.type === DOCX ? "en Word protégé" : "en version protégée";
    toast(`« ${file.name} » joint ${how}${people ? ` : ${people} ${people > 1 ? "personnes remplacées" : "personne remplacée"}` : ""}.`
      + (images ? " Ses images partent telles quelles." : "")
      + (missing ? (missing[1].length > 1 ? ` Attention : ${pages(missing[1])} n'ont pas de texte (scannées) et ne sont pas jointes.`
                                         : ` Attention : ${pages(missing[1])} n'a pas de texte (scannée) et n'est pas jointe.`) : ""),
      { label: "Voir", run: () => showSheet({ ...st, kind: "attached", view: "ai", rows: [], done: null }) });
    return out;
  }
  async function protectFiles(files) {
    const out = [];
    for (const f of files) { const p = await protectFile(f); if (p) out.push(p); }
    const dt = new DataTransfer();
    for (const f of out) dt.items.add(f);
    return dt;
  }
  const hasFiles = (dt) => !!dt && [...(dt.types || [])].includes("Files");
  // picker: the site reads input.files on "input"/"change"; both are held, then replayed
  addEventListener("input", (e) => {
    if (e.isTrusted && on && !injecting && e.target?.type === "file") e.stopImmediatePropagation();
  }, true);
  addEventListener("change", async (e) => {
    const input = e.target;
    if (!e.isTrusted || !on || injecting || input?.type !== "file" || !input.files?.length) return;
    e.stopImmediatePropagation();
    const dt = await protectFiles([...input.files]);
    input.value = "";
    if (!dt.files.length) return;
    input.files = dt.files;
    injecting = true;
    try { input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true })); }
    finally { injecting = false; }
  }, true);
  // Drop, anywhere in the window: ChatGPT and Claude take a file dropped on the whole page
  // (their "drop here" overlay), not only on the field. Window capture runs before any of the
  // site's listeners (document_start), so the site never sees the original file; it receives
  // the protected copy in a drop replayed where the user let go.
  const replayDrop = (target, x, y, dt) => {
    // the site's overlay may be gone by now (it hides on dragleave): what is under the pointer
    const at = target?.isConnected ? target : document.elementFromPoint(x, y) || document.body;
    injecting = true;
    try {
      if (!dt) {   // nothing kept: the site's overlay must still close, as after a drop outside
        at.dispatchEvent(new DragEvent("dragleave", { bubbles: true, composed: true, clientX: x, clientY: y, dataTransfer: new DataTransfer() }));
        return;
      }
      at.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, composed: true, dataTransfer: dt, clientX: x, clientY: y }));
    } finally { injecting = false; }
  };
  // text moved inside the field itself is already what the field shows: left to the browser
  let dragInField = false;
  addEventListener("dragstart", (e) => { if (e.isTrusted) dragInField = !!composerOf(e.target); }, true);
  addEventListener("dragend", () => { dragInField = false; }, true);
  addEventListener("drop", async (e) => {
    if (!e.isTrusted || !on || injecting) return;
    const { target, clientX, clientY } = e;
    if (hasFiles(e.dataTransfer)) {
      e.preventDefault(); e.stopImmediatePropagation();
      const files = [...e.dataTransfer.files];   // read now: the list empties after this event
      const dt = await protectFiles(files);
      replayDrop(target, clientX, clientY, dt.files.length ? dt : null);
      return;
    }
    // Text dragged into the field (from a document, a mail, another tab): like a paste, the
    // protected version goes in, never the original
    const ed = !dragInField && composerOf(target);
    const text = ed && e.dataTransfer?.getData("text/plain");
    if (!text?.trim()) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const r = await ask("pseudonymize", [text]);
    if (r.error) return toast(`Dépôt bloqué : ${r.error}. Rien n'a été envoyé.`);
    ed.focus();
    document.execCommand("insertText", false, r.texts[0]);
    requestAnimationFrame(() => updateChip(ed, true));
  }, true);
  // The whole thread shows the doubles (blue, sunglasses) or the real names again.
  function setAiView(on) {
    aiView = on;
    for (const h of hosted) stale.add(h);
    redraw();
  }
  // For the toolbar popup: the people of this conversation, most mentioned first, with
  // their real character (the popup is the extension's own page, never the site's).
  async function summary() {
    const seen = new Map();
    for (const [n, k] of known) {
      if (!n.isConnected || k.text !== n.data) continue;
      for (const [, , , person] of k.spans) {
        if (!person) continue;
        const x = seen.get(person.who) || seen.set(person.who, { real: person.who, fake: person.double, n: 0, person }).get(person.who);
        x.n++;
      }
    }
    const list = [...seen.values()].sort((a, b) => b.n - a.n);
    for (const x of list) { x.face = (await faceOf(x.person)).real; delete x.person; }
    return { on, aiView, people: list };
  }
  // The field empties after sending, or the user edits it: keep the chip true.
  setInterval(() => { if (chipEd && chip?.classList.contains("on") && chipItems.length) updateChip(chipEd, false); }, 1000);

  // Paste into the chat: the protected version is pasted instead.
  addEventListener("paste", async (e) => {
    if (!e.isTrusted || !on || injecting) return;
    if (e.clipboardData?.files?.length) {                  // a file or a screenshot
      e.preventDefault(); e.stopImmediatePropagation();
      const target = e.target;
      const dt = await protectFiles([...e.clipboardData.files]);
      if (!dt.files.length) return;
      injecting = true;
      try { target.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true, clipboardData: dt })); }
      finally { injecting = false; }
      return;
    }
    const ed = composerOf(e.target);
    if (!ed) return;
    const text = e.clipboardData?.getData("text/plain");
    if (!text?.trim()) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const r = await ask("pseudonymize", [text]);
    if (r.error) return toast(`Collage bloqué : ${r.error}. Rien n'a été envoyé.`);
    ed.focus();
    document.execCommand("insertText", false, r.texts[0]);
    requestAnimationFrame(() => updateChip(ed, true));
    // First time only: the field now shows names the user never typed, say why
    if (r.texts[0] !== text) chrome.storage.local.get({ explained: false }, ({ explained }) => {
      if (explained) return;
      chrome.storage.local.set({ explained: true });
      toast("Le champ montre les doublures : c'est ce que l'IA recevra. Vous lirez les vrais noms dans la conversation.");
    });
  }, true);

  // Send: whatever was typed is protected first; if Arsène cannot answer, nothing leaves.
  let passing = false;
  // Send: the message is reviewed first; with a doubtful passage the sheet opens, otherwise
  // the protected version leaves at once. If Arsène cannot answer, nothing leaves.
  const protectThenSend = (ed) => (valueOf(ed).trim() ? openSheet(ed, "send") : sendProtected(ed, null));
  async function sendProtected(ed, protectedText) {
    if (protectedText != null && protectedText !== valueOf(ed)) {
      replaceAll(ed, protectedText);
      await new Promise((ok) => setTimeout(ok, 60)); // let the editor commit its state
    }
    passing = true;
    try {
      const b = [...document.querySelectorAll("button")].find(isSend);
      if (b) b.click();
      else ed.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
    } finally { passing = false; }
  }
  addEventListener("keydown", (e) => {
    if (!e.isTrusted || !on || passing || e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    const ed = composerOf(e.target);
    if (!ed) return;
    e.preventDefault(); e.stopImmediatePropagation();
    protectThenSend(ed);
  }, true);
  addEventListener("click", (e) => {
    const b = e.target.closest?.("button");
    if (!e.isTrusted || !on || passing || !b || !isSend(b)) return;
    const ed = findComposer();
    if (!ed) return;
    e.preventDefault(); e.stopImmediatePropagation();
    protectThenSend(ed);
  }, true);

  // ---------- real names in the conversation ----------
  const known = new Map();          // text node -> { text, spans }
  const pairs = new Map();          // surrogate -> real, for copies
  const roots = new WeakMap();      // block -> its closed shadow root
  const hosted = new Set();         // blocks we host (WeakMap cannot be walked)
  let dirty = new Set(), stale = new Set(), scanTimer, frame, warned = false;

  // Elements that accept a shadow root. Controls inside keep working: see render().
  const HOSTS = new Set(["P", "DIV", "SPAN", "H1", "H2", "H3", "H4", "H5", "H6", "ARTICLE", "ASIDE",
    "BLOCKQUOTE", "SECTION", "HEADER", "FOOTER", "MAIN", "NAV"]);
  // Styles the copy must carry: page rules do not reach inside a shadow root.
  const PROPS = ["display", "font-family", "font-size", "font-weight", "font-style", "line-height", "color",
    "background-color", "text-decoration-line", "text-decoration-color", "text-underline-offset",
    "margin-top", "margin-right", "margin-bottom", "margin-left", "padding-top", "padding-right",
    "padding-bottom", "padding-left", "border-top", "border-right", "border-bottom", "border-left",
    "border-radius", "white-space", "list-style-type", "list-style-position", "vertical-align",
    "letter-spacing", "font-feature-settings", "text-align", "text-transform", "gap", "flex-direction",
    "overflow-wrap", "word-break"];

  const skip = (n) => {
    const p = n.parentElement;
    return !p || p.closest('textarea, input, [contenteditable="true"], script, style, noscript, arsene-layer, [data-arsene-probe]');
  };
  // Any text with a letter or a digit: a message typed in lower case ("est-ce que julien a
  // raison") carries lower-case doubles too.
  const worth = (t) => t.length > 1 && /[\p{L}\p{N}]/u.test(t);

  const hostOf = (n) => {   // an existing host above wins: its copy already covers this node
    for (let el = n.parentElement; el; el = el.parentElement) if (roots.has(el)) return el;
    for (let el = n.parentElement; el && el !== document.body; el = el.parentElement)
      if (HOSTS.has(el.tagName)) return el.shadowRoot ? null : el;
    return null;
  };

  function collect(root) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) {
      if (skip(n) || !worth(n.data)) continue;
      const k = known.get(n);
      if (!k || k.text !== n.data) dirty.add(n);
    }
  }

  async function scan() {
    if (!on) { dirty = new Set(); return; }
    const nodes = [...dirty].filter((n) => n.isConnected);
    dirty = new Set();
    if (!nodes.length) return;
    const texts = nodes.map((n) => n.data);
    // doubles already seen in this conversation: they settle initials two people share
    const r = await ask("spans", texts, { context: [...pairs.keys()].join("\n") });
    if (r.error) {       // never silent: the user would read doubles and think they are real
      if (!warned) toast(`Vrais noms indisponibles : ${r.error}`);
      warned = true;
      return;
    }
    warned = false;
    nodes.forEach((n, i) => {
      if (n.data !== texts[i]) return dirty.add(n);   // changed while we asked (streaming)
      known.set(n, { text: texts[i], spans: r.spans[i] });
      for (const [s, e, real] of r.spans[i]) pairs.set(texts[i].slice(s, e), real);
      if (!r.spans[i].length) return;
      const h = hostOf(n);
      if (h) stale.add(h);
    });
    if (dirty.size) schedule();
    redraw();
  }
  const schedule = () => { clearTimeout(scanTimer); scanTimer = setTimeout(scan, 120); };

  function shadowFor(h) {
    let root = roots.get(h);
    if (root) return root;
    root = h.attachShadow({ mode: "closed", slotAssignment: "manual" });
    roots.set(h, root); hosted.add(h);
    root.addEventListener("mouseover", (e) => { const m = e.target.closest?.("mark"); if (m) bubble(m); });
    root.addEventListener("mouseout", (e) => { if (e.target.closest?.("mark")) bubble(null); });
    return root;
  }

  function markSheet() {
    const t = theme();
    // The real value is generated content (::before): selection and innerText ignore it, so
    // a site that reads the user's selection gets the double, kept inline at size 0.
    // DESIGN.md « Nom dans le document », in yellow: what is shown here is the real value.
    // Other real values (phone, email, date, address): a plain yellow underline, no pill.
    return `mark { background: none; color: inherit; font: inherit; cursor: default; padding: 0;
             text-decoration: underline 2px ${t.real}; text-underline-offset: 3px; text-decoration-skip-ink: none;
             transition: background-color 180ms ease-out, box-shadow 180ms ease-out; }
      mark:hover { background: ${t.realTint}; }
      mark { border-radius: 4px; }
      mark.person { white-space: nowrap; }
      mark::after { content: attr(data-show); }
      mark.ai { text-decoration-color: ${t.double}; }
      mark.ai:hover { background: ${t.doubleTint}; }
      mark > s { font-size: 0; text-decoration: none; }
      mark .disc { width: 1em; height: 1em; vertical-align: -0.16em; margin-right: 0.25em; }
      ${discCss(t)}
      mark.sel, mark.sel:hover { background: Highlight; color: HighlightText; }`;
  }

  // Once a block has a shadow root its own elements lose their computed style: read the
  // styles from a hidden twin (doubles only) placed next to it for the time of the read.
  function stylesOf(h) {
    const twin = h.cloneNode(true), out = new Map();
    twin.setAttribute("data-arsene-probe", "");
    twin.removeAttribute("id");
    twin.style.cssText += `;position:absolute;visibility:hidden;pointer-events:none;left:-99999px;width:${h.offsetWidth}px`;
    h.after(twin);
    const a = document.createTreeWalker(h, NodeFilter.SHOW_ELEMENT), b = document.createTreeWalker(twin, NodeFilter.SHOW_ELEMENT);
    for (let x = a.nextNode(), y = b.nextNode(); x && y; x = a.nextNode(), y = b.nextNode()) {
      const cs = getComputedStyle(y);
      out.set(x, PROPS.map((p) => `${p}:${cs.getPropertyValue(p)}`).join(";"));
    }
    twin.remove();
    return out;
  }

  const hasName = (el) => {
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) { const k = known.get(n); if (k && k.text === n.data && k.spans.length) return true; }
    return false;
  };

  // Our copy of the block, real names in place of doubles. The block's own children that
  // hold no name (citation chips, buttons, links, code) are not copied: a slot shows the
  // site's live element, with its styles and handlers. Only what holds a name is copied,
  // with the page styles inlined.
  function render(h) {
    const styles = stylesOf(h);
    const root = shadowFor(h);
    const copy = document.createElement("div");
    copy.style.display = "contents";
    const slots = [];
    const drawn = new Set();   // a person's character once per block, not at every mention
    const text = (n, into) => {
      const k = known.get(n);
      if (!k || k.text !== n.data || !k.spans.length) { into.append(n.data); return; }
      let at = 0;
      for (const [s, e, real, person] of k.spans) {
        into.append(n.data.slice(at, s));
        const m = document.createElement("mark"), f = document.createElement("s");
        f.textContent = m.dataset.fake = n.data.slice(s, e); m.dataset.real = real;
        m.dataset.show = aiView ? m.dataset.fake : real;      // "what the AI read" shows the double
        if (aiView) m.classList.add("ai");
        if (person) {
          m.person = person; m.classList.add("person");
          const face = faces.get(person.who);
          if (drawn.has(person.who)) { /* already drawn in this block */ }
          else if (face?.done) drawn.add(person.who), m.append(aiView ? disc(face.done.double, "double") : disc(face.done.real, "real"));
          else faceOf(person).then((fc) => { faces.get(person.who).done = fc; stale.add(h); redraw(); });
        }
        m.append(f);
        into.append(m); at = e;
      }
      into.append(n.data.slice(at));
    };
    const walk = (node, into, top) => {
      for (const n of node.childNodes) {
        if (n.nodeType === 3) { text(n, into); continue; }
        if (n.nodeType !== 1 || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(n.tagName)) continue;
        if (top && !hasName(n)) {                  // live element of the site, untouched
          const slot = document.createElement("slot");
          into.append(slot); slots.push([slot, n]);
          continue;
        }
        if (n.tagName === "IMG" || n.tagName.toLowerCase() === "svg") { into.append(n.cloneNode(true)); continue; }
        const c = document.createElement(n.tagName === "A" ? "a" : n.tagName.toLowerCase());
        if (n.tagName === "A" && n.href) { c.href = n.href; c.target = n.target; c.rel = "noopener"; }
        c.style.cssText = styles.get(n) || "";
        into.append(c);
        walk(n, c, false);
      }
    };
    walk(h, copy, true);
    const st = document.createElement("style");
    st.textContent = markSheet();
    root.replaceChildren(st, copy);
    for (const [slot, n] of slots) slot.assign(n);   // manual slots: only the host's own children
  }

  function draw() {
    frame = null;
    for (const h of stale) if (h.isConnected) render(h);
    for (const h of hosted) if (!h.isConnected) hosted.delete(h);
    stale = new Set();
  }
  const redraw = () => { if (!frame && stale.size) frame = requestAnimationFrame(draw); };

  new MutationObserver((muts) => {
    for (const m of muts) {
      if ([...m.addedNodes, ...m.removedNodes].some((a) => a.nodeType === 1 && a.hasAttribute("data-arsene-probe"))) continue;
      for (let el = m.target.nodeType === 3 ? m.target.parentElement : m.target; el; el = el.parentElement)
        if (roots.has(el)) { stale.add(el); break; }
      if (m.type === "characterData") { if (!skip(m.target) && worth(m.target.data)) dirty.add(m.target); }
      else m.addedNodes.forEach((a) => a.nodeType === 3 ? (!skip(a) && worth(a.data) && dirty.add(a)) : a.nodeType === 1 && collect(a));
    }
    if (dirty.size) schedule();
    redraw();
  }).observe(document, { subtree: true, childList: true, characterData: true });
  addEventListener("DOMContentLoaded", () => { collect(document.body); schedule(); });
  // Safety sweep. ChatGPT rebuilds the thread when the first answer moves the page to
  // /c/<id>: known text nodes can land in a new block that has no copy yet, and a node
  // moved with the same text raises no "dirty" mutation.
  setInterval(() => {
    if (!document.body) return;
    collect(document.body);
    for (const [n, k] of known) {
      if (!n.isConnected) { known.delete(n); continue; }
      if (k.text !== n.data || !k.spans.length) continue;
      const h = hostOf(n);
      if (h && !roots.has(h)) stale.add(h);
    }
    if (dirty.size) schedule();
    redraw();
  }, 2000);

  // ---------- hover: "this is a double" ----------
  let bub;
  function bubble(m) {
    mount();
    if (!m) { bub?.classList.remove("on"); return; }
    styleTop();
    if (!bub) { bub = document.createElement("div"); bub.className = "bubble"; layer.append(bub); }
    // Figma « Bulle de survol »: real character + name → double character + double in bold
    const real = document.createElement("span"); real.className = "real-name"; real.textContent = m.dataset.real;
    const arrow = document.createElement("span"); arrow.className = "arrow"; arrow.textContent = "→";
    const b = document.createElement("b"); b.textContent = m.person?.double && m.dataset.real === m.person.who ? m.person.double : m.dataset.fake;
    const fc = m.person && faces.get(m.person.who)?.done;
    bub.replaceChildren(...(fc ? [disc(fc.real, "real")] : []), real, arrow, ...(fc ? [disc(fc.double, "double")] : []), b);
    const rc = m.getBoundingClientRect();
    bub.style.left = `${rc.left + rc.width / 2}px`; bub.style.top = `${rc.top}px`;
    bub.classList.add("on");
  }
  addEventListener("scroll", () => bubble(null), true);

  // ---------- copies leave with the real names ----------
  // One pass, whole words only: chained replacements would rewrite a real value again
  // (a date's "2" → "3" turned "06 12…" into "06 13…").
  const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const realOf = (t) => {
    if (!pairs.size) return t;
    const alt = [...pairs.keys()].sort((a, b) => b.length - a.length).map(esc).join("|");
    return t.replace(new RegExp(`(?<![\\p{L}\\p{N}])(?:${alt})(?![\\p{L}\\p{N}])`, "gu"), (f) => pairs.get(f));
  };
  // innerText is empty on a block we host (its own nodes are not rendered): read them directly.
  function lightText(el) {
    let t = "";
    for (const n of el.childNodes) {
      if (n.nodeType === 3) t += n.data;
      else if (n.nodeType === 1 && !/^(SCRIPT|STYLE|BUTTON)$/.test(n.tagName)) {
        const block = /^(P|DIV|LI|H[1-6]|PRE|BLOCKQUOTE|TR|UL|OL|TABLE)$/.test(n.tagName);
        t += (n.tagName === "BR" ? "\n" : "") + (n.tagName === "LI" ? "- " : "") + lightText(n) + (block ? "\n" : "");
      }
    }
    return t.replace(/\n{3,}/g, "\n\n");
  }
  // Selected names get the selection colour (generated content is never highlighted).
  // A selection often starts in our copy and ends in the site's next block: each end is
  // lifted to the page to know which blocks it crosses, then clamped inside each block.
  const lift = (node, offset, isEnd) => {
    while (node.getRootNode() instanceof ShadowRoot) {
      const h = node.getRootNode().host;
      node = h.parentNode; offset = [...node.childNodes].indexOf(h) + (isEnd ? 1 : 0);
    }
    return [node, offset];
  };
  document.addEventListener("selectionchange", () => {
    const sel = getSelection(), all = [];
    for (const h of hosted) { const root = roots.get(h); if (root && h.isConnected) all.push([h, root]); }
    let ranges = [];
    try { ranges = sel.rangeCount ? sel.getComposedRanges({ shadowRoots: all.map(([, r]) => r) }) : []; } catch { return; }
    for (const [h, root] of all) {
      const inner = [];
      for (const c of ranges) {
        if (c.collapsed) continue;
        const page = document.createRange();
        try { page.setStart(...lift(c.startContainer, c.startOffset, false)); page.setEnd(...lift(c.endContainer, c.endOffset, true)); } catch { continue; }
        if (!page.intersectsNode(h)) continue;
        // an end sitting in the site's own nodes of this block: a selection that stops at its
        // very start (or starts at its very end) does not cover it
        const empty = (a, b) => { const t = document.createRange(); try { t.setStart(...a); t.setEnd(...b); } catch { return false; } return t.toString() === ""; };
        if (h.contains(c.endContainer) && !root.contains(c.endContainer) && empty([h, 0], [c.endContainer, c.endOffset])) continue;
        if (h.contains(c.startContainer) && !root.contains(c.startContainer) && empty([c.startContainer, c.startOffset], [h, h.childNodes.length])) continue;
        const x = document.createRange();
        x.selectNodeContents(root);
        if (root.contains(c.startContainer)) x.setStart(c.startContainer, c.startOffset);
        if (root.contains(c.endContainer)) x.setEnd(c.endContainer, c.endOffset);
        inner.push(x);
      }
      for (const m of root.querySelectorAll("mark")) {
        const r = document.createRange(); r.selectNodeContents(m);
        m.classList.toggle("sel", inner.some((x) =>
          x.compareBoundaryPoints(Range.END_TO_START, r) < 0 && x.compareBoundaryPoints(Range.START_TO_END, r) > 0));
      }
    }
  });
  addEventListener("copy", (e) => {
    if (!e.isTrusted || !on) return;
    const t = getSelection().toString();
    if (!t || composerOf(e.target)) return;
    e.clipboardData.setData("text/plain", realOf(t));
    e.preventDefault(); e.stopImmediatePropagation();
  }, true);
  // The site's "Copy" button writes surrogates: write the real version right after it.
  addEventListener("click", (e) => {
    const b = e.target.closest?.("button");
    if (!e.isTrusted || !on || !b || !/copi|copy/i.test(`${b.getAttribute("aria-label") || ""} ${b.dataset.testid || ""}`)) return;
    // The message this button belongs to: going up from the button, the first ancestor
    // that holds messages; the last of them that comes before the button (ChatGPT's action
    // bar is not always inside the message's <article>).
    let msg = null;
    for (let el = b.parentElement; el && !msg; el = el.parentElement) {
      const before = [...el.querySelectorAll("[data-message-author-role], [data-message-role]")]
        .filter((m) => m.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
      msg = before[before.length - 1] || null;
    }
    const text = msg && lightText(msg.querySelector(".markdown, [data-assistant-markdown]") || msg).trim();
    if (!text) return;           // never overwrite the site's copy with nothing
    // after the site's own write (it may be async), and once more in case it lands late
    for (const ms of [150, 600]) setTimeout(() => navigator.clipboard.writeText(realOf(text)).catch(() => {}), ms);
  }, true);
})();
