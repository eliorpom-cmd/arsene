import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from bench import detect_regex, detect_gliner, merge
from propagate import propagate

def redact(text):
    pred = detect_gliner(text) + detect_regex(text)
    pred += propagate(text, pred)
    # keep the label of the longest span covering each region
    best = {}
    for s, e, lbl, val in sorted(pred, key=lambda x: -(x[1] - x[0])):
        best.setdefault((s, e), lbl)
    cov = merge(pred, text)
    label_of = {}
    for (s, e), lbl in best.items():
        for c in cov:
            if c[0] <= s and e <= c[1]:
                label_of.setdefault(tuple(c), lbl)
    out, mapping, counters, pos = [], {}, {}, 0
    for c in cov:
        lbl = label_of.get(tuple(c), "PII")
        val = text[c[0]:c[1]]
        if val not in mapping:
            counters[lbl] = counters.get(lbl, 0) + 1
            mapping[val] = f"[{lbl}_{counters[lbl]}]"
        out.append(text[pos:c[0]]); out.append(mapping[val]); pos = c[1]
    out.append(text[pos:])
    return "".join(out), mapping

if __name__ == "__main__":
    txt = pathlib.Path(sys.argv[1]).read_text()
    red, mp = redact(txt)
    print(red)
    print("\n" + "=" * 30 + " TABLE DE CORRESPONDANCE " + "=" * 30)
    for k, v in mp.items():
        print(f"  {v:12} <- {k!r}")
