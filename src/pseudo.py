"""Protect a folder for Claude Code.

  python src/pseudo.py install [dossier]     .pseudo/ + hooks in .claude/settings.local.json
  python src/pseudo.py uninstall [dossier]   removes the hooks, keeps the table
  python src/pseudo.py status [dossier]
  python src/pseudo.py stop                  stops the warm engine

The table (real value <-> surrogate), to share pseudonymized documents with a colleague:
  python src/pseudo.py table [dossier]                  shows it
  python src/pseudo.py export [dossier] [fichier]       encrypted copy + a passphrase to dictate
  python src/pseudo.py import <fichier> [dossier]       into a new dossier (asks the passphrase)
  python src/pseudo.py protect <document>               pseudonymized copy, to send (a .docx stays a .docx, layout kept)
  python src/pseudo.py restore <document> [table]       real names back (dossier table, or a received one)
Review before sending (what was masked, what may still identify someone):
  python src/pseudo.py review <document>                lists both
  python src/pseudo.py mask <dossier> <valeur> [TYPE]   mask it too, everywhere, from now on
  python src/pseudo.py unmask <dossier> <valeur>        never mask it again
  python src/pseudo.py relecture <dossier> apres|avant  told after the first read (default), or no read before review

The document and the table travel by two different channels, the passphrase by a third
(phone): the table alone lists every real name of the dossier.
"""
import json, os, pathlib, re, shlex, sys

SRC = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
from hook import call, SOCK

HOOK = SRC / "hook.py"
EVENTS = {  # event -> (matcher, timeout in seconds)
    "SessionStart": (None, 10),
    "UserPromptSubmit": (None, 120),
    "PreToolUse": ("*", 120),
    "PostToolUse": ("*", 300),
    "MessageDisplay": (None, 30),
    "Stop": (None, 120),              # last chance to scrub long outputs saved in clear
}
DENY = ["Read(./.pseudo/**)", "Edit(./.pseudo/**)"]   # Edit covers every file-writing tool


def ours(entry):
    # any Arsène hook, from the repo or from the app (a reinstall from the other replaces it)
    return any(str(HOOK) in h.get("command", "") or re.search(r"hook\.py'? \|\| exit 2$", h.get("command", ""))
               for h in entry.get("hooks", []))


def settings_path(folder):
    return folder / ".claude" / "settings.local.json"


def install(folder):
    (folder / ".pseudo").mkdir(mode=0o700, exist_ok=True)
    (folder / ".pseudo" / ".gitignore").write_text("*\n")
    path = settings_path(folder)
    path.parent.mkdir(exist_ok=True)
    cfg = json.loads(path.read_text()) if path.exists() else {}
    hooks = cfg.setdefault("hooks", {})
    # exit 2 is the only failure Claude Code treats as a refusal: if the interpreter is gone
    # (venv deleted), a read is refused instead of passing in clear
    # -B: the app's Python never writes bytecode into the signed app
    command = f"{shlex.quote(sys.executable)} -B {shlex.quote(str(HOOK))} || exit 2"
    for event, (matcher, timeout) in EVENTS.items():
        entries = [e for e in hooks.get(event, []) if not ours(e)]
        entry = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher:
            entry["matcher"] = matcher
        hooks[event] = entries + [entry]
    perms = cfg.setdefault("permissions", {})
    perms["deny"] = [r for r in perms.get("deny", []) if "(./.pseudo/**)" not in r] + DENY
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    # never commit the table nor these local hooks
    exclude = folder / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        have = exclude.read_text() if exclude.exists() else ""
        add = [p for p in (".pseudo/", ".claude/settings.local.json") if p not in have.split("\n")]
        if add:
            exclude.write_text(have.rstrip("\n") + ("\n" if have else "") + "\n".join(add) + "\n")
    print(f"Dossier protégé : {folder}\nLancez Claude Code dans ce dossier (claude). "
          "Le moteur démarre à l'ouverture de la session.")


def uninstall(folder):
    path = settings_path(folder)
    if not path.exists():
        return print("Rien à retirer.")
    cfg = json.loads(path.read_text())
    for event in list(cfg.get("hooks", {})):
        cfg["hooks"][event] = [e for e in cfg["hooks"][event] if not ours(e)]
        if not cfg["hooks"][event]:
            del cfg["hooks"][event]
    if not cfg.get("hooks"):
        cfg.pop("hooks", None)
    deny = cfg.get("permissions", {}).get("deny", [])
    if "permissions" in cfg:
        cfg["permissions"]["deny"] = [r for r in deny if "(./.pseudo/**)" not in r]
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print(f"Hooks retirés de {path}. La table reste dans {folder / '.pseudo'}.")


def status(folder):
    path = settings_path(folder)
    installed = path.exists() and any(ours(e) for es in json.loads(path.read_text()).get("hooks", {}).values() for e in es)
    print(f"dossier : {folder}\nprotégé : {'oui' if installed and (folder / '.pseudo').is_dir() else 'non'}")
    try:
        print(f"moteur : prêt (pid {call({'op': 'ping'}, start=False)['pid']})")
    except Exception:
        print("moteur : arrêté")


def dossier_of(path):
    """The protected dossier holding a path: the nearest folder with a .pseudo/."""
    for d in [path, *path.parents]:
        if (d / ".pseudo").is_dir():
            return d
    sys.exit(f"{path} n'est dans aucun dossier protégé (python src/pseudo.py install <dossier>).")


def phrase(prompt):
    import getpass
    return os.environ.get("PSEUDO_PHRASE") or getpass.getpass(prompt)


def table(folder):
    t = call({"op": "dump", "root": str(dossier_of(folder))})["table"]
    rows = [(label, real, sur) for label, real, sur in t["memo"]]
    shown = {real.lower() for _, real, _ in rows}
    rows += [("nom", real, sur) for real, sur in t["fwd"].items() if real not in shown]
    if not rows:
        return print("Table vide.")
    width = max(len(r[1]) for r in rows)
    for label, real, sur in sorted(rows, key=lambda r: (r[0], r[1].lower())):
        print(f"{label:<10} {real:<{width}}  ->  {sur}")
    print(f"\n{len(rows)} valeurs.")


def export(folder, out=None):
    from tablefile import seal, new_phrase
    root = dossier_of(folder)
    out = pathlib.Path(out or f"{root.name}.pseudotable").resolve()
    words = os.environ.get("PSEUDO_PHRASE") or new_phrase()
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(seal(call({"op": "dump", "root": str(root)})["table"], words))
    print(f"Table chiffrée : {out}\nPhrase de passe (à dicter, jamais dans le même envoi) :\n\n    {words}\n")


def import_(src, folder):
    from tablefile import unseal
    try:
        t = unseal(pathlib.Path(src).read_bytes(), phrase("Phrase de passe : "))
    except ValueError as e:
        sys.exit(str(e))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".pseudo").mkdir(mode=0o700, exist_ok=True)
    try:
        n = call({"op": "load", "root": str(folder), "table": t})["entries"]
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"{n} correspondances importées dans {folder}.")


def _copy_name(path, tag):
    import docxfile, formats
    stem = path.stem.removesuffix(".pseudonymise")
    ext = path.suffix.lower()
    # a Word file stays a Word file; any other document becomes its text
    return path.with_name(f"{stem}.{tag}{'.txt' if ext in formats.DOCUMENTS and ext not in docxfile.SUFFIXES else path.suffix}")


def _word(path):
    import docxfile
    return path.suffix.lower() in docxfile.SUFFIXES


def protect(path):
    from engine import extract
    root = dossier_of(path.parent)
    if _word(path):                                    # a Word file stays a Word file, layout kept
        import base64
        try:
            r = call({"op": "protect_file", "root": str(root), "name": path.name,
                      "data": base64.b64encode(path.read_bytes()).decode()})
        except RuntimeError as e:
            sys.exit(f"{path.name} : {e}")
        if r.get("unreadable"):
            sys.exit(f"{path.name} : document vide ou illisible.")
        out = _copy_name(path, "pseudonymise")
        out.write_bytes(base64.b64decode(r["data"]) if "data" in r else r["text"].encode())
        print(f"Copie pseudonymisée : {out}")
        for w in r.get("warnings", []):
            print(f"  attention : {w}")
        return
    import formats
    text, why = formats.read(path.name, path.read_bytes())
    if why:
        sys.exit(f"{path.name} : non lu ({formats.REASONS.get(why, why)}).")
    out = _copy_name(path, "pseudonymise")
    out.write_text(call({"op": "pseudonymize", "root": str(root), "texts": [text]})["texts"][0])
    print(f"Copie pseudonymisée : {out}")


def restore(path, received=None):
    from engine import extract
    if _word(path):
        return _restore_word(path, received)
    text = extract(path)
    if received:                                       # a table received from a colleague, never stored
        from tablefile import unseal
        from surrogate import Vault
        from rehydrate import rehydrate
        try:
            vault = Vault.from_dict(unseal(pathlib.Path(received).read_bytes(), phrase("Phrase de passe : ")))
        except ValueError as e:
            sys.exit(str(e))
        real = rehydrate(text, vault)
    else:
        real = call({"op": "rehydrate", "root": str(dossier_of(path.parent)), "texts": [text]})["texts"][0]
    out = _copy_name(path, "retabli")
    out.write_text(real)
    print(f"Copie avec les vrais noms : {out}")


def _restore_word(path, received=None):
    """A Word file back with the real names, layout kept (.docx in, .docx out)."""
    import base64, docxfile
    out = _copy_name(path, "retabli")
    if received:                                       # a table received from a colleague, never stored
        from tablefile import unseal
        from surrogate import Vault
        from rehydrate import rehydrate
        try:
            vault = Vault.from_dict(unseal(pathlib.Path(received).read_bytes(), phrase("Phrase de passe : ")))
            data, _ = docxfile.transform(path.read_bytes(), lambda t: rehydrate(t, vault))
        except ValueError as e:
            sys.exit(str(e))
    else:
        try:
            r = call({"op": "restore_file", "root": str(dossier_of(path.parent)), "name": path.name,
                      "data": base64.b64encode(path.read_bytes()).decode()})
        except RuntimeError as e:
            sys.exit(f"{path.name} : {e}")
        if "data" not in r:
            sys.exit(f"{path.name} : document vide ou illisible.")
        data = base64.b64decode(r["data"])
    out.write_bytes(data)
    print(f"Copie avec les vrais noms : {out}")


def real_path(path):
    """The Claude Code notice gives the path pseudonymized: find the real file back."""
    if path.exists():
        return path
    for d in [pathlib.Path.cwd(), *path.parents]:
        if (d / ".pseudo").is_dir():
            real = pathlib.Path(call({"op": "rehydrate", "root": str(d), "texts": [str(path)]})["texts"][0])
            if real.exists():
                return real
    sys.exit(f"{path} : fichier introuvable.")


def review(path):
    path = real_path(path)
    root = dossier_of(path.parent)
    r = call({"op": "review", "root": str(root), "path": str(path)})
    if _word(path):                                    # same lines as the engine read (docxfile)
        from engine import extract
        text = extract(path)
    else:
        import formats
        text = path.read_text() if path.suffix.lower() not in formats.DOCUMENTS else None
    seen, masked = set(), []
    for m in r["masked"]:
        key = (m["type"], m["value"].lower())
        if key not in seen:
            seen.add(key); masked.append(m)
    print(f"Masqué ({len(masked)} valeurs) :")
    for m in sorted(masked, key=lambda m: (m["type"], m["value"].lower())):
        print(f"  {m['type']:<6} {m['value']}  ->  {m['surrogate']}")
    print(f"\nÀ vérifier ({len(r['doubts'])}) :" if r["doubts"] else "\nRien de douteux.")
    for i, d in enumerate(r["doubts"], 1):
        ctx = ""
        if text:
            a, b = max(0, d["start"] - 30), min(len(text), d["end"] + 30)
            ctx = " ".join((text[a:d["start"]] + "[" + d["value"] + "]" + text[d["end"]:b]).split())
        print(f"  {i}. {d['value']!r} ({d['reason']})" + (f"\n     ...{ctx}..." if ctx else ""))
    if r["doubts"]:
        here = shlex.quote(str(root))
        print(f"\nMasquer : python {shlex.quote(__file__)} mask {here} \"valeur\"\n"
              f"Laisser toujours en clair : python {shlex.quote(__file__)} unmask {here} \"valeur\"\n"
              "Claude voit la nouvelle version à sa prochaine lecture du fichier.")


def decide(folder, value, mask=True, kind="PII"):
    root = dossier_of(folder)
    req = {"op": "decide", "root": str(root), "mask": [[value, kind.upper()]] if mask else [], "unmask": [] if mask else [value]}
    call(req)
    print(f"« {value} » sera {'masqué partout' if mask else 'laissé en clair'} dans {root.name}.")


def review_mode(folder, mode):
    if mode not in ("apres", "avant"):
        sys.exit("relecture : apres (prévenu après la première lecture) ou avant (pas de lecture sans relecture)")
    cfg = dossier_of(folder) / ".pseudo" / "config.json"
    d = json.loads(cfg.read_text()) if cfg.exists() else {}
    d["relecture"] = mode
    cfg.write_text(json.dumps(d, indent=2) + "\n")
    print("Relecture : " + ("Claude ne lit un fichier douteux qu'après ta relecture." if mode == "avant"
                            else "tu es prévenu après la première lecture d'un fichier douteux."))


def stop():
    try:
        call({"op": "stop"}, start=False)
        print("Moteur arrêté.")
    except Exception:
        print("Moteur déjà arrêté.")


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    folder = pathlib.Path(args[1] if len(args) > 1 else ".").resolve()
    arg = lambda i: args[i] if len(args) > i else None
    if cmd == "stop":
        stop()
    elif cmd == "start":
        # the browser host lives outside ~/Documents and cannot start the engine itself.
        # Always a fresh engine: one already running may hold older code (unknown ops).
        stop()
        call({"op": "ping"}, wait=120)                  # retries until the old engine frees its lock
        print("moteur prêt")
    elif cmd == "table":
        table(folder)
    elif cmd == "export":
        export(folder, arg(2))
    elif cmd == "import" and arg(1):
        import_(arg(1), pathlib.Path(arg(2) or ".").resolve())
    elif cmd == "protect" and arg(1):
        protect(folder)
    elif cmd == "restore" and arg(1):
        restore(folder, arg(2))
    elif cmd == "review" and arg(1):
        review(folder)
    elif cmd in ("mask", "unmask") and arg(2):
        decide(folder, arg(2), cmd == "mask", arg(3) or "PII")
    elif cmd == "relecture" and arg(2):
        review_mode(folder, arg(2))
    elif cmd in ("install", "uninstall", "status"):
        globals()[cmd](folder)
    else:
        print(__doc__)
