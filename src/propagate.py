"""Pass 3: once a person is identified anywhere, find EVERY fragment of that name
elsewhere in the document. Deterministic, and this is where recall is actually won."""
import re, unicodedata, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lexicon import rare_word

TITLE = r"(?:M\.|Mme|Mlle|Dr|Pr|Me|Ma[iî]tre|Monsieur|Madame|Mademoiselle|Docteur|Professeur)"
PARTICLE = {"de", "du", "des", "le", "la", "van", "von", "di", "el", "ben"}
STOP = {"client", "prestataire", "vendeur", "acquereur", "employeur", "salarie", "patient",
        "societe", "cabinet", "service", "objet", "adresse", "tel", "email", "france",
        # administrative and corporate vocabulary — a bad seed here poisons the whole document
        "greffe", "tribunal", "commerce", "rcs", "bodacc", "registre", "annonce", "forme",
        "juridique", "capital", "siege", "social", "denomination", "activite", "numero",
        "identification", "immatriculation", "statuts", "date", "sas", "sarl", "sasu",
        "sci", "eurl", "eur", "creation", "creations", "etablissement", "principal",
        "gerant", "president", "directeur", "directrice", "associe", "administrateur",
        "liquidateur", "membre", "tresorier", "secretaire", "mandataire", "usage",
        # roles in legal, property, medical and HR documents are never names
        "syndic", "bailleur", "preneur", "locataire", "proprietaire", "notaire", "avocat",
        "medecin", "docteur", "infirmier", "infirmiere", "kinesitherapeute", "assure", "souscripteur",
        "beneficiaire", "demandeur", "defendeur", "requerant", "epoux", "epouse", "conjoint",
        "copropriete", "coproprietaire", "gerant", "vendeur", "acquereur", "temoin", "victime"}

# French form labels: whatever follows is a person, whatever it looks like.
PER_CONTEXT = re.compile(
    r"(?:nom\s+de\s+famille|nom\s+d['’]usage|pr[ée]noms?(?:\(s\))?|patient|salari[ée]|"
    r"assur[ée]|b[ée]n[ée]ficiaire|personne\s+de\s+confiance|m[ée]decin\s+traitant|"
    # roles that introduce a person in French administrative and corporate documents
    r"g[ée]rant[^:\n]{0,45}|pr[ée]sident[^:\n]{0,45}|directeur[^:\n]{0,30}|"
    r"directrice[^:\n]{0,30}|associ[ée][^:\n]{0,45}|administrateur[^:\n]{0,30}|"
    r"commissaire aux comptes[^:\n]{0,30}|liquidateur[^:\n]{0,20}|membre[^:\n]{0,40}|"
    r"vice-pr[ée]sident[^:\n]{0,30}|tr[ée]sorier|secr[ée]taire[^:\n]{0,20}|"
    r"repr[ée]sentant[^:\n]{0,30}|mandataire[^:\n]{0,30})"
    # stay on the same line: an empty field must not capture the next label
    r"[ \t]*:[ \t]*([^\n,(]{2,60}?)[ \t]*(?=[\n,(]|$)", re.I)

def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")

def fragments(name):
    """Split a detected person name into searchable atoms."""
    name = re.sub(rf"^\s*{TITLE}\s+", "", name.strip())
    out = set()
    for tok in re.split(r"[\s,]+", name):
        tok = tok.strip(".'’-")
        if len(tok) >= 3 and re.fullmatch(r"[A-Za-zÀ-ÿ'’-]+", tok) \
           and fold(tok) not in PARTICLE and fold(tok) not in STOP:
            out.add(fold(tok))
            # each part of a compound name is searched too: "Ngombé-Vasseur" then "née Ngombé".
            # Only rare words: "Brigadier-chef" must not turn every "Chef" into a name
            parts = tok.split("-")
            if len(parts) > 1:
                out |= {fold(p) for p in parts if len(p) >= 3 and rare_word(p, 4.5)
                        and fold(p) not in PARTICLE and fold(p) not in STOP}
    return out

def mine_emails(text, frags):
    """An email whose local part contains a known name confirms its other tokens,
    and the local part itself is a handle the person reuses elsewhere in lowercase."""
    extra, handles = set(), set()
    for m in re.finditer(r"\b([\w.+-]+)@[\w.-]+\b", text):
        local = m.group(1)
        toks = [t for t in re.split(r"[._+-]", local) if len(t) >= 3 and t.isalpha()]
        if any(fold(t) in frags for t in toks) or any(f in fold(local) for f in frags):
            extra |= {fold(t) for t in toks}
            if len(local) >= 5 and re.fullmatch(r"[A-Za-z0-9]+", local):
                handles.add(fold(local))
    return extra, handles

def propagate(text, per_spans):
    """Return new PER spans for every occurrence of every known name fragment."""
    frags = set()
    for s, e, lbl, val in per_spans:
        if lbl == "PER":
            frags |= fragments(val)
    for m in PER_CONTEXT.finditer(text):
        frags |= fragments(m.group(1))
    extra, handles = mine_emails(text, frags)
    frags |= extra
    if not frags and not handles:
        return []

    spans = []
    # initials of a known person: "Y. D.", "Y.D." for "Yann Dupuis"
    for s0, e0, lbl, val in per_spans:
        if lbl != "PER":
            continue
        atoms = [w for w in re.findall(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]+", re.sub(rf"^\s*{TITLE}\s+", "", val))]
        if len(atoms) >= 2:
            a, b = atoms[0][0], atoms[-1][0]
            for m in re.finditer(rf"(?<![\w.]){a}\.\s?{b}\.(?![\w])", text):
                spans.append((m.start(), m.end(), "PER", m.group()))
    # handles (eliorpom, mhyks) are pseudonyms: match them whatever the case
    for h in handles:
        for m in re.finditer(rf"\b{re.escape(h)}\b", text, re.I):
            spans.append((m.start(), m.end(), "PER", m.group()))
    # a known name typed in lowercase ("dupont veut une réponse"), unless it is a common word
    for m in re.finditer(r"(?<![\w@./:-])[a-zà-ÿ][a-zà-ÿ'’]{3,}(?![\w@./-])", text):
        if fold(m.group()) in frags and rare_word(m.group(), 4.5):
            spans.append((m.start(), m.end(), "PER", m.group()))
    # match any capitalised word whose folded form is a known fragment
    for m in re.finditer(r"\b[A-ZÀ-ÝÉÈÊ][\wÀ-ÿ-]{2,}\b", text):
        if fold(m.group()) not in frags:
            continue
        start, end = m.start(), m.end()
        # carry the possessive along: "Elior's" must not leave "Elior" behind
        poss = re.match(r"['’]s\b", text[end:])
        if poss:
            end += poss.end()
        part = re.search(r"\b(?:LE|LA|DE|DU|DES|DI|DA|VAN|VON|EL|BEN|Le|La|De|Du|Van|Von)[ '’]$",
                         text[max(0, start - 5):start])
        if part:
            start -= len(part.group())
        # extend left over titles and initials: "M. Ferrand", "L. Marchand", "Dr Sarah Klein"
        while True:
            left = text[max(0, start - 30):start]
            ext = re.search(rf"(?:{TITLE}|\b[A-ZÀ-Ý]\.)\s*$", left)
            if not ext:
                break
            start -= len(ext.group())
        # extend right over adjacent fragment words: "Corinne Aubry"
        while True:
            nxt = re.match(r"[,\s]+([A-ZÀ-ÝÉÈÊ][\wÀ-ÿ'’-]{2,})", text[end:])
            if not nxt or fold(nxt.group(1)) not in frags:
                break
            end += nxt.end()
        spans.append((start, end, "PER", text[start:end]))
    return spans
