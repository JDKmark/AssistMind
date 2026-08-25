"""文档解析（parsers.py）单元测试。

覆盖：
1. md/txt 直读；GB18030 编码回退
2. docx（zip + document.xml）段落文本提取
3. pdf（pypdf）文本提取
4. 二进制文档归属标题注入由 seed 脚本负责（此处不测）
5. 不支持扩展名 / 损坏文件抛 UnsupportedDocumentError
"""

from __future__ import annotations

import zipfile

import pytest

from app.core.rag.parsers import UnsupportedDocumentError, extract_text


def _write_docx(path, paras: list[str]) -> None:
    """构造最小 docx：仅 word/document.xml（标准库 zipfile 写）。"""
    from xml.sax.saxutils import escape

    body = "".join(
        f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paras
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "")
        zf.writestr("word/document.xml", xml)


def _pdf_bytes(text: str) -> bytes:
    """构造单页文本 PDF（精确 xref，pypdf 可读）。文本需 ASCII（latin-1）。
    页面：Helvetica 12pt，内容流 BT /F1 12 Tf 72 720 Td (text) Tj ET。"""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode("latin-1") + b") Tj ET"
    objs.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref_pos,
    )
    return bytes(out)


# ---------- 1. md/txt ----------


def test_extract_text_md(tmp_path):
    """md 文档直读。"""
    p = tmp_path / "a.md"
    p.write_text("商品退货政策", encoding="utf-8")
    assert extract_text(str(p)) == "商品退货政策"


def test_extract_text_txt_gb18030(tmp_path):
    """非 UTF-8 文本回退 GB18030 解析。"""
    p = tmp_path / "b.txt"
    data = "订单状态说明".encode("gb18030")
    p.write_bytes(data)
    assert extract_text(str(p)) == "订单状态说明"


# ---------- 2. docx ----------


def test_extract_text_docx(tmp_path):
    """docx 按段落提取文本（空段落跳过）。"""
    p = tmp_path / "manual.docx"
    _write_docx(p, ["退货政策说明", "", "48 小时内到账"])
    text = extract_text(str(p))
    assert "退货政策说明" in text
    assert "48 小时内到账" in text


def test_extract_text_docx_bad(tmp_path):
    """损坏/非 docx 的 zip：抛 UnsupportedDocumentError。"""
    p = tmp_path / "bad.docx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("other.txt", "x")
    with pytest.raises(UnsupportedDocumentError):
        extract_text(str(p))


# ---------- 3. pdf ----------


def test_extract_text_pdf(tmp_path):
    """pypdf 提取 PDF 页面文本。"""
    p = tmp_path / "c.pdf"
    p.write_bytes(_pdf_bytes("Hello AssistMind 123"))
    text = extract_text(str(p))
    assert "Hello AssistMind 123" in text


# ---------- 4. 不支持类型 / 损坏 ----------


def test_extract_text_unsupported(tmp_path):
    """不支持的扩展名抛 UnsupportedDocumentError。"""
    p = tmp_path / "d.exe"
    p.write_bytes(b"xx")
    with pytest.raises(UnsupportedDocumentError):
        extract_text(str(p))


def test_extract_text_corrupt_pdf_raises(tmp_path):
    """非 PDF 内容但 .pdf 后缀：pypdf 解析失败抛异常（seed 侧降级跳过）。"""
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"this is not a pdf")
    with pytest.raises(Exception):
        extract_text(str(p))
