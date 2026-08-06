# AssistMind 开发环境一键启动脚本（Windows PowerShell）
# 用法：.\start-dev.ps1

Write-Host "=== AssistMind 开发环境启动 ===" -ForegroundColor Cyan

# 1. 启动 Docker 依赖
Write-Host "[1/4] 启动 Docker 依赖服务..." -ForegroundColor Yellow
docker-compose up -d
Start-Sleep -Seconds 5

# 2. 初始化数据库（首次）
Write-Host "[2/4] 初始化数据库..." -ForegroundColor Yellow
Push-Location backend
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/assistmind"
venv\Scripts\python.exe scripts/init_db.py
Pop-Location

# 3. 启动后端
Write-Host "[3/4] 启动后端（端口 8001）..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-Command", "cd backend; uvicorn app.main:app --reload --port 8001"

# 4. 启动前端
Write-Host "[4/4] 启动前端（端口 5173）..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-Command", "cd frontend; npm run dev"

Write-Host ""
Write-Host "=== 启动完成 ===" -ForegroundColor Green
Write-Host "前端: http://localhost:5173" -ForegroundColor Cyan
Write-Host "后端 API 文档: http://localhost:8001/docs" -ForegroundColor Cyan
Write-Host "测试账号: admin/admin123" -ForegroundColor Cyan
