# phase9-mall-demo-isolation Spec

- Mode: lite
- Status: done
- Created: 2026-08-23

## Why

P0 订单归属授权修复后，演示环境只有一个 `user` 账号持有全部 4 笔订单，无法演示"user1 查不到 user2 订单"的横向越权隔离效果；Admin 后台也没有任何订单可视化入口。同时运维诊断场景与商城客服场景并存使产品定位杂乱，用户已确认：下架运维产品入口（前端 + 聊天意图路由），后端 ops 代码与测试保留。

## What Changes

**后端（商城演示隔离）**
- backend/app/core/mall/mock_source.py：订单归属拆分（20240801001/002 → user1，20240801003/004 → user2）+ 实现 list_orders
- backend/app/core/mall/base.py：list_orders 抽象契约
- backend/app/core/mall/real_source.py：list_orders PostgreSQL 实现（含失败降级）
- backend/app/core/mall/data_source.py：list_orders 门面转发
- backend/scripts/init_db.py：逐账号幂等种子（新增 user1/user1123、user2/user2123）+ 存量订单归属回填
- backend/app/api/mall.py（新增）：GET /api/v1/mall/orders（require_admin）
- backend/app/main.py：注册 mall 路由
- backend/tests/unit/test_mall_data_source.py、test_mcp_mall_tools.py、test_mall_api.py（新增）、backend/tests/integration/test_mall_pg.py

**前端（商城）**
- frontend/src/api/mall.js（新增）：listOrders
- frontend/src/views/Admin/index.vue：订单列表卡片（表格 + 用户/状态筛选 + 分页）
- frontend/src/views/Login/index.vue：演示账号 quick-fill 改为 admin/agent/user1/user2
- frontend/tests/unit/Admin.spec.js：订单 API mock 与断言

**运维入口下架（后端能力保留）**
- frontend/src/router/index.js：删除 /ops 路由
- frontend/src/components/Layout/MainLayout.vue：删除运维诊断菜单项
- 删除 frontend/src/views/Ops/index.vue、frontend/src/stores/ops.js、frontend/src/api/ops.js、frontend/tests/unit/Ops.spec.js
- frontend/src/views/Chat/index.vue：删除 diagnose 提示与 /ops 跳转
- backend/app/data/intent_routes.json：删除 diagnose 意图条目
- backend/app/core/router/intent.py：_VALID_INTENTS 移除 diagnose
- backend/app/api/chat.py：删除 diagnose 分支与 app.api.ops 导入
- backend/tests/unit/test_intent_routes_ecommerce.py：适配 4 意图结构

**明确不动**：backend/app/api/ops.py、agents/ops_supervisor.py、core/ops/*、test_ops_*.py、knowledge/ops/、seed_ops_kb.py、run_eval_ops.py、eval_qa.json。

## ADDED Requirements

### Requirement: user1/user2 演示账号
系统 SHALL 在 init_db 中逐账号幂等创建 user1（密码 user1123）与 user2（密码 user2123），角色均为 user。

#### Scenario: 存量库补种
- GIVEN 数据库已存在 admin 账号与 user 账号
- WHEN 重新执行 init_db
- THEN user1/user2 被补种，既有账号不变

### Requirement: 订单归属拆分
订单 20240801001/20240801002 SHALL 归属 user1，20240801003/20240801004 SHALL 归属 user2；mock 与 PostgreSQL 数据一致；user1 查询 user2 订单返回与"订单不存在"相同形状。

#### Scenario: 跨用户隔离演示
- WHEN user1 登录后查询订单 20240801003
- THEN 返回与未知订单号一致的结果（查询 None / 物流 [] / 退款失败）

### Requirement: Admin 订单列表
系统 SHALL 提供 GET /api/v1/mall/orders（仅 admin），支持 owner_username/status 过滤与 limit/offset 分页，返回 {orders: [...], total: int}；Admin 页面 SHALL 展示订单表格。

#### Scenario: 普通用户被拒
- WHEN role=user 的 JWT 调用该接口
- THEN 返回 403

### Requirement: 运维产品入口下架
前端 SHALL 不再提供 /ops 路由与运维诊断菜单；聊天意图路由 SHALL 不再产生 diagnose 意图（相关查询落入 task/faq/unclear）；后端 /api/v1/ops/* 接口 SHALL 保持可用。

#### Scenario: 诊断关键词不再走诊断流
- WHEN 用户在聊天中输入"我下单失败了"
- THEN 意图不再为 diagnose

## Tasks

- Task 1: 后端商城隔离与订单列表 API（含全部后端测试）→ 依赖：无
- Task 2: 前端 Admin 订单表 + 登录演示账号 → 依赖：无（与 Task 1 并行，接口契约已写死）
- Task 3: 运维入口下架（前端 + 意图路由 + chat 分支 + 测试适配）→ 依赖：无

## 验收清单

- [x] user1 登录可查 20240801001/002，查 20240801003 得"订单不存在"（test_mall_data_source / test_mall_pg 越权测试）
- [x] user2 登录可查 20240801003/004，查 20240801001 得"订单不存在"（同上，集成测试用 user2 查 user1 订单）
- [x] Admin 页面可见 4 笔订单及归属，可按用户筛选（Admin.spec.js 12 测试）
- [x] 侧边栏无"运维诊断"，/ops 路由已删（grep frontend/src 无 /ops 引用）
- [x] 聊天输入"我下单失败了"不再进入诊断流（test_intent_routes_ecommerce）
- [x] 后端 pytest（not integration）432 通过，仅 5 个既有 test_langfuse_infra 环境失败（与本次无关）；test_ops_* 全部保持通过
- [x] 前端 npm run test:unit 39 个测试通过
- [x] 集成测试 test_mall_pg.py 14 个通过（含 init_db 迁移后归属回填）
