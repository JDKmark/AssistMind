# phase11-user-self-service Spec

- Mode: lite
- Status: done
- Created: 2026-08-23

## Why

phase10 角色矩阵落地后，user 角色只剩"展示"：工单页无详情查看、无任何可执行操作（操作列被隐藏后用户创建工单即失联）；订单没有用户侧页面（只能在聊天按单号逐个问）；工单 resolved 后用户无法自行确认关闭。本 spec 补齐用户自助闭环：看得到详情、管得了订单、关得掉工单。

## What Changes

**1. 我的订单页（user 自助核心）**
- backend/app/api/mall.py：新增 `GET /api/v1/mall/my-orders`（仅需登录，服务端强制 `owner_username=当前用户`，不信任客户端过滤参数），返回 `{orders, total}`，orders 项含商品明细 items（区别于 admin 的 list_orders 摘要）
- backend/app/core/mall/{base,mock_source,real_source,data_source}.py：新增 `my_orders(requester_username, status, limit, offset)` 数据源方法（mock 从 ORDERS 过滤含 items；real 查询订单+明细，失败降级 `{"orders": [], "total": 0, "degraded": ["postgres"]}`）
- frontend/src/api/mall.js：新增 `listMyOrders(params)`
- frontend/src/views/Orders/index.vue（新增）：订单卡片/表格 + 状态筛选 + 分页 + 行展开显示商品明细（品名/规格/单价/数量）；物流单号存在时展示
- frontend/src/router/index.js：新增 `/orders` 路由（title 我的订单，无 roles 限制）
- frontend/src/components/Layout/MainLayout.vue：菜单项"我的订单"仅对 `auth.role === 'user'` 显示（agent/admin 订单入口是管理后台与聊天，避免菜单噪音）

**2. 工单详情查看（全角色）**
- frontend/src/views/Tickets/index.vue：每行新增"详情"按钮，弹窗展示完整字段（标题/描述/优先级/状态/创建时间/更新时间）；数据复用 `GET /api/v1/ticket/{id}`（user 已有归属隔离，直接调 getTicket API）
- frontend/src/api/ticket.js：新增 `getTicket(id)` 封装

**3. 用户确认关闭自己的工单**
- backend/app/core/ticket_service.py：`ROLE_PERMISSIONS["resolved->closed"]` 增加 `"user"`；`update_status` 新增 `user_id` 参数——role 为 user 时校验 `ticket.user_id == user_id` 否则 PermissionError（agent/admin 不受限）
- backend/app/api/ticket.py：`update_ticket_status_api` 传入 `user_id=user.username`
- frontend/src/views/Tickets/index.vue：user 视角下，`status === 'resolved'` 的行显示"确认解决"按钮（调用 updateTicketStatus(id, 'closed')）；其余状态无操作（保持 phase10 语义）

**4. phase10 角色矩阵增补**
- user 新增：我的订单页（仅本人订单）、工单详情查看（仅本人工单）、确认关闭本人 resolved 工单

## Impact

- 受影响代码：backend/app/api/mall.py、app/api/ticket.py、app/core/ticket_service.py、app/core/mall/*、frontend/src/views/Orders/（新）、Tickets/index.vue、api/mall.js、api/ticket.js、router、MainLayout
- **BREAKING**（行为放宽）：`resolved→closed` 从仅 admin 改为 admin+user（user 限本人工单）；属有意的产品决策
- 兼容性：update_status 新增 user_id 参数带默认值，既有调用（ops 诊断链路 create_incident 无关；chat 工具不调 update_status）不受影响；测试需同步
- 既有不动：admin list_orders 契约不变（摘要无 items）、状态机其余流转角色不变、订单/工单 owner 隔离不变

## ADDED Requirements

### Requirement: 用户订单列表
系统 SHALL 提供 `GET /api/v1/mall/my-orders`：任何已登录角色可调，服务端以 JWT 用户名强制过滤 `owner_username`，客户端传入的 owner 过滤参数 SHALL 被忽略；返回含商品明细的订单分页列表。前端 SHALL 提供仅 user 角色可见的"我的订单"页面。

#### Scenario: 用户查看本人订单
- WHEN user1 请求 /mall/my-orders
- THEN 仅返回 owner_username=user1 的订单（含 items 明细、total 计数）

#### Scenario: 客服无自有订单
- WHEN agent 请求 /mall/my-orders
- THEN 返回空列表（total=0），不报错

#### Scenario: 订单明细展示
- WHEN 我的订单页渲染订单行并展开
- THEN 显示商品名/规格/单价/数量

### Requirement: 工单详情查看
Tickets 页面每行 SHALL 提供"详情"入口，弹窗展示工单完整字段；user 打开他人工单 SHALL 得到 404（复用既有归属隔离）。

#### Scenario: 查看工单详情
- WHEN 任意角色点击工单行"详情"
- THEN 弹窗显示该工单的完整描述、优先级、状态与时间

## MODIFIED Requirements

### Requirement: 工单状态流转（含用户确认关闭）
合法流转：open→in_progress（agent/admin）、in_progress→resolved（agent/admin）、resolved→closed（admin，或 user 关闭**本人**工单）。user 流转非本人工单 SHALL 抛 PermissionError。Tickets 页对 user SHALL 在 status=resolved 的本人工单行显示"确认解决"按钮，其余状态无操作。

#### Scenario: 用户确认关闭本人工单
- GIVEN user1 有一张 resolved 工单
- WHEN user1 调用 PATCH /ticket/{id}/status 传 closed
- THEN 状态变为 closed，返回 200

#### Scenario: 用户无法关闭他人工单
- GIVEN user2 有一张 resolved 工单
- WHEN user1 对其调用 closed 流转
- THEN 返回 403

## REMOVED Requirements

（无）
