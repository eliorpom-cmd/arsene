"""Detection rules, file sniffing and small round trips on short texts: the behaviour that a
regex or parser change can silently break. Runs anywhere in seconds; the round trips need
the stand-in detector when the model is absent: PYTHONPATH=<absolute path>/tests/sans-modele
Usage: python tests/rules.py"""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bench import detect_regex
import formats

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or not detail else f"  -> {detail}"))


def found(text):
    return {(text[s:e], lbl) for s, e, lbl, *_ in detect_regex(text)}


def has(text, value, lbl):
    got = found(text)
    check(f"{lbl} : {value!r}", (value, lbl) in got, sorted(got))


def none_of(text, lbl):
    got = [v for v, l in found(text) if l == lbl]
    check(f"pas de {lbl} dans {text!r}", not got, got)


# 1. rules (no model, no engine)
has("Écrire à jean.dupont@exemple.fr demain.", "jean.dupont@exemple.fr", "EMAIL")
has("ou jean.dupont [at] exemple [dot] fr.", "jean.dupont [at] exemple [dot] fr", "EMAIL")
has("ou jean.dupont (arobase) exemple.fr.", "jean.dupont (arobase) exemple.fr", "EMAIL")
has("IBAN : FR76 3000 6000 0112 3456 7890 189 merci", "FR76 3000 6000 0112 3456 7890 189", "IBAN")
has("IBAN FR7630006000011234567890189.", "FR7630006000011234567890189", "IBAN")
has("NIR 1 85 08 69 123 456 78 ok", "1 85 08 69 123 456 78", "NIR")
has("NIR 285086912345678.", "285086912345678", "NIR")
for tel in ("06 12 34 56 78", "+33 6 12 34 56 78", "04.76.12.34.56", "0612345678", "07-12-34-56-78"):
    has(f"Tél. {tel}, rappeler.", tel, "PHONE")
has("dossier RG 26/00318.", "RG 26/00318", "IDNUM")
none_of("Le 2026-10-21 et le 21/10/2026.", "IDNUM")
none_of("Montant : 1 250,00 euros, soit 12,5 %.", "PHONE")
has("Domicile : 14 rue des Lilas, 69003 Lyon\n", "14 rue des Lilas, 69003 Lyon", "ADDR")
has("Envoyer au 3 bis avenue Jean Jaurès 38400 Saint-Martin-d'Hères\n",
    "3 bis avenue Jean Jaurès 38400 Saint-Martin-d'Hères", "ADDR")
has("Bâtiment C, appartement 34", "Bâtiment C, appartement 34", "ADDR")
has("14 rue des Lilas à Lyon. Témoin : Sophie Lambert", "14 rue des Lilas à Lyon", "ADDR")
# "œ" is outside À-ÿ: the city was cut ("59700 Marcq-en-Bar" + "œul" left in clear, round trip broken)
has("Domicile : 38 rue du Docteur Schweitzer, 59700 Marcq-en-Barœul\n",
    "38 rue du Docteur Schweitzer, 59700 Marcq-en-Barœul", "ADDR")
has("38 rue du Docteur Schweitzer\n59700 Marcq-en-Barœul\n", "59700 Marcq-en-Barœul", "ADDR")
has("Cabinet 75008 PARIS CEDEX 08\n", "75008 PARIS CEDEX 08", "ADDR")

# 2. file sniffing: the content decides, a refusal says why
for name, raw, why in [
        ("vide.txt", b"", "empty"),
        ("binaire.bin", bytes(range(256)) * 4, "format"),
        ("archive.zip", b"PK\x03\x04" + b"\0" * 100, "archive"),
        ("casse.pdf", b"%PDF-1.4 garbage", "format"),
        ("faux.docx", b"PK\x03\x04" + b"\0" * 100, "format")]:
    try:
        text, got = formats.read(name, raw)
        check(f"lecture {name} : refusé ({why})", got == why and not text, got)
    except Exception as e:                                # a broken file must never crash the engine
        check(f"lecture {name} : refusé ({why})", False, f"{type(e).__name__}: {e}")
text, why = formats.read("note.txt", "Bonjour Élise Garçon".encode("utf-16"))
check("texte UTF-16 lu", why is None and "Élise Garçon" in text, repr(text))
text, why = formats.read("clients.csv", "Nom;Prénom\nGarçon;Zoé\n".encode("cp1252"))
check("CSV Windows-1252 lu", why is None and "Garçon;Zoé" in text, repr(text))
text, why = formats.read("page.html", "<p>Me <b>Jean</b> Dupont &amp; associés<script>x()</script></p>".encode())
check("HTML : texte lu, script ignoré", why is None and "Dupont & associés" in text and "x()" not in text, repr(text))

# 3. small round trips: pseudonymize then rehydrate gives the original back, byte for byte
from surrogate import Vault, pseudonymize
from rehydrate import rehydrate
TEXTS = [  # text, real values that must not be left in the protected copy
    ("Monsieur Julien MOREAU, né le 14/08/1979, demeurant 14 rue des Lilas, 69003 Lyon.", ("MOREAU", "Lilas")),
    ("Contact : julien.moreau@exemple.fr, 06 12 34 56 78, IBAN FR76 3000 6000 0112 3456 7890 189.",
     ("julien.moreau", "06 12 34 56 78", "3456 7890")),
    ("Karim Hamidi et Sophie Lambert ; témoin : Claire Dubreuil.\nFin.", ("Hamidi", "Lambert", "Dubreuil")),
    ("Domicile : 38 rue du Docteur Schweitzer\n59700 Marcq-en-Barœul\n\nFin.", ("Schweitzer", "Barœul")),
]
for i, (t, real) in enumerate(TEXTS):
    for seed in (3, 7):
        v = Vault(seed)
        p = pseudonymize(t, v)
        back = rehydrate(p, v)
        check(f"aller-retour texte {i + 1} graine {seed}", back == t, repr(back))
        check(f"aller-retour texte {i + 1} graine {seed} : rien en clair",
              not any(x in p for x in real), repr(p))

print(f"\n{ok}/{total}")
sys.exit(0 if ok == total else 1)
