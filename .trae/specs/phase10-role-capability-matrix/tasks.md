# Tasks

- [x] Task 1: 后端工单详情归属隔离 → Req: 工单详情归属隔离
  - [x] 1.1 先写失败测试：tests/unit/test_ticket_api.py 新增 user 查他人工单返回 404、agent 查任意工单 200（参考该文件现有鉴权测试模式）
  - [x] 1.2 实现：backend/app/api/ticket.py 的 get_ticket_api 中，role=="user" 且 ticket.user_id != user.username 时返回 404"工单不存在"
  - [x] 1.3 运行定向测试确认全绿
  Files: backend/app/api/ticket.py, backend/tests/unit/test_ticket_api.py
  Run: cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; venv\Scripts\python.exe -m pytest tests\unit\test_ticket_api.py -q
  Expected: 新增测试通过，既有测试无回归

- [x] Task 2: 后端知识库列表角色限制 → Req: 知识库列表角色限制
  - [x] 2.1 先写失败测试：tests/unit/test_knowledge_api.py 新增 user 角色调 /knowledge/list 返回 403、admin/agent 200（mock Qdrant 保持既有模式）
  - [x] 2.2 实现：backend/app/api/knowledge.py 的 list_docs 依赖从 get_current_user 改为角色校验（agent/admin 放行，其余 403；可用 Depends 内联或新增 require_staff 依赖）
  - [x] 2.3 运行定向测试
  Files: backend/app/api/knowledge.py, backend/tests/unit/test_knowledge_api.py
  Run: cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; venv\Scripts\python.exe -m pytest tests\unit\test_knowledge_api.py -q
  Expected: user 403 / admin+agent 200，既有测试无回归

- [x] Task 3: 前端工单页角色视角 → Req: 工单页角色视角
  - [x] 3.1 先写失败测试：frontend/tests/unit/Tickets.spec.js 新增 user 视角无操作列、agent 视角有操作列与客户列（参考现有 spec 的 store mock 模式）
  - [x] 3.2 实现：frontend/src/views/Tickets/index.vue 引入 useAuthStore，user 角色用 v-if 隐藏"操作"列；agent/admin 增加"客户"列（prop: user_id）
  - [x] 3.3 运行前端测试
  Files: frontend/src/views/Tickets/index.vue, frontend/tests/unit/Tickets.spec.js
  Run: cd frontend; npm run test:unit
  Expected: 全部测试通过

- [x] Task 4: 前端知识库页按钮按角色隐藏 → Req: 知识库页管理按钮按角色隐藏
  - [x] 4.1 先写失败测试：frontend/tests/unit/Knowledge.spec.js 新增 agent 视角无删除/重建按钮（参考现有 spec 模式）
  - [x] 4.2 实现：frontend/src/views/Knowledge/index.vue 引入 useAuthStore，非 admin 用 v-if 隐藏"重建索引"工具栏按钮与表格"删除"操作按钮
  - [x] 4.3 运行前端测试
  Files: frontend/src/views/Knowledge/index.vue, frontend/tests/unit/Knowledge.spec.js
  Run: cd frontend; npm run test:unit
  Expected: 全部测试通过

- [x] Task 5: 回归验证 → Req: 角色功能矩阵
  - [x] 5.1 后端全量：pytest tests -q -k "not integration"（仅允许既有 test_langfuse_infra 5 个环境失败）
  - [x] 5.2 前端全量：npm run test:unit
  - [x] 5.3 ruff check 改动的后端文件
  Run: cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; venv\Scripts\python.exe -m pytest tests -q -k "not integration"; cd ../frontend; npm run test:unit
  Expected: 除已知 Langfuse 环境失败外全绿

# Task Dependencies
- Task 1、Task 2 相互独立，可并行
- Task 3、Task 4 相互独立，可并行，且不依赖后端任务（前端按角色渲染不依赖 API 变化）
- Task 5 依赖 Task 1-4 全部完成
