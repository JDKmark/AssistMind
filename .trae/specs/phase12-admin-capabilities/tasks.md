# phase12-admin-capabilities Tasks

- [ ] Task 1: 审计模型与管理员服务基础 → Req: 管理操作审计、管理员业务概览
  - [ ] 1.1 新增 AuditLog 模型与幂等数据库初始化
    Files: `backend/app/models/audit.py`, `backend/scripts/init_db.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit -q -k "audit or admin"`
    Expected: 审计表可创建，字段含操作者、动作、目标、详情和时间。
  - [ ] 1.2 新增审计服务，业务失败不写成功记录，审计失败 logger.warning 后返回旁路结果
    Files: `backend/app/core/audit_service.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_audit_service.py -q`
    Expected: 成功写入、分页查询和写入失败旁路测试通过。

- [ ] Task 2: 用户启用状态与登录校验 → Req: 登录账号启用校验、受限用户角色与状态管理
  - [ ] 2.1 为 User 增加 `is_active`，初始化存量数据默认 true
    Files: `backend/app/models/user.py`, `backend/scripts/init_db.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_auth_api.py -q`
    Expected: 旧账号保持可登录，停用字段可读取。
  - [ ] 2.2 登录密码校验成功后拒绝停用账号
    Files: `backend/app/api/auth.py`, `backend/tests/unit/test_auth_api.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_auth_api.py -q`
    Expected: 停用账号 403 不签发 token，启用账号响应形状不变。

- [ ] Task 3: 管理员用户 API → Req: 管理员用户列表、受限用户角色与状态管理
  - [ ] 3.1 先写 admin 用户列表和更新接口失败测试，固定响应与错误码
    Files: `backend/tests/unit/test_admin_api.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_admin_api.py -q -k "user"`
    Expected: 初始失败原因是 admin 用户 API 尚未实现。
  - [ ] 3.2 实现用户分页查询和 user/agent 角色、启用状态更新
    Files: `backend/app/core/admin_service.py`, `backend/app/api/admin.py`, `backend/app/main.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_admin_api.py -q -k "user"`
    Expected: admin 成功，agent/user 403；不返回密码；admin 目标和 admin 新角色被拒绝；成功变更写审计。

- [ ] Task 4: 退款运营数据源与 API → Req: 管理员退款列表、退款状态流转
  - [ ] 4.1 先写 Mock/Real 退款列表、状态机、重复终态和 owner 过滤失败测试
    Files: `backend/tests/unit/test_mall_data_source.py`, `backend/tests/unit/test_mall_api.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_mall_data_source.py tests/unit/test_mall_api.py -q -k "refund"`
    Expected: 初始失败原因是管理退款契约尚未实现。
  - [ ] 4.2 扩展 MallDataSource 及 Mock/Real/门面实现
    Files: `backend/app/core/mall/base.py`, `backend/app/core/mall/mock_source.py`, `backend/app/core/mall/real_source.py`, `backend/app/core/mall/data_source.py`, `backend/app/models/mall.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_mall_data_source.py -q -k "refund"`
    Expected: 字段形状、合法状态、非法流转和 PostgreSQL degraded 语义一致。
  - [ ] 4.3 增加 admin 退款列表与状态流转 API，并写审计
    Files: `backend/app/api/mall.py`, `backend/tests/unit/test_mall_api.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_mall_api.py -q -k "refund"`
    Expected: 仅 admin 可访问，处理中可进入两个终态，终态重复流转 400。

- [ ] Task 5: 管理员概览 API → Req: 管理员业务概览
  - [ ] 5.1 先写概览字段、统计分布和依赖降级测试
    Files: `backend/tests/unit/test_admin_api.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_admin_api.py -q -k "overview"`
    Expected: 初始失败原因是聚合接口尚未实现。
  - [ ] 5.2 实现跨领域概览聚合，单个依赖失败不影响其他域
    Files: `backend/app/core/admin_service.py`, `backend/app/api/admin.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_admin_api.py -q -k "overview"`
    Expected: 响应包含六组统计和 degraded，非 admin 403。

- [ ] Task 6: 管理员审计查询 API → Req: 管理操作审计
  - [ ] 6.1 先写按 actor/action/target_type 分页查询和权限测试
    Files: `backend/tests/unit/test_admin_api.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_admin_api.py -q -k "audit"`
    Expected: 初始失败原因是审计查询 API 尚未实现。
  - [ ] 6.2 实现 admin audit API 和响应脱敏
    Files: `backend/app/api/admin.py`, `backend/app/core/audit_service.py`
    Run: `venv\Scripts\python.exe -m pytest tests/unit/test_admin_api.py -q -k "audit"`
    Expected: 只返回审计契约字段，不返回密码、token 或敏感凭证。

- [ ] Task 7: Admin 页面业务运营视图 → Req: 管理员业务运营界面、现有管理员能力保留
  - [ ] 7.1 先写用户角色、退款操作、工单筛选、审计刷新和局部失败测试
    Files: `frontend/tests/unit/Admin.spec.js`
    Run: `npm run test:unit -- Admin.spec.js`
    Expected: 初始失败原因是新管理视图和 API 调用尚未实现。
  - [ ] 7.2 新增管理员 API 封装并扩展 Admin 页面
    Files: `frontend/src/api/admin.js`, `frontend/src/api/mall.js`, `frontend/src/views/Admin/index.vue`
    Run: `npm run test:unit -- Admin.spec.js`
    Expected: admin 页面完成六个任务视图；操作后刷新受影响列表和概览；错误局部展示。
  - [ ] 7.3 更新路由/菜单权限测试，保持 user/agent 无 admin 入口
    Files: `frontend/src/router/index.js`, `frontend/src/components/Layout/MainLayout.vue`, `frontend/tests/unit`
    Run: `npm run test:unit`
    Expected: 前端全量测试通过，非 admin 访问 admin 路由回 chat。

- [ ] Task 8: 回归与关键场景验证 → Req: 全部 Requirements
  - [ ] 8.1 运行后端非 integration 全量测试并修复 phase12 引入的失败
    Files: `backend/tests/`
    Run: `venv\Scripts\python.exe -m pytest tests -q -k "not integration"`
    Expected: phase12 新增和既有测试通过；已知 Langfuse 环境污染失败单独记录。
  - [ ] 8.2 运行 Ruff 与前端全量测试
    Files: `backend/app/`, `frontend/src/`
    Run: `ruff check app tests`; `npm run test:unit`
    Expected: Ruff 通过，前端测试全绿。
  - [ ] 8.3 真实关键流程验证
    Files: `backend/tests/integration/` 或手工 HTTP 验证
    Run: `venv\Scripts\python.exe -m pytest tests/integration -q -k "admin or mall"`
    Expected: admin 管理 user1/user2 订单与退款、处理工单、查询审计；user/agent 访问管理 API 均 403。

# Task Dependencies
- Task 2 依赖 Task 1 的审计契约可独立并行，但最终需共用数据库初始化。
- Task 3 依赖 Task 1、Task 2。
- Task 4 依赖 Task 1 的审计写入契约。
- Task 5 依赖 Task 3、Task 4，并复用现有 ticket/feedback/knowledge 查询。
- Task 6 依赖 Task 1、Task 3、Task 4。
- Task 7 依赖 Task 3、Task 4、Task 5、Task 6。
- Task 8 依赖 Task 2 至 Task 7。
