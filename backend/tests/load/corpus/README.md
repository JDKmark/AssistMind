# 压测语料说明

## 来源（复用已有资产，不另造数据）

| 语料 | 文件 | 条数 | 用途 |
|---|---|---|---|
| 产品文档问答评估集 | `backend/app/data/eval_qa.json` | 25（含 4 条对抗） | S2 FAQ 未命中（完整 RAG） |
| 电商业务问答评估集 | `backend/app/data/eval_mall_qa.json` | 40（含 5 条对抗） | S2 / S5 故障注入 |
| 高频 FAQ（本目录约定） | `backend/tests/load/lib/corpus.py` `HIGH_FREQ_FAQ` | 5 | S1 缓存命中 |
| Agent 工具链问句 | `backend/tests/load/lib/corpus.py` `TASK_QUERIES` | 5 | S3 Agent 多轮 |

高频 FAQ 内容：退货政策、退货运费归属、优惠券叠加、发货时效、支付方式。

## 为什么用评估集当压测语料

1. **请求语义真实**：不是 `test-1-2-3` 这类假数据，长度分布、领域词汇、意图分布都接近线上。
2. **性能与质量双闸门**：压测后可直接用同一批问题复跑 RAGAS
   （`backend/scripts/run_eval.py`），验证「压力下触发的降级是否拖低了答案质量」。
   同一份语料承担两个闸门，避免维护两套数据集。

## 使用方式

```python
from lib.corpus import cache_hit_corpus, faq_miss_corpus, task_corpus

faq_miss_corpus().next()   # 轮询取评估集问题（默认打散，避免同一进程重复打同一条）
```

- `Corpus` 用 `itertools.cycle` 轮询，保证并发用户不会在同一时刻集中打同一条（除 S1 刻意重复）。
- 评估集缺失时降级为 `["你好"]`，不阻塞压测启动（`lib/corpus.py` 内 try/except）。

## 缓存与语料的关系

S1 依赖缓存命中，因此**压测前需要预热**：先用 `HIGH_FREQ_FAQ` 各打一轮
（或直接跑一段 S1），使 L1/L2 有数据。未预热时首轮表现为 miss，
会让 S1 的 TTFT 与总时长偏高（属于冷启动，不是稳定态）。

基线运行前的预热脚本：

```powershell
cd backend\tests\load
..\..\venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'.');from lib import auth;from lib.corpus import HIGH_FREQ_FAQ;from lib.sse_client import ask;t=auth.get_token('http://localhost:8002');[ask('http://localhost:8002',t,q) for q in HIGH_FREQ_FAQ]"
```
