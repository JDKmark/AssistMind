"""诊断：链路逐步排查——recall<0.7 样本逐条对账（GT 全文 vs top-8 检索上下文 vs 子句命中）。"""
import asyncio
import json

from app.core.infra.qdrant import get_qdrant
from app.core.rag import engine
from app.core.rag.bm25 import get_bm25

DATASET = r"D:\2026\AssistMind\backend\app\data\eval_mall_qa.json"

# recall<0.7 的样本（ds5）
TARGETS = [
    "mall 项目在 Windows 环境下如何启动 mall-admin 后端模块？",
    "如何修改 mall-admin 的 Redis 连接配置？",
    "mall-search 中 Elasticsearch 的 cluster-name 修改后需要注意什么",
    "订单状态 0 到 5 分别代表什么意思？",
    "怎么升级会员？",
    "mall 的订单表 oms_order 是做什么的？包含哪些关键字段？",
]


async def main():
    qd = get_qdrant()
    await qd.connect()
    docs = await qd.scroll_all()
    get_bm25().build(docs)

    rows = json.load(open(DATASET, encoding="utf-8"))
    for q in TARGETS:
        row = next((r for r in rows if r["question"] == q), None)
        if not row:
            continue
        gt = row["ground_truth"]
        res = await engine.retrieve(q)
        contexts = res.get("contexts", [])
        print("=" * 72)
        print(f"Q: {q}")
        print(f"GT: {gt[:150]}")
        print(f"上下文 {len(contexts)} 条:")
        for i, c in enumerate(contexts, 1):
            print(f"  #{i} {c.get('doc_id')} :: {c.get('text', '')[:46]}")
        # 子句命中（按标点拆 GT）
        import re

        clauses = [x for x in re.split(r"[，。；]", gt) if len(x) > 4]
        hits = []
        for cl in clauses:
            key = cl[:8]
            hit = any(key in c.get("text", "") for c in contexts)
            hits.append((hit, cl[:30]))
        print("  GT 子句命中:", sum(1 for h, _ in hits if h), f"/{len(hits)}")
        for h, cl in hits:
            print(f"    {'✓' if h else '✗'} {cl}")


asyncio.run(main())
