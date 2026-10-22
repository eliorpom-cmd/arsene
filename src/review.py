"""Review before sending: what was masked, and what was left in clear but looks like it
could identify someone. The engine protects ~95 % of identifiers on unseen documents; the
review is how the user catches the rest in a few seconds.

Doubts are general shapes, never lists tailored to a document:
  - the model saw an identifier, below the threshold used to mask;
  - a capitalised word inside a sentence that is not a common word (a name the rules missed);
  - a date whose year could be a birth year;
  - a long number or code (an identifier without a label)."""
import pathlib, re
from bench import detect_gliner
from lexicon import rare_word, ACRONYMS_VIEW, legal_reference, is_first_name
from propagate import fold
from surrogate import pseudonymize, SALUTATIONS, PARTICLES

# surnames borne in France, many of them words too ("Tavernier", "Boulanger")
SURNAMES = {fold(n) for n in (pathlib.Path(__file__).resolve().parents[1] / "data" / "noms-insee.tsv")
            .read_text().split()[2::2]}
LOW = 0.08                                   # the model's hesitations (masking uses 0.25)
DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]((?:19|20)\d{2})\b|\b\d{1,2}(?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|"
                  r"mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+((?:19|20)\d{2})\b", re.I)
NUMBER = re.compile(r"(?<![\w/.-])(?=[\w./ -]*\d)[A-Z0-9](?:[A-Z0-9]|(?:\s?[./-]\s?|\s)(?=[A-Z0-9])){5,}(?![\w/-])")
CODE = re.compile(r"(?<![\w-])(?=[A-Z0-9-]*\d)[A-Z0-9]{2,}(?:-[A-Z0-9]{1,}){2,}(?![\w-])")   # "DBVJ-V-B7H-NQRT"
MONEY = re.compile(r"^\s*(?:€|eur|euros?|%|k€|m€|ht|ttc)\b", re.I)
WORD = re.compile(r"(?<![\w'’-])[A-ZÀ-Ý][a-zà-ÿ]+(?:-[A-ZÀ-Ý][a-zà-ÿ]+)*(?![\w'’-])")
UPPER = re.compile(r"(?<![\w'’-])[A-ZÀ-Ý]{4,}(?:-[A-ZÀ-Ý]{2,})*(?![\w'’-])")
INITIALS = re.compile(r"(?<![\w.])(?:[A-ZÀ-Ý]\.\s?-?){2,}(?!\w)|(?<![\w.])[A-ZÀ-Ý][a-zà-ÿ]+\s[A-ZÀ-Ý]\.(?!\w)"
                      r"|(?<![\w.])(?i:mme|mlle|m\.|mr|me|madame|monsieur|ma[iî]tre|dr)\s+[A-ZÀ-Ý]\.(?!\w)")
PLACE = re.compile(r"\b(?:r[ée]sidence|b[âa]timent|b[âa]t\.|appartement|appt|escalier|lots?|parcelle|cadastr[ée]e?)\b"
                   r"[^\n,;.]{1,40}", re.I)
AMOUNT = re.compile(r"\d{1,3}(?: \d{3}){1,2}")          # "285 000": an amount, not an identifier
# the surrogate a doubt gets when the user masks it (a realistic one, never a token)
KINDS = {"model": "PER", "caps": "PER", "upper": "PER", "initials": "PER", "place": "ADDR",
         "birth": "DOB", "number": "IDNUM"}
REASONS = {"model": "le modèle hésite", "caps": "mot à majuscule inhabituel",
           "upper": "mot en majuscules inhabituel", "initials": "initiales",
           "place": "complément d'adresse ou lot",
           "birth": "date qui pourrait être une naissance", "number": "numéro ou code"}


def _sentence_start(text, i):
    """First word of a sentence, line, bullet or cell: capitalised anyway."""
    return i == 0 or re.search(r"(?:^|[.!?:;\n•*#>|-])\s*$", text[max(0, i - 4):i]) is not None


NAME_CUE = re.compile(r"(?i:\b(?:m|mme|mlle|me|dr|pr)\.?|monsieur|madame|ma[iî]tre|[ée]poux|[ée]pouse|famille|"
                      r"veuve|n[ée]e?|consorts?)\s*$")


def _name_context(text, m, line):
    """A word in capitals is a surname when something around it says so: a title or a first
    name next to it, or a "nom" field. Without it, capitals are rulings ("ORDONNE"), brands
    or drugs."""
    before, after = text[max(0, m.start() - 25):m.start()], text[m.end():m.end() + 25]
    prev = re.search(r"([A-Za-zÀ-ÿ]+)\s+$", before)
    nxt = re.match(r"\s+([A-ZÀ-Ý][a-zà-ÿ]+)", after)
    return bool(NAME_CUE.search(before) or (prev and is_first_name(prev.group(1)) and prev.group(1)[0].isupper())
                or (nxt and is_first_name(nxt.group(1)))
                or re.search(r"(?i)\bnom\b", line) and fold(m.group()) in SURNAMES)


def doubts(text, masked, year_now=2026):
    """[(start, end, reason, value)] left in clear, one per span, not overlapping masked."""
    busy = sorted((s, e) for s, e, *_ in masked)

    def free(s, e):
        # some letter or digit of it left in clear ("F-2025-" when only "11874" was masked)
        return any(text[i].isalnum() and not any(b0 <= i < b1 for b0, b1 in busy) for i in range(s, e))

    out = []

    def add(s, e, why):
        if text[s:e].strip() and free(s, e) and not any(s < o[1] and o[0] < e for o in out):
            out.append((s, e, why, text[s:e]))

    # precise rules first: a vaguer doubt never hides them
    for m in NUMBER.finditer(text):
        v = m.group()
        lead = re.match(r"(?:[A-Za-z]+[ .])+", v)       # "SIRET 804 512 339 00027": the label is not the value
        start = m.start() + (lead.end() if lead else 0)
        v = text[start:m.end()]
        if (sum(c.isdigit() for c in v) >= 6 and not DATE.fullmatch(v.strip())
                and not AMOUNT.fullmatch(v.strip())
                and not MONEY.match(text[m.end():m.end() + 6])
                and not re.fullmatch(r"(?:19|20)\d{2}(?:\s*[-/]\s*(?:19|20)\d{2})?", v.strip())
                and not legal_reference(text, start)):
            add(start, m.end(), "number")
    for m in CODE.finditer(text):
        if len(m.group()) >= 8:
            add(m.start(), m.end(), "number")
    for m in DATE.finditer(text):
        year = int(m.group(1) or m.group(2))
        if 1920 <= year <= year_now - 3 and not legal_reference(text, m.start()):
            add(m.start(), m.end(), "birth")
    for m in INITIALS.finditer(text):
        if not re.fullmatch(r"(?:M|Me|Mme|Dr|Pr)\.?", m.group().strip()):
            add(m.start(), m.end(), "initials")
    for m in PLACE.finditer(text):
        add(m.start(), m.end(), "place")
    for m in UPPER.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.end()) % (len(text) + 1)]
        if (re.search(r"[a-zà-ÿ]{3}", line) is not None                 # else a heading in capitals
                and m.group() not in ACRONYMS_VIEW and _name_context(text, m, line)):
            add(m.start(), m.end(), "upper")
    for s, e, lbl, v in detect_gliner(text, threshold=LOW):
        words = re.findall(r"[A-Za-zÀ-ÿ]+", v)
        # an identifier has digits or a rare capitalised word: not "téléphone", "Cordialement"
        if sum(c.isdigit() for c in v) >= 3 or any(w[0].isupper() and rare_word(w, 4.0) for w in words):
            add(s, e, "model")
    for m in WORD.finditer(text):
        w = m.group()
        if (not _sentence_start(text, m.start()) and rare_word(w, 3.0)
                and fold(w) not in SALUTATIONS and fold(w) not in PARTICLES
                and w.upper() not in ACRONYMS_VIEW):
            add(m.start(), m.end(), "caps")
    return sorted(out)


def review(text, vault):
    """Pseudonymize and report: {"text": protected text, "masked": [...], "doubts": [...]}.
    Items carry the real values: for the user's eyes only, never for the model."""
    spans = []
    protected = pseudonymize(text, vault, spans=spans)
    masked = [{"start": s, "end": e, "type": lbl, "value": v, "surrogate": r} for s, e, lbl, v, r in sorted(spans)]
    # a value the user already decided on (mask or leave in clear) is not asked again
    decided = vault.allowed | {fold(v.strip()) for _, v in vault.forced}
    found = [{"start": s, "end": e, "reason": REASONS[why], "type": KINDS[why], "value": v}
             for s, e, why, v in doubts(text, spans) if fold(v.strip()) not in decided]
    return {"text": protected, "masked": masked, "doubts": found}
