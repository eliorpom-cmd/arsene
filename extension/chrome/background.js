// Relays content-script requests to the native host (which talks to the local engine).
const HOST = "dev.byelior.arsene";
let port = null, next = 1;
const waiting = new Map();

function connect() {
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener((msg) => {
    const done = waiting.get(msg.id);
    if (done) { waiting.delete(msg.id); done(msg); }
  });
  port.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError?.message || "Arsène n'est pas lancé sur ce Mac";
    for (const done of waiting.values()) done({ error });
    waiting.clear();
    port = null;
  });
}

function call(req) {
  if (!port) connect();
  const id = next++;
  return new Promise((done) => {
    waiting.set(id, done);
    port.postMessage({ ...req, id });   // the host accepts only its own ops and fields
  });
}

// Ops whose answers hold real names outside the page's reach: the background asks them itself,
// a content script never relays them.
const OWN = new Set(["realname", "download"]);
chrome.runtime.onMessage.addListener((req, _sender, reply) => {
  if (OWN.has(req?.op)) { reply({ error: "opération refusée" }); return; }
  call(req).then(reply);
  return true; // async reply
});

// ---------- files downloaded from the chat: back with the real names ----------
// A file ChatGPT or Claude made (a Word letter, a PDF, a table) holds the doubles the AI read.
// Its name is restored while Chrome picks it (the downloads list shows the real one), and its
// content once it is on the disk: the engine rewrites it in place (Word in its runs, text as text;
// a PDF stays as it is). Nothing of it goes back to the site.
const CHATS = [
  [/(^|\.)(chatgpt\.com|openai\.com|oaiusercontent\.com)$/, "chatgpt.com"],
  [/(^|\.)(claude\.ai|anthropic\.com|claudeusercontent\.com)$/, "claude.ai"],
];
function chatOf(item) {
  for (const u of [item.referrer, item.finalUrl, item.url]) {
    try {
      const host = new URL(u.startsWith("blob:") ? u.slice(5) : u).hostname;
      for (const [re, site] of CHATS) if (re.test(host)) return site;
    } catch {}
  }
  return null;
}
const isOn = async (site) => (await chrome.storage.local.get({ sites: {} })).sites[site] !== false;
const base = (p) => p.split(/[\\/]/).pop();
// download id -> site, kept in session storage: the worker may sleep during a long download
const track = async (id, site) => chrome.storage.session.set({ [`dl-${id}`]: site });

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  const site = chatOf(item);
  if (!site) return;                    // any other download: Chrome goes on at once
  (async () => {
    if (!(await isOn(site))) return suggest();
    await track(item.id, site);
    const r = await call({ op: "realname", name: base(item.filename) });
    if (!r.error && r.name && r.name !== base(item.filename)) suggest({ filename: r.name, conflictAction: "uniquify" });
    else suggest();
  })().catch(() => suggest());
  return true;                          // suggest() comes later
});

chrome.downloads.onChanged.addListener(async (delta) => {
  if (delta.state?.current !== "complete") return;
  const key = `dl-${delta.id}`;
  const site = (await chrome.storage.session.get(key))[key];
  if (!site) return;
  await chrome.storage.session.remove(key);
  const [item] = await chrome.downloads.search({ id: delta.id });
  if (!item?.filename) return;
  const r = await call({ op: "download", path: item.filename });
  const d = r.download || {};
  const name = base(item.filename);
  const text = r.error ? `« ${name} » n'a pas pu être rétabli : ${r.error}. Il contient encore les doublures.`
    : d.restored ? `« ${name} » est téléchargé avec les vrais noms.`
    : d.skipped === "format" ? `« ${name} » est téléchargé tel quel : Arsène ne sait pas encore rétablir ce format.`
    : d.skipped === "pdf" ? `« ${name} » contient encore les doublures : ouvrez Arsène pour qu'il rétablisse les PDF.`
    : null;                             // nothing to restore: no message
  if (!text) return;
  // said in the chat tabs of that site (the one in front first), never in the page's own DOM
  const tabs = await chrome.tabs.query({ url: `https://${site}/*` });
  tabs.sort((a, b) => (b.active - a.active) || (b.lastAccessed - a.lastAccessed));
  if (tabs[0]) chrome.tabs.sendMessage(tabs[0].id, { op: "toast", text }).catch(() => {});
});
