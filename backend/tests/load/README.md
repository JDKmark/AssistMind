# AssistMind 压测体系（tests/load）

以 **Locust 为主**：Python 同栈、并发用户数语义天然对齐「并发 SSE 连接数」、
自带 HTML/CSV 报告、可写 SLO 断言做 CI 门禁。选型对比与理由见
`docs/性能验证与链路加固-spec.md` 第 3.1 节。

---

## 1. 一条命令跑通

```powershell
cd backend\tests\load

# ① 冒烟（1 分钟，CI 门禁口径）
$env:LOAD_HOST="http://localhost:8002"; $env:LOAD_SCENARIO="s4"
..\..\venv\Scripts\python.exe -m locust -f locustfile.py --headless --config profiles\smoke.conf

# ② 阶梯加压找拐点（10→25→50→100→200→400，每阶 3 分钟）
$env:LOAD_STAGES="10:180,25:180,50:180,100:180,200:180,400:180"
..\..\venv\Scripts\python.exe -m locust -f locustfile.py --headless --config profiles\baseline.conf

# ③ 故障注入（配 T6 开关）
$env:FAULT_RERANKER="fail"; $env:LOAD_SCENARIO="s5"
..\..\venv\Scripts\python.exe -m locust -f locustfile.py --headless -u 10 -r 5 -t 60s

# ④ 超载（触发 429/503）/ 浸泡（45 分钟）
..\..\venv\Scripts\python.exe -m locust -f locustfile.py --headless --config profiles\stress.conf
..\..\venv\Scripts\python.exe -m locust -f locustfile.py --headless --config profiles\soak.conf
```

> 注意：Locust 一旦发现 `LoadTestShape`（本仓库在设置 `LOAD_STAGES` 时才定义）就接管
> `users/spawn-rate/run-time`，此时 `-u/-r/-t` 不再生效。

---

## 2. 被测配置必须显式声明（G8）

默认 `.env` 是**低吞吐组合**（本机 CrossEncoder 重排 + 本机/网关 LLM）。压测前必须显式选组，
否则测到的是本机 CPU 而不是系统能力：

| 组 | 关键配置 | 用途 |
|---|---|---|
| B0 服务端脊梁 | `LLM_PROVIDER=mock`、`RERANKER_ENABLED=false`、`HF_HUB_OFFLINE=1` | 隔离上游与慢重排，测并发/连接/检索/SSE 写出的纯服务端能力 |
| B1 含本地重排 | `LLM_PROVIDER=mock`、`RERANKER_PROVIDER=local`、`HF_HUB_OFFLINE=1` | 量化重排对 p95 的抬升 |
| B2 云端组合 | `RERANKER_PROVIDER=siliconflow`、`LLM_PROVIDER=deepseek` | 测系统真实容量（需配额与网络） |

`HF_HUB_OFFLINE=1` 是必须的：模型已本地缓存（`~/.cache/huggingface/hub`），
不禁用联网会走系统代理下载并被 502 拒绝，**每次请求**都重试加载模型（实测单请求 ~60s）。
离线加载后 embedding 走本地缓存，稳定且不需要网络。

`LLM_PROVIDER=mock`（spec 3.5）按固定 token 数 × 固定间隔流式返回
（默认 300 × 20ms ≈ 6s），用于离线压测隔离上游限流；辅助环节（意图/改写/CRAG）
会返回可解析的结构化小响应，使全链路可走通。

---

## 3. 场景与权重

| 场景 | 文件 | 内容 | 权重 |
|---|---|---|---|
| S1 | `scenarios/s1_cache_hit.py` | 高频重复问句（缓存命中），观察命中率/TTFT/免检索免生成 | 50% |
| S2 | `scenarios/s2_faq_miss.py` | FAQ 未命中（完整 RAG），观察检索/重排耗时、p95 | 30% |
| S3 | `scenarios/s3_task_agent.py` | Agent 多轮工具链，观察驻留时间、ReAct 轮数、MCP 调用 | 20% |
| S4 | `scenarios/s4_mixed.py` | 混合（默认场景，5:3:2） | — |
| S5 | `scenarios/s5_fault_injection.py` | 故障注入，验证降级正确性 | 独立跑 |
| S6 | `scenarios/s6_spike.py` | 尖峰，观察限流/排队雪崩 | 独立跑 |
| S7 | `scenarios/s7_soak.py` | 浸泡 30–60 min，观察内存/连接/缓存泄漏 | 独立跑 |

语料直接复用项目既有资产：`app/data/eval_qa.json`（25 条）+ `app/data/eval_mall_qa.json`
（40 条）+ 一组高频 FAQ。压测后可顺手复跑 RAGAS，做**性能与质量双闸门**。

---

## 4. 指标口径（与 T5 的 `assistmind_*` 一致，不搞两套）

| 类别 | 指标 | 采集点 |
|---|---|---|
| 吞吐 | QPS（分场景/分请求名） | Locust CSV `*_stats.csv` |
| 延迟 | p50/p95/p99（总时长）、**TTFT** | Locust CSV + CSV 明细列 `ttft_ms` |
| 稳定性 | 错误率（4xx/429/503/超时）、SSE 中断（无 done）、降级触发数 | CSV 明细 `status_code/error/events/degraded` |
| 资源 | CPU/内存/RSS、DB 连接、Redis 连接 | `/metrics` + Prometheus + 系统工具 |

**TTFT 上报方式**：主请求名 `<scenario>` 记 `total_ms`；另有一条
`<scenario>:ttft` 记首个 `delta` 的耗时。这样在 Locust 报告里能直接看 TTFT 分位，
口径与 `assistmind_ttft_seconds` 相同（首个 delta，非首个字节）。设
`LOAD_REPORT_TTFT=0` 可关闭该附加行。

明细落盘：`LOAD_CSV_DIR`（默认当前目录）下的 `load_<时间戳>.csv`，
字段见 `lib/ingest.py`（每请求一行，可自行聚合 QPS/分位数）。

---

## 5. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOAD_HOST` | `http://localhost:8002` | 被测地址 |
| `LOAD_SCENARIO` | `s4` | s1–s7 |
| `LOAD_STAGES` | 空 | `users:seconds,...`，设置后启用阶梯 Shape |
| `LOAD_SPAWN_RATE` | `5` | 每阶 spawn 速率 |
| `LOAD_CSV_DIR` | 当前目录 | 明细 CSV 目录 |
| `LOAD_TOKEN` | 空 | 直接注入 JWT（优先级最高） |
| `LOAD_USERNAME` / `LOAD_PASSWORD` | 空 | 走 `/api/v1/auth/login` |
| `LOAD_TRUST_ENV` | `0` | 是否让 requests 走系统代理。**默认 0**：本机常驻代理会把 localhost 请求转发出去导致 `ProxyError` |
| `LOAD_REPORT_TTFT` | `1` | 是否上报 `:ttft` 附加行 |
| `LOAD_MAX_FAIL_RATIO` | `0.02` | 结束时错误率阈值，超阈值退出码置 1 |

鉴权优先级：`LOAD_TOKEN` → `/auth/login` → **离线自签 JWT**（读 `JWT_SECRET` 本地签发）。
`/api/v1/chat/ask` 只依赖 JWT 与 role、不查库，所以离线自签即可完整驱动全链路，
且不给 PostgreSQL 增加登录负载。

---

## 6. CI 性能门禁

```powershell
# 跑完 smoke 后比对阈值（劣化即失败）
..\..\venv\Scripts\python.exe -m lib.gate --prefix smoke --p95-ms 8000 --ttft-p95-ms 3000
```

`lib/gate.py` 读 Locust CSV，对端到端 p95、TTFT p95、失败率三项做门禁，
支持 `--thresholds thresholds.json` 落一份基线阈值随仓库演进。

分工：RAGAS 守「答得对不对」，smoke 门禁守「快不快、稳不稳」，两者并列，
都不阻塞开发环境的常规单测。

---

## 7. 目录结构

```
tests/load/
├── locustfile.py            # 入口：按 LOAD_SCENARIO 装载用户类 + 阶梯 Shape + 事件监听
├── scenarios/               # S1–S7（每个场景一个 User 类）
├── lib/
│   ├── sse_client.py        # SSE 流式请求 + TTFT 采集（iter_lines(chunk_size=1)）
│   ├── auth.py              # 登录/离线自签 JWT 并复用
│   ├── corpus.py            # 语料（复用 65 条评估集 + 高频 FAQ）
│   ├── ingest.py            # 明细 CSV 落盘（旁路，失败不影响压测）
│   └── gate.py              # CI 性能门禁
├── profiles/                # smoke / baseline / stress / soak
├── corpus/README.md         # 语料来源与使用说明
└── results/                 # 运行产物（CSV/HTML/日志），不入库
```

---

## 8. 已知坑（实现时踩过，勿回退）

1. `iter_lines(chunk_size=1)`：不设会被 urllib3 缓冲，TTFT 被虚高。
2. **系统代理**：`requests` 默认 `trust_env=True`，本机代理会劫持 localhost 请求
   （`ProxyError: 127.0.0.1:4085`）。压测客户端默认 `trust_env=False`。
3. **Locust 类名唯一性**：同一类若以两个模块属性出现会触发
   `ValueError: user classes have the same class name`，临时变量必须 `del`。
4. **`LoadTestShape` 与 CLI 互斥**：Shape 的 `tick()` 返回 `None` 表示「结束测试」，
   所以未设置 `LOAD_STAGES` 时不能在 locustfile 里定义 Shape。
5. 长连接文件描述符：SSE 是长连接，压测机与被测机分离，并检查 `ulimit -n`。
