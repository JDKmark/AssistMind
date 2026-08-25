# phase12-admin-capabilities Spec

- Mode: full
- Status: draft
- Created: 2026-08-25

## Why

当前管理员页已经展示系统健康、知识库/工单概览、商城订单和反馈追溯，但数据由前端分别请求多个领域接口后临时聚合，管理员不能管理用户角色、处理退款或追踪管理操作。phase12 将管理员入口收敛为业务运营后台，复用现有订单、工单、知识库和反馈领域规则，新增受限用户管理、退款运营、统一概览和操作审计；现有 OPS 能力保留，但不在本阶段扩展或移除。

## What Changes

- `.trae/specs/phase12-admin-capabilities/structure.md`：管理员后台分层、模块职责和数据契约。
- `backend/app/api/admin.py`：新增管理员概览、用户管理、审计查询 API。
- `backend/app/api/mall.py`：新增管理员退款列表与退款状态流转 API。
- `backend/app/main.py`：注册 `/api/v1/admin` 路由。
- `backend/app/core/admin_service.py`：管理员概览、用户分页查询、受限角色/状态变更编排。
- `backend/app/core/audit_service.py`：管理员操作审计写入与分页查询。
- `backend/app/core/mall/base.py`、`mock_source.py`、`real_source.py`、`data_source.py`：新增退款管理查询、状态流转和商城运营统计契约，Mock/Real 行为一致。
- `backend/app/models/user.py`：新增账号启用状态字段。
- `backend/app/models/audit.py`：新增管理员操作审计模型。
- `backend/app/models/mall.py`：退款状态明确支持 `处理中/已通过/已拒绝`。
- `backend/scripts/init_db.py`：幂等增加用户启用状态和审计表。
- `frontend/src/api/admin.js`：管理员概览、用户管理、审计查询封装。
- `frontend/src/api/mall.js`：退款管理 API 封装。
- `frontend/src/views/Admin/index.vue`：重构为业务运营后台，提供概览、工单、订单退款、用户、知识库反馈、审计视图。
- `backend/tests/` 与 `frontend/tests/unit/Admin.spec.js`：按 TDD 增加 API、服务、数据源、RBAC、降级和页面交互测试。

## Impact

- 新增 `/api/v1/admin/*` 与 `/api/v1/mall/refunds*` 接口；现有聊天 SSE、用户订单、管理员订单、工单、知识库和反馈接口响应形状保持不变。
- `users` 表新增 `is_active`，默认 `true`；停用账号禁止后续登录。phase12 不新增全局 JWT 会话撤销，已签发 token 仍按现有过期时间失效。
- 管理员只能在 `user` 与 `agent` 间调整角色；目标或新角色为 `admin` 时拒绝，避免单管理员误操作和权限提升。
- 退款状态从单一“处理中”扩展为终态“已通过/已拒绝”；既有申请退款幂等规则和订单归属校验不变。
- 审计写入是管理员写操作的旁路记录；审计失败必须 `logger.warning`，不得把已经成功的用户/退款变更回滚或改写为失败。
- Mock 与 Real 商城数据源必须保持退款列表、退款流转和概览统计契约一致；Real PostgreSQL 失败返回明确 `degraded`，不抛未处理异常。
- 管理后台仍只允许 admin；agent/user 的路由入口与 API 均保持拒绝。
- OPS 路由、运维数据源和诊断页面不修改。

## ADDED Requirements

### Requirement: 管理员业务概览

系统 SHALL 提供 `GET /api/v1/admin/overview`，仅 admin 可访问，并返回订单、工单、退款、用户、反馈、知识库六组统计和 `degraded` 列表。

#### Scenario: 管理员查看业务概览
- GIVEN admin 携带有效 JWT
- WHEN 请求 `GET /api/v1/admin/overview`
- THEN 返回 200，响应包含 `orders/tickets/refunds/users/feedback/knowledge/degraded`
- AND 订单含 `total/amount/by_status`，其余业务域含 `total` 或文档统计及状态分布

#### Scenario: 非管理员访问业务概览
- GIVEN agent 或 user 携带有效 JWT
- WHEN 请求 `GET /api/v1/admin/overview`
- THEN 返回 403

#### Scenario: 外部统计源降级
- GIVEN 商城 PostgreSQL 或 Qdrant 统计不可用
- WHEN admin 请求业务概览
- THEN 返回 200，可用业务域仍返回统计
- AND 不可用业务域返回零值，`degraded` 包含对应依赖名

### Requirement: 管理员用户列表

系统 SHALL 提供 `GET /api/v1/admin/users`，仅 admin 可按 `role/keyword/limit/offset` 查询用户，并返回 `{users,total}`；用户项包含 `id/username/role/is_active/created_at`，不返回密码摘要。

#### Scenario: 管理员筛选普通用户
- WHEN admin 以 `role=user&keyword=user` 查询用户
- THEN 返回匹配的分页用户和过滤后总数
- AND 响应不含 `hashed_password`

### Requirement: 受限用户角色与状态管理

系统 SHALL 提供 `PATCH /api/v1/admin/users/{user_id}`，仅 admin 可传入至少一个 `role` 或 `is_active` 字段；角色只允许在 `user` 与 `agent` 间变更，目标 admin 用户不可修改或停用。

#### Scenario: 普通用户调整为客服
- GIVEN 目标账号当前角色为 user
- WHEN admin 提交 `{"role":"agent"}`
- THEN 返回 200 和更新后的用户摘要
- AND 新登录签发的 JWT 角色为 agent
- AND 写入 `user.role.update` 审计记录

#### Scenario: 停用普通账号
- GIVEN 目标账号不是 admin 且当前启用
- WHEN admin 提交 `{"is_active":false}`
- THEN 返回 200 且 `is_active=false`
- AND 该账号后续登录返回 403 和“账号已停用”
- AND 写入 `user.status.update` 审计记录

#### Scenario: 拒绝授予管理员角色
- WHEN admin 对任意非 admin 用户提交 `{"role":"admin"}`
- THEN 返回 422，用户角色不变，不写成功审计记录

#### Scenario: 拒绝修改管理员账号
- GIVEN 目标账号角色为 admin
- WHEN admin 尝试修改其角色或启用状态
- THEN 返回 403，账号不变，不写成功审计记录

### Requirement: 管理员退款列表

系统 SHALL 提供 `GET /api/v1/mall/refunds`，仅 admin 可按 `status/owner_username/limit/offset` 查询退款，并返回 `{refunds,total}`；每项含 `refund_id/order_sn/owner_username/reason/status/created_at`。

#### Scenario: 按用户筛选退款
- WHEN admin 以 `owner_username=user1` 查询退款
- THEN 仅返回 user1 订单关联的退款和过滤后总数

#### Scenario: 非管理员查询退款
- WHEN agent 或 user 请求退款列表
- THEN 返回 403

### Requirement: 退款状态流转

系统 SHALL 提供 `PATCH /api/v1/mall/refunds/{refund_id}/status`，仅 admin 可执行 `处理中→已通过` 或 `处理中→已拒绝`，终态不可再次流转。

#### Scenario: 管理员通过退款
- GIVEN 退款状态为“处理中”
- WHEN admin 提交 `{"status":"已通过"}`
- THEN 返回 200 和更新后的退款摘要
- AND 写入 `refund.status.update` 审计记录

#### Scenario: 拒绝退款终态重复流转
- GIVEN 退款状态为“已通过”或“已拒绝”
- WHEN admin 再次变更其状态
- THEN 返回 400，退款状态不变，不写成功审计记录

#### Scenario: Mock 与 Real 退款契约一致
- WHEN 相同退款数据分别由 Mock 和 Real 数据源查询或流转
- THEN 字段形状、合法状态和非法流转语义一致

### Requirement: 管理操作审计

系统 SHALL 为用户角色变更、用户启停和退款状态流转记录审计，并提供仅 admin 可访问的 `GET /api/v1/admin/audit`，支持按 `actor/action/target_type/limit/offset` 分页查询 `{items,total}`。

#### Scenario: 查询角色变更审计
- GIVEN admin 已完成一次 user 到 agent 的角色变更
- WHEN admin 以 `action=user.role.update` 查询审计
- THEN 返回记录，包含操作者、目标用户、旧值、新值和创建时间

#### Scenario: 审计写入失败旁路
- GIVEN 业务变更已成功但审计存储写入失败
- WHEN API 完成请求
- THEN 业务 API 仍返回成功结果
- AND 服务记录 warning，不静默吞掉异常

### Requirement: 管理员业务运营界面

Admin 页面 SHALL 使用 Element Plus 提供概览、工单、订单退款、用户、知识库反馈、审计六个任务视图，并保持 admin-only 路由守卫；页面 SHALL 在列表操作后只刷新受影响视图及概览。

#### Scenario: 管理员处理退款
- GIVEN admin 进入订单退款视图且存在“处理中”退款
- WHEN 点击通过或拒绝并确认
- THEN 调用退款状态接口，显示结果消息，并刷新退款列表和概览

#### Scenario: 管理员调整用户角色
- GIVEN admin 进入用户视图并选中非 admin 用户
- WHEN 将其角色从 user 调整为 agent 并确认
- THEN 调用用户更新接口，刷新用户列表、概览和审计视图

#### Scenario: 管理员全局处理工单
- GIVEN admin 进入工单视图
- WHEN 按状态、优先级或客户筛选并执行合法状态流转
- THEN 列表显示过滤结果，状态流转复用现有工单 API 与状态机

#### Scenario: 页面部分数据失败
- GIVEN 某一管理列表 API 请求失败
- WHEN 页面加载或刷新
- THEN 该视图显示明确错误或空态，其他视图仍可操作

## MODIFIED Requirements

### Requirement: 登录账号启用校验

`POST /api/v1/auth/login` SHALL 在密码正确后检查 `User.is_active`；停用账号返回 403，启用账号保持现有 JWT 响应形状。

#### Scenario: 停用账号登录
- GIVEN 用户名和密码正确但账号 `is_active=false`
- WHEN 请求登录
- THEN 返回 403 和“账号已停用”，不签发 JWT

### Requirement: 管理后台角色矩阵

管理后台 SHALL 继续仅对 admin 显示路由和菜单，新增的概览、用户、退款、审计 API 均使用 `require_admin`；现有 agent/user 能力不得因 phase12 放宽。

#### Scenario: 前后端权限一致
- WHEN agent 或 user 尝试通过菜单、路由或直接 HTTP 请求访问新增管理能力
- THEN 前端无管理入口或被路由守卫拦截，后端直接请求返回 403

### Requirement: 现有管理员能力保留

Admin 页面 SHALL 保留系统健康、知识库管理、反馈追溯、订单总览和工单管理能力，但按业务任务重组；现有接口兼容性不变。

#### Scenario: 既有反馈追溯
- WHEN admin 打开差评反馈详情
- THEN 仍可查看问题、回答、来源、CRAG 决策、降级项和可用的 Langfuse 链接

#### Scenario: 既有知识库管理
- WHEN admin 执行文档删除或索引重建
- THEN 继续复用现有知识库 API 和 Qdrant/BM25 行为

## REMOVED Requirements

（无）

## RENAMED Requirements

（无）
