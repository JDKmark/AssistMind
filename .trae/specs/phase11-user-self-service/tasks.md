# Tasks

- [x] Task 1: 后端 my-orders 数据源与 API → Req: 用户订单列表
  - [x] 1.1 TDD：tests/unit/test_mall_data_source.py 新增 my_orders 测试（user1 返回 2 单含 items、user2 返回 2 单、状态过滤、分页、agent 空结果）
  - [x] 1.2 实现 base.py 抽象 `my_orders(self, *, requester_username, status=None, limit=50, offset=0) -> dict`（返回 {orders, total}，orders 项含 items；docstring 写明降级语义）
  - [x] 1.3 mock_source.py：从 ORDERS 按 owner+status 过滤，倒序，切片分页，行含完整 items
  - [x] 1.4 real_source.py：select(MallOrder) + MallOrderItem 联查组装 items；异常降级 {"orders": [], "total": 0, "degraded": ["postgres"]} + logger.warning
  - [x] 1.5 data_source.py 门面转发
  - [x] 1.6 TDD：tests/unit/test_mall_api.py 新增 my-orders 测试（user JWT 200 且强制 owner=本人——monkeypatch 数据源断言 requester_username 透传；无 token 401；忽略客户端 owner 参数）
  - [x] 1.7 实现 app/api/mall.py 新增 GET /my-orders（Depends(get_current_user)，服务端取 user.username）
  - [x] 1.8 集成：tests/integration/test_mall_pg.py 新增 real my_orders 契约测试（user1 两单含 items/agent 空）
  Files: backend/app/core/mall/{base,mock_source,real_source,data_source}.py, backend/app/api/mall.py, backend/tests/unit/test_mall_data_source.py, backend/tests/unit/test_mall_api.py, backend/tests/integration/test_mall_pg.py
  Run: cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; venv\Scripts\python.exe -m pytest tests\unit\test_mall_data_source.py tests\unit\test_mall_api.py tests\integration\test_mall_pg.py -q
  Expected: 全绿；ruff check 通过

- [x] Task 2: 后端用户确认关闭工单 → Req: 工单状态流转（含用户确认关闭）
  - [x] 2.1 TDD：tests/unit/test_ticket_service.py 新增——user 关闭本人 resolved 工单成功；user 关闭他人工单抛 PermissionError；agent/admin 关闭不受影响（沿用现有 mock session 模式）
  - [x] 2.2 实现 ticket_service.py：ROLE_PERMISSIONS["resolved->closed"] 加 "user"；update_status 增加 user_id: str = "" 参数，role=="user" 且 ticket.user_id != user_id 时抛 PermissionError
  - [x] 2.3 TDD：tests/unit/test_ticket_api.py 新增——user 关闭本人 resolved 工单 200 / 关闭他人工单 403
  - [x] 2.4 实现 app/api/ticket.py update_ticket_status_api 传 user_id=user.get("username")
  Files: backend/app/core/ticket_service.py, backend/app/api/ticket.py, backend/tests/unit/test_ticket_service.py, backend/tests/unit/test_ticket_api.py
  Run: cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; venv\Scripts\python.exe -m pytest tests\unit\test_ticket_service.py tests\unit\test_ticket_api.py -q
  Expected: 全绿；既有流转测试无回归

- [x] Task 3: 前端我的订单页 → Req: 用户订单列表
  - [x] 3.1 frontend/src/api/mall.js 新增 listMyOrders(params)
  - [x] 3.2 新建 frontend/src/views/Orders/index.vue：列表（订单号/状态 tag/实付/物流单号/下单时间）+ 状态筛选（待付款/待发货/已发货/已完成，change 重置第 1 页）+ el-pagination + el-table type=expand 展示 items（品名/规格/单价/数量）；空态友好；失败 ElMessage.error
  - [x] 3.3 router 新增 /orders 路由（name Orders，title 我的订单，无 roles）；MainLayout 菜单"我的订单"（icon Goods）v-if="auth.role === 'user'"
  - [x] 3.4 TDD：新建 frontend/tests/unit/Orders.spec.js——mock listMyOrders：渲染订单行与状态、展开行显示商品明细、筛选参数透传、失败不崩溃（参考 Admin.spec.js 的表格 stub provide/inject 模式）
  Files: frontend/src/api/mall.js, frontend/src/views/Orders/index.vue, frontend/src/router/index.js, frontend/src/components/Layout/MainLayout.vue, frontend/tests/unit/Orders.spec.js
  Run: cd frontend; npm run test:unit
  Expected: 全绿（含既有 44 个）

- [x] Task 4: 前端工单详情与确认关闭 → Req: 工单详情查看 / 工单状态流转（含用户确认关闭）
  - [x] 4.1 frontend/src/api/ticket.js 新增 getTicket(id)
  - [x] 4.2 Tickets/index.vue：操作列改为——全角色"详情"按钮（弹窗展示 标题/描述/优先级/状态/创建时间/更新时间，调 getTicket）；user 且 row.status==='resolved' 显示"确认解决"按钮（updateTicketStatus(row.id,'closed')，成功后刷新）；agent/admin 保留状态下拉
  - [x] 4.3 TDD：Tickets.spec.js 新增——详情弹窗渲染完整描述；user 视角 resolved 行出现确认解决按钮、open 行不出现；点击后调用 updateTicketStatus
  Files: frontend/src/api/ticket.js, frontend/src/views/Tickets/index.vue, frontend/tests/unit/Tickets.spec.js
  Run: cd frontend; npm run test:unit
  Expected: 全绿

- [x] Task 5: 回归验证
  - [x] 5.1 后端全量 pytest tests -q -k "not integration"（仅允许 test_langfuse_infra 5 个既有环境失败）
  - [x] 5.2 前端 npm run test:unit
  - [x] 5.3 ruff check 全部改动后端文件
  - [x] 5.4 手验清单：user1 登录→我的订单见 2 单可展开明细；user1 工单详情可见；user1 对 resolved 工单点确认解决→closed；user2 对 user1 工单关闭→403
  Run: cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; venv\Scripts\python.exe -m pytest tests -q -k "not integration"; cd ..\frontend; npm run test:unit
  Expected: 除已知环境失败外全绿

# Task Dependencies
- Task 1 与 Task 2 相互独立，可并行
- Task 3 依赖 Task 1 的 API 契约（已写死，可同时开工，联调在 Task 5）
- Task 4 依赖 Task 2 的接口语义（已写死，可同时开工）
- Task 5 依赖 Task 1-4 全部完成

