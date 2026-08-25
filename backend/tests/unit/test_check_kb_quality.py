"""知识库质量校验器（check_kb_quality）单元测试。

用 fake embedding（按文本哈希生成向量：相同文本 → 相同向量 cos=1）替代
项目 bge 模型，避免单测加载模型。覆盖：
1. 跨文档近似重复 → duplicate_cross
2. 空 / 超短 chunk → fragment
3. 空文档 → empty_doc
4. 干净知识库 → 无问题
5. embedding 不可用（返回 None）→ 降级 Jaccard 仍可检出重复
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_kb_quality import check_kb_quality  # noqa: E402

_EXTRA = "这是一段用于填充的、内容足够长的旁支说明文字，用于保证整体 chunk 足够长。" * 3


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """按文本 MD5 生成向量：相同文本 → 相同向量（cos=1），不同文本近似正交。"""
    vecs = []
    for t in texts:
        d = hashlib.md5(t.encode("utf-8")).digest()
        vecs.append([(b - 127) / 127.0 for b in d])
    return vecs


def _kinds(issues: list[dict]) -> list[str]:
    return [it["kind"] for it in issues]


def test_cross_document_duplicate_detected(tmp_path):
    """两个文档内容几乎相同 → 检出跨文档近似重复。"""
    (tmp_path / "a.md").write_text(
        f"商品退货政策说明：签收后 7 天内可无理由退货。{_EXTRA}", encoding="utf-8"
    )
    (tmp_path / "b.md").write_text(
        f"商品退货政策说明：签收后 7 天内可无理由退货。{_EXTRA}", encoding="utf-8"
    )
    issues = check_kb_quality(str(tmp_path), embed_fn=_fake_embed)
    assert "duplicate_cross" in _kinds(issues)
    dup = next(it for it in issues if it["kind"] == "duplicate_cross")
    assert "a" in dup["msg"] and "b" in dup["msg"]


def test_empty_doc_detected(tmp_path):
    """空文档 → empty_doc。"""
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    issues = check_kb_quality(str(tmp_path), embed_fn=_fake_embed)
    assert "empty_doc" in _kinds(issues)


def test_fragment_detected(tmp_path):
    """超短 chunk → fragment。"""
    (tmp_path / "tiny.md").write_text("短", encoding="utf-8")
    issues = check_kb_quality(str(tmp_path), min_chars=20, embed_fn=_fake_embed)
    assert "fragment" in _kinds(issues)


def test_clean_kb_no_issue(tmp_path):
    """无重复、无超短 chunk → 无问题。"""
    (tmp_path / "good.md").write_text(
        f"唯一文档内容，不与他人重复。{_EXTRA}", encoding="utf-8"
    )
    issues = check_kb_quality(str(tmp_path), min_chars=20, embed_fn=_fake_embed)
    assert issues == []


def test_embedding_unavailable_degrades_to_jaccard(tmp_path):
    """embedding 不可用（返回 None）→ 降级 Jaccard 文本相似度，仍可检出重复。"""
    (tmp_path / "a.md").write_text(
        f"完全相同的重复文档内容。{_EXTRA}", encoding="utf-8"
    )
    (tmp_path / "b.md").write_text(
        f"完全相同的重复文档内容。{_EXTRA}", encoding="utf-8"
    )
    issues = check_kb_quality(
        str(tmp_path), threshold=0.95, embed_fn=lambda texts: None
    )
    assert "duplicate_cross" in _kinds(issues)
