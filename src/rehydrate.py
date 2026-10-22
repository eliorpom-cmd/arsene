"""Put the real values back into whatever the model wrote, and say what could not be traced.

Three kinds of output text:
  1. forms of known values (case, accents, possessive, initials, emails rebuilt from names)
     -> reversed here, atom by atom;
  2. facts derived from a masked value (age, gender) -> preserved upstream by the surrogate
     choice, nothing to reverse;
  3. new content the model introduced -> not in the table by construction; reported for
     review, never guessed.
"""
import re, sys, pathlib, types, unicodedata
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from propagate import fold


INITIALS = re.compile(r"(?:[a-z]\.\s?){2,}")


def _loose(s):
    """Regex matching s with or without accents, any case."""
    out = []
    for ch in s:
        base = fold(ch)
        if base != ch.lower() or ch.lower() in "aeiouc":
            variants = {ch.lower(), ch.upper(), base, base.upper()}
            # every accented variant of the base letter
            for c in "àâäáãåèéêëìíîïòóôöõùúûüçñ":
                if fold(c) == base:
                    variants |= {c, c.upper()}
            out.append("[" + "".join(sorted(re.escape(v) for v in variants)) + "]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _styled(real, seen):
    """Write the real value in the case the model used for the surrogate."""
    if seen.isupper() and len(seen) > 1:
        return real.upper()
    if seen.islower():
        return real.lower()
    if seen[:1].isupper() and (real.isupper() or real.islower()):
        return "-".join(p[:1].upper() + p[1:].lower() for p in real.split("-"))
    return real


def rehydrate(text, vault):
    """One pass: every surrogate found is first swapped for a placeholder, then all
    placeholders become real values. Restored text is never matched again (a partial form
    "9 février" must not rewrite inside the restored "19 février")."""
    held, slots = _hold_all(text, vault)
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))][0], held)


def in_context(vault, context):
    """Initials two people of the dossier share come back as themselves (Vault._initial_form):
    a browser dossier fills up with people, and "B. R." stayed a double's initials on Claude
    (29/09). When only one of those people appears in the conversation, the form is theirs."""
    neutral = {k for k, v in vault.rev.items() if INITIALS.fullmatch(k) and fold(v) == k}
    if not context or not neutral:
        return vault
    from surrogate import Vault
    words = {fold(w) for w in re.findall(r"[\w'’-]+", context)}
    reals = {}                                            # neutral form -> real forms in context
    for real_atoms, sur_atoms in vault.people:
        if len(sur_atoms) < 2 or fold(sur_atoms[-1]) not in words:
            continue
        probe = Vault.__new__(Vault)
        probe.rev = {}
        probe._initials(real_atoms, sur_atoms)
        for k, real in probe.rev.items():
            if k in neutral:
                reals.setdefault(k, set()).add(real)
    rev = dict(vault.rev)
    rev.update({k: next(iter(r)) for k, r in reals.items() if len(r) == 1})
    return types.SimpleNamespace(rev=rev, people=vault.people)


def spans(text, vault):
    """[[start, end, real], ...]: where each surrogate sits in `text` and its real value.
    For a display that shows real values without rewriting the text (browser extension)."""
    held, slots = _hold_all(text, vault)
    held_gap = lambda s, e: text[s:e]
    out, pos, at = [], 0, 0
    for m in re.finditer(r"\x00(\d+)\x00", held):
        pos += m.start() - at
        real, fake = slots[int(m.group(1))]
        if real == fake:                                 # neutralised form (ambiguous initials)
            pos += len(fake); at = m.end()
            continue
        gap = pos - out[-1][1] if out else -1
        if gap in (0, 1) and (gap == 0 or held_gap(out[-1][1], pos) == " "):   # "C. Mercier", "Marc Garnier": one name
            out[-1][2] += (" " if gap else "") + real; out[-1][1] = pos + len(fake)
        else:
            out.append([pos, pos + len(fake), real])
        pos += len(fake); at = m.end()
    return out


def _hold_all(text, vault):
    slots = []

    def hold(real, m):
        slots.append((real, m.group()))
        return f"\x00{len(slots) - 1}\x00"

    # 1. whole values first (emails, identifiers, dates, streets, initials), longest first
    wholes = {k: v for k, v in vault.rev.items() if not re.fullmatch(r"[\w'’-]+", k)}
    for sur in sorted(wholes, key=len, reverse=True):
        if INITIALS.fullmatch(sur):
            # initials: exact capitals, isolated, never inside "A.B.C." nor after "Adoptée"
            pat = re.escape(sur.upper()).replace(r"\ ", r"\s?")
            # spaced as the model wrote it: "E.H." stays unspaced even if "E. H." matched first
            text = re.sub(rf"(?<![\w.]){pat}(?!\s?[A-ZÀ-Ý]\.)",
                          lambda m, r=vault.rev[sur]: hold(r if " " in m.group() else r.replace(" ", ""), m), text)
            continue
        text = re.sub(rf"(?<![\w\x00]){_loose(sur)}(?![\w\x00])", lambda m, r=vault.rev[sur]: hold(r, m),
                      text, flags=re.I)
    # 2. initials the model derived from a surrogate first name: "C. Mercier" -> "L. Marchand"
    for real_atoms, sur_atoms in vault.people:
        if len(sur_atoms) >= 2:
            sur_first, sur_last = sur_atoms[0], sur_atoms[-1]
            real_first = real_atoms[0]
            text = re.sub(rf"(?<![\w.]){re.escape(sur_first[0].upper())}\.\s*(?={_loose(sur_last)}\b)",
                          lambda m, r=real_first[0].upper() + ". ": hold(r, m), text)
    # 3. name atoms, word by word, keeping the case style the model used
    atoms = {k: v for k, v in vault.rev.items() if re.fullmatch(r"[\w'’-]+", k)}
    for sur in sorted(atoms, key=len, reverse=True):
        # a placeholder holds only digits: it can sit right before a name ("C." + surname)
        text = re.sub(rf"(?<![A-Za-zÀ-ÿ0-9]){_loose(sur)}(?![A-Za-zÀ-ÿ0-9])",
                      lambda m, r=vault.rev[sur]: hold(_styled(r, m.group()), m) if _takes(sur, r, m.group())
                      else m.group(), text, flags=re.I)
    return text, slots


def _takes(sur, real, seen):
    """Two-letter initials ("BR" for Bertille Ragueneau) only in capitals: "br" is a word."""
    return not (len(sur) == 2 and real.isupper()) or seen.isupper()


def leftovers(text, vault, detector=None):
    """Surrogate fragments still present, and PII-looking spans that trace to nothing."""
    left = [sur for sur in vault.rev if re.search(rf"(?<![A-Za-zÀ-ÿ0-9]){_loose(sur)}(?![A-Za-zÀ-ÿ0-9])", text, re.I)]
    untraced = []
    reals_init = {fold(v) for v in vault.rev.values() if INITIALS.fullmatch(fold(v))}
    for m in re.finditer(r"(?<![A-Za-z.])(?:[A-Z]\.\s?){2,}", text):
        if fold(m.group().strip()) not in reals_init:
            untraced.append(("INITIALES", m.group().strip()))
    if detector:
        reals = {fold(v) for v in vault.rev.values()}
        for s, e, lbl, val in detector(text):
            plausible = ((lbl == "EMAIL" and "@" in val) or
                         (lbl == "PHONE" and sum(c.isdigit() for c in val) >= 6) or
                         (lbl == "PER" and re.search(r"[A-ZÀ-Ý][a-zà-ÿ]{2,}\s+[A-ZÀ-Ý]", val)))
            if plausible and not any(fold(val) in r or r in fold(val) for r in reals):
                untraced.append((lbl, val))
    return left, untraced
