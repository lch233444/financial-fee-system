# 变更记录

本项目采用持续更新记录。尚未发布的改动写在“未发布”；正式发布时再移动到对应版本。

## 未发布（目标版本0.2.3）

### 修复

- Finalized结算覆盖期间禁止补录账户流水；HWM链持久化前序Settlement，并在Calculate、Finalize和Void时阻止倒序锁定、上游先作废及事后插入更早期间。
- Settlement Finalize冻结Company/FC历史归属，FC报表、Invoice和Excel统一使用冻结归属，不再随Client后来转移负责人而变化。
- 金额输入统一拒绝两位小数以外的静默精度损失，Fee Rate精确转换为bps；季末手工快照尊重用户取消Closing资格的选择。
- Excel直接导出锁定的Service Fee分整数；Dashboard的Settlement、Invoice、Payment和FC概览统一使用同一年度/季度口径。
- Settlement存在Draft/Issuing/Issued Invoice或下游结算时禁止Void，并拒绝重复Void。
- 生成型Excel/PDF及旧Invoice补档改为受写请求保护的POST；GET不再写文件或ExportRecord。
- Invoice签发拆成编号预留、事务外双语PDF渲染和最终确认三个阶段，并以进程内短锁保证单机并发编号唯一。
- 修正前端DTO的nullability和状态联合类型，使其与后端实际JSON一致。

### 新增

- Settlement正式切换为Sub Account级HWM：每条账户明细独立保存Original/Adjusted/Next HWM、Contribution/Withdrawal、Above HWM和Service Fee，容器层只汇总各账户结果，账户间盈亏不再抵销。
- Beginning必须来自首次明确期初Snapshot或自动继承该账户上期Closing Snapshot；Closing必须来自日期匹配且符合资格的Snapshot，正式API不再接受手填Beginning/Closing。
- Snapshot和Transaction列表新增记录级凭证状态；Draft允许缺凭证，Finalize同时由路由与SQLite Trigger阻止缺少Beginning、Closing、Contribution或Withdrawal凭证的结算。
- 新增`f2a8c7d41e90`迁移：保留既有结算为`LEGACY_GROUP_HWM`，新结算使用`ACCOUNT_HWM`/`HWM-2.0-ACCOUNT`，并建立账户HWM顺序、前序值、Snapshot资格和凭证并发保护。
- 资金与余额页改为将凭证关联到具体Snapshot或Transaction；季度结算页改为选择Beginning/Closing Snapshot、录入首次账户HWM并展示逐账户收费结果。
- 内部Excel对新口径按Sub Account逐行导出，结算及Invoice PDF新增账户级收费明细表；历史组合口径仍保持原单行输出。
- 新增账户盈利/亏损不可抵销、账户HWM继承、缺Snapshot凭证和缺Transaction凭证的回归测试。

- 新增Settlement冻结Company/FC与前序结算字段的Alembic迁移，为既有数据回填归属和HWM链，并用SQLite Trigger兜底并发期间的账本/HWM顺序。
- “客户与账户”页面新增OCR生成Draft Client/Sub Account的补全及激活表单。
- 新增代码审查缺陷回归测试，覆盖金额精度、Closing资格、账本/HWM锁定、历史FC、期间报表、导出方法与并发签发。
- 文档同步检查提升为：除同步文档本身外，任何项目文件修改都必须同步项目说明书。

### 文档

- 补齐2026-08-27用户确认的业务背景：当前唯一收费计划为利润20%，每个Sub Account独立HWM及Service Fee，跨Platform仅在客户Invoice阶段汇总，Closing/Beginning及五类财务记录的凭证要求、二态Payment、错账退款/公司承担差额、FC统计范围、Defer取舍和单机版优先级。
- 明确区分“用户确认的目标规则”和“0.2.3当前实现”，将组合级HWM、单Settlement单Invoice、可选快照/凭证、部分付款及FC收款统计列为已确认但尚未实现的差距。
- 新增交接完整性硬规则：任何在对话中确认的项目背景、术语、业务规则、例外、范围或优先级都必须在同一次任务写回GitHub交接资料，不得只依赖聊天记录或Agent记忆。
- 将项目说明书升级为工程师级持续交接手册，补齐运行拓扑、模块职责、领域关系、状态机、完整API地图、前端页面、配置项、迁移/备份、测试矩阵、构建验收、故障定位、技术债优先级和Definition of Done。
- 在README增加统一接手入口和阅读顺序，使新电脑、新工程师及新开发代理能够从GitHub直接建立可靠的项目上下文。
- 明文写入强制规则“对于项目每一次修改都要同步修改项目说明书”，并同步更新API、状态机、迁移、前端与已知缺口。

### 新增

- 建立私有GitHub源码仓库所需的忽略规则、协作规则、持续更新项目说明书和文档同步检查脚本。
- 增加跨电脑开发、外部运行组件恢复和Pull Request验收说明。

### 安全

- 测试夹具改用虚构姓名与账号，识别评估报告改用匿名样本标签。
- 明确排除客户账单、业务数据、识别输出、登录目录、发布包和大型工具二进制。

### 验证

- 后端完整测试106/106通过；前端TypeScript与Vite生产构建通过。
- 修正文档同步检查脚本在Windows中文路径下的Git文件名解析。

## 0.2.2 - 2026-08-26（候选版）

### 新增

- 增加账户余额页、供款记录、供款/资产转入记录和未知文件分类。
- 增加PDF原生文字层读取、扫描PDF多页OCR和长截图重叠分段OCR。
- 增加余额算术校验及按基金名称执行的OCR/Luna交叉核验。
- Luna改用金融系统专用独立登录目录，不继承桌面Codex配置。

### 修复

- 非余额凭证在服务端禁止生成余额快照。
- 改善Manulife多基金表、受托人和小额持仓解析。

## 0.2.1 - 2026-08-24

- 完成本地MVP、Windows onedir发布、页面安全退出、前端缓存修复和基础OCR/Luna人工复核闭环。
