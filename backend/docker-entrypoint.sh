#!/usr/bin/env bash
# 后端容器启动入口：初始化数据库 → 幂等灌库 → 启动 uvicorn。
# 知识库按 Qdrant 集合是否为空自动决定是否灌入（非空跳过，避免每次重启都重新 embed）。
set -euo pipefail

: "${AUTO_SEED_KB:=true}"
: "${QDRANT_URL:=http://qdrant:6333}"
: "${QDRANT_COLLECTION:=assistmind_docs}"

echo "[entrypoint] == AssistMind backend 初始化 =="

# 1. 建表 + 初始用户（幂等）。postgres 未就绪时重试。
for i in $(seq 1 20); do
    if python scripts/init_db.py; then
        break
    fi
    echo "[entrypoint] init_db 失败，重试 (${i}/20)…"
    sleep 3
done

# 2. 电商业务数据落 PostgreSQL（幂等：表非空跳过；MALL_DATA_SOURCE=mock 时本步可忽略）
python scripts/seed_mall_db.py || echo "[entrypoint] seed_mall_db 未执行成功（mock 模式可忽略），继续"

# 3. 知识库灌库：集合非空则跳过
if [ "${AUTO_SEED_KB}" = "true" ]; then
    count="$(
        python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('${QDRANT_URL}/collections/${QDRANT_COLLECTION}'))['result']['points_count'])" 2>/dev/null \
        || echo "-1"
    )"
    if [ "${count}" -gt 0 ] 2>/dev/null; then
        echo "[entrypoint] Qdrant 已有 ${count} 条文档，跳过灌库（秒起）"
    else
        echo "[entrypoint] 首次启动：灌入电商+运维知识库（含 embedding，约 1-3 分钟）…"
        python scripts/seed_mall_kb.py --reset
        python scripts/seed_ops_kb.py --reset
    fi
fi

echo "[entrypoint] 启动 uvicorn :8001 …"
exec uvicorn app.main:app --host 0.0.0.0 --port 8001
