# phase10-role-capability-matrix Spec

- Mode: lite
- Status: done
- Created: 2026-08-23

## Why

产品当前有三个角色（user/agent/admin），但能力边界从未被显式定义，探索发现 4 处前后端不一致或越权缺口：`GET /ticket/{id}` 任何角色可查任意工单详情（横向越权）、`GET /knowledge/list` 后端未限角色与前端声明不一致、Tickets 页面对 user 暴露状态流转按钮、Knowledge 页面对 agent 暴露删除/重建按钮。本 spec 显式定义三角色功能矩阵，并修复全部缺口。

## What Changes

**角色功能矩阵（本 spec 的核心定义）**

| 能力 | user（普通用户） | agent（客服） | admin（管理员） |
|---|---|---|---|
| 智能问答（RAG 知识问答+溯源、闲聊） | ✓ | ✓ | ✓ |
| 聊天查商品信息 | ✓ | ✓ | ✓ |
| 聊天查订单/物流/申请退款 | 仅本人订单（P0 已实现） | 任意用户订单 | 任意用户订单 |
| 创建工单 / 转人工 | ✓ | ✓ | ✓ |
| 工单列表 | 仅自己的工单（后端已隔离） | 全部工单 | 全部工单 |
| 工单列表"客户"列（user_id） | 不适用 | ✓ | ✓ |
| 工单状态流转（操作列） | ✗（隐藏） | ✓ | ✓ |
| 工单详情（单查） | 仅自己的工单 | 任意 | 任意 |
| 知识库页面/文档列表 | ✗（路由不可见） | ✓ 只读（隐藏删除/重建） | ✓ 完整管理 |
| 知识库删除文档/重建索引 | ✗ | ✗（隐藏按钮） | ✓ |
| 管理后台（健康/订单总览/统计/反馈追溯） | ✗ | ✗ | ✓ |
| 问答反馈提交（点赞/点踩） | ✓ | ✓ | ✓ |
| 反馈追溯列表 | ✗ | ✗ | ✓ |

**代码改动**
- backend/app/api/ticket.py：`GET /ticket/{id}` 增加 owner 隔离（user 角色查非本人工单返回 404，与"工单不存在"统一形状，防枚举）
- backend/app/api/knowledge.py：`GET /knowledge/list` 增加 agent/admin 角色限制（user 返回 403）
- frontend/src/views/Tickets/index.vue：user 角色隐藏"操作"列；agent/admin 增加"客户"列展示 user_id
- frontend/src/views/Knowledge/index.vue：非 admin 角色（agent）隐藏"删除"按钮与"重建索引"按钮
- 测试：后端 ticket/knowledge API 权限测试 + 前端 Tickets/Knowledge 角色渲染测试

## Impact

- 受影响代码：backend/app/api/ticket.py、backend/app/api/knowledge.py、frontend/src/views/Tickets/index.vue、frontend/src/views/Knowledge/index.vue 及对应测试
- 兼容性：`GET /knowledge/list` 对 user 从 200 变 403 属 **BREAKING**（前端本就不给 user 入口，实际调用方为零，风险可控）；`GET /ticket/{id}` 对 user 查他人工单从 200 变 404 同为收紧
- 既有已隔离能力不动：订单/物流/退款 owner 隔离（P0）、工单列表按角色隔离、update_status 角色校验、mall/orders 仅 admin

## ADDED Requirements

### Requirement: 工单详情归属隔离
`GET /api/v1/ticket/{ticket_id}` SHALL 在请求者为 user 角色且工单 user_id 与其用户名不一致时返回 404（与不存在的工单相同形状）；agent/admin 可查任意工单。

#### Scenario: 普通用户查他人工单
- GIVEN user1 已登录，存在 user2 创建的工单 TK-xxx
- WHEN user1 请求 GET /api/v1/ticket/TK-xxx
- THEN 返回 404，detail 为"工单不存在"

#### Scenario: 客服查任意工单
- GIVEN agent 已登录，存在 user2 创建的工单 TK-xxx
- WHEN agent 请求 GET /api/v1/ticket/TK-xxx
- THEN 返回 200 与工单详情

### Requirement: 知识库列表角色限制
`GET /api/v1/knowledge/list` SHALL 仅允许 admin/agent 角色访问，user 角色返回 403。

#### Scenario: 普通用户调知识库列表
- WHEN user 角色携带有效 JWT 请求 GET /api/v1/knowledge/list
- THEN 返回 403

### Requirement: 工单页角色视角
Tickets 页面 SHALL 按 role 渲染：user 隐藏"操作"列（无状态流转入口）；agent/admin 显示"客户"列（工单归属 user_id）。

#### Scenario: 普通用户视角
- WHEN user 角色登录进入工单页
- THEN 表格无"操作"列，仅显示自己的工单

#### Scenario: 客服视角
- WHEN agent 角色登录进入工单页
- THEN 表格含"操作"列与"客户"列，列表为全部工单

### Requirement: 知识库页管理按钮按角色隐藏
Knowledge 页面 SHALL 仅对 admin 显示"删除"按钮与"重建索引"按钮，agent 角色隐藏（仅只读浏览）。

#### Scenario: 客服视角
- WHEN agent 角色登录进入知识库页
- THEN 页面无删除按钮、无重建索引按钮，文档列表正常展示

## MODIFIED Requirements

### Requirement: 角色功能矩阵
系统 SHALL 按上文"What Changes"中的角色功能矩阵对三角色提供功能；前端入口（路由/菜单/按钮）与后端 API 权限（依赖注入校验）双端一致，任一端不单独放宽。

#### Scenario: 矩阵一致性
- WHEN 检查任一能力的入口可见性与后端鉴权
- THEN 前端隐藏的能力其 API 亦拒绝该角色，反之不出现"前端隐藏但后端本可放开"的错位

## REMOVED Requirements

（无）
