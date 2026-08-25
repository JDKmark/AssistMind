# AssistMind 线上演示部署指南

> 目标：把整套系统跑在一台服务器上，面试官通过公网浏览器直接访问。
> 本目录交付：`deploy.sh`（一键部署）+ `.env.prod`（生产配置）+ overlay compose（`docker-compose.app.yml`）。

---

## 一、部署方案推荐

### 方案 A（主推）：单台轻量云服务器 + Docker Compose（约 ¥70-120/月）

- **选型**：腾讯云 / 阿里云「轻量应用服务器」，**2 核 4G，磁盘 40-60G**，Ubuntu 22.04/Debian 12。
  - 为什么 4G：本项目有 Qdrant + Redis + PostgreSQL + Langfuse(可选) + Prometheus 共 7 个容器 + 2 个应用容器，外加 torch（sentence-transformers）常驻内存，2G 会很紧。
  - 为什么 40G+：后端镜像含 torch 约 2-3G，知识库向量与 PG 数据另占空间。
  - 新用户 / 学生认证通常有 3-12 个月更低价格，用完可退。
- **DeepSeek 走在线 API**：全程无 GPU 需求，按量计费很便宜（演示一天成本通常不足 1 元）。
- **优点**：演示最完整、长期可访问、可续作真实项目。

> 不推荐免费 PaaS（Render/Fly.io 等）：需绑卡、单容器长驻限制多，多服务（Qdrant+Postgres）整体开销反而更高；一台 4G 云主机最省心。

### 方案 B（零成本应急）：本机起服务 + Cloudflare 隧道

面试前一天临时分享、或不想买服务器的场景。**缺点：本机需保持开机，公网速度依赖上行带宽。**

```bash
# 终端 1：本机一键启动（Windows PowerShell，仓库根）
.\start-demo.ps1

# 终端 2：将 localhost:5173 暴露为临时公网 https 地址
cloudflared tunnel --url http://localhost:5173
# 会输出一条 https://xxxxx.trycloudflare.com，发给面试官即可
```

> cloudflared 需单独安装（Windows 版下载解压即为 exe）。隧道把 5173 的 `/api` 代理一并透传，后端无需额外暴露。

---

## 二、方案 A 完整部署步骤（10 分钟）

### 1. 服务器准备

1. 购买/开通轻量服务器，选择 **Ubuntu 22.04**（或 Debian 12）。
2. 在云控制台**安全组/防火墙放行 80（前端）与 8001（后端文档，可选）**，其余端口按需收紧。

### 2. 安装 Docker（一次性）

```bash
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker $USER   # 然后重新登录，让当前用户免 sudo docker
newgrp docker
docker compose version          # 应显示 v2.x
```

### 3. 上传代码

把仓库传到服务器（任选其一，在本地机器仓库根执行）：

```bash
# 方式 1：git clone（若仓库在远端）
git clone <repo-url> && cd AssistMind

# 方式 2：scp 打包上传（本地）
tar --exclude='backend/venv' --exclude='frontend/node_modules' -czf assistmind.tar.gz .
scp assistmind.tar.gz user@<服务器IP>:~/
ssh user@<服务器IP> 'tar -xzf assistmind.tar.gz && cd AssistMind && ls'
```

### 4. 一键部署

```bash
cd AssistMind
bash deploy/deploy.sh
```

脚本会：检查 Docker → 生成 `.env.prod`（随机 `JWT_SECRET`，交互式填入 `DeepSeek API Key`，或 `DEEPSEEK_API_KEY=sk-xxx bash deploy/deploy.sh` 传入）→ `docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build` → 等待就绪 → 打印访问地址。

**首次构建耗时约 10-20 分钟**（torch + langchain 依赖 + BGE 模型下载 + 首次灌知识库 embed）；再次部署走层缓存会很快。构建期间可另开终端 `tail -f` 观察。

### 5. 访问验证

- 浏览器打开 `http://<服务器IP>/`，用 `admin / admin123` 登录。
- 按 `docs/demo-script.md` 五段演示走查一遍（FAQ 问答 → 订单物流 → 退货闭环 → 运维诊断 → Admin）。
- 后端文档 `http://<服务器IP>:8001/docs`。

### 6.（可选）HTTPS

面试演示先 `http://IP` 完全够用。要 https 两条路：

- 有域名：给域名解析到服务器 → 装 `certbot`（Let's Encrypt 免费证书）自动续期。
- 没域名：直接加一层 Cloudflare 隧道（见方案 B），url 自带 https。

---

## 三、运维命令表（在仓库根执行）

| 操作 | 命令 |
|---|---|
| 查看全部日志（跟随后端） | `docker compose -f docker-compose.yml -f docker-compose.app.yml logs -f backend` |
| 停止全部 | `docker compose -f docker-compose.yml -f docker-compose.app.yml down` |
| 重启应用 | `docker compose -f docker-compose.yml -f docker-compose.app.yml restart backend frontend` |
| 更新代码后重部署 | 重新上传代码 → `docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build` |
| 重灌电商/运维知识库 | `docker compose -f docker-compose.yml -f docker-compose.app.yml exec backend python scripts/seed_mall_kb.py --reset`（运维库同法换 seed_ops_kb.py） |
| 主动检查后端健康 | `curl http://localhost:8001/api/v1/health` |
| 进入后端容器排障 | `docker compose -f docker-compose.yml -f docker-compose.app.yml exec backend sh` |

---

## 四、上线注意事项

- **API Key 安全**：`.env.prod` 已被 `.gitignore` 忽略**不要提交**；泄露后到 DeepSeek 平台重置。
- **公开演示口径（面试必读）**：业务数据是演示清单（取材自 macrozheng/mall 官方文档 + 自建规则），运维诊断是受控场景——被问到时主动声明，口径见 README「六、知识来源与演示口径」。
- **限流默认开启**：`RATE_LIMIT_PER_MINUTE=60`（按客户端 IP 固定窗口，Redis 不可用自动放行），防止分享链接后被刷爆 token；需要临时放开可在 `.env.prod` 调高后 `restart backend`。
- **Langfuse / Prometheus 端口默认也映射到宿主机**（3001/9090），如非必要可在安全组不对外放行。
- **重启幂等**：`init_db` / `seed_mall_db` / 知识库灌库全部幂等（集合非空跳过），无需手工干预。
- **磁盘**：容器重建会累积旧镜像，定期 `docker image prune` 释放空间。

---

## 五、文件清单

| 文件 | 作用 |
|---|---|
| `deploy/deploy.sh` | 一键部署（环境检查 / 生成 .env.prod / 构建启动 / 等待就绪） |
| `.env.prod.example` | 生产环境变量模板（复制为 `.env.prod` 填写） |
| `docker-compose.app.yml` | 应用层 overlay：backend + frontend 服务（与根 `docker-compose.yml` 叠加） |
| `backend/Dockerfile` | 后端镜像（含 entrypoint 自动 init/灌库） |
| `backend/docker-entrypoint.sh` | 容器启动入口：init_db → 幂等灌库 → uvicorn |
