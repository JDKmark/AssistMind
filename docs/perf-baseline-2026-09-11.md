# AssistMind 性能基线报告（perf-baseline-2026-09-11）

> 对应 spec：`docs/性能验证与链路加固-spec.md`（P0 可观测 → P1 压测体系 → P2 基线）。
> 本次为 P2 基线**收尾取证与报告**：T3/T5/T6/T8 与 `tests/load/` 压测体系代码已落地（791 用例全绿、coverage 84.11%），
> 本报告补齐 429 限流验证、故障注入四轮重采（前序恢复轮证据缺失）、B1 本地重排对比组、B2 真实上游标定轮，并汇总 B0 阶梯基线。
> 基线主数据（B0 阶梯加压）实测于 2026-09-11，补充取证（429 / 故障四轮 / B1）实测于 2026-09-12。

---

## 1. 环境与配置快照

### 1.1 硬件 / 系统

| 项 | 值 |
|---|---|
| CPU | Intel Core i5-12400F（6 核 12 线程） |
| 内存 | 32 GB（TotalPhysicalMemory 34,191,216,640 B） |
| OS | Windows 10 x64（10.0.19045），Docker Desktop（Linux 容器） |
| 被测进程 | 单 uvicorn 进程（无多 worker），Locust 与被测**同机**（单机压测，见 §7 偏差声明） |

### 1.2 基础设施容器

| 组件 | 镜像 | 端口 |
|---|---|---|
| Redis | redis:7-alpine | 16379（**非 6379**） |
| Qdrant | qdrant/qdrant:v1.12.4 | 6333 |
| PostgreSQL | postgres:15-alpine | 5432 |
| Prometheus / Langfuse | smart-cs-prometheus / langfuse 栈 | 9090 / 3001（本次 LANGFUSE 未启用，埋点全程 no-op） |

### 1.3 配置分组（spec G8：必须显式声明，防止「测到本机 CPU 而不是系统能力」）

三组定义如下，**实测覆盖 B0 与 B1，B2 未实跑**（见 §6 未验证项）：

| 组 | 关键配置 | 实测状态 |
|---|---|---|
| **B0 服务端脊梁** | `LLM_PROVIDER=mock`（300 token × 20ms ≈ 6s 固定流式）、`RERANKER_ENABLED=false`、`HF_HUB_OFFLINE=1`、`RATE_LIMIT_PER_MINUTE=100000`、`METRICS_ENABLED=true`、`LANGFUSE_PUBLIC_KEY/SECRET_KEY` 置空 | ✅ 阶梯加压 10→400 实测（§2） |
| **B1 含本地重排** | B0 基础上 `RERANKER_ENABLED=true` + `RERANKER_PROVIDER=local`（本地 CrossEncoder `BAAI/bge-reranker-v2-m3`，CPU 推理） | ✅ s4 10u/25u 实测（§3.3） |
| **B2 真实上游** | DeepSeek 官方直连（`LLM_PROVIDER=deepseek`，模型 `deepseek-flash`，周末空闲价）+ `RERANKER_PROVIDER=siliconflow`（免费档，实测单次 rerank 0.6–2s）；另含商汤网关对照（配额墙发现，见 §3.4） | ✅ s4 10u/25u **标定轮**实测（§3.4）；全阶梯因生成延迟与费用可控性未跑（见 §6） |

补充说明（不粉饰）：故障注入轮（§4）的实例配置为 `RERANKER_PROVIDER=siliconflow`（按既定步骤命令）但**未配置 SILICONFLOW_API_KEY**。
这不影响故障注入证据的有效性：`fault_injection.apply_reranker_fault()` 在 provider 判断**之前**命中（`reranker.py:68`），
`FAULT_RERANKER=fail` 照常触发并计数；恢复轮的重排路径则是「未配 key → 跳过重排」的既有降级分支（有 warning、无故障计数）。

### 1.4 所有实测均遵守的环境约束

- 后端启动带 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`（模型已本地缓存；不禁用会走系统代理被 502，**每次请求重试加载模型 ~60s**，前序实测过）。
- 压测客户端 `trust_env=False`（本机常驻代理会劫持 localhost 请求导致 `ProxyError`）。
- 每轮压测前 `docker exec smart-cs-redis redis-cli INCR assistmind:kb:version` 做 O(1) 缓存失效，保证请求真正走到检索层（命中语义缓存会免检索免生成）。
- 每个新实例起服务后先预热一发真实请求（首次请求含 embedding 模型加载，不计入统计口径）。

---

## 2. B0 阶梯加压基线（Mock LLM + 关闭重排）

### 2.1 命令与口径

```bash
# 被测：8012 端口，B0 配置（见 §1.3）
LOAD_STAGES="10:180,25:180,50:180,100:180,200:180,400:180" LOAD_SPAWN_RATE=5 \
LOAD_HOST=http://localhost:8012 LOAD_SCENARIO=s4 LOAD_CSV_DIR=results \
../../venv/Scripts/python.exe -m locust -f locustfile.py --headless --config profiles/baseline.conf --csv results/baseline
```

- 场景 S4 混合（S1:S2:S3 = 5:3:2），每阶 **180s**（满足 spec 3.8「每阶 ≥3 分钟」），spawn_rate 5/s。
- 数据引用自 `results/baseline_analysis.json`（`lib/analyze.py` 从明细 CSV 聚合，**未手抄改数**）。

### 2.2 阶梯加压表（实测，源：`results/baseline_analysis.json`）

| 并发 | 请求数 | QPS | p50(ms) | p95(ms) | p99(ms) | TTFT p50(ms) | TTFT p95(ms) | 错误率 | 429 | 5xx | 缓存命中率 | SSE 中断数 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 211 | 1.17 | 5,631 | 17,404 | 19,155 | 4,705 | 7,126 | 0.00% | 0 | 0 | 50.7% | 0 |
| 25 | 695 | 3.86 | 2,128 | 24,189 | 25,807 | 5,070 | 5,400 | 0.00% | 0 | 0 | 81.4% | 0 |
| 50 | 798 | 4.43 | 4,809 | 39,985 | 44,821 | — | — | 0.00% | 0 | 0 | 81.2% | 0 |
| 100 | 841 | 4.67 | 8,259 | 70,050 | 82,134 | — | — | 0.00% | 0 | 0 | 83.6% | 0 |
| 200 | 796 | 4.42 | 29,654 | 96,104 | 101,384 | — | — | 20.85% | 0 | 0 | 78.9% | 166 |
| 400 | 2,528 | 14.04 | 4,099 | 94,747 | 110,155 | — | — | 89.72% | 0 | 0 | 10.3% | 2,268 |

口径说明（照实声明，不粉饰）：

- **TTFT 在 50/100/200/400 阶无样本（ttft_n=0）**：TTFT 按首个 `delta` 事件采集（与 `assistmind_ttft_seconds` 同口径），
  高并发阶完成的请求几乎全部是缓存命中（免生成、无 delta 事件）或在窗口内未完成生成，因此无 TTFT 样本。
  有效 TTFT 参考 10/25 阶：p50 ≈ 4.7–5.1s、p95 ≈ 5.4–7.1s（mock LLM 固定 6s 生成占主导）。
- **错误率在 200/400 阶的 20.9% / 89.7% 为 SSE 中断**（客户端在 `done` 事件前超时/断开，明细 CSV `sse_broken` 列），
  不是 HTTP 5xx（429/5xx 两列全为 0）：即服务端没有主动报错，而是过载下响应慢到客户端放弃。
- **400 阶 QPS「回升」到 14.04 是快速失败造成的假象**：大量请求在窗口早期即被客户端断开（sse_broken=2,268、缓存命中率跌到 10.3%），
  「请求数 ÷ 时间」被快速完成的失败请求抬高，**不代表吞吐能力回升**，与 200 阶的 4.42 QPS 平台期不矛盾。

### 2.3 资源采样（50 并发 90s 冷缓存，实测）

源：`results/res_b0_50u.csv`（`lib/resource_sample.py`，85 个采样点，对被测进程采集）：

| 指标 | 值 |
|---|---|
| CPU（进程，单核百分比口径） | 均值 59.7%，峰值 **343.3%**（≈3.4 核打满） |
| RSS 内存 | 峰值 **2,445 MB** |
| inflight 在途请求 | 峰值 54（与 50 并发持平，无在途上限保护——T1 未做） |
| 线程数 | 峰值 57 |

---

## 3. 拐点判定与瓶颈层

### 3.1 拐点结论（实测）

**拐点在 25 → 50 并发**：QPS 增幅仅 +14.8%（3.86 → 4.43），而 p95 陡增 +65.3%（24,189 → 39,985 ms）。
此后 50–200 并发进入吞吐平台期（4.42–4.67 QPS），p95 持续恶化（40s → 70s → 96s），200 并发起出现 SSE 中断型错误。

`lib/analyze.py` 自动判定结果（`baseline_analysis.json.knee`）：`users=50, prev=25, qps_gain=0.148, p95_gain=0.653`。

### 3.2 由哪一层决定（实测证据 → 结论）

**瓶颈在单进程 CPU（本机 embedding 推理 + Python 层开销），不是并发槽或连接池**。证据：

1. **资源采样**：50 并发时被测进程 CPU 峰值 343%（多核打满）而吞吐停在 ~4.5 QPS——CPU 饱和先于任何连接限制出现。
2. **吞吐平台期 + p95 线性恶化**：并发 50→200 翻 4 倍，QPS 纹丝不动（4.43→4.42），延迟翻倍以上——典型的 CPU 饱和排队形态，
   而非连接池耗尽（那会表现为连接错误/5xx，实测 5xx=0）或在途请求堆积被拒（T1 未实现，无 503 快拒路径）。
3. **B1 对照**（§3.3）：本地重排（纯 CPU）把 10u 吞吐从 1.17 QPS 压到 0.04–0.15 QPS，直接量化了 CPU 推理对链路的支配地位。
4. Qdrant/BM25/Redis 均未报错、断路器全程闭合（`degraded_counter={}`）——数据层不是瓶颈。

**推论（估算，非实测）**：若 T1（在途并发控制）/T2（连接池与 HTTP 复用）落地，预期表现为「过载时快速 503 + p95 抬升放缓」，
但**拐点位置不会明显右移**——瓶颈在 CPU。B2 标定轮（§3.4）补充了两个实测修正：真实部署下生成延迟（reasoning 模型 TTFT 21–37s）
是比应用层 CPU 更先被用户感知的约束；且 25u 时 **Qdrant 断路器打开、19% 请求向量召回静默降级为 BM25**（G2/G3 连接管理的并发缺陷），
T2 实施的优先级据此上调。该推论待 P3 加固后复测验证。

### 3.3 B1 对比组：本地重排（spec G8 实测结论）

命令与 B0 同口径（s4 混合场景，90s，spawn 5/s），实例配置 `RERANKER_ENABLED=true RERANKER_PROVIDER=local`：

| 组 | 并发 | 请求数(90s 窗口) | QPS | p50(ms) | p95(ms) | 错误率 | 数据源 |
|---|---|---|---|---|---|---|---|
| B0 | 10 | 211(180s) | 1.17 | 5,631 | 17,404 | 0% | baseline_analysis.json |
| **B1** | **10** | **4** | **0.04** | **13,744** | **14,470** | 0% | b1_10u_analysis.json |
| **B1** | **25** | **7** | **0.08** | **35,496** | **38,030** | 0% | b1_25u_analysis.json |

（B0 为 180s 阶、B1 为 90s 轮，QPS 口径均按各自时长折算，可直接对比量级。）

**实测结论：本机重排在并发下不可用，必须切云端重排（siliconflow）**。补充实测细节：

- 预热单请求（1 并发）的 rerank 阶段实测 **25.2s**（`assistmind_stage_duration_seconds{stage="rerank"}` sum，样本 `b1_rerank_metrics.txt`）；
  spec 记录的「单次 1–2 分钟」对应更大候选集场景，量级一致：**秒级云端 rerank vs 数十秒级本机 rerank**。
- 后端 `[Perf]` 日志显示 B1 轮完成请求 `total_ms=62,000–86,000`（本地 CrossEncoder 在默认线程池中 CPU 排队，
  并发请求相互拖拽）；错误率仍为 0%——硬超时护栏与「跳过重排用 RRF」降级兜住了正确性，但延迟已不可用。
- `RERANKER_TIMEOUT=15`（护栏 20s）对本机路径**不构成有效护栏**：本地路径走 `run_in_executor` 同步等待（`reranker.py:76-81`），
  不像 siliconflow 路径有 `asyncio.wait_for` 硬超时，单次 25s 的重排会完整兑现到请求延迟上。

### 3.4 B2 标定轮：真实上游（DeepSeek 官方直连 + SiliconFlow 云端重排）

**配置**：`LLM_PROVIDER=deepseek`（官方 `https://api.deepseek.com`，模型 `deepseek-v4-flash`，即 V4.1 Flash 档，周末全天空闲价；
注：`GET /models` 列出的规范 ID 为 `deepseek-flash`，`deepseek-v4-flash` 是其别名——服务端响应 model 字段回显
`deepseek-flash`，实测两者命中同一模型）+ `RERANKER_PROVIDER=siliconflow`（免费档，实测单次 rerank 0.6–2s）。
计费按官方价目表**周末全天空闲价**（输入缓存未命中 1 元/百万 token、输出 4 元/百万 token）。

**背景**：曾先用商汤网关（`token.sensenova.cn`，同款 deepseek-v4-flash）试跑，发现其按 key 的 TPM/RPM 配额极小
（约 8 秒 3 连发即 429），只能做 3u 微标定；切官方直连后限额宽松得多，得以完成 10u/25u 两档标定。
这一对比本身是有价值的实测结论：**同款模型，不同通道的配额政策直接决定真实容量上限**。

**实测结果**（源：`results/b2_official_10u_analysis.json`、`b2_official_25u_analysis.json`、`b2_official_10u/25u_metrics.txt`）：

| 并发 | 请求数(120s) | QPS | p50(ms) | p95(ms) | TTFT p50(ms) | TTFT p95(ms) | 错误率 | 429 | 降级触发 |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 80 | 0.67 | 12,356 | 30,747 | 20,995 | 39,281 | 0% | 0 | **0**（零上游错误） |
| 25 | 94 | 0.78 | 23,874 | 56,660 | 37,168 | 50,614 | 0% | 0 | **qdrant/fallback=23**（见下） |

注：请求数为明细 CSV 口径；Locust 聚合（105/123）含关停时被终止的在途请求。

**四条实测结论**：

1. **官方直连下上游不再是最先触顶的瓶颈**：10u/25u 两轮全程零 429、零上游错误（商汤网关 3 连发即 429）。
   B0 应用层拐点 ~4.5 QPS 与 B2 实测 0.67–0.78 QPS 之间的差距，主要由**推理模型的生成耗时**（TTFT p50 21–37s，
   flash 档 reasoning 先于正文输出）与单进程 CPU 构成，而非配额。扩容杠杆排序修正为：**生成延迟（换非 reasoning 档/流式体验）→ 上游配额（多 key/提额）→ 应用层加固**。
2. **25 并发下暴露了 Qdrant 链路的并发缺陷（T2 最硬证据）**：25u 轮中 Qdrant search 连续失败 → 断路器打开
   （日志可见 `CircuitClosedState -> CircuitOpenState`）→ `qdrant/fallback=23`（约 19% 请求向量召回降级为纯 BM25）。
   根因指向 G2/G3：每请求新建 httpx 客户端 + 无连接复用，并发下连接管理失控。**用户视角 0% 错误**——降级机制完全兜住，
   但检索质量在无声下降。10u 轮未出现（连接压力未到阈值），说明该缺陷有明确的并发门槛，T2（连接池与客户端单例化）实施后需复测验证。
3. **云端重排（siliconflow）可用且快**：实测单次 rerank 0.6–2s（与 B1 本机 25.2s 对比，量级差距 ~12–40×），
   G8 的「切云端重排」结论闭合。
4. **降级链路在真实上游故障下实测有效**：qdrant 断路器打开期间所有请求仍 100% 成功（BM25 兜底），
   与 §4 故障注入证据互相印证。

**费用（周末空闲价，估算）**：两轮合计 ~190 个生成请求，按每请求 ~3k 输入 / ~0.8k 输出 token 估算，
输入 ~0.3 元 + 输出 ~0.3 元，**合计 <1 元**；DeepSeek 服务端 context cache（0.02 元/百万缓存命中）进一步摊薄重复前缀成本。
rerank 走 SiliconFlow 免费档零费用。

---

## 4. 故障注入证据（spec 3.6 四件事）

### 4.1 方法

单进程（8013）+ `FAULT_OVERRIDE_FILE` 覆盖文件热切换，**全程不重启**；每轮前 `INCR assistmind:kb:version` 清缓存；
场景 S5（faq_miss 为主 + cache_hit 对照），6 并发 40s；每轮前后 `curl /metrics` 快照 `assistmind_degradation_total`。

```bash
# 覆盖文件改写（示例：reranker fail）
echo '{"FAULT_RERANKER":"fail","FAULT_QDRANT":"ok","FAULT_LLM":"ok"}' > results/faults.json
LOAD_HOST=http://localhost:8013 LOAD_SCENARIO=s5 LOAD_CSV_DIR=results \
../../venv/Scripts/python.exe -m locust -f locustfile.py --headless -u 6 -r 6 -t 40s --only-summary --csv results/s5_fault_reranker
```

### 4.2 四轮结果（实测，源：`results/s5_round{1,2,3,4}_*.txt` 与 Locust CSV）

| 轮次 | 覆盖文件 | degradation_total 增量（轮前 → 轮后） | Locust 错误率 |
|---|---|---|---|
| reranker fail | `{"FAULT_RERANKER":"fail"}` | `reranker/fault_fail` 0→**18**；`reranker/fallback` 0→**12** | **0%**（24 请求全成功） |
| qdrant down | `{"FAULT_QDRANT":"down"}` | `qdrant/fault_down` 0→**48**；`qdrant/fallback` 0→**9** | **0%**（16 请求全成功） |
| llm error | `{"FAULT_LLM":"error"}` | `llm/fault_error` 0→**137**；`llm/fallback` 0→**30**；`query_rewrite/fallback` 0→**30** | **0%**（74 请求全成功，avg 2.4s 快速降级应答） |
| 恢复对照 | 三个全 `"ok"` | **全部计数零增长**（18/12/48/9/137/30/30 保持不变） | 0%（18 请求全成功） |

计数口径说明：`fault_*` 是故障命中层计数（含 CRAG 补充检索等二次调用，故可能大于请求数）；
`fallback` 是请求级降级计数（`done.degraded` 映射，`chat.py:101`）。每轮的 fault_* 与 fallback 数量不必相等。
前序参考值（reranker=6 / qdrant=24 / llm=20）为前序会话同口径轮次的实测值，本轮请求数更多，计数绝对值相应更大，**降级路径与触发方向完全一致**。

### 4.3 spec 3.6 四件事的证据落点

1. **降级生效**：上表 degradation_total 增量；LLM error 轮伴随 `llm/fallback` 与 `query_rewrite/fallback`（改写走 fast 失败链路降级到原问题），请求仍以模板兜底正常应答（avg 2.4s，0 失败）。
2. **有 warning**：服务端日志 `results/backend_8013_fault.log`，取证摘录见 `results/s5_fault_warnings_evidence.txt`——
   `[Fault] FAULT_RERANKER=fail：重排失败，降级用 RRF 结果` / `[Fault] FAULT_QDRANT=down：向量召回不可用，降级为仅 BM25` / `[Fault] FAULT_LLM=error：provider=mock 立即失败`，均为 WARNING 级。
3. **错误率受控**：四轮 Locust 错误率全部 **0%**（故障下用户拿到的是降级后的正常应答，不是报错）。
4. **自动恢复（无需重启）**：覆盖文件改回全 `"ok"` 后复跑同一场景，`degradation_total` 七个序列**全部零增长**；
   期间唯一的 `[Fault]` 日志是「运行时覆盖已生效: {'FAULT_RERANKER': 'ok', ...}」的配置生效通知（206→207 行），无任何故障命中。
   前序会话缺失的恢复证据（0 字节的 `s5_recovery_console.log`）本次已重采补齐（现为 4,658 字节）。

---

## 5. 429 限流验证（T8 / spec 3.7 S6）

### 5.1 命令与结果（实测，2026-09-12）

低限流实例（`RATE_LIMIT_PER_MINUTE=20`，8014 端口）+ S1 缓存命中场景 10 并发 30s：

```bash
LOAD_HOST=http://localhost:8014 LOAD_SCENARIO=s1 LOAD_CSV_DIR=results \
../../venv/Scripts/python.exe -m locust -f locustfile.py --headless -u 10 -r 10 -t 30s --only-summary --csv results/s6_ratelimit
```

- 37 个业务请求中 **18 个被 429**（Locust 错误 `http_429`，见 `results/s6_ratelimit_stats.csv` / `load_20260912_021029.csv`）；
- `/metrics` 计数器：`assistmind_rate_limited_total{scope="ip"} 18.0`（与客户端观测一致）；
- 服务端日志可见 `[RateLimit] 触发限流` WARNING 与 `429 Too Many Requests` 访问行（`results/backend_8014.log`）；
- **限流跳过名单验证**：限流激活期间连测 5 轮，`/metrics` 与 `/api/v1/health` 全部 200（取证：`results/s6_ratelimit_evidence.txt`）——
  观测端点不会被限流误杀，压测中指标采集链路始终可用。

---

## 6. 未验证项与风险（不粉饰）

| 项 | 状态 | 影响 / 风险 |
|---|---|---|
| T1 在途并发控制 / T2 连接池与 HTTP 复用 / T4 缓存击穿保护 / T7 worker 并发上限 | **未实施**（本次范围外，属 P3） | 过载时无快速 503 拒绝路径（200/400 阶的 SSE 中断是客户端放弃，不是服务端快拒）；连接池仍为默认 15 上限；热点问题并发首访仍会同时打 LLM |
| **B2 全阶梯加压** | **未跑（已完成 10u/25u 标定轮，§3.4）** | 官方直连下 429 不再是约束，但 reasoning 生成延迟（TTFT p50 21–37s）使高并发轮次耗时长、费用线性增长；标定轮已给出真实容量量级与两个关键发现（配额政策差异、Qdrant 并发缺陷）。全阶梯待 T1/T2 落地后复测更有价值 |
| **25u 下 Qdrant 断路器打开** | **已记录根因假设，未深挖** | §3.4 结论 2：指向 G2/G3 每请求新建 httpx 客户端的连接管理缺陷；具体异常原文被 rich 日志折叠，留待 T2 实施时一并修复与验证 |
| S7 浸泡（30–60 min 稳态） | **未跑** | 内存/连接/缓存泄漏未验证；B0 轮 6 分钟窗口内 RSS 已到 2.4GB，长时稳态表现未知 |
| 单机压测偏差 | **固有** | Locust 与被测同机（12 逻辑核）：压测客户端消耗 CPU/内存并与被测争抢，**实际服务端容量在本就饱和的 CPU 上可能被低估**；客户端自身也可能成为瓶颈（400 阶 sse_broken 部分源于此） |
| 指标仅单进程有效 | **固有** | `/metrics` 未启用 prometheus_client multiprocess 模式，多 worker/多副本部署时各进程指标不可见聚合（spec T5 验收项已声明「仅单进程有效」） |
| Mock LLM 的 6s 生成时间 | **人为常量** | 生成阶段的延迟与并发行为由 300 token × 20ms 固定模拟决定，不代表真实 LLM 的 TTFT 分布；TTFT 实测值（p50 ≈ 5s）以此为下界 |
| B1 阶段直方图观测缺口 | **已知问题** | B1 轮中 `stage="rerank"/"generate"` 的 histogram 计数未随请求累计（rewrite/retrieve 正常累计）。旁路埋点不影响请求正确性，但意味着个别分支的 stage 观测有缺口，待后续排查补点 |
| 前序参考值与本轮计数差异 | **已解释** | 故障轮 fault_* 计数（18/48/137 vs 前序 6/24/20）随请求数变化，降级路径与方向一致，非行为回归 |

---

## 7. 与 spec 的偏差说明

| spec 要求 | 实际执行 | 说明 |
|---|---|---|
| 每阶稳态 ≥3 分钟 | 每阶 **180s** | 满足 |
| spawn_rate 5/s | 5/s | 一致 |
| P2 基线应包含 B2 云端组 | B2 全阶梯未跑，以 **10u/25u 官方直连标定轮**替代（§3.4） | 官方 API key 由用户提供后直连（周末空闲价，费用 <1 元）；商汤网关对照发现配额墙。G8 要求的「本地组证明瓶颈层」与「云端组测真实容量」分别由 B1 与 B2 标定轮覆盖 |
| 阶梯 profile 用 `--config profiles/baseline.conf` | 同效（`LOAD_STAGES` 驱动 LoadTestShape） | 口径一致：6 阶 × 180s × 5/s |
| 故障注入对照前序实测值 | 降级路径一致、计数绝对值随请求量不同 | 已在 §4.2 解释 |
| 400 阶 QPS 回升 | **判定为快速失败假象** | 详见 §2.2 口径说明，不作为吞吐结论 |

---

## 8. 原始数据落盘索引（backend/tests/load/results/）

**吞吐 / 延迟 / 稳定性（Locust 聚合 + 明细）**

- B0 阶梯：`baseline_stats.csv`、`baseline_stats_history.csv`、`baseline_failures.csv`、明细 `load_20260911_001951.csv`、`baseline.html`
- B0 聚合分析：`baseline_analysis.json`、`baseline_analysis.md`（本报告 §2.2/§3.1 数据源）
- B1 对比：`b1_10u_stats.csv`、`b1_25u_stats.csv`、明细 `load_20260912_022942.csv`（10u）/ `load_20260912_023205.csv`（25u）、`b1_10u_analysis.json/.md`、`b1_25u_analysis.json/.md`
- 429 限流：`s6_ratelimit_stats.csv`、明细 `load_20260912_021029.csv`
- 故障注入：`s5_fault_reranker/_qdrant/_llm/_recovery` 各 `stats.csv` + `_console.log` + `stats_history.csv`、明细 `load_20260912_021{3,4}*.csv`

**稳定性 / 降级**

- 每轮 metrics 快照：`s5_round1_reranker_metrics.txt`、`s5_round2_qdrant_metrics.txt`、`s5_round3_llm_metrics.txt`、`s5_round4_recovery_metrics.txt`
- `[Fault]` warning 摘录：`s5_fault_warnings_evidence.txt`；完整日志 `backend_8013_fault.log`
- 限流取证：`s6_ratelimit_evidence.txt`、`s6_ratelimit_metrics_after.txt`、完整日志 `backend_8014.log`

**资源**

- `res_b0_50u.csv`（50 并发资源采样时序）、`res50u_stats.csv` / `res50u_stats_history.csv`（同期 Locust 口径）

**其他**

- B1 rerank 阶段指标：`b1_rerank_metrics.txt`；B1 完整日志 `backend_8013_b1.log`
- B2 标定轮（商汤网关对照）：`b2_3u_analysis.json/.md`、`b2_3u_metrics.txt`、明细 `load_20260912_095*.csv`
- B2 标定轮（官方直连）：`b2_official_10u/25u` 各 `stats.csv` + `_console.log` + `_metrics.txt` + `_analysis.json/.md`、明细 `load_20260912_13*.csv`、完整日志 `backend_8013_b2_official.log`
- 门禁日志：`pytest_coverage.log`（前序基线，791 passed / 84.11%）、`pytest_gate_20260912.log`（本次复跑）

---

## 9. 门禁复跑（2026-09-12）

```bash
cd backend
./venv/Scripts/python.exe -m pytest tests -q -k "not integration" --cov=app --cov-fail-under=70
./venv/Scripts/python.exe -m ruff check app/core/infra/metrics.py app/core/infra/fault_injection.py \
  app/core/infra/rate_limit.py app/api/chat.py app/main.py tests/load
```

- pytest：**779 执行 / 全部通过（exit 0，进度输出无任何 F 标记），coverage 84.19%**，日志 `results/pytest_gate_20260912.log`
- ruff：**All checks passed!**
- SSE 契约、降级路径、缓存分桶、断路器、RRF 截断均未触碰（本次零业务代码改动，仅取证与报告）。

**与基线数字的差异说明（照实记录）**：前序基线口径为「791 passed / 84.11%」（`pytest_coverage.log`）。
本次复跑实测 779 个用例全绿、总语句数 6033（基线日志为 5772）、coverage 84.19%——说明生成基线日志后套件与代码仍有少量演进（用例与代码均有增删），
「791」这一数字无法从两份日志的进度输出（旧 748 个执行点 / 新 779 个执行点）中复现，应以前序某次未落盘的运行为准。
本次为提交前最终门禁，以**本次日志（779 全绿 / 84.19%）**为权威口径。
另注：本机 venv 实际解释器为 Python 3.13.1（AGENTS.md 声明的目标解释器为 3.11，`.python-version` 约束 uv 安装；
本机 venv 与之有偏差，为前序会话遗留的本机环境状态，非本次引入）。
