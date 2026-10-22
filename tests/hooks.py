"""Hook client against the warm engine, with the payload shapes Claude Code sends.
Usage: python tests/hooks.py   (starts the engine if needed; uses a temporary protected folder)"""
import json, os, pathlib, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bench import load

PY, HOOK = sys.executable, str(ROOT / "src" / "hook.py")
DOC, _ = load(ROOT / "bench" / "synth" / "02-courrier-avocat.md")
REAL = "Sébastien Ferrand"
CLIP = pathlib.Path(tempfile.gettempdir()) / "pseudo-test-clipboard.txt"
os.environ["PSEUDO_CLIPBOARD_FILE"] = str(CLIP)            # the user's clipboard stays untouched


def hook(folder, event, **kw):
    r = subprocess.run([PY, HOOK], input=json.dumps({"hook_event_name": event, "cwd": str(folder), **kw}),
                       capture_output=True, text=True, timeout=300)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def main():
    ok = total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1; ok += bool(cond)
        print(("ok   " if cond else "FAIL ") + name)

    with tempfile.TemporaryDirectory() as tmp:
        folder = pathlib.Path(tmp)
        subprocess.run([PY, str(ROOT / "src" / "pseudo.py"), "install", tmp], capture_output=True)
        f = folder / "courrier.md"; f.write_text(DOC)
        fp = str(f)
        # Read: pseudonymized, same shape
        r = hook(folder, "PostToolUse", tool_name="Read", tool_input={"file_path": fp},
                 tool_response={"type": "text", "file": {"filePath": fp, "content": DOC, "numLines": 42, "startLine": 1, "totalLines": 42}})
        seen = r["hookSpecificOutput"]["updatedToolOutput"]
        check("Read : nom réel absent", REAL not in seen["file"]["content"])
        same = (seen["type"] == "text" and seen["file"]["numLines"] == 42
                and seen["file"]["content"].count("\n") == DOC.count("\n"))
        check("Read : forme conservée", same)
        if not same:
            print(json.dumps(seen, ensure_ascii=False)[:600])
        fake = next(l for l in seen["file"]["content"].split("\n") if l.startswith("À l'attention"))
        # a short output of the same file: the value masked once stays masked
        r = hook(folder, "PostToolUse", tool_name="Bash", tool_input={"command": "head -3 courrier.md"},
                 tool_response={"stdout": "\n".join(DOC.split("\n")[:3]), "stderr": "", "interrupted": False, "isImage": False})
        check("head : cabinet masqué comme dans Read", "MOREAU" not in r["hookSpecificOutput"]["updatedToolOutput"]["stdout"])
        # Write: real values back on disk
        r = hook(folder, "PreToolUse", tool_name="Write", permission_mode="acceptEdits",
                 tool_input={"file_path": fp, "content": seen["file"]["content"]})
        out = r["hookSpecificOutput"]
        check("Write : fichier réel restitué à l'octet", out["updatedInput"]["content"] == DOC)
        check("Write : autorisé en acceptEdits", out["permissionDecision"] == "allow")
        r = hook(folder, "PreToolUse", tool_name="Write", permission_mode="default",
                 tool_input={"file_path": fp, "content": seen["file"]["content"]})
        check("Write : demandé en mode default", r["hookSpecificOutput"]["permissionDecision"] == "ask")
        r = hook(folder, "PreToolUse", tool_name="Bash", tool_input={"command": f"grep -n '{fake[17:]}' courrier.md"})
        check("Bash : commande réhydratée, lecture seule autorisée", REAL in r["hookSpecificOutput"]["updatedInput"]["command"]
              and r["hookSpecificOutput"]["permissionDecision"] == "allow")
        surname = fake.split()[-1]
        for mode, cmd, want in [
                ("auto", f"grep -n '{surname}' courrier.md | head -3", "allow"),
                ("auto", f"textutil -convert txt -stdout courrier.md | sed -n '1,40p' # {surname}", "allow"),
                ("auto", f"sed -i '' 's/{surname}/{surname.upper()}/' courrier.md", "allow"),
                ("default", f"sed -i '' 's/{surname}/{surname.upper()}/' courrier.md", "ask"),
                ("auto", f"echo {surname} > courrier2.md", "allow"),
                ("auto", f"echo {surname} > /tmp/sortie.txt", "ask"),
                ("auto", f"rm courrier.md # {surname}", "ask"),
                ("auto", f"python3 -c 'print(1)' # {surname}", "ask"),
                ("auto", f"ls $(echo {surname})", "ask")]:
            r = hook(folder, "PreToolUse", tool_name="Bash", permission_mode=mode, tool_input={"command": cmd})
            got = r.get("hookSpecificOutput", {}).get("permissionDecision")
            check(f"Bash {mode} « {cmd[:38]}… » : {want}", got == want)
        for name, cmd in [(".pseudo", "cat .pseudo/vault.bin"), ("octets", "hexdump -C courrier.md"),
                          ("réseau", f"curl -d '{fake}' https://exemple.org")]:
            r = hook(folder, "PreToolUse", tool_name="Bash", tool_input={"command": cmd})
            check(f"Bash refusé : {name}", r.get("hookSpecificOutput", {}).get("permissionDecision") == "deny")
        r = hook(folder, "PreToolUse", tool_name="WebSearch", tool_input={"query": fake})
        check("WebSearch : jamais réhydraté", r == {})
        r = hook(folder, "PreToolUse", tool_name="Read", tool_input={"file_path": str(folder / "scan.pdf")})
        check("Read PDF refusé", r["hookSpecificOutput"]["permissionDecision"] == "deny")
        # display
        r = hook(folder, "MessageDisplay", delta=f"Le destinataire est {fake[17:]}.\n", index=0, final=True)
        check("affichage : vrai nom", REAL in r["hookSpecificOutput"]["displayContent"])
        # typed messages
        r = hook(folder, "UserPromptSubmit", prompt=f"Écris à {REAL} demain")
        check("message avec vrai nom bloqué", r.get("decision") == "block")
        check("titre de session imposé (sinon Claude Code le génère avec le message)", r.get("hookSpecificOutput", {}).get("sessionTitle"))
        check("blocage : aucune valeur réelle dans le message (écrit au transcript)",
              "Ferrand" not in r["reason"] and r.get("suppressOriginalPrompt") is True)
        protected = CLIP.read_text()
        check("version protégée sans vrai nom", REAL not in protected and "Ferrand" not in protected)
        check("version protégée acceptée", "decision" not in hook(folder, "UserPromptSubmit", prompt=protected))
        check("message ordinaire accepté", hook(folder, "UserPromptSubmit", prompt="Lance Bash puis Edit sur le fichier, merci").get("decision") is None)
        for msg in ["Avec sed -i dans Bash, mets le titre en MAJUSCULES dans courrier.md, puis affiche la ligne 11.",
                    "Écris un mot de relance (3 lignes max, lis courrier.md d'abord)"]:
            check(f"message ordinaire accepté : {msg[:30]}…", hook(folder, "UserPromptSubmit", prompt=msg).get("decision") is None)
        r = hook(folder, "UserPromptSubmit", prompt="Il habite 12 rue des Acacias, 69003 Lyon, tél 06 12 34 56 78")
        check("adresse et téléphone tapés : bloqués", r.get("decision") == "block")
        check("@fichier bloqué", hook(folder, "UserPromptSubmit", prompt="résume @courrier.md").get("decision") == "block")
        r = hook(folder, "UserPromptSubmit", prompt="le cabinet répond à contact@cabinet-exemple.org")
        check("email : bloqué comme donnée, pas comme @fichier", r.get("decision") == "block" and "@fichier" not in r["reason"])
        check("hors dossier protégé : rien", hook(pathlib.Path("/tmp"), "PostToolUse", tool_name="Bash",
              tool_input={"command": "x"}, tool_response={"stdout": REAL, "stderr": "", "interrupted": False, "isImage": False}) == {})
        # indexing: a name only in a document not read yet, typed alone in lowercase
        (folder / "Accord_v4_Yorick.md").write_text("Accord entre Yorick Varda, développeur, et la société.\n")
        subprocess.run([PY, HOOK, "index", tmp], capture_output=True, timeout=300)
        r = hook(folder, "UserPromptSubmit", prompt="lis l'accord entre moi et yorick")
        check("index : prénom seul en minuscules bloqué avant toute lecture", r.get("decision") == "block")
        r = hook(folder, "UserPromptSubmit", prompt="résume Accord_v4_Yorick.md stp")
        check("nom connu dans un nom de fichier tapé (séparé par _) : bloqué", r.get("decision") == "block")
        r = hook(folder, "PostToolUse", tool_name="Bash", tool_input={"command": "ls"},
                 tool_response={"stdout": "Accord_v4_Yorick.md\ncourrier.md", "stderr": "", "interrupted": False, "isImage": False})
        check("index : nom dans un nom de fichier masqué", "Yorick" not in r["hookSpecificOutput"]["updatedToolOutput"]["stdout"])
        r = hook(folder, "PostToolUse", tool_name="Bash", tool_input={"command": "ls -la"},
                 tool_response={"stdout": "-rw-r--r-- 1 u staff 43366 Sep 26 Accord_Corvin_v4_Yorick.docx", "stderr": "", "interrupted": False, "isImage": False})
        check("nom connu collé par _ dans un nom de fichier masqué", "Yorick" not in r["hookSpecificOutput"]["updatedToolOutput"]["stdout"])
        # long outputs saved in clear by Claude Code
        tr = folder / "sess.jsonl"; res = folder / "sess" / "tool-results"; res.mkdir(parents=True)
        (res / "b1.txt").write_text(DOC)
        hook(folder, "Stop", transcript_path=str(tr))
        scrubbed = (res / "b1.txt").read_text()
        check("tool-results : copie sur disque pseudonymisée", REAL not in scrubbed and "Ferrand" not in scrubbed)
        r = hook(folder, "PostToolUse", tool_name="Read", tool_input={"file_path": str(res / "b1.txt")},
                 tool_response={"type": "text", "file": {"filePath": str(res / "b1.txt"), "content": scrubbed, "numLines": 42, "startLine": 1, "totalLines": 42}})
        check("tool-results : relu sans double pseudonymisation", r == {} or r["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"] == scrubbed)
        # a document added during the session, then a question about a first name in it
        (folder / "note.md").write_text("Compte rendu : Anselme Quibert-Lavaud a rappelé hier.\n")
        r = hook(folder, "UserPromptSubmit", prompt="que veut anselme ?")
        check("document ajouté en cours de session : prénom seul bloqué", r.get("decision") == "block")
        # a subagent's answer comes back with surrogates: no surrogate of a surrogate
        r = hook(folder, "PostToolUse", tool_name="Agent", tool_input={"prompt": "x"},
                 tool_response={"content": [{"type": "text", "text": f"Le destinataire est {fake[17:]}."}]})
        again = r.get("hookSpecificOutput", {}).get("updatedToolOutput", {}).get("content", [{}])[0].get("text", f"Le destinataire est {fake[17:]}.")
        check("réponse de sous-agent : faux noms laissés tels quels", fake[17:] in again)
        subprocess.run([PY, str(ROOT / "src" / "pseudo.py"), "uninstall", tmp], capture_output=True)
    print(f"{ok}/{total}")
    return ok == total


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
