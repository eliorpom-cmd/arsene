"""Every format a lawyer or a client sends: read, and protected through the engine.

Usage: python tests/fichiers.py           (needs LibreOffice to write the test files: soffice)
       python tests/fichiers.py --sans-moteur   (reading only, no engine: ~15 s)
Without the model (cloud session): PYTHONPATH=<absolute path>/tests/sans-modele, a stand-in
detector for people (the engine starts in src/, a relative path would not reach it)."""
import base64, os, pathlib, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import formats
from formatsgen import build, NAME, OTHER, PHONE, MAIL

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or not detail else f"  -> {detail}"))


folder = pathlib.Path(tempfile.mkdtemp(prefix="formats-"))
expected = build(folder)

# 1. reading, without the engine
for name, exp in expected.items():
    text, why = formats.read(name, (folder / name).read_bytes())
    if isinstance(exp, tuple):
        check(f"lecture {name} : refusé ({exp[0]})", why == exp[0] and not text, why)
    else:
        missing = [x for x in exp if x not in text]
        check(f"lecture {name}", why is None and not missing, f"why={why} manque={missing}")
formats.read("Attestation mixte.pdf", (folder / "Attestation mixte.pdf").read_bytes())
check("PDF à moitié scanné : la page sans texte est signalée", ("pages-sans-texte", [2]) in formats.notes, formats.notes)
check("Excel : la date de naissance se lit comme une date (pas 31273)",
      "14/08/1985" in formats.read("Clients.xlsx", (folder / "Clients.xlsx").read_bytes())[0])
check("Word ancien : une rangée de tableau = une ligne, cellules par tabulations",
      f"{OTHER}\tTémoin" in formats.read("Contrat Moreau.doc", (folder / "Contrat Moreau.doc").read_bytes())[0])

if "--sans-moteur" in sys.argv:
    print(f"\n{ok}/{total}")
    sys.exit(0 if ok == total else 1)

# 2. through the engine, as the browser sends them (op review_file, then protect_file)
os.environ.setdefault("PSEUDO_HOME", tempfile.mkdtemp(prefix="ph-", dir="/tmp"))   # short: socket path
from hook import call
dossier = pathlib.Path(tempfile.mkdtemp(prefix="dossier-"))
(dossier / ".pseudo").mkdir()
REAL = (NAME, "Moreau", OTHER, "Dubois", PHONE, MAIL)
try:
    for name, exp in expected.items():
        data = base64.b64encode((folder / name).read_bytes()).decode()
        r = call({"op": "review_file", "root": str(dossier), "name": name, "data": data}, wait=120)
        if isinstance(exp, tuple):
            check(f"moteur {name} : retenu ({r.get('reason')})", r.get("unreadable") and r.get("why") == exp[0], r)
            continue
        leaks = [x for x in REAL if x in r.get("text", "") + r.get("name", "")]
        check(f"moteur {name} : copie protégée « {r.get('name')} »", r.get("text") and not leaks, f"fuites={leaks}")
    # a Word file goes as a Word file, layout kept
    data = base64.b64encode((folder / "Contrat Moreau.docx").read_bytes()).decode()
    r = call({"op": "review_file", "root": str(dossier), "name": "Contrat Moreau.docx", "data": data})
    check("moteur : un Word peut partir en Word", r.get("layout") == "docx", r.get("layout"))
    p = call({"op": "protect_file", "root": str(dossier), "name": "Contrat Moreau.docx", "data": data})
    import docxfile
    t = docxfile.text(base64.b64decode(p["data"])) if p.get("data") else ""
    check("moteur : le Word protégé est un Word, sans vrai nom", p.get("name", "").endswith(".docx") and t
          and not [x for x in REAL if x in t + p["name"]], p.get("name"))
    # a Word file with a letterhead image stays a Word file (Elior, 02/10), image kept, its
    # metadata gone
    import io, docx, struct, zlib
    def _chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    logo = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + _chunk(b"tEXt", b"Author\x00" + NAME.encode()) + _chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
            + _chunk(b"IEND", b""))
    d = docx.Document(); d.add_picture(io.BytesIO(logo)); d.add_paragraph(f"Courrier pour {NAME}.")
    buf = io.BytesIO(); d.save(buf); data = base64.b64encode(buf.getvalue()).decode()
    r = call({"op": "review_file", "root": str(dossier), "name": "Courrier Moreau.docx", "data": data})
    p = call({"op": "protect_file", "root": str(dossier), "name": "Courrier Moreau.docx", "data": data})
    out = base64.b64decode(p.get("data", "")) if p.get("data") else b""
    media = [n for n in __import__("zipfile").ZipFile(io.BytesIO(out)).namelist() if n.startswith("word/media/")] if out else []
    check("moteur : un Word avec une image part en Word, image gardée sans ses métadonnées",
          r.get("layout") == "docx" and len(media) == 1 and NAME.encode() not in out and "Moreau" not in docxfile.text(out), (r.get("layout"), media))
    r = call({"op": "review_file", "root": str(dossier), "name": "Attestation mixte.pdf",
              "data": base64.b64encode((folder / "Attestation mixte.pdf").read_bytes()).decode()})
    check("moteur : PDF à moitié scanné, la page manquante est dite", ["pages-sans-texte", [2]] in r.get("notes", []), r.get("notes"))
finally:
    try:
        call({"op": "stop"}, start=False)
    except Exception:
        pass
print(f"\n{ok}/{total}")
sys.exit(0 if ok == total else 1)
