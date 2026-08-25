# phase12-admin-capabilities 结构设计

## Why（现状缺口）
- 现状：`frontend/src/views/Admin/index.vue` 已将健康状态、知识库统计、工单统计、订单列表和反馈追溯拼接为展示面板，但后端没有管理员专属编排入口；用户管理、退款处理和管理操作审计均缺失。
- 现状：`backend/app/api/ticket.py` 已提供全量工单列表和状态流转，`backend/app/api/mall.py` 已提供 admin 订单摘要列表，`backend/app/api/feedback.py` 与 `knowledge.py` 已提供管理员只读/管理能力，但这些能力没有统一的管理员业务契约。
- 目标：建立面向业务运营的管理员后台，集中处理用户角色、工单、订单/退款、反馈追溯和知识库管理；保留现有 OPS 能力，但 phase12 不扩展或移除 OPS。

## 模式判定
- 采用：分层式 + 管理后台内的聚合式查询。
- 理由：HTTP API 负责鉴权和参数校验，管理员服务负责跨领域统计与写操作编排，现有 ticket/mall/feedback/knowledge 服务继续拥有各自领域规则；不把业务规则堆入 Vue 页面或单一上帝模块。

## 模块清单
| 模块 | 职责（一句话） | 接口签名 | 依赖 |
|---|---|---|---|
| Admin API | 校验 admin 身份，暴露管理员聚合查询、用户管理、审计查询 | `GET /api/v1/admin/overview -> dict`；`GET/PATCH /api/v1/admin/users -> dict`；`GET /api/v1/admin/audit -> dict` | admin service、deps |
| Admin Service | 编排用户统计、业务概览、审计记录，不直接处理 Vue 展示 | `async def get_overview() -> dict`；`async def list_users(...) -> dict`；`async def update_user_role(...) -> dict`；`async def list_audit_logs(...) -> dict` | SQLAlchemy models、domain services |
| User Management | 查询用户并执行受限角色变更 | `async def list_users(role, keyword, limit, offset) -> {users,total}`；`async def change_role(user_id, new_role, operator) -> dict` | User model、audit service |
| Refund Operations | 查询退款并执行幂等状态流转 | `async def list_refunds(status, owner_username, limit, offset) -> {refunds,total}`；`async def update_refund_status(refund_id, new_status, operator) -> dict` | MallRefund/MallOrder、audit service |
| Audit Service | 记录管理员写操作并分页查询 | `async def record(actor, action, target_type, target_id, detail) -> dict`；`async def list_logs(...) -> {items,total}` | AuditLog model、PostgreSQL |
| Admin Frontend API | 将管理员 HTTP 请求集中封装 | `getAdminOverview()`；`listAdminUsers(params)`；`updateUserRole(id, role)`；`listAdminRefunds(params)`；`updateRefundStatus(id,status)`；`listAuditLogs(params)` | `src/api/request.js` |
| Admin Frontend View | 按业务运营任务组织统计、用户、工单、订单/退款、反馈、知识库和审计视图 | Vue 组件内调用上述 API，不直接拼接 HTTP URL | Element Plus、Pinia auth |

## 层间数据契约

```text
JWT(admin)
  -> Admin API dependency
  -> Admin Service
  -> domain services / SQLAlchemy
  -> {data, total, degraded?}
  -> Admin View
```

管理员概览响应固定为：

```json
{
  "orders": {"total": 4, "amount": 25386.0, "by_status": {"待付款": 1, "待发货": 1, "已发货": 1, "已完成": 1}},
  "tickets": {"total": 0, "by_status": {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0}},
  "refunds": {"total": 0, "by_status": {"处理中": 0, "已通过": 0, "已拒绝": 0}},
  "users": {"total": 0, "by_role": {"admin": 0, "agent": 0, "user": 0}},
  "feedback": {"total": 0, "negative": 0, "pending_export": 0},
  "knowledge": {"documents": 0, "chunks": 0},
  "degraded": []
}
```

用户列表响应：

```json
{
  "users": [{"id":"...", "username":"user1", "role":"user", "created_at":"..."}],
  "total": 1
}
```

退款列表项：

```json
{
  "refund_id":"AF20240801001",
  "order_sn":"20240801001",
  "owner_username":"user1",
  "reason":"商品问题",
  "status":"处理中",
  "created_at":"..."
}
```

审计列表项：

```json
{
  "id":"...",
  "actor_username":"admin",
  "action":"user.role.update",
  "target_type":"user",
  "target_id":"...",
  "detail":{"old_role":"user","new_role":"agent"},
  "created_at":"..."
}
```

## 依赖方向与数据流图（ASCII）

```text
[Admin Vue]
    -> [src/api/admin.js / mall.js]
    -> [Admin API / Mall API]
    -> [require_admin]
    -> [Admin Service / Ticket Service / Mall Data Source / Feedback Service / Knowledge]
    -> [PostgreSQL / Qdrant / Mock fallback]
```

管理员写操作统一走：

```text
Admin API -> domain validation -> persist mutation -> Audit Service -> response
```

审计失败只记录 warning，不回滚已经成功的业务变更；业务变更失败不得写成功审计记录。

## 与现有代码映射
| 目标模块 | 现有代码 | 动作 |
|---|---|---|
| Admin API | `backend/app/main.py`、现有 `backend/app/api/*.py` | 新增 `backend/app/api/admin.py` 并注册路由 |
| Admin Service | `backend/app/core/` | 新增管理员聚合服务，复用领域服务，不复制订单/工单规则 |
| User Management | `backend/app/models/user.py`、`backend/app/api/auth.py` | 保持 JWT 结构；新增受限角色变更接口，禁止变更/授予 admin |
| Refund Operations | `backend/app/models/mall.py`、`backend/app/core/mall/*` | 增加管理查询与状态流转契约，保留申请退款幂等语义 |
| Audit Service | `backend/app/models/`、数据库初始化脚本 | 新增 AuditLog 模型、迁移/初始化和服务 |
| Admin View | `frontend/src/views/Admin/index.vue`、`frontend/src/api/*` | 将现有展示面板扩展为运营后台，保持 Element Plus 与角色守卫 |
| Tests | `backend/tests/`、`frontend/tests/unit/Admin.spec.js` | 先补失败测试，再实现 API、服务和页面行为 |

## 落地建议（增量路径）
- 第 1 步：先建立 AuditLog、用户角色管理和退款状态机契约，并补 admin 鉴权/归属测试。
- 第 2 步：实现管理员概览聚合和用户/退款 API，统一明确 PostgreSQL 失败时的 degraded 返回。
- 第 3 步：扩展 Admin 页面为分区/标签视图，接入用户、退款、工单详情与审计查询。
- 第 4 步：保留现有知识库、反馈追溯和健康状态区，不把 OPS 诊断逻辑迁入管理员业务服务。
- 验收：admin 可完成业务运营闭环；agent/user 无法访问 admin API；所有管理员写操作有审计记录；订单 owner 和用户工单权限不被放宽；Mock/Real 契约一致。
