# 429 限流根因定位、修复尝试与回滚（2026-09-14）

> 触发问题：「使用商汤的 API key，是不是你并发太多了？」
> **最终结论**：并发控制确有缺陷，但**不是主因**；主因是**限速器的 RPM 未计入重试，实际出站约为设定值的 2 倍**。
> 第一轮「包装客户端」式修复**引入了硬回归并已完整回滚**——详见第 5 节。

---

## 1. 结论先行

商汤 key **已经在用**（`backend/.env` 自 21:59 起 `LLM_PROVIDER=sensenova`，「切商汤」实际只改了这一行）。
`run_eval.py` 三处缺陷确实存在（第 2 节），但**实测证明它们不是 429 的主因**：
用与 v5 完全相同的 10 rpm 设置重跑，生成阶段仍然 67 次 `rpm exhausted`，42/42 全部降级。
真正的主因见第 5.2 节「根因 B」。

---

## 2. 根因（读代码确认，非推测）

`backend/scripts/run_eval.py`

| # | 缺陷 | 后果 |
|---|---|---|
| ① | `_LimitedCompletions.create()` 旧实现是「先 `inner.create()`，再判断返回值是否 awaitable」 | 同步调用一进 `create` 就已发出 HTTP，限速器只延迟取结果，**拦不住突发** |
| ② | `build_ragas_llm()` 只在 `async_client=True` 时套 `_LimitedClient` | **ragas `aevaluate` 用的同步判分客户端完全不受限速**，只靠批间 sleep；压不住 ragas 批内按指标并行调度 |
| ③ | `except` 回落路径返回未包装的 `raw_client` | instructor 包装失败时**连限速一起丢**（v2/v6 日志均有「judge 调用记录包装与 instructor 不兼容」） |

并发形态：批内 2 条样本 × 3 项指标 ≈ 6 个 ragas 作业并行（日志可见 `Job[29]` / `Job[75]`），
每个作业再发 1–3 次 judge 调用 → 批内瞬时并发 ≈ 6–9，**且全部绕过限速器**。

---

## 3. 各次运行归属（关键澄清）

| 运行 | 时间 | Provider | 打到的网关 | 失败形态 |
|---|---|---|---|---|
| 0913 | 20:08 | deepseek | `api.deepseek.com` | `IncompleteOutputException`×65 |
| v2 | 22:08–22:19 | **sensenova** | **token.sensenova.cn** | **429 × 1308**（1232 rpm + 76 tpm/rpm），42/42 全缺分 |
| v3 | 22:23–22:30 | sensenova | token.sensenova.cn | 429×12，停 **8/42**，0 分 |
| v4 | 22:36–22:42 | sensenova | token.sensenova.cn | 429×16，停 **29/42**，0 分 |
| v5 | 22:44–22:51 | sensenova | token.sensenova.cn | 429×13，停 **9/42**，0 分（已开 10 rpm 全局限速） |
| v6 | 23:08–23:22 | **deepseek** | `api.deepseek.com` | **402 Insufficient Balance × 1059**，0×429 |

**v6 的 0×429 不能用来证明限速有效**——它根本没走商汤（日志 `评分 LLM: deepseek / deepseek-v4-flash`，
说明该次 shell 覆盖成 `LLM_PROVIDER=deepseek`），打到官方直连后因余额不足快速失败。

两条链路各有各的墙：**商汤＝限流（并发）**，**DeepSeek 官方＝余额（402）**，
且 `DEEPSEEK_BASE_URL=api.deepseek.com` + `DEEPSEEK_MODEL=deepseek-v4-flash` 属错配（官方无此模型名）。

---

## 4. 改动（改前 / 改后）—— ⚠️ 已回滚，勿据此认为代码处于修复态

> 下文记录的是**第一轮修复尝试**的内容。该改动导致重跑硬崩（第 5.3 节），
> **已从备份完整还原**。当前 `run_eval.py` 与改动前逐字节一致（SHA256 校验通过）。
> 保留本节仅为留痕；下一轮修复**不要**沿用「包装客户端」的思路。

**文件**：`backend/scripts/run_eval.py`
**备份**：`backend/scripts/run_eval.py.bak-20260914限速器修复前`（54,636 B，与改前一致）

| 位置 | 改前 | 改后 |
|---|---|---|
| `_LimitedCompletions.__init__` | 无 `is_async` 参数 | 增 `is_async: bool = False`，显式区分同步/异步 |
| `_LimitedCompletions.create` | 先 `inner.create()` 再判断 awaitable；同步路径**不排队** | 同步路径 `limiter.acquire()` **先于** `inner.create()` |
| `_LimitedChat` / `_LimitedClient` | 不透传 is_async | 透传至 `_LimitedCompletions` |
| `build_ragas_llm` 包装条件 | `if async_client and _LLM_LIMITER is not None:` | `if _LLM_LIMITER is not None:`（同步 + 异步都套） |
| `build_ragas_llm` 回落 | 返回未包装的 `raw_client`（丢限速） | 返回 `limited_fallback`（只丢 capture，保留限速） |

**测试**（已随回滚删除）：曾新增 `test_limited_client_throttles_sync_create_before_sending`。
回滚后 `pytest tests/unit/test_run_eval_p0_reporting.py -q` → **24 passed**（回到原始数量）。

**验证（离线，未产生 API 费用）**
```
venv\Scripts\python.exe -m py_compile scripts/run_eval.py        # exit 0
venv\Scripts\python.exe -m pytest tests/unit/test_run_eval_p0_reporting.py -q   # 回滚后 24 passed
```

---

## 5. 重跑结果：失败，改动已回滚（2026-09-14 07:46–08:07，exit 1）

### 5.1 结果

| 项 | 实测 |
|---|---|
| 生成阶段 | 42/42 完成，但 **42/42 全部触发降级** |
| 判分阶段 | 起步即崩，`判分批次` 0 行，**0 分数、无 JSON** |
| 429（`rpm exhausted`） | **67 次**，20 分钟内均匀分布（4.8 次/分） |
| 降级原因分布 | `embedding,qdrant`×21、`query_rewrite,embedding,qdrant`×10、`query_rewrite,embedding,qdrant,llm`×7、`embedding,qdrant,llm`×4 |
| 断路器 | embedding 断路器 Open（07:52 起），Ollama 断路器 Open → `无可用 provider`×40、模板兜底×11 |

### 5.2 两个独立根因（都**不是**限速器修复造成的）

**根因 A — embedding 全挂，向量检索被跳过 41 条**

`[Embedding] embed_async 失败（将降级为仅 BM25）: 502 Bad Gateway`

实测定位：**端口 3422 是 WorkBuddy 沙箱自带代理（`sandbox-cli.exe`）**。
商汤经该代理可达（返回 401，属正常未授权），但 **`huggingface.co` 经该代理一律 502** →
`SentenceTransformer(BAAI/bge-base-zh-v1.5)` 的 Hub 校验被拦 → embedding 失败 → 断路器 Open。

**绕过方法（已实测通过）**：

```
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

→ `embed_sync OK dims = 1 768`（从本地缓存加载；torch 2.11.0+cu128，CUDA 可用）。
**在本沙箱内跑任何依赖 HF Hub 的任务都必须带这两个变量。**

**根因 B — 限速器的 RPM 未计入重试，实际出站约为设定值的 2 倍**

`_GlobalRateLimiter` 只包在 `llm_factory.call_llm` / `stream_llm` 的**入口**；
`LLM_MAX_RETRIES=1` 触发的**内部重试不计入限速器**。
故 `EVAL_LLM_RPM=10` 的实际出站 ≈ 20 rpm，超过网关实测的 ~12 rpm 上限 →
持续、均匀分布的 `rpm exhausted`。这也解释了大面积降级与断路器 Open 的连锁。

### 5.3 我引入的回归（已完整回滚）

把 `_LimitedClient` 包装套到**同步**判分客户端后，ragas 的 instructor 适配器拒绝该对象：

```
ValueError: Failed to patch openai client with Instructor:
Client must be an instance of one of: OpenAI, AsyncOpenAI. Got: _LimitedClient
```

→ `build_ragas_llm` 抛 ValueError，进程 exit 1。
**原代码的 `raw_client` 回落是必需的**——为了保留限速而把回落换成包装对象是错的。

**回滚**：
- `run_eval.py` 从 `run_eval.py.bak-20260914限速器修复前` 完整还原，**SHA256 与备份一致**，54,636 B
- 删除新增用例；`pytest tests/unit/test_run_eval_p0_reporting.py -q` → **24 passed**
- 结论：**client 包装式限速与 ragas instructor 不兼容，此路不通**。若要继续在判分侧限速，
  只能就地替换真实 OpenAI 实例的 `chat.completions.create`，或降 `EVAL_LLM_RPM` 抵消重试放大。

---

## 6. 探针与冒烟验证（2026-09-14 14:34–14:54）

### 6.1 速率探针：**不是速率问题，是模型级积分池**

每档 8 次 `max_tokens=8` 极小请求、**无重试**：

| 速率 | 成功/失败 | 错误形态 |
|---|---|---|
| 12 rpm | 4 / 4 | `insufficient_quota` |
| 8 rpm | 4 / 4 | `rpm exhausted` + `insufficient_quota` |
| 5 rpm | 7 / 1 | `rpm exhausted` |
| 3 rpm | 3 / 5 | `rpm exhausted` + `insufficient_quota` |
| **2 rpm（30s 间隔，单独复测）** | **3 / 2** | `insufficient_quota` |

**失败率与速率非单调，且在 2 rpm 下仍失败 40%** → 瓶颈不是速率。

再按模型分档（6 rpm × 3）：

| 模型 | 结果 | 错误形态 |
|---|---|---|
| `deepseek-v4-flash`（**在用**） | 1 / 3 | **`insufficient_quota`** |
| `sensenova-6.8-flash-lite` | **3 / 3** | — |
| `deepseek-v4-pro` | **3 / 3** | — |
| `glm-5.2` | 2 / 3 | `insufficient_quota` |

持续负载确认（8 rpm × 12）：

| 模型 | 成功率 | 错误形态 |
|---|---|---|
| `sensenova-6.8-flash-lite` | 83%（10/12） | 纯速率 `code:'429'` |
| `deepseek-v4-pro` | 92%（11/12） | 纯速率 `rpm exhausted` |

**结论：`deepseek-v4-flash` 的积分池已耗尽**；同 key 下其他模型只受普通速率限制。
商汤文档提到「通用积分池 / Flash-Lite 专属积分池」两套额度，与此现象一致。
**降速无法解决——必须换模型或恢复该模型的额度。**

### 6.2 冒烟验证：两个修复都生效（4 条样本子集，未动原始评估集）

用 `rows[:4]` 逐字节复制到临时目录跑的冒烟子集（未写入仓库）：

| 验证项 | 结果 |
|---|---|
| embedding 修复 | ✅ `502 Bad Gateway` **0 次**、embedding 断路器 **从未 Open** |
| 降级原因 | ✅ 只剩 `query_rewrite,llm`，**`embedding`/`qdrant` 已消失** |
| 回滚是否彻底 | ✅ 走到判分阶段、打印失败行清单、**exit 0**（不再 ValueError / exit 1） |
| 向量检索 | ✅ Qdrant + BM25 均正常，SiliconFlow 重排正常（50/46/54 候选） |
| judge | ❌ 4/4 行失败，全部 `rpm exhausted`（judge 也用 `deepseek-v4-flash`） |

**即：工具链已修好，唯一剩下的阻塞是判分模型的积分池。**

---

## 7. 待决策：换哪个模型（**这是一次新的「换尺子」，需你拍板**）

`build_ragas_llm` 与 RAG 生成链**共用 `settings.llm_profile()` 同一个模型**，
所以改 `SENSENOVA_MODEL` 会**同时换掉「作答模型」和「判分模型」**——两者都变，属重大口径变更。

| 方案 | 实测速率 | 代价 / 风险 |
|---|---|---|
| **A. 换 `sensenova-6.8-flash-lite`** | 8 rpm 83% | 轻量、便宜、有专属积分池；但与 0913 基线（v4-flash）**不同族** |
| **B. 换 `deepseek-v4-pro`** | 8 rpm 92% | 与基线**同族**（deepseek-v4-*），最接近可比；但属旗舰，单价更高，成本需先确认 |
| **C. 等 `deepseek-v4-flash` 池恢复** | — | 保口径不变；恢复时间未知 |
| **D. 换供应商** | — | SiliconFlow key 可用（重排实测正常）；DeepSeek 官方需充值 |

**无论选哪个，本轮数字都不能与 8 月/0913 那组并列比较**，只能当回归用——
这与手册里要写的统一口径是自洽的（"换了 judge 等于换了把尺子"）。


