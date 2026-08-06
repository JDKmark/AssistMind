# phase6-retrieval-quality Spec

## Why

调研发现两个事实：

1. **当前检索链路在 5-chunk 知识库上空转**：知识库全量仅 4 篇手册共 3644 字符、chunk 后 5 块，而 VECTOR_TOP_K=40 / BM25_TOP_K=40 —— 两路检索每次都返回 100% 知识库，RRF 融合 / Jaccard 去重 / rerank 全部无筛选。评估中 context_recall=0.976 / precision=0.938 是**知识库过小的饱和假象**，25/25 条样本"命中全部 4 篇"，生成上下文 75% 是噪音。
2. **answer_relevancy ≈0.53 里含可修复的"上下文污染"成分**：全库注入导致回答跨文档串味（如慢 SQL 答案混入连接池内容），反向生成的问题聚焦噪音实体，相似度被进一步压低。之前归因的"语义特性"（0.4-0.6 天然区间）仍成立，但检索失配是叠加的可修复因素。

同时假设运维对象升级为 mall 商城（macrozheng/mall：Spring Boot 多模块，文档含中文长文教程（40-50% 代码块）、reference 笔记、mall.sql 表结构、application.yml 高密度短配置）。对照现状，链路结构性弱点会在 mall 大库下集中暴露：
- **BM25 中文单字 unigram 分词**（无 jieba），IDF 区分度近乎失效，代码/配置型文档下全是假阳性——系统性噪声源
- **Chunk 规则切分无结构感知**：长代码块被硬切、Markdown 标题结构丢弃、mall.sql 表结构被切碎
- **RRF 两路等权**（k=60），BM25 噪声得不到压制
- **top-k 与库规模失配**：小库全量注入、大库（几千 chunk）不够
- **embedding 512 token 截断**（bge-base-zh-v1.5 上限 512，chunk 512 字符顶满）

目标：修复检索链路结构性弱点（BM25 分词、结构感知 chunk、融合与 top-k 适配），建设 mall 知识库，用评估验证检索质量与 answer_relevancy 的真实改善。

## What Changes

新增：
- `knowledge/mall/`：mall 知识库源文档（README 部署章节、document/reference/*.md、部署教程、application.yml、mall.sql 表结构，按来源子目录）
- `backend/scripts/seed_mall_kb.py`：mall 文档灌库脚本（结构感知切分 + 向量化 + upsert）
- `backend/app/data/eval_mall_qa.json`：mall 运维问答评估集（≥10 条，取材自 mall 文档/典型问题）
- `backend/tests/unit/test_chunking.py`、`test_bm25_tokenize.py`、`test_eval_mall_data.py` 等单测

修改：
- `backend/app/core/rag/chunking.py`：Markdown 结构感知切分（标题层级 → section_title 元数据、fenced 代码块整块保留、表格整块、CREATE TABLE 语句为单元、超长硬切保留）
- `backend/app/core/rag/bm25.py`：中文词粒度分词（jieba）
- `backend/app/core/rag/engine.py`：融合与 top-k 适配（RRF 权重可配置、top-k 参数化）
- `backend/app/config.py`：chunk/融合相关新配置项（RRF 权重、top-k）
- `backend/scripts/seed_ops_kb.py`：适配新 chunk 接口（传结构元数据）
- `backend/pyproject.toml`：新增 jieba 依赖

## Impact

- 兼容约束：`chunk_text` 返回结构保留 `{text, chunk_index, ...}`（新增 section_title 等元数据字段不破坏调用方）；检索接口返回形状不变（前端无感）；BM25/engine 对外签名不变
- 行为变化：检索不再全库注入（命中条数 < 库容量），生成上下文纯度提升 —— 预期 context_precision 与 answer_relevancy 改善、评估数字更真实
- 依赖变化：新增 jieba（纯 Python，无重依赖）
- 评估基准：25 条旧数据集保留用于回归对比；新增 mall 数据集
- 风险：分词/切分改动影响全链路检索 → 用旧数据集回归（context_* 不得下降）+ 单测覆盖

## ADDED Requirements

### Requirement: 结构感知 Chunk 切分
系统 SHALL 在 chunk_text 中识别 Markdown 结构：标题层级（#/##/###）作为 section_title 元数据、fenced 代码块（``` 包裹）整体保留不切分、表格行不拆分、超过硬限的单元按 512/64 兜底切分。

#### Scenario: 长代码块切分
- WHEN 文档含 200 行 Java 代码块且超过 chunk 上限
- THEN 该代码块作为一个整体 chunk 保留（允许超限），不按句号/字符硬切

#### Scenario: 标题结构保留
- WHEN 文档含 `## 排查步骤` 章节
- THEN 该章节 chunk 带 section_title="排查步骤" 元数据，跨章节不混块

### Requirement: mall.sql 表级切块
系统 SHALL 将 mall.sql 按 CREATE TABLE 语句切分为独立 chunk，每块带表名与表级 COMMENT 元数据。

#### Scenario: 表结构入库
- WHEN 灌入 mall.sql
- THEN 每张表（cms_/oms_/ums_/pms_/sms_ 前缀）一个 chunk，可被"订单表结构"类问题检索命中

### Requirement: BM25 中文词粒度分词
系统 SHALL 将 BM25 分词从单字 unigram 升级为中文词粒度（jieba），代码/配置标识符保留整词（order_item / user_id 不拆成泛词）。

#### Scenario: 中文查询命中
- WHEN 查询"连接池耗尽"
- THEN BM25 按词（连接池/耗尽）计 IDF，命中相关文档的得分显著高于无关文档（区分度用单元测试断言：相关 vs 无关文档得分差）

### Requirement: 检索不再全库注入
系统 SHALL 在知识库规模大于 top-k 时，单次检索返回的子集（两路融合后 ≤8 条进生成器），且评估样本的"命中文档数"小于知识库 chunk 总数。

#### Scenario: mall 库检索
- WHEN 在 mall 知识库（>50 chunk）上运行评估
- THEN 每条样本命中文档数 < 知识库 chunk 总数，且至少 1 条样本命中数 ≤3（存在筛选）

### Requirement: 融合与 top-k 可调
系统 SHALL 支持 RRF 权重配置（向量/BM25 分别可加权）与 top-k 参数调优（配置项），默认值基于修复后回归测试选定。

#### Scenario: 权重配置
- WHEN 修改配置（如 BM25 权重 0.8）
- THEN 融合结果排序随权重变化，检索接口行为不变

### Requirement: mall 知识库与评估集
系统 SHALL 提供 mall 知识库灌库脚本与 ≥10 条 mall 运维问答评估集（部署/配置/故障排查类，ground_truth 锚定源文档）。

#### Scenario: 灌库
- WHEN 执行 seed_mall_kb.py
- THEN mall 文档按结构感知切分写入 Qdrant，chunk 数 > 50，含 section_title 元数据

#### Scenario: 评估
- WHEN 运行 run_eval.py 含 mall 评估集
- THEN 输出 context_*/answer_relevancy，且旧 25 条数据集指标不回退（context_recall 常规 ≥0.95）

### Requirement: 单测
系统 SHALL 为 chunking / bm25 / 融合 / 数据集新增单测（mock 外部依赖，`-k "not integration"` 可跑）。

#### Scenario: 测试覆盖
- WHEN 运行 pytest
- THEN 覆盖：代码块不切分、标题元数据、SQL 表级切块、BM25 词分词区分度、RRF 权重生效、eval_mall_qa.json 格式

## MODIFIED Requirements

（无）

## REMOVED Requirements

（无）
