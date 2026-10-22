"""Surrogate pools: never two reals on one surrogate, origin kept when known."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import surrogate as s
from rehydrate import rehydrate, spans, in_context

fails = total = 0
def check(name, ok):
    global fails, total
    total += 1
    fails += not ok
    print(("ok  " if ok else "FAIL"), name)

# a big dossier: 3000 surnames of unknown origin + 300 Maghrebi ones, no duplicate
v = s.Vault(1)
surs = [v.atom(f"Reel{i}", v.pools("sur", "x")) for i in range(3000)]
surs += [v.atom(f"Ben{i}", v.pools("sur", "Benali")) for i in range(300)]
check("3300 noms, aucun faux nom partagé", len({s.fold(x) for x in surs}) == len(surs))

# pools used up: compounds, still unique and rehydrated whole
v = s.Vault(2)
got = [v.atom(x, [["Garnier", "Benard"]]) for x in ("Dupont", "Durand", "Lefort", "Petit")]
check("stock épuisé : noms composés uniques", len(set(got)) == 4)
check("stock épuisé : réhydratation exacte",
      rehydrate(" / ".join(got), v) == "Dupont / Durand / Lefort / Petit")

# origin kept for listed names, never forced onto a common French name
v = s.Vault(3)
def origin(real, kind):
    return s.ORIGIN[kind].get(s.fold(v.find(real) or ""))
v.person("Mohamed BENALI"); v.person("Mme Aminata Traoré"); v.person("NGUYEN Van Minh")
v.person("Mme Yasmina Dupont"); v.person("Olga Ivanova")
check("Benali -> nom maghrébin", origin("BENALI", "sur") == "maghreb")
check("Aminata -> prénom d'Afrique de l'Ouest", origin("Aminata", "F") == "afrique-ouest")
check("Nguyen -> nom vietnamien", origin("NGUYEN", "sur") == "vietnam")
check("Dupont reste un nom courant en France", origin("Dupont", "sur") is None)
check("Ivanova garde la forme féminine", v.find("Ivanova").endswith("a"))
# addresses: a real street, never letters
v = s.Vault(4)
sur = v.address("4 place Grenette, 38000 Grenoble")
check("adresse : une vraie rue, même code postal", any(st in sur for st in s.STREETS) and sur.endswith("38000 Grenoble"))
# initials two people share: neutral in the dossier, settled by the conversation (Claude, 29/09)
v = s.Vault(5)
for real, sur in ((["Claire", "Dubois"], ["Berthe", "Rousselet"]), (["Jean", "Martin"], ["Bruno", "Roche"])):
    v.people.append((real, sur)); v._initials(real, sur)
said = "Initiales : B. R."
check("initiales partagées : neutres sans contexte", spans(said, in_context(v, "")) == [])
check("initiales partagées : la personne de la conversation", [x[2] for x in spans(said, in_context(v, "Berthe Rousselet"))] == ["C. D."])
check("initiales partagées : les deux dans la conversation, neutres",
      spans(said, in_context(v, "Berthe Rousselet et Bruno Roche")) == [])
print(f"{total - fails}/{total}")
sys.exit(bool(fails))
