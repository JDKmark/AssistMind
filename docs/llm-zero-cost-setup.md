# 零成本 LLM 方案（商汤免费档 + 本地 Ollama）

> 适用场景：无付费预算，需要长期跑通 FAQ / 评测链路。
> 结论先行：**免费档有 7 款模型，但只有 `sensenova-6.8-flash-lite` 独占一个积分池**。
> 其余文本模型（`glm-5.2` / `deepseek-v4-flash` / `kimi-k3`）共用「通用积分池」，
> 实测该池对本账号长期不可用，因此工程上仍以 flash-lite 为唯一主力，
> 本地 Ollama 兜底。两者都零成本。

## 1. 免费档到底有哪些模型（2026-10-29 实测）

`GET /v1/models` 返回 7 款（10-27 起由 6 款扩到 7 款，新增 `sensenova-u1.5-lite`）。
**关键：`/models` 只表示「平台有」，不代表「你的 key 能调」，也不代表「走对话接口」。**

| 模型 ID | 类型 | `chat/completions` 实测 | 计费池 |
|---|---|---|---|
| `sensenova-6.8-flash-lite` | 多模态对话（text+image） | **200，5/5 稳定，1.8–4.0s** | **Flash-Lite 专属池** |
| `glm-5.2` | 文本对话（1M ctx / 128K out） | 200 连续 3 次后转 429 | 通用池 |
| `deepseek-v4-flash` | 文本对话（1M ctx） | 429 | 通用池 |
| `deepseek-flash` | `deepseek-v4-flash` 别名 | 429 | 通用池 |
| `kimi-k3` | 文本对话（2.8T / 1M ctx） | 429 | 通用池 |
| `sensenova-u1.5-lite` | **图片创作 / 编辑，非对话** | 404 `model is not found` | Flash-Lite 专属池 |
| `sensenova-u1-fast` | **信息图生成，非对话** | 404 | Flash-Lite 专属池 |

两个 404 不是「下线」，是**接口用错了**——它们要走
`/v1/images/generations` 和 `/v1/images/edits`，不走 `chat/completions`。
`deepseek-v4.1-flash` 单独测到 **403 `model is not available in the current token plan`**，
说明它不在免费档权限内（但仍在 `/models` 清单里，容易被误判为可用）。

### 1.1 双积分池：为什么「有 7 款却只有 1 款能用」

2026-08-28 起，商汤把「按模型限次」改成了**双积分池**：

| 池子 | 覆盖模型 | 滚动 5 小时 | 滚动周 |
|---|---|---|---|
| 通用积分池 | 所有模型 | 120,000 积分 | 1,200,000 积分 |
| Flash-Lite 专属积分池 | 仅 flash-lite 系列 | 120,000 积分 | 1,200,000 积分 |

扣减顺序：用 flash-lite 先扣专属池，专属不足再扣通用池；**用其他模型只扣通用池**。
（额度已于 10-27 由 6 万 / 60 万翻倍到 12 万 / 120 万，并全员重置到新上限。）

这就是「看起来只有 flash-lite 能用」的真正原因：
**专属池和通用池是两本独立的账**，通用池被消耗/受限时，flash-lite 完全不受影响。
实测印证——glm-5.2 慢速 60s 间隔连打 3 次仍全部 `insufficient_quota`（排除 RPM 限流），
同一时刻 flash-lite 间隔 3s 快打 5/5 全 200。

> 注：8-28 曾上线「flash-lite 消费 1 专属积分返 1 通用积分」的返赠活动，
> 已于 **10-27 下线**，不能再靠刷 flash-lite 补贴通用池。

### 1.2 429 的三种 code 不可靠

同一个语义（限流/额度不足），网关会随机返回不同 code，**不能用来判断是否该重试**：

| code | 出现场景 |
|---|---|
| `insufficient_quota` | 通用池额度不足（也可能只是瞬时限流） |
| `Throttling.BurstRate` | 突发速率超限 |
| `ModelAccountRpmRateLimitExceeded` | 账号 RPM 超限 |
| `RateLimitExceeded.EndpointTPMExceeded` | 端点 TPM 超限 |

`_is_quota_exhausted` 只能靠 code 关键字启发式判断，**别把它当成精确判据**；
真正的稳定做法是「主用专属池模型 + 本地兜底」，而不是靠猜 code 决定重试。

## 2. 关键配置

```dotenv
LLM_PROVIDER=sensenova
SENSENOVA_MODEL=sensenova-6.8-flash-lite
# flash-lite 是推理模型：不关思考时每条先烧 250+ reasoning tokens（首字 6.3s），
# 关掉后思考 token 归零、首字 2.5s，额度省一半以上
LLM_EXTRA_BODY={"thinking": {"type": "disabled"}}
```

`LLM_EXTRA_BODY` 是网关透传参数（JSON 对象），只作用于主 provider，
由 `Settings.llm_extra_body()` 解析、`_build_chat_model` 注入 `ChatOpenAI(extra_body=...)`。
配错不会阻断启动，只 warning 一次并退化为不传。

## 3. 实测性能

| 场景 | 首字 | 说明 |
|---|---|---|
| flash-lite 带思考 | 6.3–9.3s | 默认行为，另有「思考吃满 max_tokens、正文只剩 8 字」风险 |
| flash-lite 关思考 | **1.5–2.8s** | 稳定，输出完整 |
| 本地 qwen2.5-7b 热态 | **0.5–0.6s** | 44 tok/s，比云端免费档还快 |
| 本地 qwen2.5-7b 冷启 | 12.8s | 首次加载模型（4.7GB），见 4.1 常驻设置 |
| 本地 qwen3:14b | 15.8s | 24 tok/s，默认带思考，不建议做主力 |

## 4. 降级链路

```
商汤 flash-lite（主） --429/超时--> 本地 Ollama（备） --> LLMUnavailableError
```

两处已加固：

1. **额度类 429 不再重试**：`_should_retry` 区分「瞬时限流」与「额度耗尽」
   （`insufficient_quota` / `rpm exhausted` / `Insufficient Balance`），
   后者直接降级。实测降级耗时 24s → 5.5s，且不再白打 2 轮请求。
2. **关闭 SDK 内置重试**（`max_retries=0`）：重试统一由 tenacity 负责，
   避免两层重试叠加。

> Ollama 必须在运行，否则降级链路形同虚设（2026-10-29 排查时它就没起，
> 导致商汤一抖就整链路失败）。启动：`ollama serve`，或直接运行 Ollama 桌面端。

### 4.1 让本地模型常驻（重要）

Ollama 默认空闲 5 分钟卸载模型，下次降级要重新加载 4.7GB（约 13s），
很容易顶穿 `LLM_FALLBACK_TIMEOUT`，表现为「降级也失败」。

```powershell
# 方式一：服务端环境变量（推荐，重启 Ollama 后长期生效）
setx OLLAMA_KEEP_ALIVE -1

# 方式二：单次预热，把模型钉在显存里
ollama run qwen2.5-7b "预热"
# 或 curl http://localhost:11434/api/generate -d '{"model":"qwen2.5-7b:latest","keep_alive":-1,...}'
```

同时 `.env` 已把 `LLM_FALLBACK_TIMEOUT` 从 15 调到 **30**，
冷启动（13s 加载 + 生成）也能扛住。qwen2.5-7b 占 5.1GB 显存，
4060 Ti 16G 上同时留 8G 给 embedding/reranker 也够。

## 5. 没有额度时的三种用法

| 方案 | 配置 | 适用 |
|---|---|---|
| 商汤免费档 | `LLM_PROVIDER=sensenova` + flash-lite | 日常开发、评测，速度最快 |
| 本地模型当主力 | `LLM_PROVIDER=ollama` + `OLLAMA_MODEL=qwen2.5-7b:latest` | 完全断网 / 额度耗尽时 |
| 混合 | 商汤为主 + Ollama 兜底（默认链路） | 推荐 |

`LLM_PROVIDER=ollama` 是显式主用本地模型，会跳过商汤（不浪费 429 重试），
见 `llm_factory._call_llm_impl`。

> 想临时用通用池模型（`glm-5.2` / `deepseek-v4-flash` / `kimi-k3`）试跑，
> 只改 `SENSENOVA_MODEL` 即可，代码不用动；但**不要写进默认配置**——
> 它们和 flash-lite 不共享额度，实测长期 429，会让整条链路退化成「每次都先失败再降级」。

## 6. 评测链路的注意点

- judge 客户端由 `run_eval.py` 自建（`openai.OpenAI`），**不走** `LLM_EXTRA_BODY`，
  所以判分时思考仍然开着：实测单次判分 300 tok（其中思考 127），JSON 仍可解析。
- `EVAL_JUDGE_MAX_TOKENS` 默认 8192，足以容纳「思考 + JSON」；
  若改回 4096 会重现 2026-09-13 那批 `IncompleteOutputException`（15 行缺 faithfulness）。
- 免费档限流很紧，`EVAL_LLM_RPM`（默认 10）与 `EVAL_JUDGE_BATCH_SIZE`（默认 2）
  不要调大，否则整批 429。判分建议直接指向 flash-lite（专属池），
  不要指向 `glm-5.2` / `deepseek-v4-flash`（通用池，见 1.1）。

## 7. 排查手册

| 现象 | 原因 | 处理 |
|---|---|---|
| 429 `insufficient_quota` | ① 用了通用池模型（glm/deepseek/kimi）② flash-lite 专属池也耗尽 | 先确认 `SENSENOVA_MODEL=sensenova-6.8-flash-lite`；仍 429 就等滚动窗口或转 Ollama |
| 429 `Throttling.BurstRate` / `EndpointTPMExceeded` | 短时间打太快（RPM/TPM） | 降 `EVAL_LLM_RPM`，或加 `LLM_RETRY_BASE_DELAY` |
| 403 `not available in the current token plan` | 该模型不在免费档权限（如 `deepseek-v4.1-flash`） | 换 flash-lite |
| `u1.5-lite` / `u1-fast` 报 404 | 它们是画图模型，不走对话接口 | 改用 `/v1/images/generations` |
| 回答为空 / 只有几个字 | 思考吃满 `max_tokens` | 配 `LLM_EXTRA_BODY` 关思考 |
| 降级也失败 | Ollama 没起 | `ollama serve` |
| 本地服务连不上、curl 返回 502 | shell 里有 `HTTP_PROXY` 但无 `NO_PROXY` | `export NO_PROXY=localhost,127.0.0.1` |
