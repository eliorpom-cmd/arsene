"""Claude Code hook client: a thin, stdlib-only process that forwards each event to the warm
engine (src/engine.py) and fails closed. Active only inside a folder that holds `.pseudo/`.

  PreToolUse        real values back into what Claude writes or runs; `.pseudo/` and
                    unreadable formats refused; network commands carrying real values refused
  PostToolUse       every string of the tool result pseudonymized before the model sees it
  UserPromptSubmit  a message with a real value or an @file is blocked, the protected
                    version goes to the clipboard
  MessageDisplay    the answer shows the real names on screen (transcript keeps surrogates)
  SessionStart      starts the engine so the first read does not wait for the model
"""
import shlex, json, os, pathlib, re, socket, subprocess, sys, time

SRC = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path(os.environ.get("PSEUDO_HOME", pathlib.Path.home() / "Library" / "Application Support" / "Pseudonymiseur"))
SOCK = HOME / "engine.sock"
WITHHELD = "[pseudonymiseur : contenu retenu, moteur indisponible]"
TOO_LONG = "[pseudonymiseur : sortie trop longue pour être protégée à temps, contenu retenu ; lis le fichier par morceaux (offset/limit) ou filtre la commande]"
# a hook that times out lets the original output through: refuse what cannot be done in time
# (~0.15 s per page of 3000 characters; the PostToolUse timeout is 300 s)
MAX_CHARS = 1_500_000

# tools whose input may carry surrogates back to the disk; every other tool (web, MCP,
# subagents) keeps the surrogates, so no real value leaves through it
LOCAL_TOOLS = {"Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep", "LS"}
UNREADABLE = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp",
              ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".odt", ".ods", ".pages", ".numbers", ".ipynb"}
NETWORK = re.compile(r"\b(curl|wget|ssh|scp|sftp|rsync|nc|ncat|telnet|ftp|http|https|gh|mail|sendmail)\b")
# byte dumps show a file character by character: no detector reads a name spelled that way
DUMPS = re.compile(r"\b(od|hexdump|xxd|base64|base32|strings|iconv|uuencode)\b|['\"]rb['\"]|\brepr\(|\.encode\(|\bbytes\(")
CONTEXT = ("Ce dossier est protégé par un pseudonymiseur local. Les noms, adresses, numéros et dates "
           "de naissance que tu lis dans les fichiers et les sorties de commandes ont été remplacés par "
           "des valeurs fictives cohérentes (même personne = même faux nom ; sexe, format et âge "
           "conservés). Travaille normalement avec ces valeurs : quand tu écris un fichier ou lances une "
           "commande locale, elles sont remplacées automatiquement par les vraies, et l'utilisateur voit "
           "les vraies à l'écran. Il est donc normal que les octets d'un fichier sur le disque diffèrent "
           "de ce que tu lis. Conséquence : l'outil Edit échoue (« String to replace not found ») dès que le "
           "passage à modifier contient une valeur pseudonymisée, car il compare au disque avant toute "
           "conversion. Pour modifier un tel passage, réécris le fichier entier avec Write, ou utilise "
           "sed -i / perl -i dans Bash. Pour lire un document Word, RTF ou HTML : "
           "textutil -convert txt -stdout <fichier> (déjà installé sur macOS). Ne cherche jamais à retrouver les valeurs réelles (hexdump, od, xxd, base64, "
           "lecture d'octets) : l'utilisateur a choisi de ne pas te les transmettre. N'évoque pas la "
           "pseudonymisation dans tes réponses (pas de « dans ma lecture », « nom remplacé ») : "
           "l'utilisateur voit les vrais noms à l'écran, tes remarques n'auraient pas de sens pour lui. Les commandes réseau "
           "qui contiendraient des valeurs réelles sont refusées.")
KINDS = {"PER": "un nom", "connu": "un nom ou une valeur du dossier", "EMAIL": "un email",
         "PHONE": "un téléphone", "ADDR": "une adresse", "IBAN": "un IBAN", "NIR": "un numéro de sécurité sociale",
         "DOB": "une date de naissance", "IDNUM": "un identifiant"}
SKIP_KEYS = {"type", "mode", "hookEventName", "interrupted", "isImage"}


def find_root(*paths):
    for p in paths:
        if not p:
            continue
        p = pathlib.Path(p).expanduser()
        for d in [p, *p.parents] if p.is_dir() else list(p.parents):
            if (d / ".pseudo").is_dir():
                return str(d)
    return None


def call(req, wait=60, start=True):
    """One request to the engine; start it if it is not running."""
    deadline, started = time.time() + wait, 0.0
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(max(5, deadline - time.time()) + 240)
                s.connect(str(SOCK))
                s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(1 << 16)
                    if not chunk:
                        break
                    buf += chunk
            if not buf:
                raise ConnectionRefusedError("moteur en cours d'arrêt")
            reply = json.loads(buf)
            if "error" in reply:
                raise RuntimeError(reply["error"])
            return reply
        except (FileNotFoundError, ConnectionRefusedError):
            # retry the start: right after a stop, the old engine may still hold the lock
            if start and time.time() - started > 5:
                start_engine()
                started = time.time()
            if not start or time.time() > deadline:
                raise
            time.sleep(0.2)


def start_engine():
    HOME.mkdir(parents=True, exist_ok=True)
    log = open(HOME / "engine.log", "a")
    subprocess.Popen([sys.executable, str(SRC / "engine.py"), "serve"], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(SRC))


def strings(obj, path=()):
    """Every (path, string) of a JSON value, skipping structural keys."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in SKIP_KEYS:
                yield from strings(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from strings(v, path + (i,))


def replaced(obj, values):
    """The same JSON value with its strings replaced in walk order."""
    it = iter(values)

    def walk(o, key=None):
        if isinstance(o, str):
            return o if key in SKIP_KEYS else next(it)
        if isinstance(o, dict):
            return {k: (v if k in SKIP_KEYS else walk(v, k)) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return walk(obj)


def out(payload):
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0)


def deny(reason, user=None):
    out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                "permissionDecisionReason": reason}, **({"systemMessage": user} if user else {})})


def review_mode(root):
    """"apres" (default): the user is told after the first read of a file with doubts;
    "avant": the file is not read until the user has reviewed it."""
    try:
        return json.loads((pathlib.Path(root) / ".pseudo" / "config.json").read_text()).get("relecture", "apres")
    except (OSError, ValueError):
        return "apres"


def review_message(n, path, root):
    """Counts only, and the path pseudonymized: Claude Code hands this message back to the
    model (a subagent's end-of-task notice), and a file name can hold a real name.
    `pseudo.py review` takes the pseudonymized path and finds the real file."""
    what = "1 passage peut encore identifier quelqu'un" if n == 1 else f"{n} passages peuvent encore identifier quelqu'un"
    try:
        path = call({"op": "pseudonymize", "root": root, "texts": [path]})["texts"][0]
    except Exception:
        path = "<fichier>"
    return (f"Pseudonymiseur : {what} dans ce fichier. Pour relire : "
            f"python {shlex.quote(str(pathlib.Path(__file__).with_name('pseudo.py')))} review {shlex.quote(path)}")


def pre_tool(d, root):
    name, inp = d.get("tool_name", ""), d.get("tool_input") or {}
    texts = [s for _, s in strings(inp)]
    if any(re.search(r"(^|[/\s'\"=])\.pseudo(/|\b)", s) for s in texts):
        deny("Accès refusé : .pseudo/ contient la table de correspondance du pseudonymiseur.")
    if name == "Read" and pathlib.Path(inp.get("file_path", "")).suffix.lower() in UNREADABLE:
        deny("Le pseudonymiseur ne sait pas encore protéger ce format (PDF, image, bureautique). "
             "Demandez à l'utilisateur une version texte (.md ou .txt) du document.")
    if name == "Read" and inp.get("file_path") and review_mode(root) == "avant":
        try:
            n = call({"op": "gate", "root": root, "path": inp["file_path"]})["pending"]
        except Exception as e:
            deny(f"Pseudonymiseur : moteur indisponible ({e}), lecture refusée par sécurité.")
        if n:
            deny("Ce fichier attend la relecture de l'utilisateur avant d'être lu. Préviens-le et "
                 "attends qu'il te dise de reprendre.", review_message(n, inp["file_path"], root))
    shell = name in ("Bash", "PowerShell")
    if shell and DUMPS.search(inp.get("command", "")):
        deny("Commande refusée par le pseudonymiseur : un affichage octet par octet contournerait la "
             "protection. Travaille avec le texte tel que tu le lis.")
    if name not in LOCAL_TOOLS:
        return
    back = call({"op": "rehydrate", "root": root, "texts": texts})["texts"]
    if back == texts:
        return
    new = replaced(inp, back)
    if shell and NETWORK.search(new.get("command", "")):
        deny("Commande réseau refusée : elle enverrait hors de la machine des valeurs réelles "
             "que le pseudonymiseur a masquées.")
    out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": new,
                                "permissionDecision": decision(d, name, new, root),
                                "permissionDecisionReason": "Pseudonymiseur : valeurs réelles remises à l'écriture."}})


# commands that only read: a rehydrated one needs no more consent than the original
READ_ONLY = {"ls", "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "find", "wc", "textutil",
             "sed", "awk", "sort", "uniq", "cut", "file", "stat", "diff", "echo", "printf", "tr", "nl",
             "pwd", "basename", "dirname", "realpath", "column", "jq", "mdls", "pdftotext", "true", "cd"}
EDITORS = {"sed", "perl"}


def _within(path, root):
    base, target = pathlib.Path(root).resolve(), pathlib.Path(path).expanduser().resolve()
    return target == base or base in target.parents


def _shell_decision(cmd, mode, root, cwd):
    """allow: every piece only reads, or edits files of the dossier in a mode that accepts
    edits; anything else (redirections outside, deletions, installs, scripts) is asked."""
    edits_ok = mode in ("acceptEdits", "auto")
    if re.search(r"`|\$\(|<\(|\beval\b|\bxargs\b", cmd):
        return "ask"
    for piece in re.split(r"&&|\|\||;|\||\n", cmd):
        words = piece.strip().split()
        if not words:
            continue
        prog = words[0]
        for target in re.findall(r"(?<![0-9&])>>?\s*([^\s;&|]+)", piece):
            if target != "/dev/null" and not (edits_ok and _within(pathlib.Path(cwd, target), root)):
                return "ask"
        if prog == "find" and re.search(r"-(exec|execdir|delete|ok)\b", piece):
            return "ask"
        if prog == "textutil" and "-stdout" not in words:
            if not edits_ok:
                return "ask"
        if prog in EDITORS and re.search(r"\s-[a-zA-Z]*i", piece):
            if not edits_ok:
                return "ask"
            continue
        if prog == "perl" or prog not in READ_ONLY:
            return "ask"
    return "allow"


def decision(d, name, new, root):
    """Claude Code applies updatedInput only with a decision. "allow" skips the permission
    prompt, so give it only where Claude Code would not have asked anyway; else "ask", which
    shows the user the command with the real values."""
    mode = d.get("permission_mode", "default")
    if mode == "bypassPermissions":
        return "allow"
    if name in ("Bash", "PowerShell"):
        return _shell_decision(new.get("command", ""), mode, root, d.get("cwd") or root)
    path = new.get("file_path") or new.get("notebook_path") or new.get("path") or ""
    inside = bool(path) and _within(path, root)
    # long outputs Claude Code saved for this session: reading them back is part of the tool
    own = bool(path) and "/.claude/projects/" in path and "/tool-results/" in path
    if name in ("Read", "Glob", "Grep", "LS") and (inside or own or not path):
        return "allow"
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit") and inside and mode in ("acceptEdits", "auto"):
        return "allow"
    return "ask"


def scrub(d, root):
    """Long outputs saved in clear by Claude Code: pseudonymize them on disk (engine side)."""
    tp = d.get("transcript_path")
    if not tp:
        return
    folder = pathlib.Path(tp).with_suffix("") / "tool-results"
    if folder.is_dir():
        paths = [str(p) for p in folder.iterdir() if p.is_file() and not p.name.endswith(".tmp")]
        if paths:
            call({"op": "scrub", "root": root, "paths": paths})


def post_tool(d, root):
    resp = d.get("tool_response")
    texts = [s for _, s in strings(resp)]
    if not any(t.strip() for t in texts):
        return
    if sum(len(t) for t in texts) > MAX_CHARS:
        out({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                    "updatedToolOutput": replaced(resp, [TOO_LONG if t.strip() else t for t in texts])}})
    try:
        path = (d.get("tool_input") or {}).get("file_path") if d.get("tool_name") == "Read" else None
        safe = call({"op": "pseudonymize", "root": root, "texts": texts, "path": path})["texts"]
    except Exception:
        safe = [WITHHELD if t.strip() else t for t in texts]      # fail closed
    payload = {}
    if safe != texts:
        payload["hookSpecificOutput"] = {"hookEventName": "PostToolUse", "updatedToolOutput": replaced(resp, safe)}
    if path and review_mode(root) == "apres":
        try:
            n = call({"op": "notice", "root": root, "path": path})["doubts"]
        except Exception:
            n = 0                                       # the notice is a help, never a reason to fail
        if n:
            payload["systemMessage"] = review_message(n, path, root)
    if payload:
        out(payload)


# Claude Code titles a session from its first message with a model call, even when this hook
# blocks the message: a fixed title given by the hook replaces that call
TITLE = {"hookEventName": "UserPromptSubmit", "sessionTitle": "Dossier protégé"}


def prompt(d, root):
    text = d.get("prompt", "")
    cwd = pathlib.Path(d.get("cwd") or ".")
    refs = [m.group(1) for m in re.finditer(r"(?<![\w.])@([^\s@]+)", text)
            if (cwd / m.group(1).rstrip(".,;:!?)")).expanduser().exists()]
    if refs:
        out({"decision": "block", "hookSpecificOutput": TITLE, "reason":
             "Pseudonymiseur : une mention @fichier envoie le fichier en clair (sans passer par les outils). "
             f"Écrivez plutôt « lis {refs[0]} » : la lecture sera protégée."})
    try:
        # a document added during the session: index it now (only new or changed files are read)
        call({"op": "index", "root": root})
        r = call({"op": "check_prompt", "root": root, "text": text})
    except Exception as e:
        out({"decision": "block", "hookSpecificOutput": TITLE, "reason": f"Pseudonymiseur : moteur indisponible ({e}), message retenu."})
    if r["found"]:
        if os.environ.get("PSEUDO_CLIPBOARD_FILE"):      # tests: never touch the user's clipboard
            pathlib.Path(os.environ["PSEUDO_CLIPBOARD_FILE"]).write_text(r["protected"])
        else:
            subprocess.run(["pbcopy"], input=r["protected"].encode(), check=False)
        # the block message is written to the transcript on disk: name the kinds, never the values,
        # and keep the original prompt out of it
        kinds = sorted({KINDS.get(f.split(" : ")[0], "valeur") for f in r["found"]})
        # The Mac app, allowed to (Accessibility), sends the protected version itself: it gets
        # the text (doubles only) and a Darwin notification; the clipboard stays the fallback.
        auto = (HOME / "resend-on").exists() and not os.environ.get("PSEUDO_CLIPBOARD_FILE")
        if auto:
            f = HOME / "resend.json"
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump({"text": r["protected"], "t": time.time()}, fh, ensure_ascii=False)
            subprocess.run(["/usr/bin/notifyutil", "-p", "dev.byelior.arsene.resend"], check=False)
        out({"decision": "block", "suppressOriginalPrompt": True, "hookSpecificOutput": TITLE, "reason":
             "Pseudonymiseur : message retenu, il contient " + ", ".join(kinds) + " en clair. "
             + ("Arsène envoie la version protégée à sa place." if auto
                else "Version protégée copiée : collez-la (⌘V) puis Entrée.")})
    out({"hookSpecificOutput": TITLE})


def display(d, root):
    delta = d.get("delta", "")
    if delta.strip():
        back = call({"op": "rehydrate", "root": root, "texts": [delta]}, wait=5)["texts"][0]
        if back != delta:
            out({"hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": back}})


def session_start(d, root):
    # start the engine and index the dossier in the background: the session opens at once,
    # and the first message or read waits on the engine lock until the index is done
    subprocess.Popen([sys.executable, __file__, "index", root], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": CONTEXT}})


def main():
    d = json.load(sys.stdin)
    event = d.get("hook_event_name", "")
    inp = d.get("tool_input") or {}
    root = find_root(d.get("cwd"), inp.get("file_path"), inp.get("path"), inp.get("notebook_path"))
    if not root:
        return                                      # not a protected folder: do nothing
    handler = {"PreToolUse": pre_tool, "PostToolUse": post_tool, "UserPromptSubmit": prompt,
               "MessageDisplay": display, "SessionStart": session_start}.get(event)
    try:
        if event in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop"):
            scrub(d, root)
        if handler:
            handler(d, root)
    except SystemExit:
        raise
    except Exception as e:
        if event == "PreToolUse":
            deny(f"Pseudonymiseur : moteur indisponible ({e}), action refusée par sécurité.")
        if event == "PostToolUse":
            out({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                        "updatedToolOutput": replaced(d.get("tool_response"),
                                                                      [WITHHELD for _ in strings(d.get("tool_response"))])}})
        if event == "UserPromptSubmit":
            out({"decision": "block", "reason": f"Pseudonymiseur : erreur ({e}), message retenu."})


if __name__ == "__main__":
    if sys.argv[1:2] == ["index"]:
        call({"op": "index", "root": sys.argv[2]}, wait=120)
    else:
        main()
