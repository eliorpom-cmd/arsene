"""The warm engine: one background process that keeps the model loaded and serves the
Claude Code hooks over a Unix socket, so a file read costs ~0.15 s/page, not a 3 s model load.

Protocol: one JSON request per connection, one JSON reply. Every request names the dossier
root (the folder that holds `.pseudo/`); its table lives in `.pseudo/vault.bin`, encrypted.

    {"op": "ping"}
    {"op": "pseudonymize", "root": "/path", "texts": ["...", ...]}  -> {"texts": [...]}
    {"op": "rehydrate",    "root": "/path", "texts": ["...", ...]}  -> {"texts": [...]}
    {"op": "spans",        "root": "/path", "texts": ["...", ...], "context": "doubles seen"}  -> {"spans": [[[start, end, real], ...], ...]}
    {"op": "check_prompt", "root": "/path", "text": "..."}          -> {"found": [...], "protected": "..."}
    {"op": "review",       "root": "/path", "path": "/file"}          -> {"masked": [...], "doubts": [...]}  (real values: user only)
    {"op": "review_text",  "root": "/path", "text": "..."}          -> {"text": protected, "masked": [...], "doubts": [...]}
    {"op": "review_file",  "root": "/path", "name": "x.pdf", "data": base64} -> review_text + {"original", "name", "notes", "layout"?} or {"unreadable": true, "why", "reason"}
    {"op": "protect_file", "root": "/path", "name": "x.docx", "data": base64} -> {"text", "name"} + {"data": base64} for Word (layout kept)
    {"op": "restore_file", "root": "/path", "name": "x.docx", "data": base64} -> {"text", "name", "restored": n} (+ "data" for Word) or {"unreadable": true}
    {"op": "restore_path", "root": "/path", "path": "/abs/file"}     -> {"restored": n, "kind"} (file rewritten in place) or {"restored": 0, "skipped": why}
    {"op": "decide",       "root": "/path", "mask": [[v, type]], "unmask": [v]}
    {"op": "notice",       "root": "/path", "path": "/file"}          -> {"doubts": n}  (once per file version)
    {"op": "gate",         "root": "/path", "path": "/file"}          -> {"pending": n} (doubts not reviewed yet)
    {"op": "dump",         "root": "/path"}                         -> {"table": {...}}
    {"op": "load",         "root": "/path", "table": {...}}         -> {"entries": n}   (new dossier only)
    {"op": "stop"}

Run: python src/engine.py serve   (the hooks start it on demand)
"""
import json, os, pathlib, re, socket, socketserver, sys, threading, time, types

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # never call the network from the engine
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
SRC = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
import atrest  # noqa: E402  (table encrypted at rest)
import docxfile  # noqa: E402  (Word documents rewritten in place)
import formats  # noqa: E402  (every other format, read in Python)

HOME = pathlib.Path(os.environ.get("PSEUDO_HOME", pathlib.Path.home() / "Library" / "Application Support" / "Pseudonymiseur"))
SOCK = HOME / "engine.sock"
VAULT = pathlib.Path(".pseudo") / "vault.bin"          # encrypted (atrest.py); key in the keychain
STATE = pathlib.Path(".pseudo") / "state.bin"
MAX_INDEX = 20_000_000
formats.TMP = HOME / "tmp"                              # textutil's private folder (macOS fallback only)


def extract(path):
    """Plain text of a document, or "" when it is not read (images, scans, unknown formats).
    Every format is read by formats.py, in Python, on any system."""
    try:
        return formats.read(path.name, path.read_bytes())[0]
    except OSError:
        return ""


def extract_bytes(name, data):
    """Plain text of a file attached in the browser ("" when it is not read: images, scans)."""
    return extract_why(name, data)[0]


def extract_why(name, data):
    """(text, why, notes) of an attached file: why says why it was not read (formats.REASONS),
    notes what the copy leaves out (PDF pages with no text)."""
    import base64
    text, why = formats.read(name, base64.b64decode(data))
    return text, why, list(formats.notes)


RESTORE_MAX = 50_000_000
# Downloaded files rewritten as plain text (RTF is left out: its accents are escapes)
RESTORE_TEXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".html", ".htm", ".json", ".xml", ".srt", ".vtt", ".tex", ".yaml", ".yml"}


def _pdf_restore(raw, pairs):
    """The PDF with the real names, from the Mac app's PDFKit (`Arsene --restore-pdf`); None
    when the app is not there or fails (the file is then left as it is)."""
    import subprocess, tempfile
    apps = [os.environ.get("ARSENE_APP_BIN", ""), "/Applications/Arsène.app/Contents/MacOS/Arsene",
            str(pathlib.Path.home() / "Applications/Arsène.app/Contents/MacOS/Arsene")]
    app = next((a for a in apps if a and os.access(a, os.X_OK)), None)
    if not app:
        return None
    with tempfile.TemporaryDirectory(dir=HOME) as d:          # HOME is 0700: names never in /tmp
        src, out = pathlib.Path(d, "in.pdf"), pathlib.Path(d, "out.pdf")
        src.write_bytes(raw)
        r = subprocess.run([app, "--restore-pdf", str(src), str(out)], input=json.dumps([list(x) for x in pairs]),
                           capture_output=True, text=True, timeout=120)
        return out.read_bytes() if r.returncode == 0 and out.exists() else (raw if r.returncode == 0 else None)


def _layout(name, data):
    """Why a protected copy can keep the file's own format: "docx" for a Word file whose every
    text docxfile rewrites; None otherwise, and the copy is plain text. Images stay (Elior,
    02/10: the file must arrive as the user made it; a letterhead or a signature is in almost
    every lawyer's letter), their metadata removed. A Word file with an embedded object (a
    sheet, a chart's workbook) goes as text: what is inside it is never rewritten."""
    import base64
    if pathlib.Path(name).suffix.lower() not in docxfile.SUFFIXES:
        return None
    try:
        rep = docxfile.report(base64.b64decode(data))
    except ValueError:
        return None
    return None if rep["embedded"] else "docx"


def _is_docx(data):
    import base64
    try:
        docxfile.units(base64.b64decode(data))
        return True
    except ValueError:
        return False


class Engine:
    def __init__(self):
        from surrogate import Vault, pseudonymize
        from rehydrate import rehydrate, spans, in_context
        from bench import detect_gliner, detect_regex
        from propagate import propagate, fold
        self.Vault, self._pseudo, self._rehydrate, self._spans = Vault, pseudonymize, rehydrate, spans
        self._in_context = in_context
        def detect(t):
            pred = detect_gliner(t) + detect_regex(t)
            return pred + propagate(t, pred)
        self.detect, self.detect_regex, self.fold = detect, detect_regex, fold
        self.vaults, self.states, self.snaps, self.lock = {}, {}, {}, threading.Lock()
        self.waiting = 0                                 # hook requests waiting for the lock
        detect_gliner("Chargement du modèle.")          # load the model now, not on the first read

    def vault(self, root):
        root = str(pathlib.Path(root).resolve())
        if not (pathlib.Path(root) / ".pseudo").is_dir():
            raise ValueError(f"pas de dossier .pseudo/ dans {root}")
        if root not in self.vaults:
            d = atrest.load(pathlib.Path(root) / VAULT, "vault")
            self.vaults[root] = self.Vault.from_dict(d) if d is not None else self.Vault(int.from_bytes(os.urandom(4), "big"))
        return root, self.vaults[root]

    def save(self, root, vault):
        atrest.save(pathlib.Path(root) / VAULT, "vault", vault.to_dict())

    def state(self, root):
        if root not in self.states:
            self.states[root] = atrest.load(pathlib.Path(root) / STATE, "state") or {"indexed": {}, "clean": []}
        return self.states[root]

    def _file_doubts(self, root, vault, path):
        """Doubts of one file, computed once per version of it (a change reopens the review)."""
        from review import doubts
        p = pathlib.Path(path).resolve()
        st = self.state(root).setdefault("reviewed", {})
        mtime = p.stat().st_mtime if p.exists() else 0
        entry = st.get(str(p))
        if not entry or entry["mtime"] != mtime:
            text, spans = extract(p), []
            self._pseudo(text, vault, spans=spans)
            self.save(root, vault)
            entry = st[str(p)] = {"mtime": mtime, "doubts": len(doubts(text, spans))}
        return entry

    def save_state(self, root):
        atrest.save(pathlib.Path(root) / STATE, "state", self.states[root])

    def index(self, root, vault):
        """Read every document of the dossier once: a name the user types ("yorick") or that a
        file listing shows is then already known and masked. Runs in the background and yields
        the engine between pieces of ~20 pages, so a question or a read never waits for it;
        each file is indexed once (then only when it changes)."""
        st, done, saved = self.state(root), 0, time.time()
        files = []
        for path in pathlib.Path(root).rglob("*"):
            rel = path.relative_to(root)
            if path.is_file() and not any(p.startswith(".") for p in rel.parts) and path.stat().st_size <= MAX_INDEX:
                if st["indexed"].get(str(rel)) != path.stat().st_mtime:
                    files.append((path, rel))
        # file names first (instant, and what a listing shows), then contents, small files first
        self._yield()
        with self.lock:
            self._pseudo("\n".join(re.sub(r"[_.-]+", " ", p.stem) for p, _ in files), vault)
        for path, rel in sorted(files, key=lambda f: f[0].stat().st_size):
            text = extract(path)
            for piece in re.findall(r"(?s).{1,60000}(?:\n|$)", text):
                self._yield()
                with self.lock:
                    self._pseudo(piece, vault)
            with self.lock:
                st["indexed"][str(rel)] = path.stat().st_mtime
                if time.time() - saved > 10:             # progress survives a restart
                    self.save(root, vault); self.save_state(root); saved = time.time()
            done += 1
        with self.lock:
            self.save(root, vault); self.save_state(root)
        return {"indexed": done}

    def _yield(self):
        """Let interactive requests (hooks) go first."""
        while self.waiting:
            time.sleep(0.01)

    def scrub(self, root, vault, paths):
        """Claude Code writes a long tool output to disk, in clear, before any hook sees it
        (~/.claude/projects/.../tool-results/). Replace each such file by its pseudonymized
        text, once; a later Read of it must then pass unchanged."""
        st, done = self.state(root), 0
        for p in paths:
            if p in st["clean"] or not os.path.isfile(p):
                continue
            text = pathlib.Path(p).read_text(errors="replace")
            safe = self._pseudo(text, vault)
            tmp = p + ".tmp"
            with open(tmp, "w") as f:
                f.write(safe)
            os.replace(tmp, p)
            st["clean"].append(p)
            done += 1
        if done:
            self.save(root, vault); self.save_state(root)
        return {"scrubbed": done}

    def handle(self, req):
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "pid": os.getpid()}
        if op == "index":                                # background: takes the lock piece by piece
            with self.lock:
                root, vault = self.vault(req["root"])
            return self.index(root, vault)
        snap = self.snaps.get(str(pathlib.Path(req.get("root", "")).resolve()))
        if op == "rehydrate" and snap:
            # a snapshot of the table: the display (10-30 s timeout) never waits behind a
            # subagent's long outputs being pseudonymized
            return {"texts": [self._rehydrate(t, snap) for t in req["texts"]]}
        if op == "spans" and snap:
            # the batch and the doubles already seen in the conversation settle shared initials
            view = self._in_context(snap, "\n".join(req["texts"] + [req.get("context", "")]))
            return {"spans": [self._people(self._spans(t, view), view) for t in req["texts"]]}
        self.waiting += 1
        try:
            with self.lock:                              # one table, many hooks (subagents run in parallel)
                reply = self._handle(op, req)
                root, vault = self.vault(req["root"])
                self.snaps[root] = types.SimpleNamespace(rev=dict(vault.rev), people=list(vault.people))
                return reply
        finally:
            self.waiting -= 1

    def restore_path(self, path, vault):
        """A file the AI made, downloaded from ChatGPT or Claude, rewritten in place with the
        real names: Word in its runs (docxfile), PDF by the Mac app (PDFKit, see
        PDFRestore.swift), text formats as text. The file is replaced only when something
        changed, through a temporary copy beside it (never half written)."""
        p = pathlib.Path(path)
        if not p.is_absolute() or p.is_symlink() or not p.is_file():
            return {"restored": 0, "skipped": "missing"}
        if p.stat().st_size > RESTORE_MAX:
            return {"restored": 0, "skipped": "size"}
        ext, raw = p.suffix.lower(), p.read_bytes()
        if ext in docxfile.SUFFIXES and raw[:2] == b"PK":
            n = len(self._spans(docxfile.text(raw), vault))
            new = docxfile.transform(raw, lambda t: self._rehydrate(t, vault))[0] if n else raw
            kind = "docx"
        elif ext == ".pdf" and raw[:5] == b"%PDF-":
            text, _ = formats.read(p.name, raw)
            pairs = sorted({(text[a:b], real) for a, b, real, *_ in self._spans(text or "", vault) if text[a:b] != real})
            n, kind = len(pairs), "pdf"
            new = _pdf_restore(raw, pairs) if pairs else raw
            if new is None:
                return {"restored": 0, "skipped": "pdf"}
        elif ext in RESTORE_TEXT:
            for enc in ("utf-8-sig" if raw[:3] == b"\xef\xbb\xbf" else "utf-8", "cp1252"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return {"restored": 0, "skipped": "format"}
            n, kind = len(self._spans(text, vault)), "text"
            new = self._rehydrate(text, vault).encode(enc) if n else raw
        else:
            return {"restored": 0, "skipped": "format"}
        if not n or new == raw:
            return {"restored": 0, "kind": kind}
        tmp = p.with_name(f".{p.name}.arsene")
        tmp.write_bytes(new)
        os.replace(tmp, p)
        return {"restored": n, "kind": kind}

    def _people(self, spans, vault):
        """A span that is (part of) a person gets a 4th field for the extension's character:
        full real name (the face's seed), full double, and sex from the first name (INSEE)."""
        from lexicon import FIRST_NAME_SEX
        from propagate import fold
        for sp in spans:
            words = set(re.findall(r"[\w'’-]+", sp[2].lower()))
            for real_atoms, sur_atoms in vault.people:
                if words and words <= {a.lower() for a in real_atoms}:
                    sp.append({"who": " ".join(real_atoms), "double": " ".join(sur_atoms),
                               "female": FIRST_NAME_SEX.get(fold(real_atoms[0])) == "F"})
                    break
        return spans

    def _handle(self, op, req):
        root, vault = self.vault(req["root"])
        if op == "scrub":
            return self.scrub(root, vault, req["paths"])
        if op == "pseudonymize":
            if req.get("path") and req["path"] in self.state(root)["clean"]:
                return {"texts": req["texts"]}       # already pseudonymized by scrub()
            out = [self._pseudo(t, vault) if t.strip() else t for t in req["texts"]]
            self.save(root, vault)
            return {"texts": out}
        if op == "rehydrate":
            return {"texts": [self._rehydrate(t, vault) for t in req["texts"]]}
        if op == "spans":
            return {"spans": [self._people(self._spans(t, vault), vault) for t in req["texts"]]}
        if op == "check_prompt":
            return self.check_prompt(req["text"], root, vault)
        if op == "review":
            from review import review
            r = review(extract(pathlib.Path(req["path"])), vault)
            self.save(root, vault)
            entry = self._file_doubts(root, vault, req["path"])
            entry["seen"] = True
            self.save_state(root)
            return {"masked": r["masked"], "doubts": r["doubts"]}
        if op == "review_text":                          # browser: review a message before sending
            from review import review
            r = review(req["text"], vault)
            self.save(root, vault)
            return r
        if op == "review_file":                          # browser: a file attached to the chat
            from review import review
            text, why, notes = extract_why(req["name"], req["data"])
            if why or not text.strip():
                why = why or "empty"
                return {"unreadable": True, "why": why, "reason": formats.REASONS.get(why, why)}
            r = review(text, vault)
            stem, ext = os.path.splitext(req["name"])
            # the file name often holds a name too ("Contrat Dupont.docx")
            name = self._pseudo(stem, vault) + (ext if ext.lower() in (".txt", ".md", ".csv") else ".txt")
            self.save(root, vault)
            out = {**r, "original": text, "name": name, "notes": notes}
            keep = _layout(req["name"], req["data"])
            if keep:                                     # a Word file can go as a Word file
                out["layout"] = keep
            return out
        if op == "protect_file":                         # the protected copy itself, after the review
            text = extract_bytes(req["name"], req["data"])
            if not text.strip():
                return {"unreadable": True}
            stem, ext = os.path.splitext(req["name"])
            name = self._pseudo(stem, vault)
            if ext.lower() in docxfile.SUFFIXES and _is_docx(req["data"]):
                # a Word file stays a Word file: same layout, names replaced in place
                import base64
                data, report = docxfile.transform(base64.b64decode(req["data"]), lambda t: self._pseudo(t, vault))
                self.save(root, vault)
                return {"text": docxfile.text(data), "name": name + ext, "data": base64.b64encode(data).decode(),
                        "warnings": docxfile.warnings(report)}
            out = self._pseudo(text, vault)
            self.save(root, vault)
            return {"text": out, "name": name + (ext if ext.lower() in (".txt", ".md", ".csv") else ".txt")}
        if op == "restore_file":                         # app: an AI answer back with the real names
            text = extract_bytes(req["name"], req["data"])
            if not text.strip():
                return {"unreadable": True}
            restored = self._rehydrate(text, vault)
            stem, ext = os.path.splitext(req["name"])
            r = {"text": restored, "name": self._rehydrate(stem, vault) + ext,
                 "restored": len(self._spans(text, vault))}
            if ext.lower() in docxfile.SUFFIXES and _is_docx(req["data"]):
                import base64
                data, _ = docxfile.transform(base64.b64decode(req["data"]), lambda t: self._rehydrate(t, vault))
                r["data"] = base64.b64encode(data).decode()
            return r
        if op == "restore_path":                         # a file downloaded from the chat
            return self.restore_path(req["path"], vault)
        if op == "decide":
            vault.decide(req.get("mask", []), req.get("unmask", []))
            self.save(root, vault)
            return {"forced": len(vault.forced), "allowed": len(vault.allowed)}
        if op in ("notice", "gate") and req["path"] in self.state(root)["clean"]:
            return {"doubts": 0, "pending": 0}           # a copy already pseudonymized (tool-results)
        if op == "notice":
            entry = self._file_doubts(root, vault, req["path"])
            n = 0 if entry.get("notified") or entry.get("seen") else entry["doubts"]
            entry["notified"] = True
            self.save_state(root)
            return {"doubts": n}
        if op == "gate":
            entry = self._file_doubts(root, vault, req["path"])
            return {"pending": 0 if entry.get("seen") else entry["doubts"]}
        if op == "dump":
            return {"table": vault.to_dict()}
        if op == "load":
            # merging two tables could give one surrogate to two people: only into a new dossier
            if vault.rev:
                raise ValueError("ce dossier a déjà sa propre table : importer dans un dossier neuf")
            self.vaults[root] = self.Vault.from_dict(req["table"])
            self.save(root, self.vaults[root])
            return {"entries": len(self.vaults[root].rev)}
        raise ValueError(f"opération inconnue : {op}")

    def check_prompt(self, text, root, vault):
        """What in a typed message would reach the model in clear: known real values of the
        dossier, and what the detector flags that is not already a surrogate (the user
        pasting back the protected version must pass)."""
        from lexicon import rare_word
        fold, surrogates = self.fold, set(vault.rev)
        # on a short message the model alone is noisy ("3 lignes max" as an address,
        # "courrier.md" as an email): outside names, only what a rule confirms blocks
        ruled = {(s, e) for s, e, _, _ in self.detect_regex(text)}

        def blocking(span):
            s, e, lbl, val = span
            atoms = [fold(w) for w in re.findall(r"[\wÀ-ÿ'’-]+", val)]
            if vault.is_surrogate(val):
                return False
            # a lone capitalised word in a message is too often a tool, a product or a place
            # ("lance Bash", "ouvre Edit"): it blocks only when the dossier already knows it
            if lbl != "PER":
                return (s, e) in ruled
            return len([a for a in atoms if len(a) >= 2]) >= 2

        found = [f"{lbl} : {val}" for s, e, lbl, val in self.detect(text) if blocking((s, e, lbl, val))]
        # known values of the dossier, even alone and lowercase; common words excepted (a
        # false positive of a document must not block every message that says "chef")
        for real in list(vault.fwd) + [v for (_, v) in vault.memo if len(v) >= 3]:
            # "_" and "-" separate words in file names ("Bon_de_commande_Rousseau-Kaddour.docx")
            if (re.search(rf"(?<![^\W_]){re.escape(fold(real))}(?![^\W_])", fold(text))
                    and (" " in real or "@" in real or any(c.isdigit() for c in real) or rare_word(real, 4.0))):
                found.append(f"connu : {real}")
        if not found:
            return {"found": [], "protected": text}
        protected = vault.forward(self._pseudo(text, vault, only=blocking))
        self.save(root, vault)
        return {"found": sorted(set(found)), "protected": protected}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            req = json.loads(self.rfile.readline())
            if req.get("op") == "stop":
                SOCK.unlink(missing_ok=True)             # new clients start a fresh engine at once
                self.wfile.write(b'{"ok": true}\n')
                threading.Thread(target=self.server.shutdown).start()
                return
            reply = self.server.engine.handle(req)
        except Exception as e:                           # the client fails closed on any error
            reply = {"error": f"{type(e).__name__}: {e}"}
        self.wfile.write((json.dumps(reply, ensure_ascii=False) + "\n").encode())


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve():
    HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME, 0o700)                                # the socket gives out real names: owner only
    lock = open(HOME / "engine.lock", "w")
    import fcntl
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # one engine at a time
    except BlockingIOError:
        return
    if SOCK.exists():
        SOCK.unlink()
    engine = Engine()
    old = os.umask(0o077)
    server = Server(str(SOCK), Handler)
    os.umask(old)
    server.engine = engine
    print(f"moteur prêt ({time.strftime('%H:%M:%S')}, pid {os.getpid()})", flush=True)
    try:
        server.serve_forever()
    finally:
        SOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        serve()
    else:
        print(__doc__)
