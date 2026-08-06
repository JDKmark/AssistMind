"""结构感知文档分块：Markdown 标题 + 代码块/表格整体保留 + SQL DDL 按表切块。

chunk_size=512, overlap=64（可配置）。

分块策略（按优先级）：
1. SQL DDL（行首含 CREATE TABLE 的裸 SQL）→ 按 CREATE TABLE 语句整表保留，
   section_title=表名，table_comment=表级 COMMENT 注释
2. fenced 代码块（``` / ~~~ 包裹）→ 整块保留为一个 chunk（允许超过 chunk_size，
   不按句子/字符硬切），section_title 取代码块前最近的标题
3. Markdown 表格（| 开头连续行）→ 整表保留为一个 chunk；超长时按行边界切，
   不切断单元格内容
4. 普通文本 → 段落→句子→硬切（保留原有三级字符切分逻辑）
5. Markdown 标题（#/##/###）→ 提取为 section_title 元数据，写入其下所有 chunk；
   标题行本身仍留在文本流中（与旧逻辑文本兼容），并在标题处强制分节，跨章节不混块

所有 chunk 统一结构：{text, chunk_index, section_title, table_comment, ...metadata}
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_FENCE_RE = re.compile(r"^[ \t]*(```+|~~~+)")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|")
_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+|TEMPORARY\s+)?TABLE\b", re.IGNORECASE | re.MULTILINE
)
_TABLE_NAME_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+|TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\"\w.]+)",
    re.IGNORECASE,
)
_TABLE_COMMENT_RE = re.compile(r"COMMENT\s*=\s*'([^']*)'", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。.!?；;！!？])")


def chunk_text(
    text: str,
    metadata: dict[str, Any] | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[dict[str, Any]]:
    """结构感知文档分块。

    Args:
        text: 原始文本（支持 Markdown / 裸 SQL DDL）
        metadata: 附加到每个 chunk 的元数据
        chunk_size: 块大小（默认用配置）
        overlap: 重叠（默认用配置）

    Returns:
        [{text, chunk_index, section_title, table_comment, ...metadata}]
    """
    size = chunk_size or settings.CHUNK_SIZE
    ovr = overlap or settings.CHUNK_OVERLAP
    metadata = metadata or {}

    if not text or not text.strip():
        return []

    # 裸 SQL DDL：整表保留
    if _looks_like_sql_ddl(text):
        return chunk_sql_ddl(text, metadata=metadata)

    return _chunk_markdown(text, size=size, ovr=ovr, metadata=metadata)


def chunk_sql_ddl(
    sql_text: str,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 CREATE TABLE 语句切分 SQL DDL，每张表一个 chunk（整表 DDL 保留不切碎）。

    - section_title = 表名（如 oms_order，去掉 schema 前缀与反引号）
    - table_comment = 表级 COMMENT='xxx'（取语句内最后一个 `COMMENT = '...'`，
      列级注释为 `COMMENT '...'` 不带等号，不会被误取）
    - 首个 CREATE TABLE 之前的杂项（SET / DROP TABLE 等）并入首个 chunk，不丢内容

    Args:
        sql_text: SQL DDL 文本
        metadata: 附加到每个 chunk 的元数据

    Returns:
        [{text, chunk_index, section_title, table_comment, ...metadata}]
    """
    metadata = metadata or {}
    if not sql_text or not sql_text.strip():
        return []

    starts = [m.start() for m in _CREATE_TABLE_RE.finditer(sql_text)]
    if not starts:
        # 非 DDL 文本：退回普通 Markdown 分块
        logger.debug("[Chunking] chunk_sql_ddl 未发现 CREATE TABLE，退回文本分块")
        return _chunk_markdown(
            sql_text,
            size=settings.CHUNK_SIZE,
            ovr=settings.CHUNK_OVERLAP,
            metadata=metadata,
        )

    chunks: list[dict[str, Any]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(sql_text)
        stmt = sql_text[start:end].strip()
        if not stmt:
            continue
        if idx == 0 and start > 0:
            preamble = sql_text[:start].strip()
            if preamble:
                stmt = f"{preamble}\n\n{stmt}"
        chunks.append(
            {
                "text": stmt,
                "chunk_index": idx,
                **metadata,
                "section_title": _extract_table_name(stmt),
                "table_comment": _extract_table_comment(stmt),
            }
        )
    return chunks


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _looks_like_sql_ddl(text: str) -> bool:
    """裸 SQL DDL 检测：行首存在 CREATE TABLE 且未被 fenced 包裹、全文无 Markdown 标题。"""
    if _HEADING_RE.search(text):
        return False
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _CREATE_TABLE_RE.match(line):
            return True
    return False


def _extract_table_name(stmt: str) -> str:
    """提取 CREATE TABLE 表名，兼容 `oms_order` / mall.oms_order / `mall`.`oms_order`。"""
    m = _TABLE_NAME_RE.search(stmt)
    if not m:
        return ""
    raw = m.group(1).strip().strip("`\"")
    return raw.split(".")[-1]


def _extract_table_comment(stmt: str) -> str:
    """提取表级 COMMENT='xxx'（MySQL 表注释带等号，列注释不带，取最后一个匹配）。"""
    matches = _TABLE_COMMENT_RE.findall(stmt)
    return matches[-1].strip() if matches else ""


def _chunk_markdown(
    text: str,
    size: int,
    ovr: int,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Markdown 结构感知分块：逐行扫描，fence/表格/标题为结构边界。

    - 结构元素（fence/表格）整体成块，前后文本缓冲先 flush，保证块序正确
    - 标题行更新当前 section_title 并强制 flush（跨章节不混块），标题行本身仍进文本流
    """
    chunks: list[dict[str, Any]] = []
    idx = 0
    current_section = ""
    lines = text.splitlines()
    text_buf: list[str] = []
    i = 0
    n = len(lines)

    def flush_text() -> None:
        """将普通文本缓冲交给段落→句子→硬切逻辑成块。"""
        nonlocal idx
        if not text_buf:
            return
        buf = "\n".join(text_buf)
        text_buf.clear()
        for t in _chunk_plain_text(buf, size, ovr):
            chunks.append(
                {
                    "text": t,
                    "chunk_index": idx,
                    **metadata,
                    "section_title": current_section,
                    "table_comment": "",
                }
            )
            idx += 1

    def emit_block(block: str) -> None:
        """结构元素（代码块/表格分块）整块成 chunk。"""
        nonlocal idx
        if not block.strip():
            return
        chunks.append(
            {
                "text": block.strip(),
                "chunk_index": idx,
                **metadata,
                "section_title": current_section,
                "table_comment": "",
            }
        )
        idx += 1

    while i < n:
        line = lines[i]

        # fenced 代码块：整块保留
        fm = _FENCE_RE.match(line)
        if fm:
            flush_text()
            fence = fm.group(1)
            block = [line]
            i += 1
            while i < n:
                block.append(lines[i])
                if re.match(rf"^[ \t]*{re.escape(fence)}[ \t]*$", lines[i]):
                    i += 1
                    break
                i += 1
            emit_block("\n".join(block))
            continue

        # Markdown 表格：整表保留 / 按行边界切
        if _TABLE_ROW_RE.match(line):
            flush_text()
            rows = [line]
            i += 1
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                rows.append(lines[i])
                i += 1
            for block in _split_table_by_rows(rows, size):
                emit_block(block)
            continue

        # 标题：更新当前节并强制分节（跨章节不混块）
        hm = _HEADING_RE.match(line)
        if hm:
            flush_text()
            current_section = hm.group(2).strip()

        text_buf.append(line)
        i += 1

    flush_text()
    return chunks


def _chunk_plain_text(text: str, size: int, ovr: int) -> list[str]:
    """普通文本分块（原逻辑）：段落→句子→硬切，返回分块文本列表。"""
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # 段落本身超长，按句号切分
        if len(para) > size:
            sentences = _SENTENCE_SPLIT_RE.split(para)
            for sent in sentences:
                if not sent.strip():
                    continue
                if len(current) + len(sent) <= size:
                    current += sent
                else:
                    if current:
                        chunks.append(current.strip())
                        # 保留 overlap
                        current = current[-ovr:] + sent if ovr > 0 else sent
                    else:
                        # 单句超长，硬切（step 兜底防 overlap>=size 时退化为 0/负步长）
                        step = max(size - ovr, 1)
                        for i in range(0, len(sent), step):
                            chunks.append(sent[i : i + size].strip())
                        current = ""
        elif len(current) + len(para) + 2 <= size:
            current = (current + "\n\n" + para) if current else para
        else:
            if current:
                chunks.append(current.strip())
                current = para
            else:
                current = para

    if current:
        chunks.append(current.strip())

    return chunks


def _split_table_by_rows(rows: list[str], size: int) -> list[str]:
    """表格按行边界打包：整表放得下就一整块；超长按行切，不切断单元格内容。

    单行本身超长时该行仍整体保留（允许 chunk 超过 size）。
    """
    whole = "\n".join(rows)
    if len(whole) <= size:
        return [whole]

    blocks: list[str] = []
    current: list[str] = []
    cur_len = 0
    for row in rows:
        if current and cur_len + 1 + len(row) > size:
            blocks.append("\n".join(current))
            current, cur_len = [], 0
        current.append(row)
        cur_len += 1 + len(row)
    if current:
        blocks.append("\n".join(current))
    return blocks
