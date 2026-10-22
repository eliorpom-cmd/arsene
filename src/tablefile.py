"""The table as a file that can travel: encrypted with a passphrase, so a colleague who
gets the pseudonymized document by one channel and the table by another reads the real
names back, and nobody holding only one of the two does.

Format (JSON): {"format": "pseudotable", "v": 1, "kdf": {scrypt parameters, salt},
"nonce", "ct"}; AES-256-GCM, the header is authenticated with the ciphertext."""
import base64, hashlib, json, os, secrets

FORMAT, VERSION = "pseudotable", 1
SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}          # ~0.3 s and 128 MB per try
# dictated to a colleague: no crude word
CRUDE = ("merd", "putain", "connar", "conass", "salop", "bordel", "encul", "chier", "couill",
         "pute", "nichon", "bite", "branl", "niquer", "pisse")
WORDS = 6                                         # ~13 bits per word: ~78 bits


def _wordlist():
    from wordfreq import top_n_list, zipf_frequency
    # plain French words, easy to dictate on the phone: no accent, no English word or
    # name ("before", "davies"), which the list mixes in from the web
    english = set(top_n_list("en", 50000))
    return sorted({w for w in top_n_list("fr", 30000)
                   if w.isascii() and w.isalpha() and 5 <= len(w) <= 8 and w not in english
                   and not any(c in w for c in CRUDE)
                   and zipf_frequency(w, "fr") >= 3.0})


def new_phrase():
    words = _wordlist()
    return "-".join(secrets.choice(words) for _ in range(WORDS))


def _key(phrase, salt, n, r, p):
    phrase = " ".join(phrase.lower().replace("-", " ").split())   # typed with spaces or dashes
    return hashlib.scrypt(phrase.encode(), salt=salt, n=n, r=r, p=p, maxmem=2 ** 28, dklen=32)


def seal(table, phrase):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    header = {"format": FORMAT, "v": VERSION, "kdf": {"name": "scrypt", **SCRYPT, "salt": base64.b64encode(salt).decode()}}
    aad = json.dumps(header, sort_keys=True).encode()
    ct = AESGCM(_key(phrase, salt, **SCRYPT)).encrypt(nonce, json.dumps(table, ensure_ascii=False).encode(), aad)
    return json.dumps({**header, "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}).encode()


def unseal(data, phrase):
    """The table, or ValueError if the file is not a table or the phrase is wrong."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    try:
        d = json.loads(data)
        header = {k: d[k] for k in ("format", "v", "kdf")}
        assert d["format"] == FORMAT and d["v"] == VERSION and d["kdf"]["name"] == "scrypt"
        kdf = d["kdf"]
        # bounded: a crafted file must not make scrypt eat the machine
        assert kdf["n"] <= 2 ** 20 and kdf["r"] <= 16 and kdf["p"] <= 4
        key = _key(phrase, base64.b64decode(kdf["salt"]), kdf["n"], kdf["r"], kdf["p"])
        aad = json.dumps(header, sort_keys=True).encode()
        return json.loads(AESGCM(key).decrypt(base64.b64decode(d["nonce"]), base64.b64decode(d["ct"]), aad))
    except InvalidTag:
        raise ValueError("phrase de passe incorrecte (ou fichier modifié)")
    except (KeyError, AssertionError, json.JSONDecodeError, TypeError, ValueError):
        raise ValueError("ce fichier n'est pas une table exportée")
