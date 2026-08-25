# Checklist

- [x] Scenario: 普通用户查他人工单 → user 查他人工单返回 404"工单不存在"（test_ticket_api）
- [x] Scenario: 客服查任意工单 → agent 查任意工单 200（test_ticket_api）
- [x] Scenario: 普通用户调知识库列表 → user 角色 403，admin/agent 200（test_knowledge_api）
- [x] Scenario: 普通用户视角 → user 工单页无操作列（Tickets.spec.js）
- [x] Scenario: 客服视角 → agent 工单页有操作列与客户列（Tickets.spec.js）
- [x] Scenario: 知识库客服视角 → agent 知识库页无删除/重建按钮（Knowledge.spec.js）
- [x] Scenario: 矩阵一致性 → 角色功能矩阵中每项能力前端入口与后端鉴权一致（人工核对 spec 矩阵与实现：工单详情后端 owner 隔离+前端无单查入口；知识库列表后端 require_staff+前端路由 admin/agent；删除/重建后端 admin+前端按钮 admin-only；工单流转后端角色校验+前端操作列隐藏；订单/物流/退款后端 owner 隔离+聊天入口；mall/orders 与反馈列表后端 admin+前端 Admin 页）
- [x] 既有测试无回归（后端全量仅 test_langfuse_infra 5 个已知环境失败；前端 44 个全过）
- [x] 遵循项目约定（无 TypeScript、无新 UI 库、Element Plus、API 走 src/api/）
