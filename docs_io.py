"""Word / PDF 的文字擷取與個資取代。

Word：直接改段落裡的文字節點，保留原本的字型、表格與版面。
PDF：把個資所在位置真正塗銷（底下的文字會被移除，不是只蓋白色方塊），再寫上取代文字。
"""
import os
import re
import zipfile
from collections import Counter
from xml.sax.saxutils import escape

import pymupdf as fitz  # PyMuPDF
from docx import Document
from docx.oxml.ns import qn
from lxml import etree

import pii

W_P, W_T, W_DT = qn("w:p"), qn("w:t"), qn("w:delText")
EXTRA_PARTS = ("word/footnotes.xml", "word/endnotes.xml", "word/comments.xml")
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class ScannedPdfError(Exception):
    pass


# ---------------------------------------------------------------- Word

def _docx_roots(doc):
    roots = [doc.element.body]
    for sec in doc.sections:
        for hf in (sec.header, sec.footer, sec.first_page_header,
                   sec.first_page_footer, sec.even_page_header, sec.even_page_footer):
            if not hf.is_linked_to_previous:  # 避免存取時意外新增頁首頁尾
                roots.append(hf._element)
    return roots


def _own_ts(p):
    """屬於這個段落本身的文字節點（排除文字方塊裡巢狀段落的）。"""
    out = []
    for t in p.iter(W_T, W_DT):  # 含追蹤修訂裡「已刪除」的文字
        anc = t.getparent()
        while anc is not None and anc.tag != W_P:
            anc = anc.getparent()
        if anc is p:
            out.append(t)
    return out


def _docx_paragraphs(doc):
    for root in _docx_roots(doc):
        yield from root.iter(W_P)


def _extra_roots(path):
    with zipfile.ZipFile(path) as z:
        for name in EXTRA_PARTS:
            if name in z.namelist():
                yield etree.fromstring(z.read(name))


def docx_extract(path):
    doc = Document(path)
    lines = ["".join(t.text or "" for t in _own_ts(p)) for p in _docx_paragraphs(doc)]
    for root in _extra_roots(path):
        lines += ["".join(t.text or "" for t in _own_ts(p)) for p in root.iter(W_P)]
    return "\n".join(l for l in lines if l.strip())


def _replace_in_paragraph(p, pattern, mapping, counts):
    ts = _own_ts(p)
    if not ts:
        return
    texts = [t.text or "" for t in ts]
    lens = [len(s) for s in texts]
    full = "".join(texts)
    matches = list(pattern.finditer(full))
    if not matches:
        return
    starts, pos = [], 0
    for n in lens:
        starts.append(pos)
        pos += n
    for m in reversed(matches):  # 由後往前改，前面的位置不會位移
        ms, me = m.span()
        repl = mapping[m.group()]
        counts[m.group()] = counts.get(m.group(), 0) + 1
        first = True
        for i, n in enumerate(lens):
            rs = starts[i]
            if n == 0 or rs + n <= ms or rs >= me:
                continue
            a, b = max(ms - rs, 0), min(me - rs, n)
            texts[i] = texts[i][:a] + (repl if first else "") + texts[i][b:]
            first = False
    for t, s in zip(ts, texts):
        if s != (t.text or ""):
            t.text = s
            t.set(XML_SPACE, "preserve")


def _scrub_zip(path, mapping, pattern, counts):
    """整包處理：註腳／尾註／註解逐段取代；其餘 XML 與關聯檔（欄位、屬性、超連結目標等）直接字串取代；
    清掉修訂者姓名、公司／主管欄位與縮圖。"""
    tmp = path + ".tmp"
    keys = sorted((k for k in mapping if k), key=len, reverse=True)
    quote = {'"': "&quot;"}
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            name, data = item.filename, zin.read(item.filename)
            if name.startswith("docProps/thumbnail"):
                continue
            if name in EXTRA_PARTS and pattern:
                root = etree.fromstring(data)
                for p in root.iter(W_P):
                    _replace_in_paragraph(p, pattern, mapping, counts)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            if name.endswith((".xml", ".rels")):
                text = data.decode("utf-8")
                if name == "_rels/.rels":
                    text = re.sub(r"<Relationship\b[^>]*thumbnail[^>]*/>", "", text)
                if name == "docProps/app.xml":
                    text = re.sub(r"<(Company|Manager)>.*?</\1>", r"<\1></\1>", text, flags=re.S)
                text = re.sub(r'w:author="[^"]*"', 'w:author="匿名"', text)
                text = re.sub(r'w:initials="[^"]*"', 'w:initials=""', text)
                for k in keys:
                    for needle in {escape(k), escape(k, quote), k}:
                        n = text.count(needle)
                        if n:
                            text = text.replace(needle, escape(mapping[k], quote))
                            counts[k] = counts.get(k, 0) + n
                data = text.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, path)


def docx_redact(src, dst, mapping, warnings=None):
    doc = Document(src)
    pattern = pii.mapping_pattern(mapping)
    counts = {}
    if pattern:
        for p in _docx_paragraphs(doc):
            _replace_in_paragraph(p, pattern, mapping, counts)
    cp = doc.core_properties  # 作者、標題、主旨、關鍵字等中繼資料也可能含個資
    cp.author = cp.last_modified_by = cp.comments = cp.title = cp.subject = cp.keywords = cp.category = ""
    doc.save(dst)
    _scrub_zip(dst, mapping, pattern, counts)
    return counts


def _docx_raw_residual(path, mapping):
    with zipfile.ZipFile(path) as z:
        blob = "\n".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.endswith((".xml", ".rels")))
    return [k for k in mapping if k and (k in blob or escape(k) in blob) and k not in mapping[k]]


# ---------------------------------------------------------------- PDF

_FONT_CANDIDATES = [
    os.environ.get("PDF_FONT", ""),
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/msjh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _cjk_font():
    for p in _FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


_NOCLIP = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES  # 不裁切：頁面外的文字也算


def pdf_extract(path):
    with fitz.open(path) as doc:
        text = "\n".join(page.get_text("text", flags=_NOCLIP) for page in doc)
    if len(_squash(text)) < 20:
        raise ScannedPdfError(
            "這份 PDF 沒有可選取的文字（可能是掃描影像），目前無法處理。請先用 OCR 轉成可選取文字的 PDF，或改用 Word 檔。"
        )
    return text


def _squash(s):
    return re.sub(r"\s+", "", s)


def _page_chars(page):
    """依閱讀順序列出頁面所有字元（含空白），區塊之間插入分隔符 {"sep": True}。"""
    flags = _NOCLIP
    chars, line_id = [], 0
    for block in page.get_text("rawdict", flags=flags)["blocks"]:
        if block.get("type") != 0:
            continue
        chars.append({"c": "\n", "sep": True})
        for line in block["lines"]:
            line_id += 1
            for span in line["spans"]:
                for ch in span["chars"]:
                    chars.append({"c": ch["c"], "bbox": fitz.Rect(ch["bbox"]), "line": line_id,
                                  "size": span["size"], "origin": ch["origin"], "color": span["color"]})
    return chars


def _rgb(c):
    return ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)


def _find_hits(chars, keys, mapping, counts):
    """在頁面字元序列中找出每個個資的位置（忽略空白與換行，可跨行）。回傳 (hits, used)。"""
    sig = [i for i, ch in enumerate(chars) if ch.get("sep") or not ch["c"].isspace()]
    flat = "".join(chars[i]["c"] for i in sig)
    hits, used = [], {}
    for key in keys:
        needle = _squash(key)
        pos = flat.find(needle)
        while pos != -1:
            idxs = [sig[j] for j in range(pos, pos + len(needle))]
            if not any(i in used for i in idxs):
                for i in idxs:
                    used[i] = len(hits)
                hits.append({"repl": mapping[key], "idxs": idxs})
                counts[key] = counts.get(key, 0) + 1
            pos = flat.find(needle, pos + len(needle))
    return hits, used


def _safe_band(rect, base, size, lid, others):
    """整行移除用的細帶。行距緊時逐步變窄，避免碰到上下行的字；仍碰到就回報 False。"""
    band = None
    for top, bot in ((0.6, 0.12), (0.45, 0.08), (0.3, 0.05)):
        band = fitz.Rect(rect.x0 + 0.5, base - top * size, rect.x1 - 0.5, base + bot * size)
        if not any(c["line"] != lid and c["bbox"].intersects(band) for c in others):
            return band, True
    return band, False


def _clean_pdf_extras(doc, page_iter, keys, mapping):
    """書籤、附件、註解、連結、表單、XMP：頁面文字以外的地方。"""
    hit = lambda t: any(_squash(k) in _squash(t or "") for k in keys)
    toc = doc.get_toc()
    if toc:
        doc.set_toc([[lvl, pii.apply_text(title, mapping), pg] for lvl, title, pg in toc])
    for name in list(doc.embfile_names()):  # 內嵌附件看不到、也無法逐字檢查，一律移除
        doc.embfile_del(name)
    doc.del_xml_metadata()
    for page in page_iter:
        for a in list(page.annots() or []):
            if hit(" ".join(str(v) for v in a.info.values())) or hit(a.info.get("content")):
                page.delete_annot(a)
        for l in page.get_links():
            if hit(l.get("uri")):
                page.delete_link(l)
        for w in list(page.widgets() or []):
            if hit(str(w.field_value)):
                page.delete_widget(w)


def pdf_redact(src, dst, mapping, warnings=None):
    """含個資的每一行：整行文字移除，再把套用取代後的整行依序重新寫回。

    不用「只塗掉個資那幾個字」的做法，因為 PyMuPDF 局部塗銷後，後面的字位置會跑掉。
    """
    keys = sorted((k for k in mapping if _squash(k)), key=len, reverse=True)
    counts = {}
    font_path = _cjk_font()
    measure = fitz.Font(fontfile=font_path) if font_path else fitz.Font("china-t")
    fontname = "piicjk" if font_path else "china-t"
    doc = fitz.open(src)
    if warnings is None:
        warnings = []
    _clean_pdf_extras(doc, doc, keys, mapping)
    for page in doc:
        chars = _page_chars(page)
        hits, used = _find_hits(chars, keys, mapping, counts)
        if not hits:
            continue
        by_line = {}
        for i, ch in enumerate(chars):
            if not ch.get("sep"):
                by_line.setdefault(ch["line"], []).append(i)
        others = [c for c in chars if not c.get("sep")]
        bands = {}
        plans = []  # 每個受影響的行：(x0, 基線, 寬度, [(文字, 字級, 顏色)])
        for lid in sorted({chars[i]["line"] for i in used}):
            idxs = by_line[lid]
            rect = fitz.Rect(chars[idxs[0]]["bbox"])
            for i in idxs:
                rect |= chars[i]["bbox"]
            size = max(chars[i]["size"] for i in idxs)
            base = chars[idxs[0]]["origin"][1]
            runs = []
            for i in idxs:
                ch, h = chars[i], used.get(i)
                if h is not None and i != hits[h]["idxs"][0]:
                    continue  # 個資的後續字元：整段只在第一個字的位置寫一次取代文字
                text = hits[h]["repl"] if h is not None else ch["c"]
                if runs and runs[-1][1:] == [ch["size"], ch["color"]]:
                    runs[-1][0] += text
                else:
                    runs.append([text, ch["size"], ch["color"]])
            # 整行移除：只取基線附近的細帶，避免碰到上下行
            band, safe = _safe_band(rect, base, size, lid, others)
            if not safe:
                warnings.append(f"第 {page.number + 1} 頁有行距過緊的行，個資附近的相鄰文字可能被一併移除，請人工核對該頁。")
            bands[lid] = band
            page.add_redact_annot(band, fill=False)
            plans.append((rect.x0, base, rect.width, runs))
        for hit in hits:  # 個資所在處另外塗白，連向量描邊的文字也蓋住
            for r in {chars[i]["line"]: None for i in hit["idxs"]}:
                rs = [chars[i]["bbox"] for i in hit["idxs"] if chars[i]["line"] == r]
                u = fitz.Rect(rs[0])
                for x in rs[1:]:
                    u |= x
                dx = min(1.0, u.width / 4)
                b = bands[r]
                page.add_redact_annot(fitz.Rect(u.x0 + dx, max(u.y0, b.y0), u.x1 - dx, min(u.y1, b.y1)), fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        if font_path:
            page.insert_font(fontname="piicjk", fontfile=font_path)
        for x0, base, width, runs in plans:
            total = sum(measure.text_length(t, fontsize=sz) for t, sz, _ in runs)
            limit = min(width + 30, page.rect.x1 - 15 - x0)
            scale = 1.0 if total <= limit else max(limit / total, 0.6)
            x = x0
            for t, sz, color in runs:
                page.insert_text(fitz.Point(x, base), t, fontsize=sz * scale, fontname=fontname, color=_rgb(color))
                x += measure.text_length(t, fontsize=sz * scale)
    if font_path:
        try:
            doc.subset_fonts()  # 只留用到的字，避免檔案暴增
        except Exception:
            pass
    doc.set_metadata({})
    doc.save(dst, garbage=4, deflate=True)
    doc.close()
    return counts


# ---------------------------------------------------------------- 共用

def extract(path, ext):
    return docx_extract(path) if ext == ".docx" else pdf_extract(path)


def redact(src, dst, ext, mapping, warnings=None):
    if ext == ".docx":
        return docx_redact(src, dst, mapping, warnings)
    return pdf_redact(src, dst, mapping, warnings)


def _pdf_extra_residual(path, keys):
    out = []
    with fitz.open(path) as doc:
        blobs = [("書籤", t[1]) for t in doc.get_toc()]
        blobs.append(("中繼資料", " ".join(str(v) for v in (doc.metadata or {}).values())))
        blobs.append(("XMP", doc.get_xml_metadata() or ""))
        for n in doc.embfile_names():
            blobs += [("附件", n), ("附件內容", doc.embfile_get(n).decode("utf-8", "ignore"))]
        for page in doc:
            blobs += [("註解", " ".join(str(v) for v in a.info.values())) for a in page.annots() or []]
            blobs += [("連結", l.get("uri") or "") for l in page.get_links()]
            blobs += [("表單欄位", str(w.field_value)) for w in page.widgets() or []]
    for k in keys:
        for where, text in blobs:
            if _squash(k) in _squash(text):
                out.append(f"{k}（{where}）")
    return out


def residual(dst, ext, mapping):
    """重新讀回輸出檔，檢查原始個資是否還殘留（文字層，加上 Word 整包 XML、PDF 的書籤附件註解等）。"""
    flat = _squash(extract_raw(dst, ext))
    keys = [k for k in mapping if _squash(k) and k not in mapping[k]]
    left = [k for k in keys if _squash(k) in flat]
    if ext == ".docx":
        left += [k for k in _docx_raw_residual(dst, mapping) if k not in left]
    else:
        left += _pdf_extra_residual(dst, keys)
    return left


def extract_raw(path, ext):
    if ext == ".docx":
        return docx_extract(path)
    with fitz.open(path) as doc:
        return "\n".join(page.get_text("text", flags=_NOCLIP) for page in doc)


def integrity_ok(src, dst, ext, mapping):
    """輸出檔的文字（忽略空白）應該等於「原文套用取代」的結果；不一致代表可能誤刪或位移。"""
    expected = _squash(pii.apply_text(_squash(extract_raw(src, ext)), {_squash(k): v for k, v in mapping.items() if _squash(k)}))
    return Counter(expected) == Counter(_squash(extract_raw(dst, ext)))
