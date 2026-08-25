# AssistMind 面试演示一键启动（Windows PowerShell）
# 用法：.\start-demo.ps1
# 演示剧本见 docs/demo-script.md
# 说明：比 start-dev.ps1 更稳更省事——等依赖健康、自动建库/灌库（集合非空跳过，秒起）、起后端不加 --reload。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# 与 backend/.env 保持一致的数据库名（compose 默认建 smart_cs，这里确保 assistmind 存在）
$DbName = "assistmind"

Write-Host ""
Write-Host "=== AssistMind 面试演示启动 ===" -ForegroundColor Cyan

# ---------- 0. 预检 ----------
Write-Host "[0/5] 预检环境..." -ForegroundColor Yellow
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERR] 未找到 docker，请先安装 Docker Desktop。" -ForegroundColor Red
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERR] Docker 未运行，请先启动 Docker Desktop。" -ForegroundColor Red
    exit 1
}
Write-Host "      Docker OK" -ForegroundColor Green

$envPath = Join-Path $Root "backend/.env"
$dsKeySet = $false
if (Test-Path $envPath) {
    $dsKeySet = [bool](Select-String -Path $envPath -Pattern '^\s*DEEPSEEK_API_KEY\s*=\s*\S+' -Quiet)
}
if ($dsKeySet) {
    Write-Host "      DEEPSEEK_API_KEY：已配置" -ForegroundColor Green
} else {
    Write-Host "      [警告] backend/.env 未配置 DEEPSEEK_API_KEY —— 演示将降级到 Ollama/模板兜底，回答质量下降。" -ForegroundColor Yellow
    Write-Host "            建议先在 backend/.env 填入后重跑本脚本（见 README「快速启动」）。" -ForegroundColor Yellow
}

# ---------- 1. 启动基础设施 ----------
Write-Host "[1/5] 启动基础设施容器（qdrant/redis/postgres 等）..." -ForegroundColor Yellow
docker compose up -d *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERR] docker compose up 失败，请查看 docker compose ps / logs。" -ForegroundColor Red
    exit 1
}

function Wait-ContainerHealthy {
    param([string]$Name, [int]$TimeoutSec = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $status = docker inspect --format '{{.State.Health.Status}}' $Name 2>$null
        if ($status -eq "healthy") {
            Write-Host "      $Name healthy" -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 3
    }
    Write-Host "      [警告] $Name 未在 ${TimeoutSec}s 内 healthy（当前: $status），继续尝试…" -ForegroundColor Yellow
}

Wait-ContainerHealthy "smart-cs-qdrant"
Wait-ContainerHealthy "smart-cs-redis"
Wait-ContainerHealthy "smart-cs-postgres"

# ---------- 2. 初始化数据库 + 知识库 ----------
Write-Host "[2/5] 初始化数据库与知识库..." -ForegroundColor Yellow
Push-Location (Join-Path $Root "backend")
try {
    # 确保目标数据库存在（compose 默认建 smart_cs，本项目用 assistmind）
    $ensureDb = @'
import asyncio, asyncpg

async def main():
    conn = await asyncpg.connect(
        host="localhost", port=5432,
        user="postgres", password="postgres",
        database="postgres", timeout=10,
    )
    exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname='assistmind'")
    if not exists:
        await conn.execute("CREATE DATABASE assistmind")
        print("db assistmind created")
    else:
        print("db assistmind exists")
    await conn.close()

asyncio.run(main())
'@
    venv\Scripts\python.exe -c $ensureDb

    $env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/$DbName"
    venv\Scripts\python.exe scripts/init_db.py
    if ($LASTEXITCODE -ne 0) { throw "init_db 失败" }

    # 电商业务数据落 PG（幂等：表非空跳过；MALL_DATA_SOURCE=mock 时本步可忽略）
    venv\Scripts\python.exe scripts/seed_mall_db.py

    # 知识库：集合非空则跳过，避免每次演示都重新 embed
    $count = -1
    try {
        $coll = Invoke-RestMethod -Uri "http://localhost:6333/collections/assistmind_docs" -TimeoutSec 10
        $count = [int]$coll.result.points_count
    } catch {
        $count = -1   # 集合不存在 → 需要灌库
    }
    if ($count -gt 0) {
        Write-Host "      向量库已有 $count 条文档，跳过灌库（秒起）" -ForegroundColor Green
    } else {
        Write-Host "      首次启动：灌入电商+运维知识库（含 embedding，约 1-3 分钟）..." -ForegroundColor Yellow
        venv\Scripts\python.exe scripts/seed_mall_kb.py --reset
        if ($LASTEXITCODE -ne 0) { throw "seed_mall_kb 失败" }
        venv\Scripts\python.exe scripts/seed_ops_kb.py --reset
        if ($LASTEXITCODE -ne 0) { throw "seed_ops_kb 失败" }
    }
}
finally {
    Pop-Location
}

# ---------- 3. 启动后端 ----------
Write-Host "[3/5] 启动后端（端口 8001，不加 --reload 更稳）..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-Command", "cd backend; venv\Scripts\python.exe -m uvicorn app.main:app --port 8001"

# ---------- 4. 启动前端 ----------
Write-Host "[4/5] 启动前端（端口 5173）..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-Command", "cd frontend; npm run dev"

# ---------- 5. 等待就绪并输出 ----------
Write-Host "[5/5] 等待后端就绪（首次需构建 BM25 索引）..." -ForegroundColor Yellow
$deadline = (Get-Date).AddSeconds(120)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-RestMethod -Uri "http://localhost:8001/api/v1/health" -TimeoutSec 3
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds 3
    }
}

Write-Host ""
Write-Host "=== 启动完成 ===" -ForegroundColor Green
Write-Host "前端:        http://localhost:5173" -ForegroundColor Cyan
Write-Host "后端 API 文档: http://localhost:8001/docs" -ForegroundColor Cyan
Write-Host "测试账号:    admin/admin123（演示全程用这个）" -ForegroundColor Cyan
if (-not $ready) {
    Write-Host "[警告] 后端健康检查未在 120s 内就绪，请稍等浏览器重试；详见后端窗口日志。" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "演示数据速查（背熟现场直接念）：" -ForegroundColor Cyan
Write-Host "  订单  20240801001 已发货(顺丰 SF1234567890) / 01002 待发货 / 01003 已完成(可退货) / 01004 待付款(退款被拒)"
Write-Host "  商品  P001 华为Mate60Pro ¥6999 / P003 戴森V12 ¥4990(无忧退货) / P005 联想拯救者 ¥8999"
Write-Host ""
Write-Host "完整演示剧本（五段 + 话术 + 防翻车）：docs/demo-script.md" -ForegroundColor Cyan
Write-Host ""
