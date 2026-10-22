"""PII detection benchmark: parse {{TYPE|value}} markers into gold spans, run detectors, score."""
import json, os, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lexicon import has_first_name, birth_context, legal_reference, model_view, rare_word, person_cue, is_date

MARK = re.compile(r"\{\{([A-Z]+)\|(.*?)\}\}", re.S)

def load(path):
    """Strip markers, return (plain_text, [(start, end, type, value)])."""
    raw, out, gold, pos = path.read_text(), [], [], 0
    for m in MARK.finditer(raw):
        out.append(raw[pos:m.start()])
        start = sum(len(c) for c in out)
        val = m.group(2)
        out.append(val)
        gold.append((start, start + len(val), m.group(1), val))
        pos = m.end()
    out.append(raw[pos:])
    return "".join(out), gold

# --- Detector 1: deterministic regexes ---
RULES = [
    # "RAOUAFI Mohamed", "BELLECULLEE Corentin, Pierre" — surname in caps, then given names
    # initials or a title before a surname in capitals: "P.-H. TREZEGUET-MARIN", "Mme LE BIHAN"
    ("PER",   re.compile(r"(?:\b(?:[A-Z]\.-?){1,3}|\b(?:M\.|Mme|Mlle|Me|Ma[iî]tre|Dr|Pr|Monsieur|Madame))\s*"
                         r"(?:(?:LE|LA|DE|DU|DES|DI|DA|VAN|VON|EL|BEN)[ '’])?[A-ZÀ-ÝÉÈÊÎÔÛÇ]{2,}"
                         r"(?:[-'’][A-ZÀ-ÝÉÈÊÎÔÛÇ]{2,})*\b")),
    ("PER",   re.compile(r"\b(?:(?:LE|LA|DE|DU|DES|DI|DA|VAN|VON|EL|BEN)[ '’])?[A-ZÀ-ÝÉÈÊÎÔÛÇ]{3,}(?:[-'’ ][A-ZÀ-ÝÉÈÊÎÔÛÇ]{2,})*"
                         r"[ ]+[A-ZÀ-ÝÉÈÊ][a-zà-ÿ]{2,}(?:,?[ ]+[A-ZÀ-ÝÉÈÊ][a-zà-ÿ]{2,}){0,4}")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("EMAIL", re.compile(r"[\w.+-]+\s*[\[(]\s*(?:at|arobase)\s*[\])]\s*[\w-]+"
                         r"(?:\s*[\[(]\s*(?:dot|point)\s*[\])]\s*\w+|\.\w+)+", re.I)),
    ("IBAN",  re.compile(r"\bFR\d{2}(?:[ ]?[A-Z0-9]{4}){5}[ ]?[A-Z0-9]{1,3}\b")),
    ("NIR",   re.compile(r"\b[12][ ]?\d{2}[ ]?\d{2}[ ]?\d{2,3}[ ]?\d{3}[ ]?\d{3}[ ]?\d{2}\b")),
    ("PHONE", re.compile(r"(?:\+33[ ]?\(?0?\)?[ ]?|\b0)\d(?:[ .-]?\d{2}){4}\b")),
    # (an ISO or slashed date is not an identifier: excluded by the lookahead)
    ("IDNUM", re.compile(r"(?<![\d/-])\b(?!\d{4}-\d{2}-\d{2}\b|\d{1,2}/\d{1,2}/\d{2,4}\b)"
                         r"(?:n[°o]\s*)?[A-Z0-9]{2,}(?:[-/][A-Z0-9]{2,})+\b|\b\d{9,14}\b"
                         r"|\b[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\b")),
    # Full address: run lazily up to the postcode, so complements ("Bât. C") are kept
    # but the sentence that follows the city is not swallowed.
    ("ADDR",  re.compile(r"\b\d{1,4}\s?(?:bis|ter|quater)?\s*(?:rue|avenue|av\.|boulevard|bd|"
                         r"chemin|route|place|all[ée]e|impasse|quai|cours|voie|square|villa|"
                         r"faubourg|passage|sentier|contour|quartier|lieu-dit|"
                         r"hameau|r[ée]sidence|zone|mail|esplanade|parc)\s+"
                         r"(?:[^\n;]{2,60}?\s*\d{5}\s+[A-ZÉÈÀÂÎÔÛ][A-Za-zÀ-ÿ'’\-]*"
                         r"(?:[ ][A-Za-zÀ-ÿ'’\-]+){0,3}"
                         # no postcode: up to a comma, a sentence end ("Lyon. Témoin", but
                         # "St. Jacques" goes on) or a label ("Témoin :")
                         r"|(?:(?![ ]{2})(?!(?<=[a-zà-ÿ]{3})\.\s+[A-ZÀ-Ý])(?!\s*:)[^\n,;]){2,50})", re.I)),
    # Address complement: "Bâtiment C, appartement 34", "Résidence Les Pins, escalier 2"
    ("ADDR",  re.compile(r"\b(?:B[âa]t(?:iment)?\.?|Appartement|Appt\.?|Escalier|Esc\.|R[ée]sidence|"
                         r"[ÉE]tage|Lot(?:issement)?\.?)\s+[A-Z0-9][\w'’-]*(?:[ ][A-Z][\w'’-]*)*"
                         r"(?:,\s*(?:appartement|appt\.?|[ée]tage|escalier|porte)\s+[\w-]+)*")),
    # Postcode + city on its own: survives PDF column layout that splits the address.
    ("ADDR",  re.compile(r"\b\d{5}\s+[A-ZÉÈÀÂÎÔÛ][A-Za-zÀ-ÿ'’\-]*"
                         r"(?:[ ][A-Za-zÀ-ÿ0-9'’\-]+){0,3}(?:\s+CEDEX\s*\d*)?", re.I)),
]

DATE = (r"\d{2}/\d{2}/(?:19|20)\d{2}"
        r"|\d{1,2}(?:er)?\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|"
        r"octobre|novembre|décembre)\s+(?:19|20)\d{2}")

PARTICLES_LC = {"de", "du", "des", "la", "le", "les", "d", "l", "van", "von", "der", "den", "di", "da",
                "del", "della", "y", "e", "ben", "bin", "el", "al", "dit", "dite"}

ORG_WORDS = (r"\b(mutuelle|assurances?|banque|groupe|holding|fondation|association|syndicat|"
             r"cabinet|agence|clinique|h[ôo]pital|laboratoire|pharmacie|[ée]tude|office|soci[ée]t[ée]|"
             r"sarl|sas|sci|eurl|conseil|consulting|services?|immobili[èe]re?|partners)\b")

DATE_WORDS = re.compile(r"janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|"
                        r"novembre|d[ée]cembre", re.I)

ACRONYMS = {"RCS", "RM", "SIREN", "SIRET", "BODACC", "TVA", "EUR", "USD", "SAS", "SARL",
            "SASU", "SCI", "EURL", "SA", "SNC", "SCP", "SELARL", "GIE", "CS", "BP", "ZA",
            "ZI", "CEDEX", "INSEE", "URSSAF", "CAF", "CPAM", "IBAN", "BIC", "NIR", "INE",
            "IPP", "SEJ", "RG", "PDG", "DRH", "CDI", "CDD", "HT", "TTC", "EARL", "SCM"}

# Rules where the LABEL in the document carries the meaning, not the value's shape.
CONTEXT_RULES = [
    # a date is a date of birth only when something nearby says so
    ("DOB", re.compile(r"(?:\bn[ée]e?\s+(?:à\s+[^\n,;]{2,40}?\s+)?le|date\s+(?:et\s+lieu\s+)?de\s+naissance"
                       r"\s*:?|naissance\s*:)\s*(" + DATE + r")", re.I)),
    # an identifier is whatever follows an identifier label, whatever its shape
    ("IDNUM", re.compile(r"(?:\bn[°o]\s*|\bnum[ée]ro\s*:?\s*|\bmatricule\s*:?\s*|"
                         r"\bdossier\s+|\br[ée]f[.:]\s*|\btoque\s*|\bIPP\s*|\bSEJ[- ]|"
                         r"\bRG\s*|\bcommande\s+n[°o]\s*|\battestation\s+n[°o]\s*|\bpasseport\s*(?:n[°o]\s*)?|"
                         r"\bcarte\s+d['’]identit[ée]\s*(?:n[°o]\s*)?|\bCNI\s*|\bpermis(?:\s+de\s+conduire)?\s*(?:n[°o]\s*)?|"
                         r"\bimmatricul[ée]e?\s+|\bplaque\s*:?\s*)"
                         # a short qualifier between label and value ("N° allocataire CAF : "),
                         # and markdown bold around the label ("**Contrat n° :** ")
                         r"(?:[\w’' ]{1,30}?\s*:)?\s*(?:\*\*|__)?\s*"
                         r"((?-i:[#A-Z0-9][A-Z0-9 ./#-]{1,30}?))(?=\s*(?:[,.;)\n—–|]|$))", re.I)),
]

# A labelled identifier must actually contain a digit, otherwise it is prose.
def _has_digit(m):
    return any(c.isdigit() for c in m)

def _trim_date(v):
    return re.sub(r"\s*(?:du|le)?\s*\d{4}-\d{2}-\d{2}\s*$", "", v).strip()

# Tables (CSV, pasted spreadsheet, markdown): the column header says what the cells are.
_HEADER_TYPES = [
    ("PER",   r"^(?:nom|pr[ée]noms?|name|surname|last ?name|first ?name|nom d['’]usage|"
              r"client|salari[ée]|patient|b[ée]n[ée]ficiaire|contact|employ[ée]|locataire|propri[ée]taire)\b"),
    ("PHONE", r"(?:t[ée]l|t[ée]l[ée]phone|phone|portable|mobile|gsm)"),
    ("EMAIL", r"(?:e-?mail|courriel|mail)"),
    ("ADDR",  r"(?:adresse|address|domicile|rue)"),
    ("DOB",   r"(?:naissance|birth|dob|date de naissance)"),
    ("IBAN",  r"(?:iban|rib)"),
    ("NIR",   r"(?:nir|s[ée]cu|s[ée]curit[ée] sociale)"),
    ("IDNUM", r"(?:matricule|n[°o] ?dossier|dossier|contrat|r[ée]f[ée]rence|identifiant|\bid\b)"),
]


def detect_tables(text):
    out, lines, pos = [], text.split("\n"), 0
    starts = []
    for ln in lines:
        starts.append(pos); pos += len(ln) + 1
    i = 0
    while i < len(lines):
        sep = next((c for c in (";", "\t", "|") if lines[i].count(c) >= 1), None)
        if not sep and lines[i].count(",") >= 2 and sum(
                bool(any(re.search(rx, h.strip().lower()) for _, rx in _HEADER_TYPES))
                for h in lines[i].split(",")) >= 2:
            sep = ","                                     # comma CSV with a recognisable header
        if not sep:
            i += 1; continue
        j = i
        while j + 1 < len(lines) and lines[j + 1].count(sep) >= 1:
            j += 1
        if j - i >= 1:                                    # a header and at least one row
            def cells(k):
                res, off = [], 0
                for c in lines[k].split(sep):
                    res.append((starts[k] + off, c)); off += len(c) + 1
                return res
            header = cells(i)
            col_types = []
            for _, h in header:
                hf = h.strip().strip("*").lower()
                col_types.append(next((t for t, rx in _HEADER_TYPES if hf and re.search(rx, hf)), None))
            if any(col_types):
                for k in range(i + 1, j + 1):
                    if re.fullmatch(r"[\s|:\-]*", lines[k]):   # markdown separator row
                        continue
                    for col, (st, c) in enumerate(cells(k)):
                        t = col_types[col] if col < len(col_types) else None
                        val = c.strip().strip("*").strip()
                        if t and val and re.search(r"\w", val) and not (
                                t == "IDNUM" and len(re.sub(r"\W", "", val)) < 3):
                            s0 = st + c.index(val)
                            out.append((s0, s0 + len(val), t, val))
        i = j + 1
    return out


_PAIR = re.compile(r"\b([A-ZÀ-Ý][a-zà-ÿ'’-]{1,})[ ]([A-ZÀ-Ý][a-zà-ÿ'’-]{1,})\b")


def detect_name_pairs(text):
    view, out = model_view(text), []
    for m in _PAIR.finditer(view):
        a, c = m.group(1), m.group(2)
        if (has_first_name(a) or has_first_name(c)) and not re.fullmatch(
                r"(?:Monsieur|Madame|Mademoiselle|Ma[iî]tre|Docteur|Saint|Sainte|Rue|Avenue|Place|Le|La|Les|De|Du|Des|En|Au|Aux|Par|Pour|Avec|Et)", a):
            out.append((m.start(), m.end(), "PER", text[m.start():m.end()]))
    return out


_ANY_DATE = re.compile(DATE if False else r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2}\b|\b\d{1,2}(?:er)?\s+"
                       r"(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|"
                       r"novembre|décembre)\s+(?:19|20)\d{2}\b", re.I)


def detect_birth_dates(text):
    return [(m.start(), m.end(), "DOB", m.group()) for m in _ANY_DATE.finditer(text)
            if birth_context(text, m.start(), window=80) and not legal_reference(text, m.start())]


# French rural addressing has no number: "Lieu-dit La Chesnaie", "Hameau des Granges"
_RURAL = re.compile(r"\b(?:Lieu-dit|Lieudit|Hameau|Quartier|Domaine|Ferme|Mas|Le Bourg|La Bourg)\b"
                    r"(?:\s+(?:de\s+la|de|du|des|d['’]|la|le|les|l['’]))*\s*[A-ZÀ-Ý][\w'’-]*(?:[ ][A-ZÀ-Ý][\w'’-]*)*")

# a rare word in capitals, alone, in running text: "le témoin MOUGENOT a déclaré"
_CAPS_WORD = re.compile(r"(?<![A-Z]\s)\b[A-ZÀ-ÝÉÈÊÎÔÛÇ]{5,}(?:-[A-ZÀ-ÝÉÈÊÎÔÛÇ]{2,})?\b(?!\s[A-Z]{2})")


def detect_rare_caps(text):
    out = []
    for m in _CAPS_WORD.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end()) if "\n" in text[m.end():] else len(text)]
        if line.strip() == m.group() or m.group() in ACRONYMS:
            continue                                   # a heading, not a name
        if all(rare_word(p, 2.5) for p in m.group().split("-")) and person_cue(text, m.start()):
            out.append((m.start(), m.end(), "PER", m.group()))
    return out


def detect_regex(text):
    out = detect_name_pairs(text) + detect_birth_dates(text) + detect_rare_caps(text)
    out += [(m.start(), m.end(), "ADDR", m.group()) for m in _RURAL.finditer(text)]
    for lbl, rx in RULES:
        for m in rx.finditer(text):
            if lbl == "PER" and m.group().split()[0].strip("-'’") in ACRONYMS:
                continue
            # "SCI DU PETIT PONT", "ORDONNE Monsieur": capitals alone do not make a surname
            if lbl == "PER" and not re.match(r"(?:[A-Z]\.|M\.|Mme|Mlle|Me\b|Ma[iî]tre|Dr|Pr|Monsieur|Madame)", m.group()) \
                    and not has_first_name(re.sub(r"^[A-ZÀ-ÝÉÈÊÎÔÛÇ' -]+?(?=[ ][A-ZÀ-Ý][a-zà-ÿ])", "", m.group())):
                continue
            if lbl in ("IDNUM", "PHONE", "DOB") and legal_reference(text, m.start()):
                continue
            if lbl == "PER" and re.search(ORG_WORDS, m.group(), re.I):
                continue                      # "MUTUELLE HARMONIE" is an organisation
            if lbl in ("IDNUM", "PHONE") and is_date(m.group()):
                continue
            if lbl == "IDNUM" and not _has_digit(m.group()):
                continue                      # "SAINT-GOBAIN" is a word, not an identifier
            out.append((m.start(), m.end(), lbl, m.group()))
    # an identifier label followed by "69 du 2025-12-05": the identifier is "69"
    out += [(m.start(1), m.start(1) + len(_trim_date(m.group(1))) if lbl == "IDNUM" else m.end(1),
             lbl, _trim_date(m.group(1)) if lbl == "IDNUM" else m.group(1))
            for lbl, rx in CONTEXT_RULES for m in rx.finditer(text)
            if not legal_reference(text, m.start(1)) and not (lbl == "IDNUM" and DATE_WORDS.search(m.group(1)))
            and (lbl != "IDNUM" or (_has_digit(_trim_date(m.group(1)))
                                  and len(re.sub(r"\W", "", _trim_date(m.group(1)))) >= 3
                                  and not re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}",
                                                   m.group(1).strip())))]
    return out + detect_tables(text)

# --- Detector 2: GLiNER ---
LABELS = {
    "person": "PER", "full postal address": "ADDR", "email address": "EMAIL",
    "phone number": "PHONE", "date of birth": "DOB",
    "social security number": "NIR", "bank account number": "IBAN",
    "identification number": "IDNUM",
}
_model = None
def detect_gliner(text, threshold=float(os.environ.get("GLINER_THRESHOLD", "0.25"))):
    global _model
    if _model is None:
        from gliner import GLiNER
        _model = GLiNER.from_pretrained(os.environ.get("GLINER_MODEL", "knowledgator/gliner-pii-edge-v1.0"))
    ROLES = {"greffe", "tribunal", "tribunal de commerce", "rcs", "bodacc", "sas", "sarl",
             "sasu", "sci", "eurl", "societe", "greffe du tribunal", "client", "prestataire", "vendeur", "acquereur", "acquéreur", "employeur",
             "salarie", "salarié", "patient", "societe", "société", "partie", "parties",
             "cabinet", "employe", "employé", "interesse", "intéressé",
             # form labels: the label is never the value
             "nom", "prenom", "prénom", "prenom(s)", "prénom(s)", "nom de famille",
             "nom d'usage", "adresse", "telephone", "téléphone", "courriel", "email",
             "date de naissance", "beneficiaire", "destinataire", "expediteur",
             "signature", "objet", "reference", "référence", "candidat", "titulaire"}
    # GLiNER silently truncates past ~384 tokens. Slide a window with overlap so a
    # long document is fully seen, and map every span back to absolute offsets.
    WIN, OVERLAP = 1200, 200
    view = model_view(text)
    assert len(view) == len(text)
    ents, pos = [], 0
    while pos < len(text):
        cut = min(len(text), pos + WIN)
        if cut < len(text):
            nl = text.rfind("\n", pos + WIN // 2, cut)
            if nl > pos:
                cut = nl
        for e in _model.predict_entities(view[pos:cut], list(LABELS), threshold=threshold):
            s0, e0 = e["start"] + pos, e["end"] + pos
            ents.append({**e, "start": s0, "end": e0, "text": text[s0:e0]})
        if cut >= len(text):
            break
        pos = max(cut - OVERLAP, pos + 1)
    out = []
    for e in ents:
        core = re.sub(r"^(?:le|la|les|l['’]|un|une)\s+", "", e["text"].strip().lower())
        if e["label"] == "person" and (core in ROLES or re.search(
                r"\b(greffe|tribunal|rcs|bodacc|s\.?a\.?s|sarl|sasu|eurl|"
                r"soci[ée]t[ée]|commerce|registre)\b", core)):
            continue
        lab = LABELS[e["label"]]
        if lab == "PER":
            letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", e["text"])
            if not re.search(r"[A-ZÀ-Ý]", view[e["start"]:e["end"]]):
                continue                      # "le preneur", "votre frère"
            # trim common words at the edges of a name: "Contrat Dupont Jean" -> "Dupont Jean"
            while True:
                m0 = re.match(r"\s*([A-Za-zÀ-ÿ]+)[\s_]+", text[e["start"]:e["end"]])
                if m0 and not has_first_name(m0.group(1)) and not rare_word(m0.group(1), 4.5) \
                        and m0.group(1).lower() not in PARTICLES_LC:
                    e = {**e, "start": e["start"] + m0.end()}
                    continue
                m1 = re.search(r"[\s_]+([A-Za-zÀ-ÿ]+)\s*$", text[e["start"]:e["end"]])
                if m1 and not has_first_name(m1.group(1)) and not rare_word(m1.group(1), 4.5):
                    e = {**e, "end": e["start"] + m1.start()}
                    continue
                break
            e["text"] = text[e["start"]:e["end"]]
            vwords = re.findall(r"[A-Za-zÀ-ÿ]+", view[e["start"]:e["end"]])
            if any(w.islower() and w not in PARTICLES_LC for w in vwords):
                continue                      # "Claire de la situation": names are capitalised
            words = re.findall(r"[A-Za-zÀ-ÿ]+", e["text"])
            if len(words) == 1 and not has_first_name(words[0]) and not rare_word(words[0], 4.0):
                continue                      # "Bilan", "Copie": a common word alone
            if letters.isupper() and len(letters) <= 4 and not has_first_name(e["text"]):
                continue                      # "DFG", "MAIF": acronyms
            if letters.isupper() and not has_first_name(e["text"]) and \
                    not all(rare_word(w, 3.0) for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", e["text"])):
                continue                      # "CONSEIL", "ORDONNE"; "MAZEAU" is kept
        t = e["text"]
        digits = sum(c.isdigit() for c in t)
        if lab == "PER" and re.fullmatch(r"\s*(?:M\.|Mme|Mlle|Monsieur|Madame|Mademoiselle|Ma[iî]tre|Dr|Docteur)\s*", t):
            continue                          # a title alone is not a person
        if lab == "PER" and re.search(ORG_WORDS, t, re.I):
            continue                          # an organisation, not a person
        if lab == "ADDR" and not re.search(r"[A-Za-zÀ-ÿ]{2,}", t):
            continue                          # "2026" alone
        if lab == "ADDR" and not (digits or re.search(r"\b(rue|avenue|bd|boulevard|chemin|route|place|"
                                                       r"all[ée]e|impasse|quai|cours|lieu-dit|quartier|"
                                                       r"r[ée]sidence|b[âa]t|appartement|hameau)\b", t, re.I)):
            continue                          # a city alone does not locate a person
        if lab == "ADDR" and re.fullmatch(r"\s*\d{1,2}\s*(?:h|H|/)\s*\d{0,2}\s*", t):
            continue                          # "14 h 10", "14/9"
        if lab in ("IDNUM", "PHONE", "NIR", "IBAN") and is_date(t):
            continue                          # a date is never an identifier
        if lab == "IBAN" and not re.match(r"\s*[A-Z]{2}\d{2}", t):
            continue
        if lab == "NIR" and digits < 13:
            continue
        if lab == "IDNUM" and digits < 4:
            continue                          # "PHQ-9 : 13", "coefficient 385"
        if lab == "DOB" and not birth_context(text, e["start"]):
            continue                          # an ordinary date is not an identifier
        if lab in ("IDNUM", "PHONE", "DOB") and legal_reference(text, e["start"]):
            continue
        out.append((e["start"], e["end"], lab, e["text"]))
    return out

def merge(spans, text=""):
    """Union overlapping spans; bridge gaps that hold only separators."""
    cov, spans = [], sorted((s, e) for s, e, *_ in spans)
    for s, e in spans:
        # bridging needs the text: without it, merge only what actually overlaps
        gap = text[cov[-1][1]:s] if (cov and text) else None
        if cov and (s <= cov[-1][1] or (gap is not None and len(gap) <= 3
                                        and re.fullmatch(r"[\s,;:.\-–—]*", gap))):
            cov[-1][1] = max(cov[-1][1], e)
        else:
            cov.append([s, e])
    return cov

def covered(span, cov):
    s, e = span
    return any(c[0] <= s and e <= c[1] for c in cov)

def score(gold, pred, text=''):
    cov = merge(pred, text)
    caught = [g for g in gold if covered((g[0], g[1]), cov)]
    missed = [g for g in gold if not covered((g[0], g[1]), cov)]
    gold_cov = merge([(g[0], g[1]) for g in gold])
    fp = [p for p in pred if not any(p[0] < c[1] and c[0] < p[1] for c in gold_cov)]
    return caught, missed, fp

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "combo"
    files = sorted(pathlib.Path(sys.argv[2]).glob("*.md"))
    all_missed, all_fp, n_gold, n_caught = [], [], 0, 0
    per_type = {}
    for f in files:
        text, gold = load(f)
        pred = []
        if which in ("regex", "combo"): pred += detect_regex(text)
        if which in ("gliner", "combo", "full"): pred += detect_gliner(text)
        if which == "full":
            pred += detect_regex(text)
            from propagate import propagate
            pred += propagate(text, pred)
        caught, missed, fp = score(gold, pred, text)
        n_gold += len(gold); n_caught += len(caught)
        for g in gold:
            d = per_type.setdefault(g[2], [0, 0]); d[0] += 1
        for g in caught:
            per_type[g[2]][1] += 1
        all_missed += [(f.name, *m) for m in missed]
        all_fp += [(f.name, *p) for p in fp]
    print(f"\n=== {which.upper()} — {len(files)} documents ===")
    print(f"Rappel global : {n_caught}/{n_gold} = {100*n_caught/n_gold:.1f}%")
    print(f"Faux positifs (sur-caviardage) : {len(all_fp)}\n")
    print(f"{'type':8} {'gold':>5} {'attrapé':>8} {'rappel':>8}")
    for t, (g, c) in sorted(per_type.items(), key=lambda x: x[1][1]/x[1][0]):
        print(f"{t:8} {g:>5} {c:>8} {100*c/g:>7.0f}%")
    print("\n--- FUITES (non couvertes) ---")
    for fn, s, e, t, v in all_missed:
        print(f"  [{t:6}] {v!r:45} ({fn})")
    print("\n--- FAUX POSITIFS ---")
    for fn, s, e, t, v in all_fp[:40]:
        print(f"  [{t:6}] {v!r:45} ({fn})")
