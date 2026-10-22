"""The table on the disk: encrypted, migrated from the old clear file, never readable without
its key, never silently replaced. Runs with PSEUDO_HOME set: the key lives in a test file,
the user's keychain is not touched."""
import json, os, pathlib, sys, tempfile

HOME = tempfile.mkdtemp()
os.environ["PSEUDO_HOME"] = HOME
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import atrest  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("ok  " if ok else "ÉCHEC", name, detail)


d = pathlib.Path(tempfile.mkdtemp()) / ".pseudo"
d.mkdir()
table = {"rev": {"aubin": "Julien"}, "memo": [["PER", "Julien Moreau", "Aubin Godet"]]}

# 1. saved encrypted: no real name in the file
atrest.save(d / "vault.bin", "vault", table)
raw = (d / "vault.bin").read_bytes()
check("chiffrée : aucun vrai nom dans le fichier", b"Julien" not in raw and b"Moreau" not in raw and raw.startswith(b"ARS1"))
check("relue à l'identique", atrest.load(d / "vault.bin", "vault") == table)
check("droits 0600", (d / "vault.bin").stat().st_mode & 0o777 == 0o600)
check("clé de test hors du trousseau", (pathlib.Path(HOME) / "table.key").exists())

# 2. a file of one kind cannot pass for another (state read as a table)
try:
    atrest.load(d / "vault.bin", "state"); check("type lié au chiffrement", False)
except Exception:
    check("type lié au chiffrement", True)

# 3. tampered: refused
bad = bytearray(raw); bad[-1] ^= 1
(d / "x.bin").write_bytes(bytes(bad))
try:
    atrest.load(d / "x.bin", "vault"); check("fichier altéré refusé", False)
except Exception:
    check("fichier altéré refusé", True)

# 4. migration: the old clear table becomes encrypted, the clear file goes
old = pathlib.Path(tempfile.mkdtemp()) / ".pseudo"
old.mkdir()
(old / "vault.json").write_text(json.dumps(table, ensure_ascii=False))
check("ancienne table en clair relue", atrest.load(old / "vault.bin", "vault") == table)
check("puis chiffrée, le fichier en clair supprimé", (old / "vault.bin").exists() and not (old / "vault.json").exists())

# 5. key lost: nothing is read, and no new key is made to read it
(pathlib.Path(HOME) / "table.key").unlink()
atrest._key = None
try:
    atrest.load(d / "vault.bin", "vault"); check("clé perdue : lecture refusée", False)
except atrest.KeyMissing:
    check("clé perdue : lecture refusée", True)
check("clé perdue : aucune clé neuve créée par la lecture", not (pathlib.Path(HOME) / "table.key").exists())

print(f"{sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
