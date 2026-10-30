"""不需要 AI 的離線測試：驗證 Word / PDF 取代邏輯沒有誤刪、沒有殘留。
執行：.venv/bin/python -W ignore tests/test_redact.py
"""
import re, sys
from collections import Counter
sys.path.insert(0, ".")
import docs_io, pii

ITEMS = [(t, ty) for t, ty in [
    ("王大明", "NAME"), ("陳小華", "NAME"), ("李美玲", "NAME"),
    ("A123456789", "ID"), ("B234567891", "ID"),
    ("0912-345-678", "PHONE"), ("0933-111-222", "PHONE"), ("(04)834-5678", "PHONE"),
    ("wang.daming@example.com", "EMAIL"), ("1234-5678-9012-3456", "BANK"),
    ("彰化縣員林市中山路二段123號", "ADDRESS"), ("雲林縣斗六市大學路100號", "ADDRESS"),
    ("臺中市西屯區台灣大道三段九十九號十二樓之三", "ADDRESS"),
    ("民國60年5月12日", "BIRTH"), ("員林段123-4地號", "LAND"),
]]
items = [{"text": t, "type": ty} for t, ty in ITEMS]
sq = lambda s: re.sub(r"\s+", "", s)

def check(path, ext, mode):
    mapping = pii.build_mapping(items, mode)
    out = f"/tmp/t_{mode}{ext}"
    docs_io.redact(path, out, ext, mapping)
    before = docs_io.extract_raw(path, ext)
    after = docs_io.extract_raw(out, ext)
    left = docs_io.residual(out, ext, mapping)
    expected = sq(pii.apply_text(sq(before), {sq(k): v for k, v in mapping.items()}))
    diff = Counter(expected) - Counter(sq(after)), Counter(sq(after)) - Counter(expected)
    ok = not left and not diff[0] and not diff[1]
    print(("PASS" if ok else "FAIL"), ext, mode, "residual=", left, "缺字=", dict(diff[0]), "多字=", dict(diff[1]))
    return ok

ok = all(check(p, e, m) for p, e in (("samples/範例_申請書.docx", ".docx"), ("samples/範例_申請書.pdf", ".pdf"))
         for m in ("label_typed", "label_generic", "fake"))
sys.exit(0 if ok else 1)
