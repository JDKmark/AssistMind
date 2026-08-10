"""结构感知分块单测：代码块/表格/SQL 三类结构不切碎 + 标题元数据 + 旧逻辑回归。"""

from __future__ import annotations

import re

from app.core.rag.chunking import chunk_sql_ddl, chunk_text

# ---------------------------------------------------------------------------
# 旧逻辑参考实现（chunking.py 重写前的三级字符切分），用于回归对比
# ---------------------------------------------------------------------------


def _legacy_chunk_text(text: str, chunk_size: int, overlap: int) -> list[dict]:
    """重写前 chunk_text 的原始逻辑（段落→句子→硬切）。"""
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks: list[dict] = []
    current = ""
    idx = 0

    for para in paragraphs:
        if len(para) > chunk_size:
            sentences = re.split(r"(?<=[。.!?；;！!？])", para)
            for sent in sentences:
                if not sent.strip():
                    continue
                if len(current) + len(sent) <= chunk_size:
                    current += sent
                else:
                    if current:
                        chunks.append({"text": current.strip(), "chunk_index": idx})
                        idx += 1
                        current = current[-overlap:] + sent if overlap > 0 else sent
                    else:
                        for i in range(0, len(sent), chunk_size - overlap):
                            chunks.append(
                                {"text": sent[i : i + chunk_size].strip(), "chunk_index": idx}
                            )
                            idx += 1
                        current = ""
        elif len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para) if current else para
        else:
            if current:
                chunks.append({"text": current.strip(), "chunk_index": idx})
                idx += 1
                current = para
            else:
                current = para

    if current:
        chunks.append({"text": current.strip(), "chunk_index": idx})

    return chunks


# ---------------------------------------------------------------------------
# 普通文本：旧逻辑回归
# ---------------------------------------------------------------------------


def test_chunk_plain_text_regression_default_size():
    """普通中文文本（无标题/代码块/表格）分块结果与旧逻辑完全一致。"""
    text = """症状描述：服务接口大面积超时，错误日志出现连接池耗尽提示。连接池监控 active 连接数等于最大池大小。

常见根因：配置变更错误缩小连接池。慢 SQL 或未释放连接导致连接被长期占用。突发流量超过连接池容量。

排查步骤：第一步查看告警。第二步查看指标。第三步查看日志。第四步查看变更记录。"""
    expected = _legacy_chunk_text(text, 512, 64)
    actual = chunk_text(text)
    assert [c["text"] for c in actual] == [c["text"] for c in expected]
    assert [c["chunk_index"] for c in actual] == [c["chunk_index"] for c in expected]
    # 无标题时 section_title 为空字符串（兼容旧结构）
    assert all(c["section_title"] == "" for c in actual)
    assert all(c["table_comment"] == "" for c in actual)


def test_chunk_plain_text_regression_small_size():
    """小 chunk_size 触发句子切分 + overlap，结果仍与旧逻辑一致。"""
    text = (
        "连接池参数调整涉及多个维度。最大连接数决定并发上限。最小空闲连接数影响预热速度。"
        "等待超时时间控制排队时长。连接回收周期影响空闲释放。"
        "\n\n"
        "慢 SQL 排查需要结合执行计划。全表扫描是常见性能瓶颈。索引失效会导致扫描行数激增。"
    )
    expected = _legacy_chunk_text(text, 60, 10)
    actual = chunk_text(text, chunk_size=60, overlap=10)
    assert [c["text"] for c in actual] == [c["text"] for c in expected]
    assert all(c["section_title"] == "" for c in actual)


def test_chunk_plain_text_hard_cut_fallback():
    """无句号超长段落：按 chunk_size/overlap 硬切兜底，结果与旧逻辑一致。"""
    text = "甲" * 2000
    expected = _legacy_chunk_text(text, 512, 64)
    actual = chunk_text(text)
    assert [c["text"] for c in actual] == [c["text"] for c in expected]
    assert [c["chunk_index"] for c in actual] == [c["chunk_index"] for c in expected]
    assert len(actual) >= 3
    assert all(len(c["text"]) <= 512 for c in actual)
    assert all(set(c["text"]) == {"甲"} for c in actual)
    assert all(c["section_title"] == "" for c in actual)


def test_chunk_metadata_merge():
    """外部 metadata 完整保留，且不覆盖结构感知字段。"""
    text = "## 标题\n\n普通段落内容。\n\n```\ncode\n```"
    metadata = {
        "doc_id": "d1",
        "title": "t",
        "source": "knowledge/ops/d1.md",
        "category": "ops",
        "security_group": ["user", "agent", "admin"],
    }
    chunks = chunk_text(text, metadata=metadata)
    assert chunks
    for c in chunks:
        for k, v in metadata.items():
            assert c[k] == v
        assert "text" in c and "chunk_index" in c


def test_chunk_empty_text():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


# ---------------------------------------------------------------------------
# 代码块：整体保留
# ---------------------------------------------------------------------------


def test_chunk_code_block_200_lines_java_kept_whole():
    """200 行 Java fenced 代码块整体保留为一个 chunk，不被字符硬切。

    标题行后紧跟代码块：标题并入块首（孤儿标题修复），代码块仍整体保留。
    """
    code_lines = [f'    log.info("处理订单 {i}");' for i in range(200)]
    java_code = "\n".join(code_lines)
    doc = f"## 排查步骤\n\n```java\n{java_code}\n```"
    chunks = chunk_text(doc)
    # 标题行并入块首，只有 1 个 chunk（无孤儿标题 chunk）
    assert len(chunks) == 1
    code_chunk = chunks[0]
    assert code_chunk["text"].startswith("## 排查步骤\n```java\n")
    assert code_chunk["text"].endswith("\n```")
    assert code_chunk["text"] == f"## 排查步骤\n```java\n{java_code}\n```"
    assert len(code_chunk["text"]) > 512  # 允许超过 chunk_size，证明未硬切
    assert code_chunk["section_title"] == "排查步骤"
    # 首尾行完整（未被切碎）
    assert code_lines[0] in code_chunk["text"]
    assert code_lines[-1] in code_chunk["text"]


def test_chunk_code_block_no_heading():
    """无标题时代码块 section_title 为空字符串。"""
    code = "\n".join(f"x = {i}" for i in range(50))
    chunks = chunk_text(f"```python\n{code}\n```")
    assert len(chunks) == 1
    assert chunks[0]["text"] == f"```python\n{code}\n```"
    assert chunks[0]["section_title"] == ""


def test_chunk_code_block_after_text_flushes_buffer():
    """代码块前的正文先成块，代码块整体在后，顺序正确。"""
    doc = "前置说明文字。\n\n```\ncode_line_1\ncode_line_2\n```\n\n后置文字。"
    chunks = chunk_text(doc)
    texts = [c["text"] for c in chunks]
    assert "前置说明文字。" in texts[0]
    assert any(t == "```\ncode_line_1\ncode_line_2\n```" for t in texts)
    assert texts[-1] == "后置文字。"


# ---------------------------------------------------------------------------
# 标题元数据
# ---------------------------------------------------------------------------


def test_chunk_heading_section_title():
    """`## 排查步骤` 下 chunk 的 section_title == '排查步骤'。"""
    doc = """# 排查手册

## 症状

服务接口大面积超时，错误日志出现连接池耗尽提示。

## 排查步骤

1. 查看告警
2. 查看指标
3. 查看日志
"""
    chunks = chunk_text(doc)
    assert chunks
    for c in chunks:
        if "查看告警" in c["text"]:
            assert c["section_title"] == "排查步骤"
        if "连接池耗尽提示" in c["text"]:
            assert c["section_title"] == "症状"
        if c["text"].startswith("# 排查手册"):
            # 文档标题行后紧跟 `## 症状`（无正文）：标题并入首个节的文本流
            # （孤儿标题修复），chunk 归属其内容所在的最深节
            assert c["section_title"] == "症状"
            assert "连接池耗尽提示" in c["text"]


def test_chunk_heading_no_cross_section_mix():
    """跨章节不混块：各 chunk 只含本章节内容，section_title 与内容所属章节一致。"""
    sec1 = "第一章节的内容段落。" * 30
    sec2 = "第二章节的内容段落。" * 30
    doc = f"## 第一章\n\n{sec1}\n\n## 第二章\n\n{sec2}\n\n"
    chunks = chunk_text(doc, chunk_size=100, overlap=10)
    assert len(chunks) >= 4  # 两章各自切出多个 chunk
    for c in chunks:
        if "第一章节" in c["text"]:
            assert c["section_title"] == "第一章", c
        if "第二章节" in c["text"]:
            assert c["section_title"] == "第二章", c
        # 单个 chunk 不允许同时含两章内容
        assert not ("第一章节" in c["text"] and "第二章节" in c["text"])


# ---------------------------------------------------------------------------
# 表格：不拆行
# ---------------------------------------------------------------------------


def _sample_table() -> str:
    return "\n".join(
        [
            "| 字段 | 类型 | 说明 |",
            "|---|---|---|",
            "| id | bigint | 主键，订单唯一标识 |",
            "| order_no | varchar(64) | 订单编号，全局唯一 |",
            "| status | int | 订单状态：0-待支付 1-已支付 2-已发货 3-已完成 4-已关闭 |",
            "| create_time | datetime | 创建时间 |",
            "| amount | decimal(10,2) | 订单金额，单位元 |",
        ]
    )


def test_chunk_table_kept_whole():
    """短表格整表保留为一个 chunk，单元格内容完整。"""
    table = _sample_table()
    chunks = chunk_text(table)
    assert len(chunks) == 1
    assert chunks[0]["text"] == table
    assert "| status | int | 订单状态：0-待支付 1-已支付 2-已发货 3-已完成 4-已关闭 |" in chunks[0]["text"]
    assert chunks[0]["section_title"] == ""


def test_chunk_table_section_title():
    """表格 chunk 继承最近标题，且 text 前拼标题行（表格带语义上下文）。"""
    doc = f"## 数据字典\n\n{_sample_table()}"
    chunks = chunk_text(doc)
    # 标题后紧跟表格：标题并入块首（孤儿标题修复），text 以标题行开头
    table_chunks = [c for c in chunks if "| 字段 | 类型 | 说明 |" in c["text"]]
    assert table_chunks
    assert all(c["text"].startswith("## 数据字典") for c in table_chunks)
    assert all(c["section_title"] == "数据字典" for c in table_chunks)


def test_chunk_table_overlong_split_by_row():
    """超长表格按行边界切：单元格内容完整，不切断行。"""
    rows = [f"| row_{i:02d} | " + "x" * 100 + " |" for i in range(10)]
    table = "\n".join(rows)
    assert len(table) > 512
    chunks = chunk_text(table)
    assert len(chunks) >= 2
    # 每个 chunk 的每一行都是完整原始行（未被切断的单元格）
    for c in chunks:
        for line in c["text"].splitlines():
            assert line in rows, f"单元格被切断: {line!r}"
    # 按序拼接后与原表完全一致（只在行边界切）
    assert "\n".join(c["text"] for c in chunks) == table


# ---------------------------------------------------------------------------
# 孤儿标题：标题行并入结构块（表格/fence）
# ---------------------------------------------------------------------------


def test_markdown_heading_followed_by_table_no_orphan():
    """标题后紧跟表格：标题行并入表格块，不产生仅含标题的孤儿 chunk。"""
    doc = "## 订单状态\n\n| 状态值 | 含义 |\n|---|---|\n| 0 | 待付款 |"
    chunks = chunk_text(doc)
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("## 订单状态")
    assert "| 0 | 待付款 |" in chunks[0]["text"]
    # 不存在仅含标题行的孤儿 chunk
    assert not any(c["text"].strip() == "## 订单状态" for c in chunks)
    assert chunks[0]["section_title"] == "订单状态"


def test_markdown_table_splits_keep_heading_prefix():
    """标题后跟超长表格：按行边界切出的每个块都以标题行开头。"""
    rows = [f"| row_{i:02d} | " + "x" * 100 + " |" for i in range(12)]
    table = "\n".join(rows)
    assert len(table) > 512
    doc = f"## 订单状态\n\n{table}"
    chunks = chunk_text(doc)
    assert len(chunks) >= 2
    for c in chunks:
        assert c["text"].startswith("## 订单状态"), c["text"][:60]
        assert c["section_title"] == "订单状态"


def test_markdown_heading_followed_by_fence_no_orphan():
    """标题后紧跟 fenced 代码块：标题行并入代码块，无孤儿标题。"""
    doc = "## 代码\n\n```python\nx = 1\n```\n"
    chunks = chunk_text(doc)
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("## 代码")
    assert "```python" in chunks[0]["text"]
    assert "x = 1" in chunks[0]["text"]
    assert not any(c["text"].strip() == "## 代码" for c in chunks)
    assert chunks[0]["section_title"] == "代码"


def test_markdown_heading_plain_text_merged():
    """标题后跟普通文本：标题与正文同一 chunk，跨章节不混块。"""
    doc = "## 第一章\n\n正文段落内容。\n\n## 第二章\n\n另一段内容。"
    chunks = chunk_text(doc)
    assert chunks[0]["text"].startswith("## 第一章")
    assert "正文段落内容。" in chunks[0]["text"]
    assert chunks[0]["section_title"] == "第一章"
    # 跨章节隔离：单个 chunk 不允许同时含两章内容
    assert all(
        not ("第一章" in c["text"] and "第二章" in c["text"]) for c in chunks
    )


def _no_orphan_heading_chunk(chunks) -> None:
    """断言不存在「仅含标题行」的孤儿 chunk。"""
    for c in chunks:
        lines = [l for l in c["text"].splitlines() if l.strip()]
        assert not (1 <= len(lines) <= 2 and all(l.strip().startswith("#") for l in lines)), (
            f"孤儿标题 chunk: {c['text']!r}"
        )


def test_markdown_heading_followed_by_heading_no_orphan():
    """标题后紧跟标题（无正文）：上一级标题并入下一节文本流，不产生孤儿标题。"""
    doc = (
        "# 排查手册\n\n"
        "## 症状\n\n"
        "服务接口大面积超时，错误日志出现连接池耗尽提示。\n\n"
        "## 排查步骤\n\n"
        "1. 查看告警\n2. 查看指标\n3. 查看日志\n"
    )
    chunks = chunk_text(doc)
    _no_orphan_heading_chunk(chunks)
    # 文档标题行并入首个节的文本流
    title_chunks = [c for c in chunks if c["text"].startswith("# 排查手册")]
    assert title_chunks
    assert "连接池耗尽提示" in title_chunks[0]["text"]


def test_markdown_heading_followed_by_overlong_paragraph_no_orphan():
    """标题后跟超长段落（无标点、无结构元素）：标题并入正文块首，不单独成 chunk。"""
    content = "\n".join(["功能 | 完成", "----|----"] + [f"集成模块{i:02d} | ✔" for i in range(60)])
    assert len(content) > 512
    doc = f"## 框架搭建\n\n{content}"
    chunks = chunk_text(doc)
    assert chunks[0]["text"].startswith("## 框架搭建")
    _no_orphan_heading_chunk(chunks)
    assert chunks[0]["section_title"] == "框架搭建"


# ---------------------------------------------------------------------------
# SQL DDL：按表切块
# ---------------------------------------------------------------------------

_SQL_DDL = """SET NAMES utf8mb4;

DROP TABLE IF EXISTS `oms_order`;

CREATE TABLE `oms_order` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `order_sn` varchar(64) DEFAULT NULL COMMENT '订单编号',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=100 DEFAULT CHARSET=utf8mb4 COMMENT='订单表';

CREATE TABLE oms_order_item (
  id bigint(20) NOT NULL,
  order_id bigint(20) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单商品表';
"""


def test_chunk_sql_ddl_by_table():
    """CREATE TABLE 独立成块：section_title=表名，table_comment=表级注释，DDL 不切碎。"""
    chunks = chunk_sql_ddl(_SQL_DDL)
    assert len(chunks) == 2

    first = chunks[0]
    assert first["section_title"] == "oms_order"
    assert first["table_comment"] == "订单表"
    assert "CREATE TABLE `oms_order`" in first["text"]
    assert "COMMENT='订单表'" in first["text"]
    # 整表 DDL 完整保留（结尾注释行未被切掉）
    assert ") ENGINE=InnoDB AUTO_INCREMENT=100 DEFAULT CHARSET=utf8mb4 COMMENT='订单表';" in first["text"]
    assert "SET NAMES utf8mb4" in first["text"]  # 表前杂项并入首个 chunk
    assert "DROP TABLE IF EXISTS `oms_order`" in first["text"]

    second = chunks[1]
    assert second["section_title"] == "oms_order_item"
    assert second["table_comment"] == "订单商品表"
    assert "CREATE TABLE oms_order_item" in second["text"]
    # 表 2 不混入表 1 的内容
    assert "主键ID" not in second["text"]


def test_chunk_text_auto_detect_sql():
    """chunk_text 自动识别裸 SQL DDL 并按表切块。"""
    chunks = chunk_text(_SQL_DDL, metadata={"doc_id": "mall", "category": "mall"})
    assert len(chunks) == 2
    assert chunks[0]["section_title"] == "oms_order"
    assert chunks[0]["table_comment"] == "订单表"
    assert chunks[0]["doc_id"] == "mall"
    assert chunks[1]["section_title"] == "oms_order_item"


def test_chunk_sql_ddl_schema_and_if_not_exists():
    """schema 前缀 / IF NOT EXISTS 的表名提取正确。"""
    sql = (
        "CREATE TABLE IF NOT EXISTS mall.oms_order (\n"
        "  id bigint(20) NOT NULL\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商城订单表';"
    )
    chunks = chunk_sql_ddl(sql)
    assert len(chunks) == 1
    assert chunks[0]["section_title"] == "oms_order"
    assert chunks[0]["table_comment"] == "商城订单表"


def test_chunk_sql_ddl_wide_table_not_cut():
    """超长 DDL（>512 字符）整表保留为一个 chunk，不按字符硬切。"""
    cols = "\n".join(f"  `col_{i:03d}` varchar(64) DEFAULT NULL COMMENT '字段{i}'" for i in range(30))
    sql = (
        f"CREATE TABLE `oms_order` (\n  `id` bigint(20) NOT NULL,\n{cols}\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单表';"
    )
    assert len(sql) > 512
    chunks = chunk_sql_ddl(sql)
    assert len(chunks) == 1
    # table_comment 注入 text 开头（检索语义增强：SQL 词面与自然语言查询差距大）
    assert chunks[0]["text"].startswith("【订单表】")
    assert chunks[0]["section_title"] == "oms_order"
    assert chunks[0]["table_comment"] == "订单表"
    assert "`col_029` varchar(64) DEFAULT NULL COMMENT '字段29'" in chunks[0]["text"]  # 末列完整


def test_chunk_sql_ddl_empty():
    assert chunk_sql_ddl("") == []
    assert chunk_sql_ddl("   \n") == []


def test_chunk_yaml_injects_filename_prefix():
    """YAML 配置 chunk 注入文件名前缀（LLM 语义锚点，与 SQL 表注释同构）。

    application-prod.yml 的 datasource 段此前完全裸（无文件名），生成时被
    LLM 当成"未提及"→ answer_relevancy=0；注入【application-prod.yml】后
    embedding 与生成都能感知配置归属文件。
    """
    yaml_text = (
        "spring:\n"
        "  datasource:\n"
        "    url: jdbc:mysql://db:3306/mall\n"
        "    username: reader\n"
        "    password: '123456'\n"
    )
    chunks = chunk_text(
        yaml_text,
        metadata={
            "doc_id": "config/application-prod",
            "title": "application-prod.yml",
            "source": "kb",
            "category": "mall",
            "security_group": ["user"],
        },
    )
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("【application-prod.yml】")
    assert "jdbc:mysql://db:3306/mall" in chunks[0]["text"]


def test_chunk_yaml_detects_via_source_when_title_bare():
    """title 被调用方去掉扩展名时（如 seed 脚本 title="application-prod"），
    通过 source/doc_id 路径后缀仍识别为 YAML（此前 title 无 .yml 后缀导致
    注入逻辑从未触发，库里一直是裸配置块）。"""
    yaml_text = (
        "spring:\n"
        "  datasource:\n"
        "    url: jdbc:mysql://db:3306/mall\n"
    )
    chunks = chunk_text(
        yaml_text,
        metadata={
            "doc_id": "config/application-prod",
            "title": "application-prod",
            "source": "knowledge/mall/config/application-prod.yml",
            "category": "mall",
            "security_group": ["user"],
        },
    )
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("【application-prod.yml】")
    assert "jdbc:mysql://db:3306/mall" in chunks[0]["text"]


def test_chunk_yaml_no_prefix_when_markdown():
    """带 Markdown 标题的文本不做 YAML 前缀注入（非裸配置）。"""
    md_text = "# 部署说明\n\nspring:\n  datasource:\n    url: x\n"
    chunks = chunk_text(
        md_text,
        metadata={"doc_id": "doc", "title": "doc.md", "source": "kb",
                  "category": "mall", "security_group": ["user"]},
    )
    assert all(not c["text"].startswith("【doc") for c in chunks)
