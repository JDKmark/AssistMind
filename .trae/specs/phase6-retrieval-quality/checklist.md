# phase6-retrieval-quality Checklist

- [ ] `chunking.py` 支持 Markdown 结构：标题 → section_title 元数据；fenced 代码块整体保留；表格不拆行；超长单元 512/64 兜底
- [ ] mall.sql 按 CREATE TABLE 切块，带表名与 COMMENT 元数据
- [ ] `bm25.py` 用词粒度分词（jieba），"连接池耗尽"查询下相关文档得分显著高于无关文档（单测断言）
- [ ] RRF 权重与 top-k 可配置，权重变化改变融合排序（单测）
- [ ] `knowledge/mall/` 源文档按来源子目录就位；`seed_mall_kb.py` 灌库后 chunk 数 > 50 且含 section_title
- [ ] `eval_mall_qa.json` ≥10 条（含 1-2 条对抗），字段完整，ground_truth 锚定源文档
- [ ] 旧 25 条数据集回归：context_recall 常规 ≥0.95 不回退
- [ ] mall 评估集跑通，每条命中文档数 < 库 chunk 总数（检索筛选生效，不再全库注入）
- [ ] 新增单测（chunking/bm25/融合/数据集）全部通过，pytest -k "not integration" 全绿
- [ ] 既有测试无回归（后端 pytest + 前端 vitest）
- [ ] 无前端改动、无 TypeScript、无新增 UI 库
- [ ] AGENTS.md 补充：BM25 词粒度分词约定、结构感知 chunk 约定、jieba 依赖
