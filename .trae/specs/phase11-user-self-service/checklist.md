# Checklist

- [x] Scenario: 用户查看本人订单 → user1 调 /mall/my-orders 仅得本人 2 单且含 items（test_mall_api + test_mall_data_source，含 requester_username 透传与客户端 owner 参数忽略断言）
- [x] Scenario: 客服无自有订单 → agent 调 /mall/my-orders 得空列表不报错（单测 + 集成）
- [x] Scenario: 订单明细展示 → 我的订单页展开行显示品名/规格/单价/数量（Orders.spec.js 6 用例）
- [x] Scenario: 我的订单菜单可见性 → 仅 user 角色侧边栏见"我的订单"；agent/admin 不可见（MainLayout v-if role==='user'）
- [x] Scenario: 查看工单详情 → 详情弹窗展示完整描述/优先级/状态/时间（Tickets.spec.js）
- [x] Scenario: 用户确认关闭本人工单 → user 对本人 resolved 工单流转 closed 返回 200（test_ticket_api + 真实库端到端：status=closed）
- [x] Scenario: 用户无法关闭他人工单 → user 对他人 resolved 工单流转 closed 返回 403（test_ticket_api + 真实库端到端：PermissionError"不属于当前用户"）
- [x] Scenario: 确认解决按钮条件渲染 → user 视角仅 resolved 行显示按钮（Tickets.spec.js）
- [x] 既有测试无回归（后端 460 个测试仅 test_langfuse_infra 5 个已知环境失败；前端 54/54 全过）
- [x] 遵循项目约定（无 TypeScript、Element Plus、Pinia、API 走 src/api/、mock 与 real 数据一致、失败降级不静默）
- [x] 手验：user1 登录全流程走通（订单→展开→工单详情→确认解决；真实库端到端验证通过）
