"""Native messaging host: the browser extension talks to the warm engine through this.

Chrome/Safari speak length-prefixed JSON on stdin/stdout. Each message:
    {"id": n, "op": "pseudonymize" | "spans" | "people", "dossier": "name", "texts": [...]}
and the reply echoes the id: {"id": n, "texts": [...]} / {"id": n, "spans": [...]} /
{"id": n, "people": count} (toolbar popup) / {"id": n, "review": {...}} or {"id": n, "error": "..."}.
"review" (texts[0]) returns what is masked and the doubtful passages left in clear, with real
values, for the review before sending; "decide" (mask [[value, type]], unmask [value], forget [value]) keeps
the user's choices in the dossier's table. "file" (name, data in base64) does the review of
an attached file: {"file": {..., "original", "name", "notes", "layout"?}} or {"file": {"unreadable": true,
"why", "reason"}}. "protect" (name, data) gives the protected Word file itself: {"file": {"name", "data"}}.
"realname" (name) gives a downloaded file's name with the real names, for Chrome's downloads list;
"download" (path) rewrites a file downloaded from the chat in place with the real names. Both come
from the extension's background only (it refuses them from pages): their answers never reach a site.

"spans" never returns rehydrated text, only where each surrogate sits and its real value:
the extension shows real names in a closed shadow copy of each block instead of writing
them into the page, so the chat site's own scripts only ever see surrogates.

A browser dossier is a folder under Arsène's support directory holding `.pseudo/`,
so the table stays on the Mac and is shared with the rest of Arsène.
"""
import json, pathlib, re, struct, sys

HERE = pathlib.Path(__file__).resolve().parent
# install.sh puts src/ and this host side by side in ~/Library/Application Support, outside
# ~/Documents: Chrome launches hosts as their own process, which macOS refuses ~/Documents.
SRC = HERE.parents[1] / "src"
sys.path.insert(0, str(SRC))
import hook  # noqa: E402  (engine client)
STOPPED = "Arsène ne démarre pas : voir engine.log"


def engine(req):
    # installed by install.sh outside ~/Documents: the host starts the engine itself
    try:
        return hook.call(req)
    except (FileNotFoundError, ConnectionRefusedError):
        raise RuntimeError(STOPPED)
    except TimeoutError:
        raise RuntimeError("Arsène démarre encore : réessayez dans un instant")

DOSSIERS = hook.HOME / "dossiers"


def root(name):
    # Only a plain name: never let a page choose a path on the disk.
    name = re.sub(r"[^\w\- ]", "", name or "navigateur").strip() or "navigateur"
    path = DOSSIERS / name
    (path / ".pseudo").mkdir(parents=True, exist_ok=True)
    return str(path)


def read():
    head = sys.stdin.buffer.read(4)
    if len(head) < 4:
        return None
    return json.loads(sys.stdin.buffer.read(struct.unpack("<I", head)[0]))


def write(msg):
    data = json.dumps(msg, ensure_ascii=False).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
    sys.stdout.buffer.flush()


def main():
    # when the extension last reached the host (a time, nothing else)
    try:
        (hook.HOME / "host-seen").write_text(str(int(__import__("time").time())))
    except OSError:
        pass
    while (msg := read()) is not None:
        try:
            if msg.get("op") not in ("pseudonymize", "spans", "people", "review", "decide", "file", "protect", "realname", "download", "ping"):
                raise ValueError("opération refusée")
            if msg["op"] == "ping":
                write({"id": msg.get("id"), "ok": True})
                continue
            if msg["op"] == "people":          # how many people the dossier protects, never who
                table = engine({"op": "dump", "root": root(msg.get("dossier"))})["table"]
                names = {v.casefold() for label, v, _ in table["memo"] if label == "PER"}
                # "Moreau" alone is the same person as "Julien Moreau": count full names only
                whole = [n for n in names if not any(n != m and set(n.split()) <= set(m.split()) for m in names)]
                write({"id": msg.get("id"), "people": len(whole)})
                continue
            if msg["op"] == "review":
                r = engine({"op": "review_text", "root": root(msg.get("dossier")), "text": msg["texts"][0]})
                write({"id": msg.get("id"), "review": {k: r[k] for k in ("text", "masked", "doubts")}})
                continue
            if msg["op"] == "file":
                r = engine({"op": "review_file", "root": root(msg.get("dossier")), "name": str(msg["name"]), "data": msg["data"]})
                reply = {"id": msg.get("id"), "file": r}
                # Chrome drops any message over 1 MB from the host
                if len(json.dumps(reply, ensure_ascii=False).encode()) > 1_000_000:
                    raise ValueError("fichier trop long pour Arsène (environ 150 pages au plus pour l'instant)")
                write(reply)
                continue
            if msg["op"] == "protect":             # a Word file attached as a Word file, after the review
                r = engine({"op": "protect_file", "root": root(msg.get("dossier")), "name": str(msg["name"]), "data": msg["data"]})
                reply = {"id": msg.get("id"), "file": {k: r[k] for k in ("name", "data", "warnings") if k in r}}
                if "data" not in r or len(json.dumps(reply).encode()) > 1_000_000:
                    raise ValueError("copie Word trop lourde pour Arsène")   # the content script falls back to text
                write(reply)
                continue
            if msg["op"] == "realname":            # a downloaded file's name, for the downloads list
                name = str(msg["name"])[:255]
                write({"id": msg.get("id"), "name": engine({"op": "rehydrate", "root": root(msg.get("dossier")), "texts": [name]})["texts"][0]})
                continue
            if msg["op"] == "download":            # the file itself, rewritten by the engine
                r = engine({"op": "restore_path", "root": root(msg.get("dossier")), "path": str(msg["path"])})
                write({"id": msg.get("id"), "download": {k: r[k] for k in ("restored", "kind", "skipped") if k in r}})
                continue
            if msg["op"] == "decide":
                engine({"op": "decide", "root": root(msg.get("dossier")),
                        "mask": [[str(v), str(t)] for v, t in msg.get("mask", [])], "unmask": [str(v) for v in msg.get("unmask", [])],
                        "forget": [str(v) for v in msg.get("forget", [])]})
                write({"id": msg.get("id"), "ok": True})
                continue
            req = {"root": root(msg.get("dossier")), "texts": msg["texts"]}
            if msg["op"] == "pseudonymize":
                write({"id": msg.get("id"), "texts": engine({"op": "pseudonymize", **req})["texts"]})
            else:
                req["context"] = str(msg.get("context", ""))[:20000]
                write({"id": msg.get("id"), "spans": engine({"op": "spans", **req})["spans"]})
        except Exception as e:  # the extension must always get an answer, never hang
            write({"id": msg.get("id"), "error": str(e)})


if __name__ == "__main__":
    main()
