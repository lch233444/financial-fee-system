# 变更记录

本项目采用持续更新记录。尚未发布的改动写在“未发布”；正式发布时再移动到对应版本。

## 0.2.6 - 2026-08-28（Windows运行版）

### 新增

- “季度结算”新增“公司内部财务Excel”区：可按年度、季度和客户筛选Finalized Settlement，逐项选择或全选筛选结果，并在导出前核对所选数量和Service Fee合计。
- 批量导出继续使用既有受写保护的`POST /api/exports/excel`，一次传入所选Settlement ID；后端只接受存在且Finalized的记录，输出按公司模板逐Sub Account列示账户期间、HWM和锁定Service Fee。
- 导出数据行的Company、Remarks、Invoice、Client、FC、Platform和A/C文字列启用自动换行并垂直居中，保留模板列宽同时避免公司内部查看时长名称被截断。

### 模板

- 用户确认原Excel主表中的中文小字属于个人备注，不得作为公司模板内容。`新收费计划计算.xlsx`已清空第3行以后的示例数据、公式和个人备注，保留正式英文列标题、样式、列宽、日期/金额格式及空白数据行。
- “Defer 延付利息（待确认）”工作表继续原样保留但不参与当前收费计算；运行时导出仍会再次清空主表数据区后写入正式记录。

### 验证

- 新增模板无个人备注/示例数据和多Settlement批量导出回归测试；后端117/117、前端TypeScript与Vite生产构建通过。
- 使用公司模板生成双Settlement/双Sub Account样例，核对两张工作表、字段、公式、锁定Service Fee和文字换行；主表个人中文备注匹配为0、公式错误匹配为0，两张工作表均完成视觉检查。
- Windows构建脚本将pytest及后续构建的TEMP、TMP和`--basetemp`统一放到仓库内`backend/test-tmp`，构建成功后删除，避免依赖当前电脑不可访问的C盘pytest临时目录。
- 0.2.6候选包共396个文件、613,013,867字节；主EXE SHA-256为`C4D659FEAB7A8FB0FF2617EC960773833706FF6BE41FCB47173E337309F1D2BB`，ProductVersion/FileVersion为0.2.6/0.2.6.0，公司模板SHA-256为`A9A3A573F51F48FE315190390EF4E993285134130F910A1E6A4CB6E1B35BF980`。
- 候选EXE在8001端口和F盘隔离空数据根通过版本、loopback、模板、批量Excel前端文字、POST接口和安全退出验收；未读取真实客户文件或调用Luna识别额度。
- 正式切换前创建并完整读取校验`financial_system_backup_20260828_122302.zip`，SHA-256为`7E534A5C3BCD985488FEF2102BE11409D92488C7E0CB5D8BA46EE46153CE6BCD`。0.2.6在8000端口接管既有`F:\财务系统\财务数据`并通过正式验收；已停止并删除0.2.5运行目录，本机只保留0.2.6正式运行版，测试和构建临时文件已清理。

## 0.2.5 - 2026-08-28（Windows运行版）

### 简化

- 用户明确要求不增加假设性防御分支，并要求本机只保留最新运行版。协作规则和项目说明书同步写明边界：删除静默吞错、多级fallback、隔离副本和重复恢复逻辑，但保留金额、HWM、凭证、人工确认、审计、路径、loopback及必要事务补偿。
- 未确认导入删除改为单一路径：继续与OCR/Luna/确认共享锁，验证未确认、未关联Snapshot和原件路径后直接删除原件；同步解除其他导入的语义重复引用，再删除记录并写审计。不再创建`tmp/statement-delete`隔离文件或数据库失败恢复分支。
- 删除Statement导入请求开头重复的`rollback/expire_all`及Luna持锁期间不可能发生的二次状态重载；抽取统一的导入记录404查询，并复用本地OCR字段字典和同一确认时间。
- 备份ZIP解压与待恢复目录合并为同一套格式、路径和逐文件哈希校验；恢复清理不再忽略marker或暂存目录删除错误。
- 数据库启动缺少Alembic配置或迁移目录时明确失败，不再静默`create_all`；Codex状态不再吞掉未知异常，OCR语言检测和PDF字体注册只捕获已知错误，安全退出不再隐藏stop hook失败。
- 前端API和下载共用服务端错误解析；构建脚本固定覆盖唯一`release`候选目录，并自动生成当次EXE哈希、文件数、字节数和版本结果，移除手工复制旧版构建结果的流程。

### 本地清理

- 精确永久删除0.2.1、0.2.3旧运行版、三个仓库候选发布包、23个测试/验收临时目录及构建产物，共31个目标、3,354个文件、3,158,133,678字节（2.941 GiB）。源码仓库、0.2.4当时正式版、`F:\财务系统\财务数据`及完整备份均核验保留。
- 0.2.5正式验收后继续删除0.2.4、固定`release`候选、PyInstaller/前端产物、测试隔离目录及Python/TypeScript缓存，共15个目标、1,189个文件、686,566,848字节（0.639 GiB）。两轮合计删除46个目标、4,543个文件、3,844,700,526字节（约3.58 GiB），本机最终只保留0.2.5正式运行版。

### 验证

- 前端TypeScript与Vite生产构建通过；后端完整测试115/115通过。未读取真实客户文件，也未调用Luna识别额度。
- 0.2.5固定候选包共396个文件、613,011,499字节；主EXE SHA-256为`69E3E9FEE74BB2283FE0E194AA73A9942454CC1D0EC8B6CF3D3AA7564BFD9ADE`，ProductVersion/FileVersion为0.2.5/0.2.5.0，内置Codex签名有效。
- 候选EXE在8001端口用程序生成的空白PNG完成`unknown`导入、直接删除、记录404、原件归零、无删除隔离目录和安全退出；未读取真实客户文件或调用Luna。
- 正式切换前创建并验证`financial_system_backup_20260828_112030.zip`，SHA-256为`F9ADF55EFA805B50D15E5CEBC5AA2B82E12F805D9814FC0413EBDB42502CE610`。0.2.5正式版随后在真实数据根通过版本、loopback、Alembic head `a6d1f4c28b73`、OpenAPI DELETE、最新前端资源、禁缓存及Luna `ready`验收并保持运行。

## 0.2.4 - 2026-08-27（Windows运行版）

### 修复

- 修复本地OCR分类为`unknown`、Luna分类为`empf_account_page`时页面仍直接判定为非余额凭证的问题。仅在这一组合下显示人工复核表单，并要求财务额外确认已查看原件和采用Luna文档类型；字段冲突确认仍为独立硬前置。
- 后端确认接口同步验证受限覆盖规则并记录`LUNA_HUMAN_CONFIRMED`审计来源；本地明确分类为供款类凭证时，即使Luna返回余额页也继续禁止生成Balance Snapshot。

### 新增

- 导入记录增加删除API和页面按钮：只允许删除未确认、未关联Balance Snapshot的记录；数据库记录、原始文件及OCR/Luna结果同步清理，已确认记录由前后端双重禁止。
- 删除过程与识别/确认共享锁，原件先移动到数据根隔离区，数据库提交失败时恢复；成功删除留下不含客户原文的`STATEMENT_IMPORT_DELETED`审计事件。

### 验证

- 新增Luna文档类型人工采用、供款类不可覆盖、未确认删除、已确认拒删和本地写请求标记回归测试；后端115/115通过，前端TypeScript与Vite生产构建通过。
- 0.2.4 Windows候选包完成PyInstaller、Codex/Tesseract校验和隔离EXE实测：程序生成的测试PNG从`unknown`导入到删除后记录404、原件与隔离区均无残留；未读取客户文件或调用Luna额度。
- 切换前创建并验证真实数据完整备份，安全退出0.2.3后从全新正式目录启动0.2.4；真实数据根与Alembic head不变，线上OpenAPI、前端资源和Luna `ready`状态均通过，0.2.3/0.2.1目录完整保留。

## 0.2.3 - 2026-08-27（Windows运行版）

### 修复

- 修复Luna App Server继承源码仓库cwd而加载`AGENTS.md`、导致辅助识别全部转人工的问题：进程、Thread和Turn统一切换到应用专用、带固定项目根标记的隔离workspace；任何残留`instructionSources`仍失败关闭。
- 修复Luna对PDF只发送第一页的问题：20页内逐页渲染为无元数据PNG并在同一Turn按序识别，超过20页明确要求拆分，临时图片始终清理。
- Finalized结算覆盖期间禁止补录账户流水；HWM链持久化前序Settlement，并在Calculate、Finalize和Void时阻止倒序锁定、上游先作废及事后插入更早期间。
- Settlement Finalize冻结Company/FC历史归属，FC报表、Invoice和Excel统一使用冻结归属，不再随Client后来转移负责人而变化。
- 金额输入统一拒绝两位小数以外的静默精度损失，Fee Rate精确转换为bps；季末手工快照尊重用户取消Closing资格的选择。
- Excel直接导出锁定的Service Fee分整数；Dashboard的Settlement、Invoice、Payment和FC概览统一使用同一年度/季度口径。
- Settlement存在Draft/Issuing/Issued Invoice或下游结算时禁止Void，并拒绝重复Void。
- 生成型Excel/PDF及旧Invoice补档改为受写请求保护的POST；GET不再写文件或ExportRecord。
- Invoice签发拆成编号预留、事务外双语PDF渲染和最终确认三个阶段，并以进程内短锁保证单机并发编号唯一。
- 修正前端DTO的nullability和状态联合类型，使其与后端实际JSON一致。

### 新增

- 用户确认每个Sub Account使用自己的Starting Date和Closing Date独立计算。结算明细新增账户日期和Days，流水汇总、Snapshot匹配、凭证检查、Finalized期间锁定、Excel及PDF均改用账户期间；容器日期只表示最早至最晚的汇总展示范围。
- 新增`a6d1f4c28b73`迁移，为历史账户明细从容器无损回填期间，并把SQLite财务Trigger切换到账户级日期。
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

- 后端完整测试111/111通过；前端TypeScript与Vite生产构建通过；两页合成PDF逐页传输测试及结算PDF视觉渲染检查通过。
- 生成0.2.3独立Windows构建包；主EXE ProductVersion/FileVersion分别为0.2.3/0.2.3.0，SHA-256为`63454835363048CB087CFF20CBC03A81DF2202F9829178FFD7459988141C2AC9`，构建目录395个文件、612995200字节。
- 候选EXE在8001端口和隔离测试数据根通过`/api/health`、`/api/system-info`、Luna状态初始化及安全退出验收；Luna只报告专用环境未登录，不再报告项目指令来源。未上传真实客户文件，真实识别尚未验收。
- 定位当前电脑弹窗持续出现的直接原因：8000端口仍运行0.2.1旧正式包，并未运行0.2.3构建包。创建并验证真实数据备份后安全退出0.2.1，从全新正式目录启动0.2.3；正式数据根不变、数据库迁移至Alembic head，旧目录完整保留回退。
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
