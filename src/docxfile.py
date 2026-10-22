"""A Word document (.docx) pseudonymized or restored in place, layout kept.

The text goes through the same function as any other text (pseudonymize or rehydrate,
applied once to the whole document so detection keeps its context), then each change is
written back into the runs it came from: bold, italics, styles, lists, tables, headers,
footers, footnotes, comments and text boxes stay as they were.

Why the XML directly (lxml) rather than python-docx: python-docx does not reach footnotes,
endnotes, comments, text boxes, tracked changes or field codes, and a real name left in any
of them is a leak. lxml is what python-docx is built on anyway (BSD licence); it also keeps
the namespace prefixes Word relies on (mc:Ignorable names them), which ElementTree rewrites.

How a change finds its runs: the text of a paragraph is a sequence of characters, each one
known to come from one w:t (or an attribute, or a tab that cannot change). The old and new
text are compared word by word; each replaced passage is spread over the w:t it covered, in
proportion to their lengths, so a name cut in two runs ("Du" + "pont", the second in bold)
comes out cut the same way ("Go" + "det") and restoring it gives the original cut back.

Fail closed: after writing, the document is read again and must give exactly the new text;
otherwise ValueError, and the caller sends nothing.

Images stay in the document, their metadata removed (strip_image_metadata). What is not
rewritten (listed in the report, never silently): what images show, and embedded objects
(a scanned signature, a letterhead, an Excel sheet inside the document), chart caches.
The thumbnail of the first page (docProps/thumbnail.*) is dropped: it shows the real text.

    units(data)            -> [str]  the text, one line per paragraph (table rows: cells by tabs)
    text(data)             -> str    the same lines joined, what review and indexing read
    transform(data, fn)    -> (bytes, report)   fn(str) -> str on the joined text
"""
import difflib, io, re, zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
SUFFIXES = {".docx", ".docm", ".dotx", ".dotm"}
# document metadata that holds names: title, author, company, keywords...
PROPS = {"docProps/core.xml": {"title", "subject", "creator", "keywords", "description",
                               "lastModifiedBy", "category", "contentStatus"},
         "docProps/app.xml": {"Company", "Manager", "lpstr"},
         "docProps/custom.xml": {"lpwstr", "lpstr", "bstr"}}
PEOPLE_ATTRS = {"author", "userId", "providerId"}     # comments, tracked changes, people.xml


def w(tag):
    return f"{{{W}}}{tag}"


class _Text:
    """The text of an element."""
    def __init__(self, el):
        self.el = el

    def get(self):
        return self.el.text or ""

    def set(self, value):
        self.el.text = value
        # Word trims leading and trailing spaces of a w:t without it
        if value != value.strip() and self.el.tag in (w("t"), w("delText"), w("instrText")):
            self.el.set(XML_SPACE, "preserve")


class _Attr:
    """The value of an attribute (an author, an email in a link)."""
    def __init__(self, el, name):
        self.el, self.name = el, name

    def get(self):
        return self.el.get(self.name) or ""

    def set(self, value):
        self.el.set(self.name, value)


class _Attrs:
    """One value repeated in many attributes (the author of 300 tracked changes): one line,
    written back to all of them."""
    def __init__(self, pairs):
        self.pairs = pairs
        self.el = pairs[0][0]

    def get(self):
        return self.pairs[0][0].get(self.pairs[0][1]) or ""

    def set(self, value):
        for el, name in self.pairs:
            el.set(name, value)


class _Unit:
    """One line of the document: characters, each tied to (holder, index) or to None when it
    cannot change (a tab between two cells, a tab inside a paragraph)."""
    def __init__(self):
        self.slots = []                      # (holder, index) or (None, char)

    def add(self, holder):
        self.slots += [(holder, i) for i in range(len(holder.get()))]

    def fixed(self, char):
        self.slots.append((None, char))

    def text(self):
        cache = {}                                           # get() builds a new string each call
        out = []
        for h, i in self.slots:
            if h is None:
                out.append(i)
            else:
                t = cache.get(id(h))
                if t is None:
                    t = cache[id(h)] = h.get()
                out.append(t[i])
        return "".join(out)


def _para_units(p, out, later):
    """Units of one paragraph: visible text, split at line breaks; deleted text (tracked
    changes) and field codes in units of their own, so they never glue to the visible words."""
    cur, deleted, instr = _Unit(), _Unit(), _Unit()

    def walk(el):
        nonlocal cur
        for c in el:
            if not isinstance(c.tag, str):                   # comment, processing instruction
                continue
            if c.tag == w("pPr") or c.tag == w("rPr"):
                continue                                     # tab stops, never text
            if c.tag == w("t"):
                cur.add(_Text(c))
            elif c.tag == w("delText"):
                deleted.add(_Text(c))
            elif c.tag == w("instrText"):
                instr.add(_Text(c))
            elif c.tag in (w("tab"), w("ptab")):
                cur.fixed("\t")
            elif c.tag in (w("br"), w("cr")):
                out.append(cur); cur = _Unit()
            elif c.tag == w("noBreakHyphen"):
                cur.fixed("-")
            elif c.tag == w("txbxContent"):
                later.append(c)                              # a text box: its own paragraphs
            elif c.tag == w("p"):
                later.append(c)
            else:
                if c.tag == w("fldSimple") and c.get(w("instr")):
                    u = _Unit(); u.add(_Attr(c, w("instr"))); out.append(u)
                walk(c)

    walk(p)
    out.append(cur)
    for u in (deleted, instr):
        if u.slots:
            out.append(u)


def _single_para_row(tr):
    """A table row as one line (cells by tabs), as textutil gives it and as the detector
    reads tables typed by their headers; only when each cell is one simple paragraph."""
    if any(c.tag not in (w("tc"), w("trPr"), w("tblPrEx")) for c in tr if isinstance(c.tag, str)):
        return None                                          # a cell inside a content control...
    cells = [c for c in tr if c.tag == w("tc")]
    paras = []
    for c in cells:
        ps = [x for x in c if x.tag == w("p")]
        if len(ps) != 1 or any(x.tag == w("tbl") for x in c):
            return None
        if any(x.tag in (w("br"), w("cr"), w("txbxContent"), w("delText"), w("instrText"))
               or (x.tag == w("fldSimple") and x.get(w("instr"))) for x in ps[0].iter()):
            return None
        paras.append(ps[0])
    return paras or None


def _block(el, out, later):
    for c in el:
        if not isinstance(c.tag, str):
            continue
        if c.tag == w("p"):
            _para_units(c, out, later)
        elif c.tag == w("tr"):
            paras = _single_para_row(c)
            if paras:
                u = _Unit()
                for i, p in enumerate(paras):
                    if i:
                        u.fixed("\t")
                    tmp = []
                    _para_units(p, tmp, later)
                    u.slots += tmp[0].slots
                out.append(u)
            else:
                _block(c, out, later)
        else:
            _block(c, out, later)


def _part_units(root):
    out, later = [], []
    _block(root, out, later)
    while later:                                             # text boxes, in order of appearance
        el = later.pop(0)
        if el.tag == w("p"):
            _para_units(el, out, later)
        else:
            _block(el, out, later)
    return out


def _sweep(root, units):
    """Anything that still holds text and was not reached: a unit each (charts in SmartArt,
    DrawingML a:t, unusual nesting). Never leave text out of the pseudonymization."""
    seen = {id(h.el) for u in units for h, _ in u.slots if h}
    for el in root.iter(w("t"), w("delText"), w("instrText"), f"{{{A}}}t"):
        if id(el) not in seen and el.text:
            u = _Unit(); u.add(_Text(el)); units.append(u)


def _order(name):
    """Body first (detection reads names in their context), then the rest, stable."""
    rank = (0 if name == "word/document.xml" else 1 if name.startswith("word/header") or name.startswith("word/footer")
            else 2 if name.startswith("word/") else 3)
    return rank, name


class _Doc:
    def __init__(self, data):
        from lxml import etree
        self.etree = etree
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            names = self.zip.namelist()
        except zipfile.BadZipFile:
            raise ValueError("ce fichier n'est pas un document Word (.docx)")
        if "word/document.xml" not in names:
            raise ValueError("ce fichier n'est pas un document Word (.docx)")
        self.infos = self.zip.infolist()
        self.trees, self.units, self.report = {}, [], {"images": 0, "embedded": 0, "charts": 0, "thumbnail": False}
        people = {}                                          # author name -> its attributes
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=False)
        for name in sorted(names, key=_order):
            low = name.lower()
            if low.startswith("word/media/"):
                self.report["images"] += 1
            elif low.startswith("word/embeddings/"):
                self.report["embedded"] += 1
            elif low.startswith("word/charts/") and low.endswith(".xml") and "/_rels/" not in low:
                self.report["charts"] += 1
            if low.startswith("docprops/thumbnail"):
                self.report["thumbnail"] = True
            if not low.endswith((".xml", ".rels")):
                continue
            if not (low.startswith("word/") or low.startswith("docprops/") or low.startswith("customxml/item")
                    or low.endswith(".rels")):
                continue
            if low.startswith("customxml/itemprops"):
                continue
            root = etree.fromstring(self.zip.read(name), parser)
            self.trees[name] = root
            before = len(self.units)
            if name.endswith(".rels"):
                for r in root.iter(f"{{{REL}}}Relationship"):
                    if r.get("TargetMode") == "External" and r.get("Target"):
                        u = _Unit(); u.add(_Attr(r, "Target")); self.units.append(u)
            elif name in PROPS:
                for el in root.iter():
                    if isinstance(el.tag, str) and etree.QName(el).localname in PROPS[name] and (el.text or "").strip():
                        u = _Unit(); u.add(_Text(el)); self.units.append(u)
            elif low.startswith("customxml/"):
                # bound content controls copy these back into the document when Word opens it
                for el in root.iter():
                    if isinstance(el.tag, str) and len(el) == 0 and (el.text or "").strip():
                        u = _Unit(); u.add(_Text(el)); self.units.append(u)
            elif low.startswith("word/"):
                part = _part_units(root)
                _sweep(root, part)
                self.units += part
                for el in root.iter():
                    if not isinstance(el.tag, str):
                        continue
                    for k in el.attrib:
                        if etree.QName(k).localname in PEOPLE_ATTRS and el.get(k).strip():
                            people.setdefault(el.get(k), []).append((el, k)); before = -1
            if len(self.units) == before:
                del self.trees[name]                         # nothing to rewrite there
        for pairs in people.values():
            u = _Unit(); u.add(_Attrs(pairs)); self.units.append(u)

    def lines(self):
        # a line never holds a newline: a value never crosses it, and splitting stays exact
        return [u.text().replace("\n", " ") for u in self.units]

    def save(self):
        out = io.BytesIO()
        drop = {i.filename for i in self.infos if i.filename.lower().startswith("docprops/thumbnail")}
        with zipfile.ZipFile(out, "w") as z:
            for info in self.infos:
                if info.filename in drop:
                    continue
                if info.filename in self.trees:
                    root = self.trees[info.filename]
                    if drop and info.filename in ("_rels/.rels", "[Content_Types].xml"):
                        self._unlist(root, drop)
                    data = self.etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                elif drop and info.filename in ("_rels/.rels", "[Content_Types].xml"):
                    root = self.etree.fromstring(self.zip.read(info.filename))
                    self._unlist(root, drop)
                    data = self.etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                else:
                    data = self.zip.read(info.filename)
                    if info.filename.lower().startswith("word/media/"):
                        data = strip_image_metadata(data)
                z.writestr(info, data)                        # same name, date and compression
        return out.getvalue()

    @staticmethod
    def _unlist(root, drop):
        for el in list(root):
            target = (el.get("Target") or el.get("PartName") or "").lstrip("/")
            if target in drop:
                root.remove(el)


def _tokens(s):
    return re.findall(r"\w+|\s|[^\w\s]", s)


def _apply(unit, new):
    """Write the new text of one line into its holders."""
    old = unit.text().replace("\n", " ")
    if old == new:
        return
    a_tok, b_tok = _tokens(old), _tokens(new)
    a_pos = [0]
    for t in a_tok:
        a_pos.append(a_pos[-1] + len(t))
    b_pos = [0]
    for t in b_tok:
        b_pos.append(b_pos[-1] + len(t))
    # per holder: the output of each of its characters (replacement, "" when deleted)
    outs = {}

    def out_of(h):
        if id(h) not in outs:
            outs[id(h)] = (h, list(h.get()))
        return outs[id(h)][1]

    ops = difflib.SequenceMatcher(None, a_tok, b_tok, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        a, b = a_pos[i1], a_pos[i2]
        s = new[b_pos[j1]:b_pos[j2]]
        movable = [k for k in range(a, b) if unit.slots[k][0]]
        if not movable:
            # pure insertion (or only tabs in the way): next to the nearest writable character
            k = next((k for k in range(a - 1, -1, -1) if unit.slots[k][0]), None)
            if k is not None:
                h, i = unit.slots[k]; o = out_of(h); o[i] = o[i] + s
                continue
            k = next((k for k in range(b, len(unit.slots)) if unit.slots[k][0]), None)
            if k is None:
                raise ValueError("ligne sans texte modifiable")
            h, i = unit.slots[k]; o = out_of(h); o[i] = s + o[i]
            continue
        # contiguous pieces of the passage, one per holder, in order
        pieces = []
        for k in movable:
            h, i = unit.slots[k]
            if pieces and pieces[-1][0] is h and pieces[-1][2] == i:
                pieces[-1][2] = i + 1
            else:
                pieces.append([h, i, i + 1])
        total = sum(e - s0 for _, s0, e in pieces)
        cum, cut = 0, 0
        for n, (h, s0, e) in enumerate(pieces):
            cum += e - s0
            nxt = len(s) if n == len(pieces) - 1 else round(len(s) * cum / total)
            o = out_of(h)
            o[s0] = s[cut:nxt]
            for i in range(s0 + 1, e):
                o[i] = ""
            cut = nxt
    for h, o in outs.values():
        h.set("".join(o))


def strip_image_metadata(data):
    """An image of the document without what its file says beside the pixels: EXIF (place
    of the shot, camera owner, date), XMP, IPTC, comments. The pixels are copied byte for
    byte, never re-encoded. Anything not recognised is returned as it is."""
    if data[:2] == b"\xff\xd8":                                # JPEG: segments up to the scan
        out, i = bytearray(b"\xff\xd8"), 2
        while i + 4 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            if marker == 0xDA:                                 # start of scan: the rest is the image
                return bytes(out + data[i:])
            size = int.from_bytes(data[i + 2:i + 4], "big")
            if marker not in (0xE1, 0xED, 0xFE):               # APP1 (EXIF, XMP), APP13 (IPTC), comment
                out += data[i:i + 2 + size]
            i += 2 + size
        return data                                            # not the expected shape: untouched
    if data[:8] == b"\x89PNG\r\n\x1a\n":                         # PNG: chunks, text and EXIF dropped
        out, i = bytearray(data[:8]), 8
        while i + 12 <= len(data):
            size = int.from_bytes(data[i:i + 4], "big")
            kind = data[i + 4:i + 8]
            if kind not in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"):
                out += data[i:i + 12 + size]
            i += 12 + size
            if kind == b"IEND":
                return bytes(out)
        return data
    return data


def units(data):
    return _Doc(data).lines()


def report(data):
    """What a protected copy would leave as it is (images, embedded objects, charts)."""
    return _Doc(data).report


def text(data):
    return "\n".join(units(data))


def transform(data, fn):
    """The document with fn applied to its text; fn gets the whole text (one line per unit)
    and must keep its lines. Returns (bytes, report)."""
    doc = _Doc(data)
    lines = doc.lines()
    new = fn("\n".join(lines)).split("\n")
    if len(new) != len(lines):                               # never seen; line by line then
        new = [fn(x) if x.strip() else x for x in lines]
        if any("\n" in x for x in new):
            raise ValueError("le texte a changé de forme, document non écrit")
    for u, x in zip(doc.units, new):
        _apply(u, x)
    out = doc.save()
    if _Doc(out).lines() != new:                             # read back from the bytes
        raise ValueError("réécriture du document inexacte, document non écrit")
    return out, doc.report


def warnings(report):
    """What the user must know about a protected copy (French, shown as is)."""
    out = []
    if report.get("images"):
        out.append(f"{report['images']} image(s) laissée(s) telle(s) quelle(s) : vérifiez qu'aucune ne montre un nom (en-tête, signature, tampon).")
    if report.get("embedded"):
        out.append(f"{report['embedded']} objet(s) incorporé(s) (tableur, autre document) non traité(s) : à retirer avant l'envoi.")
    if report.get("charts"):
        out.append(f"{report['charts']} graphique(s) : les textes visibles sont protégés, pas les données du tableur incorporé.")
    return out
