# OPS-002：慢 SQL（Slow Query）排查手册

## 症状
- 数据库 CPU 持续高位，接口延迟明显上升
- 日志出现「Slow query detected」或「Lock wait timeout exceeded」
- 依赖该数据库的服务整体变慢，上游网关延迟升高

## 常见根因
1. 新发布的查询未走索引（全表扫描）
2. 数据量增长导致原有索引失效
3. 锁竞争（长事务占用行锁）

## 排查步骤
1. 查看告警：延迟类告警（latency_p95 / cpu_usage）
2. 查看指标：数据库 CPU、连接等待数、接口延迟
3. 查看日志：搜索 slow query / lock wait / full table scan 关键线索
4. 查看变更记录：排查故障前最近一次发布版本

## 恢复动作
- 为慢 SQL 涉及的字段补充索引
- 紧急时可回滚问题版本
- 对复杂查询做分页与缓存优化
