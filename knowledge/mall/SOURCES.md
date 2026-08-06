# mall 知识库取材说明（SOURCES）

本目录内容全部取自 GitHub 开源项目 macrozheng/mall（Spring Boot 多模块电商商城，中文文档）。
拉取时间：2026-08-05，基于 `master` 分支（Spring Boot 3.x + JDK 17）。
所有文件均为 GitHub 原始内容，未做改写；节选部分在下方注明。

## 目录结构

| 子目录 | 内容 | 来源 | 说明 |
|---|---|---|---|
| `README/README.md` | 项目 README（13.5KB，完整） | `https://github.com/macrozheng/mall/blob/master/README.md` | 完整保留，含环境搭建/部署章节 |
| `reference/*.md`（7 篇） | document/reference 参考文档 | `https://github.com/macrozheng/mall/tree/master/document/reference` | 全部 7 篇完整拉取 |
| `config/*.yml`（3 个） | mall-admin 模块配置 | `https://github.com/macrozheng/mall/blob/master/mall-admin/src/main/resources/` | application.yml / application-dev.yml / application-prod.yml 完整 |
| `sql/mall_tables.sql` | 数据库表结构（节选） | `https://github.com/macrozheng/mall/blob/master/document/sql/mall.sql` | **节选**：原始文件 407,690 字节，仅提取全部 76 条 CREATE TABLE 语句，省略 DROP TABLE 与 INSERT 数据语句 |

## 节选与替代说明

1. **`sql/mall_tables.sql` 为节选**：`document/sql/mall.sql` 完整文件含大量 INSERT 初始化数据，
   本知识库只保留表结构（CREATE TABLE 部分，76 张表，69KB），表级注释保留在语句末尾
   `COMMENT = 'xxx'`。表清单覆盖 cms（内容）/oms（订单）/pms（商品）/sms（营销）/ums（用户）五大模块。
2. **部署长文（mall_deploy_docker 等）**：GitHub 仓库内不含《mall 在 Linux 环境下的部署（基于 Docker）》
   等长文（位于 macrozheng.com 文档站），以 `reference/` 下的 deploy-windows.md / docker.md / linux.md
   及 README「环境搭建」章节替代，内容同为官方取材。
3. **`reference/deploy-windows.md`**：Windows 环境搭建手册（IDEA/Eclipse/MySQL/Redis/ES/MongoDB/RabbitMQ/OSS），
   其中 OSS 配置指向 `application.properties`（历史版本路径），与当前 `config/application.yml` 均为仓库内真实内容。

## 自建内容说明

4. **`business/` 下业务规则与演示数据为项目自建**（锚定 mall.sql 官方表结构字段，如
   `oms_order_return_apply.status`、`pms_product.service_ids/promotion_type`、
   `ums_member_level.priviledge_*` 等），非 macrozheng/mall 官方内容；其中演示商品/订单数据
   为电商客服演示固定清单，数值口径属本项目演示配置。
