"""知识库质量校验器：跨文档近似重复 / 文档内重复段落 / 空或超短 chunk 检测。

对应 RAG 排查顺序第一步「文档质量」：知识库未更新或内容矛盾前，先找
近似重复与碎片化 chunk（文章数据源维度：重复内容会让检索结果冗余、语义缓存失效）。

用法（backend/ 下）：
    venv\\Scripts\\python.exe scripts/check_kb_quality.py [--root <知识库目录>]

- 默认扫仓库根 knowledge/（mall/ + ops/ + 其他子目录，递归）
- 支持 .md/.txt/.sql/.yml/.pdf/.docx（复用 app.core.rag.parsers）
- 检测项：
  1) 空 / 超短 chunk（< min_chars 且非表结构块）→ fragment
  2) 文档内近似重复段落 → duplicate_inline
  3) 跨文档近似重复 chunk（来源不同却能互相命中）→ duplicate_cross
- 近似重复判定：embedding 余弦 > 阈值（默认 0.92），复用项目 bge 模型
  （embed_sync；嵌入失败时降级为 Jaccard 文本相似度，不阻断检查）
- 发现非 info 级问题退出码 1（可供 CI 门槛）；仅 info 时为 0

注意：PDF/DOCX 解析失败的文件会跳过并记 warning（与 seed 一致，不误报）。
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys

from app.core.rag.chunking import chunk_text
from app.core.rag.embedding import embed_sync
from app.core.rag.parsers import extract_text

logger = logging.getLogger(__name__)

# knowledge/ 位于仓库根，脚本从 backend/ 运行时需回退两级
_DEFAULT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge")

_SUPPORTED_EXTS = (".md", ".txt", ".sql", ".yml", ".pdf", ".docx")

# 参与两两比较的 chunk 上限（O(n^2)；超出时仅比较前 N 个，避免脚本跑太久）
_MAX_CHUNKS = 400
# 空/超短 chunk 阈值（字符数）
_MIN_CHARS = 20


def _cosine(a: list[float], b: list[float]) -> float:
    import numpy as np

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _jaccard_chars(a: str, b: str) -> float:
    """文本字符集合 Jaccard（embedding 不可用时的降级相似度）。"""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _load_chunks(root_dir: str) -> tuple[list[dict], list[dict]]:
    """加载并切分全部文档，返回 (chunks, parse_issues)。"""
    chunks: list[dict] = []
    issues: list[dict] = []
    for path in sorted(glob.glob(os.path.join(root_dir, "**", "*"), recursive=True)):
        if not os.path.isfile(path):
            continue
        if not path.lower().endswith(_SUPPORTED_EXTS):
            continue
        if os.path.basename(path).lower() == "sources.md":
            continue  # 取材说明，非知识内容
        rel = os.path.relpath(path, root_dir).replace("\\", "/")
        doc_id = os.path.splitext(rel)[0]
        title = os.path.splitext(os.path.basename(path))[0]
        try:
            text = extract_text(path)
        except Exception as e:
            issues.append(
                {"level": "warning", "kind": "unparsable", "msg": f"{rel} 解析失败: {e}"}
            )
            continue
        if not text.strip():
            issues.append(
                {"level": "warning", "kind": "empty_doc", "msg": f"{rel} 为空文档"}
            )
            continue
        for c in chunk_text(text, metadata={"doc_id": doc_id, "title": title}):
            c["doc_id"] = doc_id
            c["title"] = title
            chunks.append(c)
    return chunks, issues


def _similarity(a: list[float], b: list[float], ta: str, tb: str) -> float:
    """优先 cosine；向量缺失时降级 Jaccard。"""
    if a is not None and b is not None and len(a) == len(b) and a and b:
        return _cosine(a, b)
    return _jaccard_chars(ta, tb)


def check_kb_quality(
    root_dir: str,
    threshold: float = 0.92,
    min_chars: int = _MIN_CHARS,
    embed_fn=None,
) -> list[dict]:
    """检查知识库质量，返回问题列表（每条含 level/kind/msg）。

    embed_fn 可注入（测试或跳过模型加载）；缺省用项目 embed_sync。
    """
    if embed_fn is None:
        embed_fn = embed_sync
    chunks, issues = _load_chunks(root_dir)

    # 1) 空 / 超短 chunk（表结构块允许短）
    for c in chunks:
        text = c.get("text", "").strip()
        if c.get("table_comment"):
            continue
        if len(text) < min_chars:
            issues.append(
                {
                    "level": "warning",
                    "kind": "fragment",
                    "msg": f"{c['doc_id']} 超短 chunk（{len(text)} 字）: {text[:30]!r}",
                }
            )

    limit = min(len(chunks), _MAX_CHUNKS)
    sampled = chunks[:limit]
    try:
        vectors = embed_fn([c.get("text", "") for c in sampled])
        if vectors is not None and len(vectors) == len(sampled):
            pass
        else:
            vectors = [None] * len(sampled)
            logger.warning("[CheckKB] embedding 返回异常，降级 Jaccard 相似度")
    except Exception as e:
        logger.warning("[CheckKB] embedding 失败（降级 Jaccard）: %s", e)
        vectors = [None] * len(sampled)

    # 2/3) 两两近似重复
    seen_pairs = set()
    duplicate_issues = 0
    for i in range(len(sampled)):
        ci = sampled[i]
        for j in range(i + 1, len(sampled)):
            cj = sampled[j]
            sim = _similarity(vectors[i], vectors[j], ci.get("text", ""), cj.get("text", ""))
            if sim < threshold:
                continue
            key = tuple(sorted((ci["doc_id"], cj["doc_id"])))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            same_doc = ci["doc_id"] == cj["doc_id"]
            issues.append(
                {
                    "level": "warning",
                    "kind": "duplicate_inline" if same_doc else "duplicate_cross",
                    "msg": (
                        f"{ci['doc_id']} 与 {cj['doc_id']} {'同文档重复段落' if same_doc else '近似重复（疑似过时内容并存）'}"
                        f"（相似度 {sim:.2f}，来源 {ci.get('title', '')} / {cj.get('title', '')}）"
                    ),
                }
            )
            duplicate_issues += 1
            if duplicate_issues >= 20:
                # 一对重复可能蔓延整个库（文档整体复制的极端情况），限 20 条避免刷屏
                issues.append(
                    {
                        "level": "info",
                        "kind": "duplicate_truncated",
                        "msg": f"重复检测到上限，剩余 {len(sampled) - i - 1} 个 chunk 未继续枚举",
                    }
                )
                return issues

    return issues


def _print_report(issues: list[dict]) -> int:
    """打印报告。返回退出码（有非 info 级问题 → 1）。"""
    print("===== 知识库质量检查 =====")
    if not issues:
        print("未发现问题；知识库结构健康。")
        return 0
    for it in issues:
        print(f"[{it['level']}] {it['msg']}")
    n_warn = sum(1 for it in issues if it["level"] != "info")
    print(f"\n共 {len(issues)} 条提示（{n_warn} 条需处理）")
    return 1 if n_warn else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="知识库质量检查")
    parser.add_argument("--root", default=_DEFAULT_ROOT, help="知识库根目录（默认仓库根 knowledge/）")
    parser.add_argument("--threshold", type=float, default=0.92, help="近似重复余弦阈值")
    parser.add_argument("--min-chars", type=int, default=_MIN_CHARS, help="超短 chunk 阈值")
    parser.add_argument("--no-embed", action="store_true", help="跳过 embedding（用 Jaccard 相似度）")
    args = parser.parse_args()

    embed_fn = None if not args.no_embed else lambda texts: None
    issues = check_kb_quality(
        args.root, threshold=args.threshold, min_chars=args.min_chars, embed_fn=embed_fn
    )
    return _print_report(issues)


if __name__ == "__main__":
    sys.exit(main())
