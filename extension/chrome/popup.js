// Toolbar popup: state of Arsène on the current site, the people of this conversation,
// "what the AI read", and the per-site pause (stored in `sites`, read by content.js).
const $ = (id) => document.getElementById(id);
const SITES = ["chatgpt.com", "claude.ai"];

const ask = (msg) => new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) =>
  resolve(r || { error: chrome.runtime.lastError?.message || "pas de réponse" })));
const askTab = (tab, msg) => chrome.tabs.sendMessage(tab.id, msg).catch(() => null);

// The host starts the engine when it is stopped: only a failed install or a slow start show here.
function stopped(error) {
  const starting = /démarre/.test(error);
  $("why").textContent = starting ? "Arsène démarre…" : "Arsène ne démarre pas";
  $("hint").textContent = starting ? "Le modèle se charge, quelques secondes." : "Relancez install.sh, puis regardez engine.log dans ~/Library/Application Support/Pseudonymiseur.";
  $("why").parentElement.classList.toggle("starting", starting);
  for (const id of ["stopped", "hint"]) $(id).hidden = false;
  for (const id of ["status", "count", "badge"]) $(id).hidden = true;
}

function row({ real, fake, n, face }) {
  const r = document.createElement("div"); r.className = "row";
  const names = document.createElement("div"); names.className = "names";
  const b = document.createElement("b"); b.textContent = real;
  const s = document.createElement("span"); s.textContent = `→ ${fake}`;
  names.append(b, s);
  const t = document.createElement("p"); t.className = "times"; t.textContent = `${n}×`;
  const d = document.createElement("span"); d.className = "disc";
  d.append(new DOMParser().parseFromString(face, "image/svg+xml").documentElement);
  r.append(d, names, t);
  return r;
}

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url ? new URL(tab.url) : null;
  const site = url && SITES.includes(url.hostname) && !url.pathname.startsWith("/code") ? url.hostname : null;
  const { sites } = await chrome.storage.local.get({ sites: {} });
  const on = !!site && sites[site] !== false;   // on unless paused on this site

  const count = await ask({ op: "people", dossier: "navigateur" });
  if (count.error) stopped(count.error);
  else $("count").textContent = count.people === 0 ? "Personne à protéger pour l'instant."
    : `${count.people} ${count.people === 1 ? "personne protégée" : "personnes protégées"} dans ChatGPT, Claude et vos documents`;

  $("label").textContent = !site ? "Arsène protège ChatGPT et Claude" : on ? "Protection active" : "Protection en pause";
  $("badge").hidden = !on || !!count.error;

  if (!site) { $("tools").hidden = true; return; }

  const toggle = $("toggle");
  toggle.textContent = on ? "Pause sur ce site" : "Activer sur ce site";
  toggle.classList.toggle("main", !on);
  toggle.addEventListener("click", async () => {
    await chrome.storage.local.set({ sites: { ...sites, [site]: !on } });
    // the page drops or regains its rendering cleanly
    await askTab(tab, { op: "reload" });
    window.close();
  });

  $("aiView").hidden = !on;
  $("foot").classList.toggle("alone", !on);
  if (!on) return;

  const sum = await askTab(tab, { op: "summary" });
  if (sum?.people?.length) {
    $("people").replaceChildren(...sum.people.map(row));
    $("thread").hidden = false;
  }
  const sw = $("aiView");
  sw.setAttribute("aria-checked", String(!!sum?.aiView));
  sw.addEventListener("click", async () => {
    const r = await askTab(tab, { op: "aiView", on: sw.getAttribute("aria-checked") !== "true" });
    if (r) sw.setAttribute("aria-checked", String(r.aiView));
  });
})();
