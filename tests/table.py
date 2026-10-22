"""The table travels: protect a document, export the table encrypted, import it at a
colleague's, restore the real names; a Word document keeps its layout on the way.
Usage: python tests/table.py (starts the engine if needed; without the model, e.g. in a
cloud session: PYTHONPATH=tests/sans-modele, a stand-in detector for people)"""
import io, os, pathlib, subprocess, sys, tempfile, zipfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import docxfile
from docxgen import contract
from bench import load
from tablefile import seal, unseal

PY, CLI = sys.executable, str(ROOT / "src" / "pseudo.py")
DOC, _ = load(ROOT / "bench" / "synth" / "02-courrier-avocat.md")
REAL = "Sébastien Ferrand"
ok = total = 0


def check(name, cond):
    global ok, total
    total += 1; ok += bool(cond)
    print(("ok   " if cond else "FAIL ") + name)


def cli(*a, phrase=None):
    env = {**os.environ, **({"PSEUDO_PHRASE": phrase} if phrase else {})}
    return subprocess.run([PY, CLI, *map(str, a)], capture_output=True, text=True, env=env, timeout=300)


# the file format alone
blob = seal({"rev": {"x": "Dupont"}}, "alpha-beta")
check("format : bonne phrase", unseal(blob, "alpha beta") == {"rev": {"x": "Dupont"}})
for bad, why in ((lambda: unseal(blob, "alpha-gamma"), "mauvaise phrase"),
                 (lambda: unseal(blob.replace(b'"n": 131072', b'"n": 65536'), "alpha-beta"), "en-tête modifié"),
                 (lambda: unseal(b"{}", "x"), "fichier quelconque")):
    try:
        bad(); check(f"format : {why} refusé", False)
    except ValueError:
        check(f"format : {why} refusé", True)
check("format : aucun nom en clair dans le fichier", b"Dupont" not in blob)

with tempfile.TemporaryDirectory() as tmp:
    a, b = pathlib.Path(tmp, "cabinet-A"), pathlib.Path(tmp, "confrere-B")
    a.mkdir(); b.mkdir()
    cli("install", a)
    doc = a / "courrier.md"; doc.write_text(DOC)

    r = cli("protect", doc)
    sent = a / "courrier.pseudonymise.md"
    check("protect : copie écrite", sent.exists())
    check("protect : nom réel absent de la copie", sent.exists() and REAL not in sent.read_text())

    r = cli("table", a)
    check("table : le vrai nom y figure", "Ferrand" in r.stdout)

    r = cli("export", a, pathlib.Path(tmp, "A.pseudotable"))
    words = r.stdout.strip().splitlines()[-1].strip()
    blob = pathlib.Path(tmp, "A.pseudotable").read_bytes()
    check("export : phrase de 6 mots donnée", len(words.split("-")) == 6)
    check("export : fichier chiffré, sans nom", b"Ferrand" not in blob and b"pseudotable" in blob)
    check("export : fichier en 0600", oct(pathlib.Path(tmp, "A.pseudotable").stat().st_mode)[-3:] == "600")

    # the colleague: the pseudonymized copy by mail, the table by another channel
    got = b / "courrier.pseudonymise.md"; got.write_text(sent.read_text())
    r = cli("restore", got, pathlib.Path(tmp, "A.pseudotable"), phrase=words)
    back = b / "courrier.retabli.md"
    check("restore avec table reçue : texte d'origine exact", back.exists() and back.read_text() == DOC)
    r = cli("restore", got, pathlib.Path(tmp, "A.pseudotable"), phrase="mauvaise-phrase")
    check("restore : mauvaise phrase refusée", r.returncode != 0 and "phrase" in r.stderr)

    r = cli("import", pathlib.Path(tmp, "A.pseudotable"), b, phrase=words)
    check("import : correspondances chargées", r.returncode == 0 and "importées" in r.stdout)
    back.unlink()
    cli("restore", got)
    check("restore avec la table importée : texte d'origine exact", back.exists() and back.read_text() == DOC)
    r = cli("import", pathlib.Path(tmp, "A.pseudotable"), a, phrase=words)
    check("import : refusé dans un dossier qui a déjà sa table", r.returncode != 0)

# a Word document: protected as a Word document, restored to the same document
WORD_REAL = [b"Moreau", b"MOREAU", b"moreau", b"Hamidi", b"HAMIDI", b"Lambert", b"Dubreuil"]


def zparts(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def canon(xml):
    from lxml import etree
    root = etree.fromstring(xml)
    for el in root.iter():
        if isinstance(el.tag, str):
            el.attrib.pop("{http://www.w3.org/XML/1998/namespace}space", None)
    return etree.tostring(root, method="c14n")


def same_document(a, b):
    """Every XML part equal as Word reads it (the thumbnail, dropped on purpose, aside), every
    other file equal to the byte."""
    a, b = zparts(a), zparts(b)
    skip = ("_rels/.rels", "[Content_Types].xml")
    return set(a) - set(b) <= {"docProps/thumbnail.jpeg"} and all(
        (canon(a[n]) == canon(b[n])) if n.endswith((".xml", ".rels")) else a[n] == b[n]
        for n in b if n not in skip)


with tempfile.TemporaryDirectory() as tmp:
    a, b = pathlib.Path(tmp, "cabinet-W"), pathlib.Path(tmp, "confrere-W")
    a.mkdir(); b.mkdir()
    cli("install", a)
    src = a / "Contrat.docx"; src.write_bytes(contract())
    r = cli("protect", src)
    sent = a / "Contrat.pseudonymise.docx"
    check("word : copie protégée écrite en .docx", sent.exists() and r.returncode == 0)
    data = sent.read_bytes() if sent.exists() else b""
    parts = zparts(data) if data else {}
    left = sorted({f"{n}:{v.decode()}" for n, raw in parts.items() for v in WORD_REAL if v in raw})
    check("word : aucun vrai nom dans aucune partie (corps, tableau, en-tête, pied, note, "
          "commentaire, zone de texte, révision, lien, propriétés)", data and not left)
    if left:
        print("     ", left)
    check("word : la copie reste un document Word lisible, mêmes lignes",
          data and len(docxfile.units(data)) == len(docxfile.units(src.read_bytes())))
    doc = parts.get("word/document.xml", b"")
    check("word : mise en forme gardée (gras, italique, titre, listes, tableau)",
          doc.count(b"<w:b/>") == zparts(src.read_bytes())["word/document.xml"].count(b"<w:b/>")
          and doc.count(b"<w:i/>") == 1 and b'w:val="Heading1"' in doc and doc.count(b"<w:numPr>") == 2
          and doc.count(b"<w:tc>") == 9)
    check("word : image signalée à l'utilisateur", "image" in r.stdout)

    cli("export", a, pathlib.Path(tmp, "W.pseudotable"), phrase="alpha-beta-gamma-delta-epsilon-zeta")
    got = b / "Contrat.pseudonymise.docx"; got.write_bytes(data)
    r = cli("restore", got, pathlib.Path(tmp, "W.pseudotable"), phrase="alpha-beta-gamma-delta-epsilon-zeta")
    back = b / "Contrat.retabli.docx"
    check("word : restore avec table reçue → .docx", back.exists())
    check("word : aller-retour, même texte ligne par ligne",
          back.exists() and docxfile.units(back.read_bytes()) == docxfile.units(src.read_bytes()))
    check("word : aller-retour, même document (runs, styles, tableau, en-têtes, notes)",
          back.exists() and same_document(src.read_bytes(), back.read_bytes()))
    r = cli("restore", sent)
    here = a / "Contrat.retabli.docx"
    check("word : restore avec la table du dossier (moteur), même document",
          here.exists() and same_document(src.read_bytes(), here.read_bytes()))

print(f"{ok}/{total}")
sys.exit(ok != total)
