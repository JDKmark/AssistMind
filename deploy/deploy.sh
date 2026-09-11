#!/usr/bin/env bash
# AssistMind 线上演示一键部署（Ubuntu / Debian + Docker）
# 用法：bash deploy/deploy.sh
# 流程：检查环境 → 生成/补全 .env.prod → 构建并启动（overlay compose）→ 等待就绪 → 输出访问信息。
# 前置：已安装 Docker + compose 插件；服务器安全组/防火墙放行 80 与 8001。
set -euo pipefail

# 定位仓库根
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m[OK]  %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!]   %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[ERR] %s\033[0m\n' "$*" >&2; exit 1; }

# ---------- 0. 检查环境 ----------
step "0. 检查 Docker 环境"
command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker（参考 deploy/README.md）。"
docker info >/dev/null 2>&1 || die "Docker 未运行（服务未启动或当前用户不在 docker 组）。"
docker compose version >/dev/null 2>&1 || die "需要 docker compose 插件（v2）。"
ok "docker + compose 可用"

free_gb=$(($(df -Pk . | awk 'NR==2{print $4}') / 1024 / 1024))
if [ "${free_gb}" -lt 15 ]; then
    warn "剩余磁盘 ${free_gb}GB，首次构建需下载 torch(~2GB)+ embedding 模型，建议 ≥20GB。"
fi

# ---------- 1. 准备 .env.prod ----------
step "1. 准备 .env.prod"
if [ ! -f .env.prod ]; then
    cp .env.prod.example .env.prod
    warn "已从 .env.prod.example 生成 .env.prod（含随机 JWT_SECRET）。"
fi

# JWT_SECRET：空则生成
if ! grep -q '^JWT_SECRET=.\+' .env.prod; then
    js="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${js}|" .env.prod
    ok "JWT_SECRET 已生成。"
fi

# DeepSeek Key：优先环境变量，其次交互输入
if ! grep -q '^DEEPSEEK_API_KEY=.\+' .env.prod; then
    if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
        sed -i "s|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}|" .env.prod
    elif [ -t 0 ]; then
        printf "请输入 DeepSeek API Key（不会回显，也可 DEEPSEEK_API_KEY=xxx bash deploy/deploy.sh 传入）: "
        read -r -s ds_key
        printf '\n'
        [ -n "${ds_key}" ] || die "未输入 Key。"
        sed -i "s|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=${ds_key}|" .env.prod
    else
        die ".env.prod 缺少 DEEPSEEK_API_KEY，请先在 .env.prod 填写后重跑。"
    fi
fi
ok "DEEPSEEK_API_KEY 就绪（将不再显示明文）。"

# DATABASE_MIGRATION_URL：生产必须配置独立迁移账号（phase13 最小权限）。
# runtime（DATABASE_URL）只跑业务 DML；DDL/授权由迁移账号执行；不回退默认凭据。
if ! grep -q '^DATABASE_MIGRATION_URL=.\+' .env.prod; then
    die ".env.prod 缺少 DATABASE_MIGRATION_URL：生产部署必须配置独立数据库迁移账号（runtime 账号仅业务 DML，不拥有 DDL 权限）。参考 .env.prod.example。"
fi
ok "DATABASE_MIGRATION_URL 已配置（迁移账号与 runtime 账号分离）。"

# runtime 账号校验：禁止 postgres 超级用户 / 占位符未填写 / 与迁移账号复用。
# （python 参考实现：app/config.py Settings.db_minimal_privilege_error，容器入口使用之；
#   此处 bash 版为部署前置校验，生产语义更严——postgres 无条件拒绝；两处修改需同步）
runtime_url="$(grep -E '^DATABASE_URL=' .env.prod | tail -1 | cut -d= -f2-)"
migration_url="$(grep -E '^DATABASE_MIGRATION_URL=' .env.prod | tail -1 | cut -d= -f2-)"
runtime_user="${runtime_url#*://}"; runtime_user="${runtime_user%%@*}"; runtime_user="${runtime_user%%:*}"
migration_user="${migration_url#*://}"; migration_user="${migration_user%%@*}"; migration_user="${migration_user%%:*}"
if [ -z "${runtime_user}" ] || [ "${runtime_user}" = "<your_runtime_user>" ]; then
    die "DATABASE_URL 未填写独立 runtime 账号：生产部署必须配置非 postgres 的独立账号（模板中 <your_runtime_user> 需替换成实际账号）。"
fi
if [ "${runtime_user}" = "postgres" ]; then
    die "DATABASE_URL 仍使用 postgres 超级用户：最小权限要求 runtime 账号不是默认超级用户，请改为独立账号（如 assistmind_runtime）。"
fi
if [ -n "${migration_user}" ] && [ "${migration_user}" = "${runtime_user}" ]; then
    die "DATABASE_MIGRATION_URL 与 DATABASE_URL 使用同一账号：迁移账号与 runtime 账号必须分离。"
fi
ok "runtime 账号已校验（${runtime_user}，非 postgres、与迁移账号分离）。"

# ---------- 2. 构建并启动 ----------
step "2. 构建并启动应用"
warn "首次构建需安装 torch/langchain 等依赖，约 10-20 分钟；二次构建走层缓存会很快。"
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build

# ---------- 3. 等待就绪 ----------
step "3. 等待服务就绪（首次含模型下载+灌知识库，最多 10 分钟）"
backend_up=0
for i in $(seq 1 120); do
    if curl -fsS -o /dev/null "http://localhost:8001/api/v1/health" 2>/dev/null; then
        backend_up=1
        break
    fi
    printf '.'
    sleep 5
done
printf '\n'
[ "${backend_up}" = "1" ] || warn "backend 未就绪，请用 docker compose logs -f backend 排查（缺少 DeepSeek Key / Qdrant 未灌库等）。"

frontend_up=0
for i in $(seq 1 12); do
    if curl -fsS -o /dev/null "http://localhost/" 2>/dev/null; then
        frontend_up=1
        break
    fi
    sleep 3
done

# ---------- 4. 输出访问信息 ----------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
: "${IP:=<服务器公网IP>}"
URL="http://${IP}"
printf '\n'
printf '============================================================\n'
printf ' 前端页面:      %s/\n'       "${URL}"
printf ' 后端 API 文档: %s:8001/docs\n' "${URL}"
printf ' 测试账号:      admin / admin123\n'
printf '------------------------------------------------------------\n'
printf ' 查看日志:      docker compose -f docker-compose.yml -f docker-compose.app.yml logs -f\n'
printf ' 停止:          docker compose -f docker-compose.yml -f docker-compose.app.yml down\n'
printf ' 重启:          docker compose -f docker-compose.yml -f docker-compose.app.yml restart\n'
printf ' 重灌知识库:    docker compose -f docker-compose.yml -f docker-compose.app.yml exec backend python scripts/seed_mall_kb.py --reset\n'
printf '                然后同上执行 seed_ops_kb.py --reset\n'
printf '============================================================\n'
[ "${backend_up}" = "1" ] && ok "部署完成，浏览器打开 ${URL}/（需在安全组放行 80 端口）" \
                          || warn "部署未完全就绪，请先排查日志。"
exit 0
