"""Pseudonymize with realistic surrogates instead of opaque tokens.

A surrogate keeps the attributes the model needs to reason (gender, form, city, year of
birth) and replaces what identifies. Every name is mapped component by component, so any
form the model writes back ("M. Mercier", "Mercier's", "L. Mercier", "MERCIER") reverses.
"""
import hashlib, random, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from bench import detect_regex, detect_gliner, merge
from propagate import propagate, fold, TITLE

from lexicon import FIRST_NAME_SEX, rare_word, is_first_name, is_date
from origins import ORIGINS, ORIGIN_OF
import pathlib as _pl

def _insee_pool(sex, n=None):
    rows = []
    for line in (_pl.Path(__file__).resolve().parents[1] / "data" / "prenoms-insee.tsv").read_text().splitlines()[1:]:
        name, sx, births = line.split("\t")
        if sx == sex and "-" not in name and len(name) >= 4:
            rows.append((int(births), name))
    rows.sort(reverse=True)
    # a surrogate must never be a common word: rehydration would rewrite "une vision claire"
    return [name.capitalize() for _, name in rows
            if rare_word(name, 3.5) and not name.lower().startswith("m")][:n]


# French two-letter words and common two-letter capitals: never taken for initials
FR_TWO = set("""le la de du un en et au il on ne se ce sa ma ta me te tu ou si ni ça va vu dû eu an as
    es ai os or oh ah là où dr mr ok tv ue us uk fr pc cv rh pv ca ht tt
    re tr fw fwd cc pj nb ps ex vs no id ip ag dg ce it rc cs ss sn ma mo go ko km kg cm mm""".split())


def _initial(word):
    """Kind of first letter, as French elision sees it: vowel, h, or consonant."""
    c = fold(word)[:1]
    return "v" if c in "aeiouy" else "h" if c == "h" else "c"


def _surname_pool(n=5000):
    """Most common surnames of people born in France 1961-2000 (INSEE, Licence Ouverte):
    thousands of names, of every origin, so a big dossier never runs short."""
    rows = (_pl.Path(__file__).resolve().parents[1] / "data" / "noms-insee.tsv").read_text().splitlines()[1:]
    return [name.capitalize() for name, _ in (r.split("\t") for r in rows) if rare_word(name, 3.5)][:n]


# the 800 most given: rarer ones ("Calliste") read as invented
FIRST_F = _insee_pool("F", 800)
FIRST_M = _insee_pool("M", 800)
SURNAMES = _surname_pool()
# names common in France: they never inherit the origin of the rest of the person
# ("Yasmina Dupont" keeps a French surname)
COMMON = {"sur": {fold(n) for n in
          (_pl.Path(__file__).resolve().parents[1] / "data" / "noms-insee.tsv").read_text().split()[2:3000:2]}}
GENERAL = {"sur": SURNAMES, "F": FIRST_F, "M": FIRST_M}
# folded name -> origin, and per origin the names fit to be surrogates (no short or common
# word, no first name in "M" that would read as "M." in initials)
ORIGIN = {kind: {fold(n): o for n, o in names.items()} for kind, names in ORIGIN_OF.items()}
for _f, _m in (("ska", "ski"), ("ova", "ov"), ("eva", "ev"), ("ina", "in")):
    # feminine surnames are recognised from the masculine form listed
    ORIGIN["sur"].update({k[:-len(_m)] + _f: o for k, o in list(ORIGIN["sur"].items())
                          if k.endswith(_m) and o in ("russie", "pologne")})
ORIGIN_POOLS = {o: {kind: [n for n in names.split() if len(n) >= 4 and "-" not in n and rare_word(n, 3.5)
                           and not (kind != "sur" and n.lower().startswith("m"))]
                    for kind, names in lists.items()}
                for o, lists in ORIGINS.items()}
STREETS = ["rue des Lilas", "avenue de la Gare", "rue du Moulin", "chemin des Écoles",
           "rue Pasteur", "allée des Tilleuls", "boulevard de la République", "rue de l'Église"]
# gender from the title first, then from a small list of common first names
FEM_TITLES = {"mme", "madame", "mlle", "mademoiselle"}
MASC_TITLES = {"m.", "m", "monsieur"}
FEM_NAMES = {"camille", "nadia", "helene", "sylvie", "anne", "leila", "corinne", "josette",
             "isabelle", "sarah", "amandine", "cecile", "clemence", "elisa", "lisa", "murielle"}


def _loose_fold(folded):
    """Regex matching a folded word with or without its accents."""
    table = {"a": "[aàâä]", "e": "[eéèêë]", "i": "[iîï]", "o": "[oôö]", "u": "[uùûü]", "c": "[cç]", "y": "[yÿ]"}
    return "".join(table.get(ch, re.escape(ch)) for ch in folded)


def _cased(sur, real):
    """The surrogate is written in the case of the real word: "jean" -> "mahé", "DUPONT" -> "HUET"."""
    return sur.upper() if real.isupper() and len(real) > 1 else sur.lower() if real.islower() else sur


SALUTATIONS = {"monsieur", "madame", "mademoiselle", "messieurs", "mesdames", "maitre", "docteur",
               "professeur", "m", "mme", "mlle", "me", "dr", "pr", "cher", "chere", "chers"}

PARTICLES = {"de", "du", "des", "la", "le", "les", "d", "l", "d'", "l'", "van", "von", "der", "den",
             "di", "da", "del", "della", "y", "e", "et", "ben", "bin", "el", "al", "dit", "dite",
             "epouse", "ep", "nee", "ne", "veuve", "vve", "famille", "epoux"}


class Vault:
    """The key-value table for one dossier. Stable across documents of the same dossier."""

    def __init__(self, seed=0):
        self.rng = random.Random(seed)
        self.fwd = {}      # real atom (folded) -> surrogate atom
        self.rev = {}      # surrogate atom (folded) -> real atom (original spelling)
        self.used = set()
        self.people = []   # (real atoms, surrogate atoms) per person, for initials
        # keyed hashes of every folded word of the dossier, to keep surrogates out of it
        # without storing a copy of the documents in the table
        self.salt = bytes(self.rng.getrandbits(8) for _ in range(16))
        self.seen = set()
        self.tokens, self.counters = {}, {}   # fail-closed tokens, stable per value
        self.memo = {}                        # (type, value) -> surrogate
        self.forced = []                      # [type, value] the user asked to mask too
        self.allowed = set()                  # folded values the user asked never to mask

    def _free(self, x):
        # a surrogate already present in the source would be "rehydrated" by mistake
        return fold(x) not in self.used and fold(x) not in self.rev and not self._in_source(x)

    def _pick(self, pools, real=None):
        """First pool with a free name wins (same origin, then general). Never a surrogate
        given twice: two reals on one surrogate would rehydrate to the wrong person.
        Same kind of first letter as the real name (vowel, h, consonant): the model elides
        around the surrogate ("d'Aubin"), and "d'Julien" would come back."""
        for pool in pools:
            free = [x for x in pool if self._free(x)]
            if real:
                free = [x for x in free if _initial(x) == _initial(real)] or free
            if free:
                choice = self.rng.choice(free)
                break
        else:                                             # every pool used up: "Garnier-Benali"
            # rehydration matches the longest atoms first, so the compound comes back whole
            base = pools[-1]
            for _ in range(100000):
                choice = f"{self.rng.choice(base)}-{self.rng.choice(base)}"
                if self._free(choice):
                    break
            else:
                raise RuntimeError("surrogate pool exhausted")
        self.used.add(fold(choice))
        return choice

    @staticmethod
    def pools(kind, word, origin=None):
        """Surrogate pools for one name atom: its own origin if it has a known one, else
        the origin of the person, then the general pool of that kind ("sur", "F", "M")."""
        f = fold(word)
        common = f in COMMON["sur"] if kind == "sur" else f in FIRST_NAME_SEX
        o = ORIGIN[kind].get(f) or (None if common else origin)
        if not o:
            return [GENERAL[kind]]
        pool = ORIGIN_POOLS[o][kind]
        # feminine surnames keep their form: "Ivanova" -> "Smirnova", "Nowakowska" -> "Kowalska"
        if kind == "sur" and o == "russie" and f.endswith(("ova", "eva", "ina")):
            pool = [n + "a" for n in pool if n.endswith(("v", "n"))]
        elif kind == "sur" and o == "pologne" and f.endswith("ska"):
            pool = [n[:-1] + "a" for n in pool if n.endswith("ski")]
        return [pool, GENERAL[kind]]

    @staticmethod
    def origin_of(words):
        """Origin of a person: the first of its words found in an origin list."""
        for w in words:
            for kind in ("sur", "F", "M"):
                o = ORIGIN[kind].get(fold(w.strip(".,")))
                if o:
                    return o
        return None

    def _hash(self, word):
        return hashlib.blake2b(word.encode(), key=self.salt, digest_size=8).hexdigest()

    def _in_source(self, x):
        """A single word is checked whole; in a street, only its long words ("rue des Lilas")."""
        words = re.findall(r"[\w-]{2,}", fold(x))
        if len(words) > 1:
            words = [w for w in words if len(w) >= 4]
        return any(self._hash(w) in self.seen for w in words)

    def remember(self, text):
        self.seen |= {self._hash(w) for w in re.findall(r"[\w-]{2,}", fold(text))}

    FIELDS = ("fwd", "rev", "used", "people", "seen", "tokens", "counters", "memo")
    # decisions taken at review, applied to every later read of the dossier
    DECISIONS = ("forced", "allowed")

    def to_dict(self):
        d = {k: getattr(self, k) for k in self.FIELDS}
        d.update(used=sorted(self.used), seen=sorted(self.seen), memo=[[l, v, s] for (l, v), s in self.memo.items()],
                 salt=self.salt.hex(), rng=self.rng.getstate(), version=1,
                 forced=self.forced, allowed=sorted(self.allowed))
        return d

    @classmethod
    def from_dict(cls, d):
        v = cls()
        for k in cls.FIELDS:
            setattr(v, k, d[k])
        v.used, v.seen = set(d["used"]), set(d["seen"])
        v.forced, v.allowed = [list(x) for x in d.get("forced", [])], set(d.get("allowed", []))
        v.people = [tuple(p) for p in d["people"]]
        # tables saved before 28/09 kept colliding initials (last person won): replaying
        # every person neutralises each form that two people share
        for real_atoms, sur_atoms in v.people:
            v._initials(real_atoms, sur_atoms)
        # tables that pseudonymized their own surrogates (before is_surrogate): drop the
        # entries whose "real" value is itself a surrogate
        v.fwd = {k: x for k, x in v.fwd.items() if fold(k) not in v.rev or fold(v.rev[fold(k)]) == fold(k)}
        v.rev = {k: x for k, x in v.rev.items() if fold(x) not in v.rev or fold(x) == k}
        # tables written before the fix of shaped(): an identifier made of words kept its
        # lowercase letters, names included ("Yorick Varda" -> "Yorick Qarda"). Drop those.
        v.memo = {(l, val): s for l, val, s in d["memo"]
                  if not (l not in ("PER", "EMAIL", "DOB", "ADDR") and re.search(r"[a-zà-ÿ]{3}", val)
                          and not s.startswith("["))}
        v.salt = bytes.fromhex(d["salt"])
        st = d["rng"]
        v.rng.setstate((st[0], tuple(st[1]), st[2]))
        return v

    def decide(self, mask=(), unmask=()):
        """Review decisions: mask [(value, type)] too, never mask [value] again."""
        for val, lbl in mask:
            self.allowed.discard(fold(val.strip()))
            if [lbl, val.strip()] not in self.forced:
                self.forced.append([lbl, val.strip()])
        for val in unmask:
            self.forced = [f for f in self.forced if fold(f[1]) != fold(val.strip())]
            self.allowed.add(fold(val.strip()))

    def token(self, lbl, val):
        """Opaque token, the same for a value across the whole dossier."""
        key = fold(val)
        if key not in self.tokens:
            self.counters[lbl] = self.counters.get(lbl, 0) + 1
            self.tokens[key] = f"[{lbl}_{self.counters[lbl]}]"
            self.rev[fold(self.tokens[key])] = val
        return self.tokens[key]

    def forced_in(self, text):
        """Spans of the values the user asked to mask, wherever they appear."""
        out = []
        for lbl, val in self.forced:
            # word by word: re.escape turns a space into "\ ", which a later replace would break
            pattern = r"\s+".join(_loose_fold(w) for w in fold(val).split())
            for m in re.finditer(rf"(?<![\w@]){pattern}(?![\w@])", text, re.I):
                out.append((m.start(), m.end(), lbl, m.group()))
        return out

    def is_surrogate(self, val):
        """A value this table produced: a subagent's answer or a pasted protected message
        comes back through the hooks and must not get a surrogate of a surrogate."""
        body = re.sub(rf"^\s*{TITLE}\s+", "", val.strip())
        if fold(body) in self.rev:
            return True
        # compound names are registered part by part ("Huet-Pasquier" = "huet" + "pasquier")
        atoms = [fold(w) for w in re.findall(r"[\wÀ-ÿ'’]+", body) if fold(w) not in PARTICLES and len(w) >= 2]
        return bool(atoms) and all(a in self.rev for a in atoms)

    def atom(self, real, pool, loose=False):
        """One surrogate per spelling ("Kevin" and "Kévin" are two entries, so each comes
        back as written). loose=True (email local parts, which carry no accents) reuses the
        entry of any spelling that folds the same."""
        key = real.lower()
        if loose and key not in self.fwd:
            key = next((k for k in self.fwd if fold(k) == fold(real)), key)
        if key not in self.fwd:
            sur = self._pick(pool if pool and isinstance(pool[0], list) else [pool], real)
            self.fwd[key] = sur
            self.rev[fold(sur)] = real
        return self.fwd[key]

    def find(self, real):
        """Surrogate of a word, whatever its case; accents must match unless none known."""
        return self.fwd.get(real.lower()) or next((s for k, s in self.fwd.items() if fold(k) == fold(real)), None)

    def person(self, text):
        m = re.match(rf"\s*({TITLE})\s+", text)
        title = m.group(1) if m else ""
        body = text[m.end():] if m else text
        toks = [t for t in re.split(r"(\s+|,\s*|_)", body)]
        words = [t for t in toks if t.strip(" ,") and re.search(r"[A-Za-zÀ-ÿ]", t)]
        # surname = the ALLCAPS word if any, else the last word
        caps = [w for w in words if w.isupper() and len(w) > 2]
        surname = caps[0] if caps else (words[-1] if words else "")
        if len(words) == 1 and not caps and is_first_name(words[0]):
            surname = ""                                  # "marie" alone is a first name
        if fold(title) in FEM_TITLES:
            fem = True
        elif fold(title) in MASC_TITLES:
            fem = False
        else:
            sexes = [FIRST_NAME_SEX.get(fold(w.strip(".,"))) or
                     ("F" if fold(w.strip(".,")) in ORIGIN["F"] else "M" if fold(w.strip(".,")) in ORIGIN["M"] else None)
                     for w in words]
            sexes = [x for x in sexes if x]
            fem = bool(sexes) and sexes[0] == "F"
        origin = self.origin_of(words)
        out = []
        for t in toks:
            w = t.strip()
            if not w or not re.search(r"[A-Za-zÀ-ÿ]", w):
                out.append(t); continue
            if fold(w.strip(".,")) in PARTICLES:
                out.append(t); continue                    # "de", "Le", "van": never an atom
            if re.fullmatch(r"[A-ZÀ-Ý]\.", w):             # initial: keep it an initial
                out.append(t); continue
            kind = "sur" if w == surname else ("F" if fem else "M")
            if "-" in w.strip("-") and all(len(x) >= 2 for x in w.split("-")):
                # compound names part by part: "Ngombé-Vasseur" keeps its link with "Ngombé",
                # and "s.brassart@" still matches "Brassart-Nguyen"
                # and each part keeps its own case ("Brigadier-chef" must come back lowercase)
                s = "-".join(_cased(self.atom(x, self.pools(kind, x, origin)), x) for x in w.split("-"))
            else:
                s = _cased(self.atom(w, self.pools(kind, w, origin)), w)
            out.append(t.replace(w, s))
        real_atoms = [w.strip(" ,") for w in words if not re.fullmatch(r"[A-ZÀ-Ý]\.", w.strip())
                      and fold(w.strip(".,")) not in PARTICLES]
        sur_atoms = [self.find(a) or "-".join(self.find(x) or x for x in a.split("-")) for a in real_atoms]
        self.people.append((real_atoms, sur_atoms))
        self._initials(real_atoms, sur_atoms)
        return (title + " " if title else "") + "".join(out)

    def _initials(self, real_atoms, sur_atoms):
        """The model derives initials from the surrogate names ("A.B.L."). They are not
        values of the document, so register every form of them up front."""
        if len(real_atoms) != len(sur_atoms) or len(real_atoms) < 2:
            return
        r = [a[0].upper() for a in real_atoms]
        s = [a[0].upper() for a in sur_atoms]
        orders = [list(range(len(r))), [0, len(r) - 1], [len(r) - 1, 0],
                  [len(r) - 1] + list(range(len(r) - 1))]
        for idx in orders:
            ri, si = [r[i] for i in idx], [s[i] for i in idx]
            if si[0] == "M":        # "M. R." is also "Monsieur R.": it would come back rewritten
                continue
            for sep in (".", ". "):
                self._initial_form(sep.join(si) + ".", sep.join(ri) + ".")
            # undotted initials can spell a word ("LES"): rehydration would rewrite every "les"
            # two letters too: models write "CD" for Claire Dubois (ChatGPT, 28/09). No
            # frequency test can judge two letters; rehydration takes them in capitals only.
            k = fold("".join(si))
            two = len(idx) == 2 and k not in FR_TWO
            if (two or len(idx) >= 3 and rare_word("".join(si), 2.0)) and (k not in self.rev or self.rev[k].isupper()):
                self._initial_form("".join(si), "".join(ri))

    def _initial_form(self, sur, real):
        """Two real people behind the same surrogate initials ("B. G." for Sophie Lambert and
        for someone of an earlier test, seen on Claude 28/09): the form comes back as itself,
        for good. A wrong person's initials are worse than the double's."""
        k = fold(sur)
        if k in self.rev and fold(self.rev[k]) != fold(real):
            self.rev[k] = sur
        else:
            self.rev[k] = real

    def set_reference_dates(self, text):
        """Every (month, day) the dossier mentions, plus today: ages are computed against these."""
        import datetime
        months = {m: i + 1 for i, m in enumerate(
            ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"])}
        cuts = {(datetime.date.today().month, datetime.date.today().day)}
        for d, mo in re.findall(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](?:19|20)\d{2}\b", text):
            cuts.add((int(mo), int(d)))
        for d, mo in re.findall(r"\b(\d{1,2})(?:er)?\s+(" + "|".join(months) + r")\b", text, re.I):
            cuts.add((months[mo.lower()], int(d)))
        self.cuts = sorted(c for c in cuts if 1 <= c[0] <= 12 and 1 <= c[1] <= 31)

    def forward(self, text):
        """Apply the table to new text (a question the user types): every value already
        replaced in this dossier gets the very same surrogate, longest first; then name
        atoms catch partial mentions ("M. Durand" when "Paul Durand" was replaced)."""
        out = text
        for (lbl, real), sur in sorted(self.memo.items(), key=lambda x: -len(x[0][1])):
            if len(real) >= 3:
                out = re.sub(rf"(?<![\w-]){re.escape(real)}(?![\w-])", lambda m, s3=sur: s3, out, flags=re.I)
        for real_key, sur in sorted(self.fwd.items(), key=lambda x: -len(x[0])):
            out = re.sub(rf"(?<![\w-]){_loose_fold(fold(real_key))}(?![\w-])",
                         lambda m, s3=sur: s3.upper() if m.group().isupper() and len(m.group()) > 1 else s3,
                         out, flags=re.I)
        return out

    def email(self, real):
        if "@" not in real:                               # "jean.dupont [at] gmail [dot] com"
            sur = f"contact{self.rng.randint(100, 999)} [at] exemple [dot] fr"
            self.rev[fold(sur)] = real
            return sur
        local, domain = real.split("@", 1)
        parts = re.split(r"([._-])", local)
        new = []
        for p in parts:
            if self.find(p):
                new.append(self.find(p).lower())
            elif len(p) == 1 and p.isalpha():
                new.append(p)
            elif re.fullmatch(r"[._-]", p):
                new.append(p)
            elif p.isalpha() and len(p) >= 2 and is_first_name(p):
                # a first name in an address: same atom as the person, whatever comes first
                sex = FIRST_NAME_SEX.get(fold(p), "M")
                new.append(self.atom(p, self.pools(sex, p), loose=True).lower())
            elif p.isalpha() and len(p) >= 3:
                new.append(self.atom(p, self.pools("sur", p), loose=True).lower())
            elif p.isdigit():
                new.append("".join(str(self.rng.randint(0, 9)) for _ in p))
            else:
                m = re.match(r"([a-zà-ÿ]+)(\d+)$", p, re.I)       # "tournier83"
                if m and len(m.group(1)) >= 3:
                    new.append(self.atom(m.group(1), self.pools("sur", m.group(1)), loose=True).lower() +
                               "".join(str(self.rng.randint(0, 9)) for _ in m.group(2)))
                else:
                    new.append(self.atom(p, [f"contact{self.rng.randint(10,99)}"]).lower())
        sur = "".join(new) + "@" + self.atom(domain, [f"exemple-{self.rng.randint(100,999)}.fr"])
        self.rev[fold(sur)] = real
        return sur

    def shaped(self, real):
        """Same shape, different characters: phone, IBAN, identifiers."""
        if len(re.sub(r"\W", "", real)) < 3 or is_date(real):
            return real                                    # fail-closed upstream
        if re.search(r"[a-zà-ÿ]{3}", real):
            # words, not an identifier ("Where an action by Yorick Varda"): changing only the
            # capitals would leave the name readable. Fail closed upstream (a token).
            return real
        for _ in range(50):                                # never two reals on one surrogate
            sur = self._shape_once(real)
            # random letters can spell a word ("DES"): rehydration would rewrite every "des"
            letters = re.findall(r"[A-Za-z]{2,}", sur)
            if (fold(sur) not in self.rev and fold(sur) != fold(real)
                    and all(rare_word(w, 2.0) and not self._in_source(w) for w in letters)):
                break
        else:
            return real                                    # no safe shape ("CÉT" -> "LES"): fail closed upstream
        self.rev[fold(sur)] = real
        return sur

    def _shape_once(self, real):
        sur = "".join(str(self.rng.randint(0, 9)) if c.isdigit() else
                      (chr(self.rng.randint(65, 90)) if c.isalpha() and c.isupper() and
                       not real.startswith(("FR", "+33")) else c) for c in real)
        if real.startswith("FR"): sur = "FR" + sur[2:]
        if re.fullmatch(r"\s*0\d(?:[ .-]?\d{2}){4}\s*", real):
            sur = real[:2] + sur[2:]                      # keep "06", "09": a believable number
        if real.startswith("+33"):
            sur = real[:4] + sur[4:]
        return sur

    def dob(self, real):
        """Keep the year, and pick day/month between the same two reference dates as the
        real one: the age at any date of the dossier is then exactly preserved."""
        m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-]((?:19|20)\d{2})", real)
        months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                  "septembre", "octobre", "novembre", "décembre"]
        if m:
            d, mo, year = int(m.group(1)), int(m.group(2)), m.group(3)
        else:
            m = re.search(r"(\d{1,2})(?:er)?\s+(" + "|".join(months) + r")\s+((?:19|20)\d{2})",
                          real, re.I)
            if not m:
                return real   # fail-closed upstream turns this into a token
            d, mo, year = int(m.group(1)), months.index(m.group(2).lower()) + 1, m.group(3)
        cuts = getattr(self, "cuts", [])
        lo = max([c for c in cuts if c < (mo, d)], default=(1, 0))
        hi = min([c for c in cuts if c >= (mo, d)], default=(12, 32))
        cands = [(a, b) for a in range(1, 13) for b in range(1, 29)
                 if lo < (a, b) < hi and (a, b) != (mo, d)]
        same_month = [c for c in cands if c[0] == mo]
        a, b = self.rng.choice(same_month or cands) if cands else (mo, d)
        num = re.search(r"\d{1,2}([/.-])\d{1,2}[/.-]", real)
        if num:
            sep = num.group(1)
            sur = f"{b:02d}{sep}{a:02d}{sep}{year}"
        else:
            sur = f"{'1er' if b == 1 else b} {months[a - 1]} {year}"
        self.rev[fold(sur)] = real
        # the model also writes the birthday without the year ("anniversaire le 14 août"):
        # register day-month forms, like initials
        for sd, rd in ((f"{'1er' if b == 1 else b} {months[a - 1]}", f"{'1er' if d == 1 else d} {months[mo - 1]}"),
                       (f"{b:02d}/{a:02d}", f"{d:02d}/{mo:02d}")):
            self.rev.setdefault(fold(sd), rd)
        return sur

    def address(self, real):
        """Replace the street, keep postcode and city: they carry jurisdiction."""
        m = re.match(r"\s*\d{1,4}\s?(?:bis|ter)?\s+[^,\n]*", real)
        if not m:
            return real
        # "14 rue des Lilas à Lyon.": the city and the full stop stay (jurisdiction, sentence)
        tail = re.search(r"\s+(?:à|a)\s+\S.*$|\.\s*$", m.group())
        if tail and tail.start() > 0:
            m = re.match(re.escape(m.group()[:tail.start()]), real)
        # a street may serve twice: the whole address, number included, is the key
        street = self.rng.choice([x for x in STREETS if not self._in_source(x)] or STREETS)
        for _ in range(1000):                             # never two addresses on one surrogate
            sur_street = f"{self.rng.randint(2, 98)} {street}"
            if fold(sur_street) not in self.rev:
                break
            street = self.rng.choice(STREETS)
        self.rev[fold(sur_street)] = m.group().strip()
        # The street alone, as a model quotes it back ("allée des Tilleuls", seen on ChatGPT
        # 28/09): only while one real street stands behind it, or it could not come back right.
        bare = lambda v: re.sub(r"\s+(?:à|a)\s+.*$", "", re.sub(r"^\s*\d{1,4}\s?(?:bis|ter)?\s+", "", v)).rstrip(" .")
        key = fold(street)
        reals = {bare(v) for k, v in self.rev.items() if re.fullmatch(r"\d{1,4} " + re.escape(key), k)}
        if len(reals) == 1:
            self.rev[key] = reals.pop()
        else:
            self.rev.pop(key, None)
        return sur_street + real[m.end():]


def pseudonymize(text, vault, context="", only=None, spans=None):
    """only: keep just the detections this predicate accepts (a typed message masks only what blocked it).
    spans: a list that receives (start, end, type, real, surrogate) for every masked value, in
    the coordinates of text (for the review before sending)."""
    vault.set_reference_dates(context + "\n" + text)
    vault.remember(context + "\n" + text)
    pred = detect_gliner(text) + detect_regex(text)
    pred += propagate(text, pred)
    pred += vault.forced_in(text)                   # "mask this too", decided at review
    pred = [x for x in pred if fold(x[3].strip()) not in vault.allowed]   # "never mask this"
    if only:
        pred = [x for x in pred if only(x)]
    pred = [x for x in pred if not vault.is_surrogate(x[3])]
    # a value never crosses a table cell or a line: cut every span at tabs and newlines
    cut = []
    for s0, e0, lbl, v in pred:
        start = s0
        for m in re.finditer(r"[\t\n]+", text[s0:e0]):
            if text[start:s0 + m.start()].strip():
                cut.append((start, s0 + m.start(), lbl, text[start:s0 + m.start()]))
            start = s0 + m.end()
        if text[start:e0].strip():
            cut.append((start, e0, lbl, text[start:e0]))
    # each piece must still have the shape of its type, or another detector owns it
    shape = {"ADDR": lambda v: re.search(r"\d|\b(rue|avenue|bd|boulevard|chemin|route|place|all[ée]e|"
                                         r"impasse|quai|cours|lieu-dit|hameau|r[ée]sidence|b[âa]t)", v, re.I),
             "PHONE": lambda v: sum(c.isdigit() for c in v) >= 8,
             "EMAIL": lambda v: "@" in v or re.search(r"\[\s*at\s*\]", v, re.I),
             # a title alone ("Madame, Monsieur,") is a salutation, never a name
             "PER": lambda v: (re.search(r"[A-ZÀ-Ý]", v) or v.islower())
                    and any(fold(w) not in SALUTATIONS and fold(w) not in PARTICLES
                            for w in re.findall(r"[\wÀ-ÿ'’-]+", v))}
    pred = [x for x in cut if shape.get(x[2], lambda v: True)(x[3])]
    label_at = {}
    for s, e, lbl, _ in sorted(pred, key=lambda x: -(x[1] - x[0])):
        label_at.setdefault((s, e), lbl)
    out, pos, n = [], 0, 0
    # values known before this text: built once per call (new ones are detected in this text)
    known = _known_pattern(vault)
    for c0, c1 in merge(pred):          # overlaps only: never glue two different values
        lbl = next((l for (s, e), l in label_at.items() if c0 <= s and e <= c1), "PII")
        val = text[c0:c1]
        memo = (lbl, val if lbl == "PER" else fold(val))   # same value, same surrogate
        if memo in vault.memo:
            rep = vault.memo[memo]
        else:
            rep = {"PER": vault.person, "EMAIL": vault.email, "DOB": vault.dob,
                   "ADDR": vault.address}.get(lbl, vault.shaped)(val)
            vault.memo[memo] = rep
        if fold(rep) == fold(val):
            # fail closed: a replacer that could not transform the value must not let it pass.
            # Same value, same token, across the whole dossier.
            rep = vault.token(lbl, val)
        out += [mask_known(text[pos:c0], vault, known, spans, pos), rep]; pos = c1
        if spans is not None:
            spans.append((c0, c1, lbl, val, rep))
    out.append(mask_known(text[pos:], vault, known, spans, pos))
    _link_form_fields(text, vault)
    out = "".join(out)
    # a forced value kept inside a bigger surrogate (the city of an address): the user's
    # decision wins, it becomes a token there too
    for lbl, val in vault.forced:
        out = re.sub(rf"(?<![\w@]){_loose_fold(fold(val)).replace(' ', r'\s+')}(?![\w@])",
                     lambda m, l=lbl: vault.token(l, m.group()), out, flags=re.I)
    return out


def _known_pattern(vault):
    """One compiled pattern for every known value, rebuilt only when the table grew."""
    sig = (len(vault.memo), len(vault.fwd))
    cached = getattr(vault, "_known", None)
    if cached and cached[0] == sig:
        return cached[1], cached[2]
    pats = []
    for (lbl, key), sur in vault.memo.items():
        if lbl != "PER" and len(key) >= 4 and re.search(r"[\d@ ]", key) and fold(sur) != fold(key):
            pats.append((key, sur, _loose_fold(fold(key))))
    for key, sur in vault.fwd.items():
        if len(key) >= 3 and re.fullmatch(r"[\w'’-]+", key):
            pats.append((key, sur, re.escape(key)))
    rx, by_fold = None, {}
    if pats:
        pats.sort(key=lambda p: -len(p[0]))
        for key, sur, _ in pats:
            by_fold.setdefault(fold(key), (key, sur))
        # "_" separates words in file names ("Accord_v4_Yorick.docx"): letters and digits only
        rx = re.compile(r"(?<![^\W_])(?:" + "|".join(p for _, _, p in pats) + r")(?![^\W_])", re.I)
    vault._known = (sig, rx, by_fold)
    return rx, by_fold


def mask_known(text, vault, known=None, spans=None, offset=0):
    """A value masked once is masked everywhere, even where the detector misses it this time
    (a short command output has less context than the whole file). Whole values with a digit,
    an @ or a space; name atoms when capitalised, or rare words when lowercase."""
    rx, by_fold = known or _known_pattern(vault)
    if rx is None or not text.strip():
        return text

    def sub(m):
        w = m.group()
        key, sur = by_fold.get(fold(w), (None, None))
        if key is None:
            return w
        if " " not in key and not re.search(r"\d", key) and w.islower() and not rare_word(key, 4.0):
            return w                                  # "chef" stays a word, "Chef" may be a name
        if fold(w) in vault.allowed:
            return w
        out = _cased(sur, w) if re.fullmatch(r"[\w'’-]+", key) else sur
        if spans is not None:
            spans.append((offset + m.start(), offset + m.end(), "CONNU", w, out))
        return out
    return rx.sub(sub, text)


def _link_form_fields(text, vault):
    """Forms put surname and given names on separate lines. Join them so the initials the
    model derives from the full name ("L.F.M.") are known."""
    sur = re.search(r"nom\s+de\s+famille\s*:[ \t]*([^\n]+)", text, re.I)
    giv = re.search(r"pr[ée]noms?(?:\(s\))?\s*:[ \t]*([^\n]+)", text, re.I)
    if not (sur and giv):
        return
    real = giv.group(1).split() + sur.group(1).split()
    if all(vault.find(a) for a in real):
        vault._initials(real, [vault.find(a) for a in real])
