# 数据库公开边界

公开演示 SQL 为 `demo/financial_report.sql`：5 家虚构公司，2022–2024 年 FY，四张财务表各 15 行。数字和业务说明全部由项目原创，Apache-2.0，不对应真实上市公司。

使用 `scripts/demo-seed.ps1` / `scripts/demo-seed.sh` 导入隔离 `finsight_demo`，脚本同时生成公司登记表和独立 SELECT-only 查询账户。业务/ETL 的 DB_* 与 Agent 查询的 SQL_DB_* 分开。

旧 `database/financial_report.sql` 的原始数据缺少已确认的再分发依据，仅保存在 Git 忽略的本地归档中。当前同名文件是无数据的兼容说明；公开仓库、提交历史和发布包均不包含旧数据，现有本地数据库也不会因代码仓库重建而更改。
