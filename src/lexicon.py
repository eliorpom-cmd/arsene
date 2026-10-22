"""General properties of French text used to decide what is a person, a birth date or a
public reference. Built from open data, not from the test documents.

- First names: INSEE "Fichier des prénoms" 1900-2025 (Licence Ouverte), names given at least
  200 times. A word in capitals is a surname only if an attested first name goes with it.
- A date is a date of birth only when a birth marker is next to it.
- A reference to a legal text (loi n° 89-462, article 1641) is public: never masked.
"""
import pathlib, re, unicodedata

_DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "prenoms-insee.tsv"


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def _load():
    names, sex = set(), {}
    for line in _DATA.read_text(encoding="utf-8").splitlines()[1:]:
        name, s, _ = line.split("\t")
        names.add(fold(name)); sex[fold(name)] = s
    return names, sex


FIRST_NAMES, FIRST_NAME_SEX = _load()


def is_first_name(word):
    """'Jean-Pierre' counts if every part is attested."""
    parts = [p for p in re.split(r"[-'’]", fold(word.strip(".,;:"))) if p]
    return bool(parts) and all(p in FIRST_NAMES for p in parts)


def has_first_name(text):
    return any(is_first_name(w) for w in re.findall(r"[A-Za-zÀ-ÿ'’-]{2,}", text))


BIRTH_MARKER = re.compile(r"(?:\bn[ée]e?\b|naissance|naître|birth)", re.I)


DATE_ANY = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(?:er)?\s+"
                      r"(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|"
                      r"novembre|décembre|decembre)\s+(?:19|20)\d{2}\b", re.I)


def is_date(value):
    return bool(DATE_ANY.fullmatch(value.strip()))


def birth_context(text, start, window=60):
    """A birth marker in the same sentence, shortly before the date, with no other date
    in between ("né le 17 août 1962, a déposé le 22 janvier 2026": only the first)."""
    before = text[max(0, start - window):start]
    before = re.split(r"\n|[.!?]\s", before)[-1]
    markers = list(BIRTH_MARKER.finditer(before))
    return bool(markers) and not DATE_ANY.search(before[markers[-1].end():])


# whole words only: "Sanchez-Loiseau" must not read as "loi"
LEGAL_REF = re.compile(r"(?<![\w-])(?:loi|d[ée]cret|ordonnance|arr[êe]t[ée]|articles?|art\.|directive|"
                       r"r[èe]glement|circulaire|code)(?![\w-])\s*(?:n[°o]\s*)?[\w./-]*\s*$", re.I)


def legal_reference(text, start, window=30):
    """True when the span continues a reference to a legal text."""
    return bool(LEGAL_REF.search(text[max(0, start - window):start]))


# --- A view of the text for the model: same length, same offsets, normalised casing -----
# The model relies on capitals to recognise names. People type "rdv avec jean dupont",
# exports write "DUPONT", files are called "Contrat_Dupont_Jean.pdf". The view fixes the
# casing only; the model still decides from context, and every offset stays valid on the
# original text.
from wordfreq import zipf_frequency

ACRONYMS_VIEW = {"RCS", "SIREN", "SIRET", "TVA", "EUR", "SAS", "SARL", "SASU", "SCI", "EURL",
                 "CEDEX", "INSEE", "URSSAF", "CAF", "CPAM", "IBAN", "BIC", "NIR", "PDF", "CDI",
                 "CDD", "RIB", "DRH", "PDG", "HT", "TTC", "SNC", "SCP", "GIE", "SELARL", "RGPD"}
_FILE = re.compile(r"\b[\w.\-]+\.(?:pdf|docx?|xlsx?|odt|ods|txt|csv|png|jpe?g|heic|eml|msg|pages)\b", re.I)
_WORD = re.compile(r"[A-Za-zÀ-ÿ]+(?:['’][A-Za-zÀ-ÿ]+)?")


def _cap(w):
    return w[:1].upper() + w[1:].lower()


def lowercase_style(text):
    """Chat messages and quick notes are typed without capitals. In such text a lowercase
    first name may be a person; in normally written text it is an ordinary word."""
    starts = re.findall(r"(?:^|[.!?]\s+|\n)\s*([A-Za-zÀ-ÿ])", text)
    if len(starts) < 3:
        return not re.search(r"[A-ZÀ-Ý]", text)
    return sum(c.islower() for c in starts) / len(starts) >= 0.5


def model_view(text):
    chars = list(text)
    informal = lowercase_style(text)
    # separators inside file names and identifiers-like tokens: "Contrat_Dupont_Jean.pdf"
    for m in _FILE.finditer(text):
        for i in range(m.start(), m.end()):
            if text[i] in "_-":
                chars[i] = " "
    for i, c in enumerate(text):
        if c == "_":
            chars[i] = " "
    view = "".join(chars)
    words = list(_WORD.finditer(view))
    out = list(view)

    def put(m, w):
        if len(w) == len(m.group()):
            out[m.start():m.end()] = list(w)

    for k, m in enumerate(words):
        w = m.group()
        if len(w) >= 5 and w.isupper() and w not in ACRONYMS_VIEW:   # short caps are acronyms
            put(m, _cap(w))                             # "DUPONT" -> "Dupont"
        elif informal and w.islower() and fold(w) in FIRST_NAMES and len(w) >= 3:
            put(m, _cap(w))                             # "jean" -> "Jean"
            if k + 1 < len(words):                      # "jean dupont" -> "Jean Dupont"
                n = words[k + 1]
                gap = view[m.end():n.start()]
                if n.group().islower() and gap in (" ", "-") and zipf_frequency(n.group(), "fr") < 5.0:
                    put(n, _cap(n.group()))
    return "".join(out)


def rare_word(w, threshold=4.5):
    """Frequency of the word as written (accents matter: "assemblée" is common,
    "assemblee" is not a word)."""
    return max(zipf_frequency(w.lower(), "fr"), zipf_frequency(fold(w), "fr")) < threshold


# Words that announce a person in the same sentence: titles and roles.
PERSON_CUE = re.compile(r"(?<![\w-])(?:M\.|Mme|Mlle|Me|Ma[iî]tre|Dr|Pr|Monsieur|Madame|Mademoiselle|"
                        r"sieur|dame|t[ée]moin|requis|requ[ée]rant|d[ée]biteur|cr[ée]ancier|salari[ée]e?|"
                        r"employ[ée]e?|patient|patiente|[ée]l[èe]ve|locataire|bailleur|client|cliente|"
                        r"assur[ée]e?|conjoint|[ée]poux|[ée]pouse|pr[ée]venu|victime|plaignant|"
                        r"g[ée]rant|associ[ée]|dirigeant|nomm[ée]e?|famille)(?![\w-])", re.I)


def person_cue(text, start, window=40):
    before = re.split(r"\n|[.!?]\s", text[max(0, start - window):start])[-1]
    return bool(PERSON_CUE.search(before))
