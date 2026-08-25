# phase9 复盘

三个并行子代理（后端商城、前端商城、运维下架）文件零冲突，一次通过；关键在委托前把接口契约（list_orders 签名/返回形状、账号密码、订单归属拆分）写死进提示，并明确"不动文件"清单避免并行互踩。唯一插曲：Admin.spec.js 的 el-table stub 需升级为按 data 渲染才能断言列插槽内容——前端组件测试断言表格列时先检查 stub 是否透传 row。

运维"仅下架入口"决策（前端删、后端留）通过 AskUserQuestion 一次确认，避免了误删 test_ops_* 与 knowledge/ops 的不可逆损失。
