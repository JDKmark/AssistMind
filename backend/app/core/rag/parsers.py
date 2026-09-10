"""文档解析：ETL 的第一步（二进制 → 纯文本）。

支持：
- .md / .txt：纯文本直读（无结构损失）
- .pdf：pypdf（纯 Python 依赖）提取文本，多页以空行分隔
- .docx：标准库 zipfile + xml 解析 word/document.xml（无第三方依赖），按段落换行

PDF/DOCX 无 Markdown 结构：调用方（seed_*_kb.py）应把文件名作为首位 Markdown
标题注入文本（如 `# {title}\n\n{text}`），让结构感知切块把标题并入首个 chunk，
保留文档归属语义（避免 LLM 无法确认归属文件而生成「资料未提及」元话语）。

解析失败时抛 UnsupportedDocumentError（调用方负责降级跳过并记 warning，
保证单个坏文档不阻塞整体灌库）。
"""

from __future__ import annotations

import logging
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

_SUPPORTED_EXTS = (".md", ".txt", ".pdf", ".docx")

# 预测文本编码：非 UTF-8 的 md/txt 按 GB18030 兜底（中文 Windows 文档常见）
_ENCODINGS = ("utf-8", "gb18030")


class UnsupportedDocumentError(ValueError):
    """不支持的文档扩展名或文档结构损坏。"""


def _read_text(path: str) -> str:
    """读取纯文本文件，UTF-8 失败回退 GB18030。"""
    data = Path(path).read_bytes()
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocumentError(f"无法解码文本文件: {path}")


def _extract_pdf(path: str) -> str:
    """用 pypdf 提取 PDF 文本（多页空行分隔）。

    扫描型/图片型 PDF 无文本层，extract_text 返回空 → 返回空串。
    """
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as e:
            logger.warning("[Parser] PDF 页文本提取失败（跳过该页）: %s", e)
            text = ""
        if text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)


def _extract_docx(path: str) -> str:
    """从 docx（zip 内 word/document.xml）提取段落文本（标准库实现）。

    只取每个 w:p 里的 w:t 文本，忽略样式/图片；空段落跳过。
    """
    with zipfile.ZipFile(path) as zf:
        if "word/document.xml" not in zf.namelist():
            raise UnsupportedDocumentError(f"不是有效的 docx（缺 word/document.xml）: {path}")
        try:
            xml_bytes = zf.read("word/document.xml")
        except (KeyError, zipfile.BadZipFile) as e:
            raise UnsupportedDocumentError(f"读取 docx 内容失败: {path}: {e}")

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise UnsupportedDocumentError(f"解析 docx XML 失败: {path}: {e}")
    paras = []
    for p in root.findall(".//w:p", ns):
        text = "".join(t.text or "" for t in p.findall(".//w:t", ns))
        if text.strip():
            paras.append(text.strip())
    return "\n".join(paras)


def extract_text(path: str) -> str:
    """按扩展名提取文档纯文本。不支持的扩展名抛 UnsupportedDocumentError。"""
    ext = Path(path).suffix.lower()
    if ext in (".md", ".txt"):
        return _read_text(path)
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        return _extract_docx(path)
    raise UnsupportedDocumentError(f"不支持的文档类型: {ext}")
