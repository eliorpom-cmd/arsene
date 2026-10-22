"""One test file per format a lawyer or a client sends, every value invented.

Usage: python tests/formatsgen.py <folder>   (or: from formatsgen import build; build(folder))
Writes the files and returns {file name: what its text must contain}. The old binary formats
(.doc, .ppt, .xls) and .odt/.odp/.ods/.rtf/.pdf come out of LibreOffice (soffice, any system):
real files from a real office suite, not ones written to suit our readers. LibreOffice cannot
write Outlook's .msg: that one is assembled here (_cfb, a minimal compound file writer, and
the properties of [MS-OXMSG]), so it only proves our reader follows the specification.
Test-only libraries: python-docx, python-pptx, openpyxl, reportlab, cryptography."""
import datetime, email.message, io, pathlib, shutil, struct, subprocess, sys, tempfile, zipfile

NAME, OTHER = "Julien Moreau", "Claire Dubois"
PHONE, MAIL = "06 12 34 56 78", "julien.moreau@gmail.com"
SOFFICE = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"
_TMP = tempfile.mkdtemp(prefix="soffice-")                  # LibreOffice's throwaway profile, left to the OS
TEXT = (f"Contrat de travail\n\nEntre la société Batiloire et M. {NAME}, né le 14 août 1985, "
        f"domicilié 14 rue des Lilas à Lyon.\nTéléphone : {PHONE}. Courriel : {MAIL}.\n"
        f"Témoin : Mme {OTHER}.\n")


def _soffice(src, ext, out):
    """Convert with LibreOffice into out/<stem>.<ext> (filters named for the binary formats)."""
    filt = {"doc": "doc:MS Word 97", "ppt": "ppt:MS PowerPoint 97", "xls": "xls:MS Excel 97"}.get(ext, ext)
    tmp = pathlib.Path(_TMP)
    subprocess.run([SOFFICE, "--headless", f"-env:UserInstallation=file://{tmp}/profile", "--convert-to", filt,
                    "--outdir", str(tmp), str(src)], capture_output=True, timeout=180, check=True)
    made = tmp / f"{src.stem}.{ext}"
    if not made.exists():
        raise RuntimeError(f"soffice n'a pas produit {made.name}")
    return made


def word(path):
    import docx
    d = docx.Document()
    d.add_heading("Contrat de travail", 1)
    p = d.add_paragraph("Entre la société Batiloire et M. ")
    p.add_run(NAME).bold = True
    p.add_run(", né le 14 août 1985, domicilié 14 rue des Lilas à Lyon.")
    d.add_paragraph(f"Téléphone : {PHONE}. Courriel : {MAIL}.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Nom", "Qualité"
    t.cell(1, 0).text, t.cell(1, 1).text = OTHER, "Témoin"
    d.sections[0].header.paragraphs[0].text = f"Dossier {NAME}"
    d.save(path)


def slides(path):
    from pptx import Presentation
    pr = Presentation()
    s = pr.slides.add_slide(pr.slide_layouts[1])
    s.shapes.title.text = f"Affaire {NAME}"
    s.placeholders[1].text = f"Témoin : {OTHER}\nTéléphone : {PHONE}"
    s.notes_slide.notes_text_frame.text = f"Rappeler {MAIL}"
    pr.save(path)


def sheet(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Clients"
    ws.append(["Nom", "Date de naissance", "Téléphone"])
    ws.append([NAME, datetime.date(1985, 8, 14), PHONE])
    ws.append([OTHER, datetime.date(1978, 3, 3), "07 98 76 54 32"])
    ws["B2"].number_format = ws["B3"].number_format = "DD/MM/YYYY"
    wb.save(path)


def scanned_pdf(path):
    """A page with a picture and no text layer, like a scanner's output."""
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    for y in range(700, 400, -12):                           # strokes standing for printed lines
        c.rect(72, y, 400, 4, fill=1, stroke=0)
    c.showPage(); c.save()


def mixed_pdf(path):
    """Page 1 with text, page 2 scanned: what a client sends after signing page 2."""
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    c.drawString(72, 750, f"Attestation de M. {NAME}, téléphone {PHONE}.")
    c.showPage()
    c.rect(72, 600, 400, 4, fill=1, stroke=0)
    c.showPage(); c.save()


def locked_pdf(src, path):
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(src))
    w.encrypt("secret-de-test", algorithm="AES-256")
    w.write(path)


def eml(path, attach):
    m = email.message.EmailMessage()
    m["From"] = f"{NAME} <{MAIL}>"
    m["To"] = "Maître Durand <cabinet.durand@example.fr>"
    m["Subject"] = f"Mon licenciement ({NAME})"
    m["Date"] = "Tue, 29 Sep 2026 10:12:00 +0200"
    m.set_content(f"Bonjour Maître,\nJe vous joins mon contrat. Mon témoin est {OTHER}.\n{NAME}\n{PHONE}")
    m.add_attachment(attach.read_bytes(), maintype="application",
                     subtype="vnd.openxmlformats-officedocument.wordprocessingml.document", filename=attach.name)
    path.write_bytes(bytes(m))


# ---------- a minimal compound file (OLE / CFB v3) writer, for the .msg ----------
def _cfb(tree):
    """tree: {name: bytes | {name: ...}} -> bytes. Streams under 4096 bytes go to the mini
    stream, as the format requires; siblings chained by their right link, in the order of
    the specification (length, then upper case)."""
    SECT, MINI, FREE, END, FATS, NOS = 512, 64, 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
    entries = [{"name": "Root Entry", "type": 5, "kids": [], "data": b""}]

    def add(parent, items):
        for name, v in items.items():
            e = {"name": name, "type": 1 if isinstance(v, dict) else 2, "kids": [], "data": b"" if isinstance(v, dict) else v}
            entries.append(e); parent["kids"].append(len(entries) - 1)
            if isinstance(v, dict):
                add(e, v)
    add(entries[0], tree)
    mini, big = bytearray(), []                              # big: (entry, bytes)
    minifat = []
    for e in entries[1:]:
        if e["type"] != 2:
            continue
        if len(e["data"]) < 4096:
            n = max(1, -(-len(e["data"]) // MINI))
            e["start"] = len(minifat) if e["data"] else END
            if e["data"]:
                minifat += list(range(len(minifat) + 1, len(minifat) + n)) + [END]
                mini += e["data"] + b"\x00" * (n * MINI - len(e["data"]))
        else:
            big.append(e)
    ndir = -(-len(entries) * 128 // SECT)
    nminifat = -(-len(minifat) * 4 // SECT)
    nmini = -(-len(mini) // SECT)
    nbig = sum(-(-len(e["data"]) // SECT) for e in big)
    nfat = 1
    while nfat * 128 < nfat + ndir + nminifat + nmini + nbig:
        nfat += 1
    fat, chains = [FATS] * nfat, []

    def chain(n):
        start = len(fat)
        fat.extend(list(range(start + 1, start + n)) + [END])
        return start
    dir_start = chain(ndir)
    minifat_start = chain(nminifat) if nminifat else END
    mini_start = chain(nmini) if nmini else END
    for e in big:
        e["start"] = chain(-(-len(e["data"]) // SECT))
    fat += [FREE] * (nfat * 128 - len(fat))
    entries[0]["start"], entries[0]["data"] = mini_start, bytes(mini)

    def key(i):
        n = entries[i]["name"]
        return (len(n), n.upper())
    left = {}
    for e in entries:
        kids = sorted(e["kids"], key=key)
        e["child"] = kids[0] if kids else NOS
        for a, b in zip(kids, kids[1:]):
            left[a] = b
    d = bytearray()
    for i, e in enumerate(entries):
        nm = e["name"].encode("utf-16-le") + b"\x00\x00"
        d += nm.ljust(64, b"\x00") + struct.pack("<HBB", len(nm), e["type"], 1)
        d += struct.pack("<III", NOS, left.get(i, NOS), e["child"]) + b"\x00" * 16 + struct.pack("<I", 0) + b"\x00" * 16
        d += struct.pack("<IQ", e.get("start", END) if (e["data"] or e["type"] == 5) else END, len(e["data"]))
    d += b"\x00" * (ndir * SECT - len(d))
    for k in range(len(entries), ndir * 4):                  # unused entries
        o = k * 128
        d[o + 68:o + 80] = struct.pack("<III", NOS, NOS, NOS)
    mf = b"".join(struct.pack("<I", x) for x in minifat).ljust(nminifat * SECT, b"\xff")
    head = bytearray(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16)
    head += struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\x00" * 6
    head += struct.pack("<IIIIIIIII", 0, nfat, dir_start, 0, 4096, minifat_start, nminifat, END, 0)
    difat = list(range(nfat)) + [FREE] * (109 - nfat)
    head += b"".join(struct.pack("<I", x) for x in difat)
    body = b"".join(struct.pack("<I", x) for x in fat) + bytes(d) + mf + bytes(mini).ljust(nmini * SECT, b"\x00")
    for e in big:
        body += e["data"].ljust(-(-len(e["data"]) // SECT) * SECT, b"\x00")
    return bytes(head) + body


def msg(path, attach):
    u = lambda s: s.encode("utf-16-le")                      # PT_UNICODE, no terminator
    when = int((datetime.datetime(2026, 9, 29, 8, 12) - datetime.datetime(1601, 1, 1)).total_seconds() * 1e7)
    props = b"\x00" * 8 + struct.pack("<IIII", 1, 1, 1, 1) + b"\x00" * 8
    props += struct.pack("<IIQ", 0x00390040, 6, when)         # PR_CLIENT_SUBMIT_TIME
    tree = {
        "__substg1.0_0037001F": u(f"Mon licenciement ({NAME})"),
        "__substg1.0_0C1A001F": u(NAME),
        "__substg1.0_5D01001F": u(MAIL),
        "__substg1.0_1000001F": u(f"Bonjour Maître,\r\nMon témoin est {OTHER}.\r\n{NAME}\r\n{PHONE}"),
        "__properties_version1.0": props,
        "__recip_version1.0_#00000000": {
            "__substg1.0_3001001F": u("Maître Durand"), "__substg1.0_39FE001F": u("cabinet.durand@example.fr"),
            "__properties_version1.0": b"\x00" * 8},
        "__attach_version1.0_#00000000": {
            "__substg1.0_3707001F": u(attach.name), "__substg1.0_37010102": attach.read_bytes(),
            "__properties_version1.0": b"\x00" * 8},
    }
    path.write_bytes(_cfb(tree))


def p7m(src, path):
    """The document signed in a CMS envelope, content inside (CAdES-style attached signature)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import pkcs7
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())            # throwaway, for this test file only
    who = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Signataire de test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(who).issuer_name(who).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    out = (pkcs7.PKCS7SignatureBuilder().set_data(src.read_bytes()).add_signer(cert, key, hashes.SHA256())
           .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))
    path.write_bytes(out)


def build(out):
    """Every test file in out; {file: [strings its text must hold]} (None: must be refused, with why)."""
    out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    must = [NAME, OTHER]
    exp = {}
    word(out / "Contrat Moreau.docx"); exp["Contrat Moreau.docx"] = must + [PHONE, MAIL]
    for ext in ("doc", "odt", "rtf", "pdf", "html"):
        shutil.move(_soffice(out / "Contrat Moreau.docx", ext, out), out / f"Contrat Moreau.{ext}")
        exp[f"Contrat Moreau.{ext}"] = must + [PHONE]
    slides(out / "Plaidoirie Moreau.pptx"); exp["Plaidoirie Moreau.pptx"] = must + [PHONE, MAIL]
    for ext in ("ppt", "odp"):
        shutil.move(_soffice(out / "Plaidoirie Moreau.pptx", ext, out), out / f"Plaidoirie Moreau.{ext}")
        exp[f"Plaidoirie Moreau.{ext}"] = must + [PHONE]
    sheet(out / "Clients.xlsx"); exp["Clients.xlsx"] = must + ["14/08/1985", PHONE]
    for ext in ("xls", "ods"):
        shutil.move(_soffice(out / "Clients.xlsx", ext, out), out / f"Clients.{ext}")
        exp[f"Clients.{ext}"] = must + ["14/08/1985"]
    (out / "Export Moreau.csv").write_bytes(f"Nom;Téléphone\n{NAME};{PHONE}\n{OTHER};07 98 76 54 32\n".encode("cp1252"))
    exp["Export Moreau.csv"] = must + ["Téléphone"]
    (out / "Notes Moreau.txt").write_text(TEXT); exp["Notes Moreau.txt"] = must
    eml(out / "Message Moreau.eml", out / "Contrat Moreau.docx"); exp["Message Moreau.eml"] = must + [MAIL, "Pièce jointe : Contrat Moreau.docx"]
    msg(out / "Message Moreau.msg", out / "Contrat Moreau.pdf"); exp["Message Moreau.msg"] = must + [MAIL, "Pièce jointe : Contrat Moreau.pdf"]
    p7m(out / "Contrat Moreau.pdf", out / "Contrat Moreau.pdf.p7m"); exp["Contrat Moreau.pdf.p7m"] = must
    # a renamed file: the content decides, not the extension
    shutil.copy(out / "Contrat Moreau.docx", out / "Contrat renommé.doc"); exp["Contrat renommé.doc"] = must
    mixed_pdf(out / "Attestation mixte.pdf"); exp["Attestation mixte.pdf"] = [NAME]
    # refused, each with its reason
    scanned_pdf(out / "Scan Moreau.pdf"); exp["Scan Moreau.pdf"] = ("scan",)
    locked_pdf(out / "Contrat Moreau.pdf", out / "Verrouillé.pdf"); exp["Verrouillé.pdf"] = ("encrypted",)
    (out / "Photo.png").write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452") + b"\x00" * 32); exp["Photo.png"] = ("image",)
    with zipfile.ZipFile(out / "Pièces.zip", "w") as z:
        z.writestr("a.txt", TEXT)
    exp["Pièces.zip"] = ("archive",)
    (out / "Note.pages").write_bytes(b"PK\x03\x04" + b"\x00" * 26); exp["Note.pages"] = ("format",)
    return exp


if __name__ == "__main__":
    for k, v in build(sys.argv[1] if len(sys.argv) > 1 else "formats-test").items():
        print(k, "->", v)
