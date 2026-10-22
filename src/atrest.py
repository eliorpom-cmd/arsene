"""The dossier's table and state, encrypted on the disk (AES-256-GCM).

The table lists every real name of the dossier: in clear on the disk, any backup, sync or
stolen laptop would carry them. The key is random, kept in the macOS login keychain (never
on a command line: `security -i` reads it from stdin); with PSEUDO_HOME set (tests), in a
0600 file there instead, so test runs never fill the user's keychain.

Losing the key means losing the table: `pseudo.py export` is the backup. A missing key with
an encrypted file on the disk is an error, never a reason to start a new table."""
import json, os, pathlib, secrets, subprocess
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ARS1"
SERVICE, ACCOUNT = "dev.byelior.arsene", "table"
_key = None


class KeyMissing(RuntimeError):
    pass


def _home():
    return os.environ.get("PSEUDO_HOME")


def _read_key():
    if _home():
        f = pathlib.Path(_home()) / "table.key"
        return bytes.fromhex(f.read_text().strip()) if f.exists() else None
    r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                       capture_output=True, text=True)
    return bytes.fromhex(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _new_key():
    k = secrets.token_bytes(32)
    if _home():
        f = pathlib.Path(_home()) / "table.key"
        f.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(k.hex())
        return k
    r = subprocess.run(["security", "-i"], capture_output=True, text=True,
                       input=f'add-generic-password -s {SERVICE} -a {ACCOUNT} -l "Arsène (clé de la table)" -w {k.hex()}\n')
    if r.returncode != 0 or _read_key() != k:
        raise RuntimeError("impossible d'enregistrer la clé de la table dans le trousseau")
    return k


def key(create):
    """The table key; created only when asked (no encrypted file exists yet)."""
    global _key
    if _key is None:
        _key = _read_key()
        if _key is None and create:
            _key = _new_key()
    if _key is None:
        raise KeyMissing("clé de la table introuvable dans le trousseau : rien n'est lu. "
                         "Restaurer la table avec pseudo.py import et sa phrase.")
    return _key


def load(path, kind):
    """The JSON stored at path (encrypted), or None. A plain file from before is read once
    and replaced by its encrypted form."""
    path = pathlib.Path(path)
    if path.exists():
        data = path.read_bytes()
        if not data.startswith(MAGIC):
            raise ValueError(f"{path.name} : fichier inconnu")
        return json.loads(AESGCM(key(False)).decrypt(data[4:16], data[16:], kind.encode()))
    plain = path.with_suffix(".json")
    if plain.exists():
        value = json.loads(plain.read_text())
        save(path, kind, value)
        if load(path, kind) != value:                   # never shred the only readable copy
            raise RuntimeError(f"{path.name} : chiffrement non vérifié, la table en clair est gardée")
        # the clear copy is overwritten before it goes (best effort: APFS copies on write,
        # and an older backup may still hold it)
        size = plain.stat().st_size
        with open(plain, "r+b") as f:
            f.write(secrets.token_bytes(size))
            f.flush(); os.fsync(f.fileno())
        plain.unlink()
        return value
    return None


def save(path, kind, value):
    """Atomic: a crash never leaves half a table."""
    path = pathlib.Path(path)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key(True)).encrypt(nonce, json.dumps(value, ensure_ascii=False).encode(), kind.encode())
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(MAGIC + nonce + ct)
    os.replace(tmp, path)
