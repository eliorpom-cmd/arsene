"""Stands in for GLiNER where the model cannot be downloaded (a cloud session without access
to huggingface.co): PYTHONPATH=tests/sans-modele python tests/table.py

Finds people only, by a plain rule: a first name attested by INSEE followed by capitalized
words ("Julien Moreau", "Karim HAMIDI"). Everything else (propagation of fragments, rules,
surrogates, rehydration, the table) is the real code. Numbers measured with it mean nothing
for detection: it only lets the plumbing be tested end to end."""
import re


class _Model:
    def predict_entities(self, text, labels, threshold=0.5):
        from lexicon import has_first_name
        out, end = [], 0
        # every capitalized word may start a name: "Affaire Julien Moreau" gives "Julien Moreau"
        for w in re.finditer(r"\b[A-ZÀ-Ý][a-zà-ÿ]+\b", text):
            if w.start() < end or not has_first_name(w.group(0)):
                continue
            m = re.compile(r"[^ ]+(?:[ ]+[A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+)+").match(text, w.start())
            if m:
                out.append({"start": m.start(), "end": m.end(), "label": "person", "text": m.group(0), "score": 0.9})
                end = m.end()
        return out


class GLiNER:
    @classmethod
    def from_pretrained(cls, *a, **k):
        return _Model()
