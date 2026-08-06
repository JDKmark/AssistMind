# phase6-retrieval-quality Tasks

- [ ] Task 1: BM25 中文词粒度分词（独立）
  - [ ] 1.1 pyproject.toml 加 jieba 依赖并安装
  - [ ] 1.2 `bm25.py` 分词升级：jieba 词粒度 + 英文/数字标识符整词保留（order_item、user_id 不拆泛词）
  - [ ] 1.3 单测 `test_bm25_tokenize.py`：中文词切分、"连接池耗尽"查询下相关文档得分显著高于无关文档（区分度断言）
  - [ ] 1.4 既有 RAG 检索测试回归（test_rag_engine / test_bm25）

- [ ] Task 2: 结构感知 Chunk 切分（独立，可与 Task 1 并行）
  - [ ] 2.1 `chunking.py`：Markdown 标题层级解析 → section_title 元数据；fenced 代码块（``` 包裹）整体保留不切分；表格行不拆分；超长单元按 512/64 兜底
  - [ ] 2.2 mall.sql 支持：按 CREATE TABLE 语句切块，带表名与表级 COMMENT 元数据（新函数或参数化）
  - [ ] 2.3 单测 `test_chunking.py`：代码块整体保留、标题元数据、跨章节不混块、SQL 表级切块
  - [ ] 2.4 `seed_ops_kb.py` 适配（传 section_title 等元数据进 payload）

- [ ] Task 3: 融合权重与 top-k 配置化（依赖 Task 1、2）
  - [ ] 3.1 config.py 加 RRF 权重（VECTOR_WEIGHT/BM25_WEIGHT）与 top-k（VECTOR_TOP_K/BM25_TOP_K 参数化，默认值经回归选定）
  - [ ] 3.2 `engine.py` 的 `_rrf_fuse` 支持权重；top-k 读配置
  - [ ] 3.3 单测：权重变化改变融合排序；默认配置下既有检索测试通过

- [ ] Task 4: mall 知识库与评估集（依赖 Task 2）
  - [ ] 4.1 取材 mall 文档（README 部署章节、document/reference/*.md、部署教程长文、application.yml、mall.sql）→ `knowledge/mall/` 按来源子目录
  - [ ] 4.2 新增 `scripts/seed_mall_kb.py`：结构感知切分 + 向量化 + upsert（参考 seed_ops_kb.py，支持 --reset）
  - [ ] 4.3 新增 `app/data/eval_mall_qa.json`：≥10 条 mall 运维问答（部署/配置/故障排查，ground_truth 锚定源文档，含 1-2 条对抗）
  - [ ] 4.4 单测 `test_eval_mall_data.py`：数据集格式校验
  - [ ] 4.5 执行 seed_mall_kb.py 实际灌库，确认 chunk 数 > 50 且含 section_title

- [ ] Task 5: 全量评估对比与回归（依赖 Task 1-4）
  - [ ] 5.1 旧 25 条数据集重跑：context_recall 常规 ≥0.95 不回退；对比修复前后 context_*/answer_relevancy
  - [ ] 5.2 mall 评估集跑通：输出 context_*/answer_relevancy；验证"命中文档数 < 库 chunk 总数"（检索筛选生效）
  - [ ] 5.3 前后端全量测试回归（pytest 非集成 + vitest）

# Task Dependencies
- Task 1、2 互不依赖，可并行
- Task 3 依赖 Task 1、2（权重/top-k 调优基于新分词与切分）
- Task 4 依赖 Task 2（结构感知切分是灌库前提）
- Task 5 依赖 Task 1-4
