"""The text of a document, whatever its format, on any system (Mac, Linux, Windows).

What lawyers and their clients actually send (see docs/formats.md): Word (.docx, old .doc),
PDF (text or scanned), emails saved from Outlook (.msg) or another client (.eml), signed
documents (.p7m), spreadsheets (.xlsx, .xls, .ods), slides (.pptx, .ppt, .odp), LibreOffice
(.odt), RTF, HTML, plain text and CSV exports from practice software (often in Windows-1252).

    read(name, raw) -> (text, why)   why = None when read, else the reason it was not:
        "scan"       a PDF with no text layer (a scan, a photo turned into a PDF)
        "encrypted"  a PDF, Word or Excel file protected by a password
        "image"      a picture: there is no text to read without OCR
        "archive"    a .zip / .rar / .7z: open it and attach the documents themselves
        "signature"  a detached signature (.p7s) with no document inside
        "format"     a format Arsène does not read (Pages, Keynote, WordPerfect, a binary)
        "empty"      a document with no text at all
    notes                                -> the last read's remarks (pages with no text...)

Only permissive libraries: lxml (BSD) for
the XML formats, pypdf (BSD) for PDF, olefile (BSD) for the old Office binaries and Outlook,
xlrd (BSD) for .xls, striprtf (BSD) for RTF. The old .doc and .ppt binaries, .msg and .p7m
are read by code written here from Microsoft's published specifications ([MS-DOC], [MS-PPT],
[MS-OXMSG]) and RFC 5652: the libraries that do it (antiword, extract-msg...) are GPL.
textutil (macOS) stays a fallback for what is not read here (.webarchive, .rtfd, odd RTF).

Every reader fails closed: an exception gives ("", "format"), never a partial text that
would look complete.
"""
import email, email.policy, io, pathlib, re, struct, subprocess, zipfile
from html.parser import HTMLParser

IMAGES = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}
ARCHIVES = {".zip", ".rar", ".7z", ".gz", ".tar", ".tgz"}
NOT_READ = {".pages", ".numbers", ".key", ".wpd", ".wps", ".xps", ".oxps", ".mdb", ".accdb", ".exe", ".dmg"}
TEXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".log", ".vcf", ".ics"}
OOXML = {".docx": "word", ".docm": "word", ".dotx": "word", ".dotm": "word",
         ".xlsx": "xl", ".xlsm": "xl", ".xltx": "xl", ".pptx": "ppt", ".pptm": "ppt", ".ppsx": "ppt", ".potx": "ppt"}
ODF = {".odt", ".ods", ".odp", ".ott", ".ots", ".otp", ".odg"}
TMP = pathlib.Path.home() / "tmp"         # set by the engine to its own private folder
# formats whose protected copy is plain text (.txt): everything read here but plain text itself
DOCUMENTS = set(OOXML) | ODF | {".pdf", ".doc", ".dot", ".xls", ".xlt", ".ppt", ".pps", ".pot", ".msg", ".eml", ".mht",
                                ".mhtml", ".rtf", ".html", ".htm", ".xhtml", ".webarchive", ".rtfd", ".wordml", ".p7m", ".p7c"}
DEPTH = 3                                 # an email in an email in a signed file, not deeper
notes = []


def _xml(data):
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    return etree.fromstring(data, parser)


def _local(el):
    t = el.tag
    return t.rsplit("}", 1)[-1] if isinstance(t, str) else ""


# ---------- sniffing: a file renamed, or saved without its extension ----------
def sniff(raw):
    """The extension a file really has, from its first bytes ("" when unknown)."""
    if raw[:5] == b"%PDF-":
        return ".pdf"
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":       # OLE: old Office, Outlook
        try:
            import olefile
            ole = olefile.OleFileIO(io.BytesIO(raw))
            for stream, ext in (("WordDocument", ".doc"), ("Workbook", ".xls"), ("Book", ".xls"),
                                ("PowerPoint Document", ".ppt"), ("__properties_version1.0", ".msg")):
                if ole.exists(stream):
                    return ext
            if ole.exists("EncryptionInfo"):                 # an encrypted .docx/.xlsx
                return ".encrypted"
        except Exception:
            pass
        return ""
    if raw[:2] == b"PK":
        try:
            names = set(zipfile.ZipFile(io.BytesIO(raw)).namelist())
        except zipfile.BadZipFile:
            return ""
        if "word/document.xml" in names:
            return ".docx"
        if "xl/workbook.xml" in names:
            return ".xlsx"
        if "ppt/presentation.xml" in names:
            return ".pptx"
        if "content.xml" in names:
            return ".odt"
        return ".zip"
    if raw[:4] in (b"\x89PNG", b"GIF8") or raw[:3] == b"\xff\xd8\xff" or raw[:4] in (b"II*\x00", b"MM\x00*"):
        return ".png"
    if raw[:5] == b"{\\rtf":
        return ".rtf"
    return ""


# ---------- plain text: UTF-8, UTF-16, or Windows-1252 from older practice software ----------
def plain(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if b"\x00" in raw[:4096]:
        raise ValueError("binary")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


# ---------- HTML ----------
class _Html(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
             "article", "header", "footer", "blockquote", "pre", "dt", "dd", "hr", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("td", "th"):
            self.out.append("\t")
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a":                                       # a mailto: link holds an address
            href = dict(attrs).get("href") or ""
            if href.lower().startswith("mailto:"):
                self.out.append(f" {href[7:]} ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"[ \t\r\n]+", " ", data))


def html(raw_or_text):
    s = raw_or_text if isinstance(raw_or_text, str) else plain(raw_or_text)
    p = _Html(); p.feed(s); p.close()
    lines = [re.sub(r" *\t *", "\t", x).strip(" ") for x in "".join(p.out).split("\n")]
    return _squeeze("\n".join(x.lstrip("\t") for x in lines))


def _squeeze(s):
    return re.sub(r"\n{3,}", "\n\n", s).strip("\n")


# ---------- PDF ----------
def pdf(raw):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(raw))
    if r.is_encrypted:
        try:
            if not r.decrypt(""):                            # often only an owner password
                return "", "encrypted"
        except Exception:
            return "", "encrypted"
    pages = [(p.extract_text() or "").strip() for p in r.pages]
    blank = [i + 1 for i, t in enumerate(pages) if len(re.sub(r"\W", "", t)) < 3]
    if len(blank) == len(pages):
        return "", "scan"
    if blank:
        notes.append(("pages-sans-texte", blank))
    return "\n\n".join(t for t in pages if t), None


# ---------- Office Open XML (.docx is docxfile.py; here spreadsheets and slides) ----------
def _paras(root, para="p", run_text="t", cell=None, row=None):
    """Lines of an XML tree: one per paragraph, table rows as cells joined by tabs."""
    out = []

    def text_of(el):
        return "".join(t.text or "" for t in el.iter() if _local(t) == run_text)

    def walk(el):
        for c in el:
            name = _local(c)
            if row and name == row:
                out.append("\t".join(" ".join(text_of(p) for p in x.iter() if _local(p) == para).strip()
                                     for x in c if _local(x) == cell))
            elif name == para:
                out.append(text_of(c))
            else:
                walk(c)
    walk(root)
    return out


def _num(name):
    m = re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def pptx(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    out = []
    for kind, label in (("slides/slide", "Diapositive"), ("notesSlides/notesSlide", "Notes de la diapositive")):
        for n in sorted((x for x in names if re.fullmatch(rf"ppt/{kind}\d+\.xml", x)), key=_num):
            lines = [x for x in _paras(_xml(z.read(n)), "p", "t", "tc", "tr") if x.strip()]
            if kind.startswith("notes"):
                lines = [x for x in lines if not re.fullmatch(r"\d+", x.strip())]   # the slide number
            if lines:
                out.append(f"{label} {_num(n)}\n" + "\n".join(lines))
    for n in sorted(x for x in names if x.startswith("ppt/comments/") and x.endswith(".xml")):
        lines = ["".join(t.text or "" for t in c.iter() if _local(t) in ("text", "t")) for c in _xml(z.read(n))]
        out += [f"Commentaire : {x}" for x in lines if x.strip()]
    if "ppt/commentAuthors.xml" in names:
        out += [f"Auteur d'un commentaire : {a.get('name')}" for a in _xml(z.read("ppt/commentAuthors.xml")) if a.get("name")]
    out += _ooxml_props(z, names)
    return "\n\n".join(out), None


def _ooxml_props(z, names):
    out = []
    for n, keys in (("docProps/core.xml", {"title", "subject", "creator", "lastModifiedBy", "description", "keywords"}),
                    ("docProps/app.xml", {"Company", "Manager"})):
        if n in names:
            out += [f"{e.text.strip()}" for e in _xml(z.read(n)).iter() if _local(e) in keys and (e.text or "").strip()]
    return ["Propriétés du fichier : " + " ; ".join(out)] if out else []


# Excel stores dates as day counts; a date of birth must read as a date to be detected
_DATE_IDS = set(range(14, 23)) | {45, 46, 47}


def _serial_date(v, epoch1904=False):
    import datetime
    try:
        d = float(v)
    except ValueError:
        return v
    base = datetime.datetime(1904, 1, 1) if epoch1904 else datetime.datetime(1899, 12, 30)
    t = base + datetime.timedelta(days=d)
    return t.strftime("%d/%m/%Y") if t.hour == t.minute == 0 else t.strftime("%d/%m/%Y %H:%M")


def xlsx(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    shared = []
    if "xl/sharedStrings.xml" in names:
        shared = ["".join(t.text or "" for t in si.iter() if _local(t) == "t") for si in _xml(z.read("xl/sharedStrings.xml"))]
    dates = set()
    if "xl/styles.xml" in names:
        st = _xml(z.read("xl/styles.xml"))
        custom = {f.get("numFmtId"): f.get("formatCode") or "" for f in st.iter() if _local(f) == "numFmt"}
        xfs = next((x for x in st if _local(x) == "cellXfs"), [])
        for i, xf in enumerate(x for x in xfs if _local(x) == "xf"):
            fid = xf.get("numFmtId") or "0"
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", custom.get(fid, ""))
            if int(fid) in _DATE_IDS or (fid in custom and re.search(r"[dy]", code, re.I)):
                dates.add(str(i))
    wb = _xml(z.read("xl/workbook.xml"))
    e1904 = any(_local(e) == "workbookPr" and e.get("date1904") in ("1", "true") for e in wb.iter())
    rels = {}
    if "xl/_rels/workbook.xml.rels" in names:
        rels = {r.get("Id"): r.get("Target") for r in _xml(z.read("xl/_rels/workbook.xml.rels"))}
    out = []
    for s in (e for e in wb.iter() if _local(e) == "sheet"):
        rid = next((v for k, v in s.attrib.items() if k.endswith("}id")), None)
        target = rels.get(rid, "")
        part = target.lstrip("/") if target.startswith("/") else "xl/" + target
        if part not in names:
            continue
        rows = []
        for r in (e for e in _xml(z.read(part)).iter() if _local(e) == "row"):
            cells = []
            for c in r:
                if _local(c) != "c":
                    continue
                t, v = c.get("t"), next((x.text or "" for x in c if _local(x) == "v"), "")
                if t == "s":
                    v = shared[int(v)] if v.isdigit() and int(v) < len(shared) else ""
                elif t == "inlineStr":
                    v = "".join(x.text or "" for x in c.iter() if _local(x) == "t")
                elif c.get("s") in dates and v:
                    v = _serial_date(v, e1904)
                col = re.match(r"[A-Z]+", c.get("r") or "")
                if col:                                      # empty cells keep the columns aligned
                    i = 0
                    for ch in col.group(0):
                        i = i * 26 + ord(ch) - 64
                    cells += [""] * (i - 1 - len(cells))
                cells.append(v.replace("\n", " ").replace("\t", " "))
            if any(x.strip() for x in cells):
                rows.append("\t".join(cells).rstrip("\t"))
        if rows:
            out.append(f"Feuille « {s.get('name')} »\n" + "\n".join(rows))
    for n in sorted(x for x in names if re.fullmatch(r"xl/comments\d*\.xml", x)):
        out += ["Commentaire : " + "".join(t.text or "" for t in c.iter() if _local(t) == "t")
                for c in _xml(z.read(n)).iter() if _local(c) == "comment"]
    out += _ooxml_props(z, names)
    return "\n\n".join(out), None


def xls(raw):
    import xlrd
    try:
        b = xlrd.open_workbook(file_contents=raw)
    except xlrd.biffh.XLRDError as e:
        return ("", "encrypted") if "encrypt" in str(e).lower() else ("", "format")
    out = []
    for sh in b.sheets():
        rows = []
        for i in range(sh.nrows):
            cells = []
            for c in sh.row(i):
                if c.ctype == xlrd.XL_CELL_DATE:
                    cells.append(_serial_date(c.value, b.datemode == 1))
                elif c.ctype == xlrd.XL_CELL_NUMBER:
                    cells.append(str(int(c.value)) if c.value == int(c.value) else str(c.value))
                else:
                    cells.append(str(c.value).replace("\n", " ").replace("\t", " "))
            if any(x.strip() for x in cells):
                rows.append("\t".join(cells).rstrip("\t"))
        if rows:
            out.append(f"Feuille « {sh.name} »\n" + "\n".join(rows))
    return "\n\n".join(out), None


# ---------- OpenDocument (LibreOffice) ----------
def odf(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    out = []

    def text_of(el):
        parts = [el.text or ""]
        for c in el:
            n = _local(c)
            if n == "s":
                parts.append(" " * int(c.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}c") or 1))
            elif n == "tab":
                parts.append("\t")
            elif n == "line-break":
                parts.append(" ")
            elif n not in ("note-citation",):
                parts.append(text_of(c))
            parts.append(c.tail or "")
        return "".join(parts)

    def walk(el, lines):
        for c in el:
            n = _local(c)
            if n == "table-row":
                cells = []
                for cell in c:
                    if _local(cell) not in ("table-cell", "covered-table-cell"):
                        continue
                    v = " ".join(text_of(p) for p in cell.iter() if _local(p) in ("p", "h")).strip()
                    rep = int(cell.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated") or 1)
                    cells += [v] * (min(rep, 64) if v else 1)     # 16 000 empty columns: one is enough
                line = "\t".join(cells).rstrip("\t")
                if line.strip():
                    lines.append(line)
            elif n in ("p", "h"):
                lines.append(text_of(c))
                for note in (x for x in c.iter() if _local(x) == "note-body"):
                    walk(note, lines)
            elif n == "page":                                # slides (.odp)
                lines.append(f"Diapositive « {c.get('{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}name')} »")
                walk(c, lines)
            elif n == "table" and c.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name") and _local(el) == "spreadsheet":
                lines.append(f"Feuille « {c.get('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name')} »")
                walk(c, lines)
            else:
                walk(c, lines)

    if "content.xml" in names:
        lines = []
        walk(_xml(z.read("content.xml")), lines)
        out += [x for x in lines if x.strip()]
    if "styles.xml" in names:                                # headers and footers of the pages
        lines = []
        for el in _xml(z.read("styles.xml")).iter():
            if _local(el) in ("header", "footer", "header-left", "footer-left", "header-first", "footer-first"):
                walk(el, lines)
        # a spreadsheet's header holds fields only ("???", "Page 1"): kept out
        out += [x for x in lines if x.strip() and not re.fullmatch(r"[\W\d]*(Page|Feuille)?[\W\d]*", x.strip())]
    if "meta.xml" in names:
        meta = [e.text.strip() for e in _xml(z.read("meta.xml")).iter()
                if _local(e) in ("creator", "initial-creator", "title", "subject", "description", "keyword") and (e.text or "").strip()]
        if meta:
            out.append("Propriétés du fichier : " + " ; ".join(meta))
    return "\n".join(out), None


# ---------- RTF ----------
def rtf(raw):
    from striprtf.striprtf import rtf_to_text
    s = raw.decode("latin-1")                                # RTF is 7-bit; \'e9 escapes decoded below
    out = rtf_to_text(s, encoding="cp1252", errors="replace")
    # striprtf ends each table cell with "|": a row "a|b|" becomes "a<tab>b"
    out = "\n".join("\t".join(x[:-1].split("|")) if x.endswith("|") else x for x in out.split("\n"))
    return _squeeze(out), None


# ---------- old Word (.doc, Word 97 to 2003) [MS-DOC] ----------
_FIELD_ONLY = re.compile(r"^\s*(PAGE|NUMPAGES|SECTIONPAGES|DATE|TIME|TOC|SEQ|STYLEREF|REF|PAGEREF|FILENAME|SAVEDATE|PRINTDATE|CREATEDATE)\b", re.I)


def doc(raw):
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(raw))
    wd = ole.openstream("WordDocument").read()
    ident, flags = struct.unpack_from("<H", wd, 0)[0], struct.unpack_from("<H", wd, 0x0A)[0]
    if ident != 0xA5EC:
        return "", "format"                                  # Word 6/95 and older
    if flags & 0x0100:
        return "", "encrypted"
    table = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
    csw = struct.unpack_from("<H", wd, 32)[0]
    p = 34 + csw * 2
    cslw = struct.unpack_from("<H", wd, p)[0]
    p += 2 + cslw * 4 + 2                                    # fibRgLw, then cbRgFcLcb
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, p + 33 * 8)   # 34th pair: fcClx / lcbClx
    clx = table[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:                   # Prc: formatting, skipped
        i += 3 + struct.unpack_from("<h", clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 0x02:
        return "", "format"
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    parts = []
    for k in range(n):
        fc = struct.unpack_from("<I", plc, (n + 1) * 4 + k * 8 + 2)[0]
        count = cps[k + 1] - cps[k]
        if fc & 0x40000000:                                  # compressed: one byte per character
            start = (fc & 0x3FFFFFFF) // 2
            parts.append(wd[start:start + count].decode("cp1252", errors="replace"))
        else:
            parts.append(wd[fc:fc + 2 * count].decode("utf-16-le", errors="replace"))
    s = "".join(parts)
    # fields: \x13 code \x14 result \x15 ; the code of a page number is noise, the code of a
    # link may hold an address, so only the noise goes
    s = re.sub(r"\x13([^\x13\x14\x15]*)\x14", lambda m: "" if _FIELD_ONLY.match(m.group(1)) else f"{m.group(1)} ", s)
    s = re.sub(r"\x13([^\x13\x14\x15]*)\x15", lambda m: "" if _FIELD_ONLY.match(m.group(1)) else m.group(1), s)
    # a cell ends with \x07, a table row with one more: "a\x07b\x07\x07" is the row "a<tab>b"
    s = s.replace("\x07\x07", "\n").replace("\x07\r", "\n").replace("\x07", "\t").replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    s = re.sub(r"[\x00-\x08\x0e-\x1f\x13\x14\x15]", "", s)
    s = re.sub(r"\t\n", "\n", s)
    return _squeeze(s), None


# ---------- old PowerPoint (.ppt) [MS-PPT] ----------
def ppt(raw):
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(raw))
    if ole.exists("EncryptedSummary"):
        return "", "encrypted"
    s = ole.openstream("PowerPoint Document").read()
    out = []

    def walk(a, b, master):
        while a + 8 <= b:
            ver_inst, typ, ln = struct.unpack_from("<HHI", s, a)
            body = a + 8
            end = min(body + ln, b)
            if ver_inst & 0x000F == 0x000F:                  # a container
                walk(body, end, master or typ == 0x03F8)     # MainMaster: "Click to edit" texts
            elif not master and typ == 0x0FA0:               # TextCharsAtom (UTF-16)
                out.append(s[body:end].decode("utf-16-le", errors="replace"))
            elif not master and typ == 0x0FA8:               # TextBytesAtom (8-bit)
                out.append(s[body:end].decode("cp1252", errors="replace"))
            a = body + ln
    walk(0, len(s), False)
    lines, seen = [], set()
    for t in out:
        t = t.replace("\r", "\n").replace("\x0b", "\n").strip()
        if t and t not in seen and t not in ("*",):   # "*": the slide number placeholder                              # the outline and the slide repeat it
            seen.add(t); lines.append(t)
    return "\n\n".join(lines), None


# ---------- Outlook (.msg) [MS-OXMSG] ----------
def _msg_props(ole, prefix):
    """{property id: value} of one storage: strings, binaries, and the fixed-length times."""
    import datetime
    out = {}
    for entry in ole.listdir(streams=True, storages=False):
        if len(entry) != len(prefix) + 1 or entry[:len(prefix)] != prefix:
            continue
        m = re.fullmatch(r"__substg1\.0_([0-9A-F]{4})([0-9A-F]{4})", entry[-1], re.I)
        if not m:
            continue
        pid, typ = m.group(1).upper(), m.group(2).upper()
        data = ole.openstream(entry).read()
        if typ == "001F":
            out[pid] = data.decode("utf-16-le", errors="replace").rstrip("\x00")
        elif typ == "001E":
            out[pid] = data.decode("cp1252", errors="replace").rstrip("\x00")
        elif typ == "0102":
            out[pid] = data
    path = prefix + ["__properties_version1.0"]
    if ole.exists("/".join(path)):
        p = ole.openstream(path).read()
        skip = 32 if not prefix else (8 if prefix[-1].startswith(("__recip", "__attach")) else 24)
        for k in range(skip, len(p) - 15, 16):
            tag, = struct.unpack_from("<I", p, k)
            if tag & 0xFFFF == 0x0040:                       # PT_SYSTIME
                ft, = struct.unpack_from("<Q", p, k + 8)
                if ft:
                    out.setdefault(f"{tag >> 16:04X}", datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=ft // 10))
    return out


def msg(raw, depth):
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(raw))
    top = _msg_props(ole, [])
    head = []
    sender = " ".join(x for x in (top.get("0C1A"), f"<{top['5D01'] if '5D01' in top else top.get('0C1F', '')}>") if x and x != "<>")
    if sender:
        head.append(f"De : {sender}")
    storages = sorted({e[0] for e in ole.listdir(streams=True, storages=True) if e})
    recips = []
    for st in (x for x in storages if x.startswith("__recip_version1.0_")):
        r = _msg_props(ole, [st])
        name, addr = r.get("3001", ""), r.get("39FE") or r.get("3003", "")
        recips.append(f"{name} <{addr}>" if addr and addr != name else name)
    if recips:
        head.append("À : " + ", ".join(x for x in recips if x))
    else:
        for pid, label in (("0E04", "À"), ("0E03", "Cc")):
            if top.get(pid):
                head.append(f"{label} : {top[pid]}")
    when = top.get("0039") or top.get("0E06")
    if when:
        head.append("Date : " + when.strftime("%d/%m/%Y %H:%M"))
    if top.get("0037"):
        head.append(f"Objet : {top['0037']}")
    body = top.get("1000") or ""
    if not body.strip() and top.get("1013"):
        h = top["1013"]
        body = html(h.decode("utf-8", errors="replace") if isinstance(h, bytes) else h)
    if isinstance(body, bytes):
        body = plain(body)
    parts = ["\n".join(head), body.strip()]
    for st in (x for x in storages if x.startswith("__attach_version1.0_")):
        a = _msg_props(ole, [st])
        name = a.get("3707") or a.get("3704") or "pièce jointe"
        data = a.get("3701")
        parts.append(_attachment(name, data if isinstance(data, bytes) else None, depth))
    return _squeeze("\n\n".join(p for p in parts if p)), None


def _attachment(name, data, depth):
    if data is None:
        return f"Pièce jointe : {name} (non lue)"
    t, why = read(name, data, depth + 1)
    if why:
        return f"Pièce jointe : {name} (non lue : {REASONS.get(why, why)})"
    return f"Pièce jointe : {name}\n{t}"


# ---------- other email clients (.eml, RFC 5322) ----------
def eml(raw, depth):
    m = email.message_from_bytes(raw, policy=email.policy.default)
    head = []
    for k, label in (("From", "De"), ("To", "À"), ("Cc", "Cc"), ("Bcc", "Cci"), ("Reply-To", "Répondre à"), ("Date", "Date"), ("Subject", "Objet")):
        if m.get(k):
            head.append(f"{label} : {m.get(k)}")
    body = m.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = html(text)
    parts = ["\n".join(head), text.strip()]
    for a in m.iter_attachments():
        name = a.get_filename() or ("message.eml" if a.get_content_type() == "message/rfc822" else "pièce jointe")
        if a.get_content_type() == "message/rfc822":
            inner = a.get_payload(0) if a.is_multipart() else None
            data = inner.as_bytes() if inner is not None else a.get_payload(decode=True)
        else:
            data = a.get_payload(decode=True)
        parts.append(_attachment(name, data, depth))
    return _squeeze("\n\n".join(p for p in parts if p)), None


# ---------- signed files (.p7m, CAdES / CMS, RFC 5652) ----------
def _ber(b, i):
    """(tag, constructed, start of content, end of content or None when indefinite, next)."""
    tag = b[i]; i += 1
    if tag & 0x1F == 0x1F:
        while b[i] & 0x80:
            i += 1
        i += 1
    ln = b[i]; i += 1
    if ln == 0x80:
        return tag, True, i, None
    if ln & 0x80:
        k = ln & 0x7F
        ln = int.from_bytes(b[i:i + k], "big"); i += k
    return tag, bool(tag & 0x20), i, i + ln


def _children(b, start, end):
    i = start
    while (end is None or i < end) and i < len(b):
        if end is None and b[i:i + 2] == b"\x00\x00":
            return
        tag, cons, s, e = _ber(b, i)
        if e is None:                                        # indefinite: find its end by walking
            last = s
            for c in _children(b, s, None):
                last = c[4]
            e_full = last + 2
            yield tag, cons, s, None, e_full
            i = e_full
        else:
            yield tag, cons, s, e, e
            i = e


def _octets(b, tag, cons, s, e):
    if not cons:
        return b[s:e]
    return b"".join(_octets(b, *c[:4]) for c in _children(b, s, e))


def p7m(raw):
    """The signed document inside a CMS SignedData (a signature is not a document)."""
    if raw[:1] != b"\x30":                                   # base64 or PEM armour
        import base64
        body = re.sub(rb"-----[^-]+-----", b"", raw)
        raw = base64.b64decode(re.sub(rb"\s", b"", body))
    # ContentInfo { contentType, [0] SignedData { version, digestAlgorithms, encapContentInfo { type, [0] eContent } } }
    ci = next(_children(raw, 0, None))
    content = [c for c in _children(raw, ci[2], ci[3])]
    sd_wrap = next(c for c in content if c[0] == 0xA0)
    sd = next(_children(raw, sd_wrap[2], sd_wrap[3]))
    fields = list(_children(raw, sd[2], sd[3]))
    encap = fields[2]
    econt = [c for c in _children(raw, encap[2], encap[3]) if c[0] == 0xA0]
    if not econt:
        return None                                          # detached signature
    inner = next(_children(raw, econt[0][2], econt[0][3]))
    return _octets(raw, *inner[:4])


# ---------- the entry point ----------
REASONS = {"scan": "PDF scanné, sans texte", "encrypted": "protégé par un mot de passe", "image": "image",
           "archive": "archive", "signature": "signature seule, sans le document", "format": "format non lu",
           "empty": "aucun texte"}


def read(name, raw, depth=0):
    """(text, why): see the module docstring."""
    if depth == 0:
        notes.clear()
    ext = pathlib.Path(name).suffix.lower()
    real = sniff(raw)
    if real == ".encrypted":
        return "", "encrypted"
    # trust the content over the name (a .doc that is really a .docx, a PDF saved as .txt),
    # except between formats that share a container
    if real and real != ext:
        same = ((real == ".odt" and ext in ODF) or (real == ".zip" and (ext in OOXML or ext in ODF))
                or (real in OOXML and OOXML.get(ext) == OOXML[real]))
        if not same:
            ext = real
    if depth > DEPTH:
        return "", "format"
    try:
        if ext in (".p7m", ".p7c"):
            inner = p7m(raw)
            if inner is None:
                return "", "signature"
            return read(name[:-4] if name.lower().endswith((".p7m", ".p7c")) else name, inner, depth + 1)
        if ext == ".p7s":
            return "", "signature"
        if ext in IMAGES:
            return "", "image"
        if ext in ARCHIVES:
            return "", "archive"
        if ext in NOT_READ:
            return "", "format"
        if OOXML.get(ext) == "word":
            import docxfile
            text, why = docxfile.text(raw), None
        elif OOXML.get(ext) == "xl":
            text, why = xlsx(raw)
        elif OOXML.get(ext) == "ppt":
            text, why = pptx(raw)
        elif ext in ODF:
            text, why = odf(raw)
        elif ext == ".pdf":
            text, why = pdf(raw)
        elif ext in (".doc", ".dot"):
            text, why = doc(raw)
        elif ext in (".xls", ".xlt"):
            text, why = xls(raw)
        elif ext in (".ppt", ".pps", ".pot"):
            text, why = ppt(raw)
        elif ext == ".msg":
            text, why = msg(raw, depth)
        elif ext in (".eml", ".mht", ".mhtml"):
            text, why = eml(raw, depth)
        elif ext == ".rtf":
            text, why = rtf(raw)
        elif ext in (".html", ".htm", ".xhtml"):
            text, why = html(raw), None
        elif ext in (".webarchive", ".rtfd", ".wordml"):
            text, why = _textutil(ext, raw)
        else:
            text, why = plain(raw), None
    except Exception:
        return "", "format"
    if why:
        return "", why
    if not text.strip():
        return "", "empty"
    return text, None


def _textutil(ext, raw):
    """macOS only: what this module does not read itself."""
    import secrets
    TMP.mkdir(mode=0o700, parents=True, exist_ok=True)       # private folder, emptied at once
    p = TMP / (secrets.token_hex(8) + ext)
    try:
        p.write_bytes(raw)
        p.chmod(0o600)
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(p)], capture_output=True, text=True, timeout=120)
        return r.stdout, None
    except FileNotFoundError:
        return "", "format"
    finally:
        p.unlink(missing_ok=True)
