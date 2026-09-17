# 代码质量审查全部修复计划

> 执行流程：使用 Superpowers 的 subagent-driven-development、test-driven-development 和 verification-before-completion；每组完成后先复核需求，再复核代码质量。

**目标：** 修复 2026-09-16 审查确认的全部 11 项问题及相邻过期文案，交付经过完整验证的 1.0.3。

**架构：** 沿用现有路由、共享契约、SQLite 写锁和现金分配台账，仅在已证实缺陷的边界增加验证。当前财务政策及历史财务记录保持；测试和候选仅使用 F 盘隔离数据。

**技术：** FastAPI、SQLAlchemy、SQLite/Alembic、React/TypeScript、pytest、Vitest、PyInstaller。

基线：`5c1a0f85ca76214eb6c3eb6eddfc1a7657be9008`；工作区 `F:\fffix0917\src`。本计划是执行记录，业务规则仍以项目说明书为准。用户已要求全部修复；常规正式升级沿用 AGENTS 中的持续授权。

## 1. 账户、结算与迁移（问题 2、9、10）

- [x] 将已有双连接日期竞态、旧迁移 DDL 中断、CLOSED 客户建账户复现转换为正确行为测试，先确认失败。
- [x] `routes/master.py`：账户更新首次读取前取得 `BEGIN IMMEDIATE`；创建账户不论请求状态均拒绝 CLOSED 客户。
- [x] `services/settlement_current.py`：Calculate/Finalize 共同重新判断 Closing 日期为季末或当前实际结束日；保留原有资格与凭证校验。
- [x] 新增日期守卫契约及 `b917c0a31003` 迁移（前版 a916c0e2b102）；只保护未来 Finalize，不改历史账目。
- [x] `d4f8a1c73b29_quarter_opening_boundary.py`：读校验、DDL 和版本推进使用显式事务；注入异常后版本及 Trigger 不变，正常重试通过。
- [x] 跑相关 pytest，需求复核、代码复核通过。

## 2. Invoice 与归档（问题 3、4、5、11）

- [x] 在 `backend/tests` 补迟到来源组合更正、空/损坏/摘要不符 PDF 恢复、连续更正现金展示、无编号 VOID PDF 回归，先确认失败。
- [x] `routes/invoices.py`：仅公司变更且不重算时校验相同来源；保留资金与完整替代链限制。
- [x] `services/invoice_archive.py`、`serializers.py` 与恢复路由共用双语 PDF 检查：普通文件、非空、可解析有页、已有可信摘要一致；失败保持 ISSUING。
- [x] `serializers.py`：当前现金分配为零时返回零，原始收款独立保留；下载无编号作废草稿返回受控错误。
- [x] 跑相关 pytest，需求复核、代码复核通过。

## 3. 原件与 AI 进程（问题 6、8）

- [x] 补原件缺失/内容改变后的 confirm 零写入和旧 reader 延迟 EOF 的确定性并发回归，先确认失败。
- [x] `routes/statements.py`：首次业务写入前校验原件路径、普通文件及 SHA-256，不一致返回 409 并保持待复核。
- [x] `services/codex_app_server.py`：reader、响应、通知和失败只影响其所属进程代次，旧进程不能污染新 pending 请求。
- [x] 跑相关 pytest，需求复核、代码复核通过。

## 4. 页面与完整结构契约（问题 1、7）

- [x] `frontend/tests/paymentEvidence.test.tsx` 补 A 填写付款后切换 B 的测试：金额、日期、备注与文件不能继承，重新填写后只提交 B 的内容。
- [x] `InvoicesPage.tsx` 以账单 ID 重建付款表单，提交绑定当次账单身份；`SetupPage.tsx` 去除公司名参与编号的旧说明。
- [x] 补当前库丢失任意必需 Trigger 时启动与备份均拒绝、保留现场的回归。
- [x] `database.py`、`services/backup.py` 共用完整当前结构校验；纳入新增日期守卫契约及版本，保持支持的旧库恢复路径。
- [x] 跑针对性测试，需求复核、代码复核通过。

## 5. 集成及交付

- [x] 同步 README、项目说明书、CHANGELOG、操作手册、构建清单与版本标识；清理报告列出的过期规则文案。
- [x] 完整后端、前端测试及生产构建；检查本次差异与文档范围，独立复核全部修复。
- [x] 固定 release 构建 Windows 候选，用隔离数据验收实际 EXE、HTTP、页面、OCR、归档和退出。
- [x] 升级前完整备份、同版本隔离恢复、新版迁移及历史字段/文件一致性验证通过后正式切换；预检冲突即停止升级，不擅改旧账。
- [x] 正式验收、升级后完整包、独立旧程序回滚包；提交并同步 GitHub。
- [ ] 本次精确清理未完成：F:\fffix0917删除遇访问/属性错误后按规则停止；部分临时文件和退役1.0.2仍保留，未改属性、Force或更换工具重试。

测试命令统一设置 F 盘 TEMP/TMP；Python 使用项目 `.venv\Scripts\python.exe`，后端完整回归先运行 `test_system_backup.py`；前端执行 `pnpm --dir frontend run test` 和 `pnpm --dir frontend run build`。各项证据存入 `F:\fffix0917\logs` 及正式升级证据目录。

执行结果：全部实现、544/72全量回归及正式数据验证已完成。独立代码复核发现的双归档歧义已补三项先失败后通过测试并修复；需求复核11项通过。发布与验证边界见项目说明书12.19；Git交付与精确清理结果以证据目录JSON为准。
