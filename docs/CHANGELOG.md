# 变更记录

本项目采用持续更新记录。尚未发布的改动写在“未发布”；正式发布时再移动到对应版本。

## 0.2.9 - 2026-08-28（Windows运行版）

### 新增

- Invoice Draft改为按Client、年度、季度和Fee Plan由服务端自动纳入全部Finalized Settlement；不同Platform只在客户Invoice阶段汇总，逐Sub Account冻结Platform、账户号码、账户期间和Service Fee。
- 新增`InvoiceSource`、`InvoiceLine`和`InvoiceIssueAttempt`。同一Settlement最多属于一张活动Invoice，同一客户季度Fee Plan最多一张Draft/Issuing/Issued Invoice；历史`LEGACY_GROUP_HWM`只迁移为单一汇总行，不臆造账户拆分。
- Issue前应用与SQLite Trigger均复核同组Finalized来源全集、来源金额、冻结行和Invoice总额；Draft后出现迟到Settlement会拒绝签发，需先作废Draft并重建。
- 新增ISSUING恢复接口和界面：完整双语归档可完成签发，缺失时退回Draft并清理部分文件；已预留编号永不复用，恢复动作写入IssueAttempt和AuditEvent。
- Dashboard增加Year/Quarter筛选；FC服务统计只显示当前管理客户数、期间收费客户数和Finalized Service Fee，不再把Company已收/未收列为FC指标。

### 修正与保护

- Invoice PDF改用Invoice冻结的Company/FC及冻结账户行，不再从Client当前Company关系取抬头和收款资料；跨Platform账单显示逐账户明细与合计。
- 已登记的Issued/VOID Invoice PDF下载前会匹配ExportRecord并重算SHA-256；缺记录、不可读或哈希变化时拒绝返回且不重生成，避免损坏或被覆盖的文件冒充正式归档。
- Draft提供作废重建入口；Payment提交增加界面防双击。默认Issue/Payment/流水/Snapshot日期改用本地日历日期，避免北京时间凌晨被UTC换算成前一天；PDF下载错误会显示服务端原因。
- SQLite新增Payment最小一致性保护：仅Issued Invoice可新增正数Payment，数据库层累计不得超出Invoice金额，存在Payment时不得把Invoice转Void。二态Payment、凭证、差额、更正和退款仍留待下一批。
- Windows版本源统一到0.2.9，并同时校验后端、前端、VersionInfo字符串和FixedFileInfo数值版本；构建后再次读取EXE，修复0.2.8字符串为0.2.8但数值仍为0.2.7的问题。
- 构建脚本固定校验Excel母版SHA-256、Tesseract版本及SHA-256；母版文件本身未修改，仍保持`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。

### 迁移与验证

- 新增Alembic revision `c4b7f1d92e60`。旧Invoice保留ID、编号、金额、PDF字段和Payment；VOID来源回填inactive，其余状态active；ISSUING/ISSUED/有编号VOID回填签发尝试。
- 空库、无版本旧库、四态旧Invoice、Payment保留、ACCOUNT_HWM/LEGACY_GROUP_HWM、重复upgrade、部分新结构拒绝、唯一索引及Trigger行为均有迁移回归。金额不对平、重复活动组/来源或残缺辅助表会安全停止，不自动猜测修复。
- 后端完整测试136/136、迁移专项8/8、前端TypeScript/Vite生产构建和Windows构建通过。候选包生成`构建结果.txt`前397个文件、613,163,027字节，主EXE SHA-256为`934838A9C432304FADEA47D3FB1BE6AF88F851AF7C0FD5A8A520EB76EA512E02`；ProductVersion为0.2.9，FileVersion和FixedFileInfo数值版本均为0.2.9.0。
- 候选EXE在8001端口和F盘隔离合成数据根完成空库迁移、跨Platform两Settlement/两账户冻结行、双语PDF、FC三项统计、OpenAPI、前端页面及安全退出验收。数据库head为`c4b7f1d92e60`，Invoice合计HKD 20.00并保留20.00/0.00两行；页面无横向溢出，控制台无告警或错误。
- 正式切换前由0.2.8创建并校验`financial_system_backup_20260828_145358.zip`，共396,943字节，SHA-256为`3E7D0F4D385DBF81BF109E562260C43CEB14146A582453BAE566CEE824AD466C`；ZIP CRC、Manifest及逐文件哈希通过，F盘副本迁移到`c4b7f1d92e60`后完整性、外键和关键结构检查通过。
- 0.2.9已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.9_20260828\FinancialFeeSystem`接管8000端口及原数据根。正式数据库head、完整性、外键、OpenAPI、最新前端资源和禁缓存通过；旧0.2.8运行目录已永久删除，本机只保留0.2.9正式运行版，独立业务数据与完整备份保留。
- 本批未读取真实客户资料、未调用Luna处理真实文件，也未修改Excel母版。

## 0.2.8 - 2026-08-28（Windows运行版）

### 修正

- 0.2.7仅依据Git历史重建模板，未完整还原用户原工作簿。0.2.8改以用户重新提供的18,122字节原始`新收费计划计算.xlsx`为唯一母版，不再重建工作簿。
- 仅清空主表第5行及以下18个个人备注单元格：`D5/F5/H5/M5/N5/O5/P5/Q5/S5/T5/U5/D6/M6/L7/D8/M9/M10/H12`。主表第3、4行两条示例和17个原公式、Defer页数据和36个公式全部保留。
- 除`xl/worksheets/sheet1.xml`的上述18个单元格内容外，原工作簿其余12个包部件逐项SHA-256保持一致；文档属性、自定义属性、计算链和原主题不再因重新保存而丢失。模板SHA-256为`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。
- 模板回归测试改为锁定精确备注单元格和关键原始包部件，不再错误要求主表第5行以下所有单元格为空。

### 验证

- 公司模板通过双工作表内容、公式及视觉检查：工作表顺序不变，主表17个公式、Defer页36个公式，公式错误匹配为0，主表中文内容仅保留第3、4行的“例子”。
- 系统实际Excel导出继续按Finalized Settlement逐Sub Account写入Client、账户期间、金额、HWM、锁定Service Fee和逐行公式；导出结构与公司母版保持对应。
- 后端117/117、前端TypeScript与Vite生产构建通过。0.2.8候选包生成构建结果前395个文件、613,033,170字节；主EXE SHA-256为`2DFF2CC7DF31E9AF33BD0CA7FFCB9EC12D02BDAC9DA8947DDAB1AD1A4777A975`，ProductVersion/FileVersion为0.2.8/0.2.8.0。
- 候选EXE在8001端口和隔离数据根通过版本、loopback、模板哈希、最新前端、导出POST、实际双Sub Account Excel导出、禁缓存及安全退出验收；未读取真实客户文件或调用Luna识别额度。
- 正式切换前由0.2.7创建并逐文件哈希校验`financial_system_backup_20260828_131515.zip`，SHA-256为`AAA8FA63BB2C2374332241E310FB0D7DD052210CCDBB207EEBF339E84491AE07`。0.2.8随后接管8000端口及既有`F:\财务系统\财务数据`并通过正式验收；0.2.7运行目录已永久删除，候选、构建、测试和Spreadsheet临时文件已清理，本机只保留0.2.8正式运行版。

## 0.2.7 - 2026-08-28（Windows运行版）

### 修正

- 纠正0.2.6对公司Excel模板清理范围的错误理解：从Git历史恢复原模板，主表第1至4行保持原样，其中第3、4行的两条示例及17个公式全部恢复；只清空主表第5行及以下的个人中文小字，样式不变。
- “Defer 延付利息（待确认）”工作表的原始数据和36个公式完整保留。当前系统仍不使用该页参与收费计算。
- 模板回归测试改为明确锁定主表17个原公式、两条示例、第5行以下为空及Defer公式数量，防止以后再次把“删除个人小字”扩大成删除模板公式。

### 验证

- 使用系统实际导出接口生成双Finalized Settlement样例，核对Client、Sub Account、Starting/Closing Date、金额、HWM、锁定Service Fee及逐行Excel公式；模板和系统导出均完成两张工作表视觉检查，公式错误匹配为0。
- 后端117/117、前端TypeScript与Vite生产构建通过。0.2.7候选包共396个文件、613,019,444字节；主EXE SHA-256为`CAD80580C09209B4B72DD80692334ED614B4FF7D38B694F3AA6AF4E98B103A82`，ProductVersion/FileVersion为0.2.7/0.2.7.0，修复后公司模板SHA-256为`59A1C381BFA82CBE814BDF1C4821E49EBE8D2579DFDD58A3A8A9BF18F84E5BF6`。
- 候选EXE在8001端口和F盘隔离空数据根通过版本、loopback、模板、批量Excel前端文字、POST接口、禁缓存及安全退出验收；未读取真实客户文件或调用Luna识别额度。
- 正式切换前通过0.2.6创建并逐文件哈希校验`financial_system_backup_20260828_124955.zip`，SHA-256为`724A6A123AD8864C9DF4E0683E7A6AFB7A91E38A75FD8036785DF045EDE4A279`。0.2.7随后接管8000端口及既有`F:\财务系统\财务数据`并通过正式验收；0.2.6运行目录已删除，本机只保留0.2.7正式运行版，测试、构建及Spreadsheet临时文件已清理。

## 0.2.6 - 2026-08-28（Windows运行版）

### 新增

- “季度结算”新增“公司内部财务Excel”区：可按年度、季度和客户筛选Finalized Settlement，逐项选择或全选筛选结果，并在导出前核对所选数量和Service Fee合计。
- 批量导出继续使用既有受写保护的`POST /api/exports/excel`，一次传入所选Settlement ID；后端只接受存在且Finalized的记录，输出按公司模板逐Sub Account列示账户期间、HWM和锁定Service Fee。
- 导出数据行的Company、Remarks、Invoice、Client、FC、Platform和A/C文字列启用自动换行并垂直居中，保留模板列宽同时避免公司内部查看时长名称被截断。

### 模板

- 该版本当时错误地把“删除主表第5行及以下个人中文小字”扩大为清空第3行后的示例和公式；此行为已在0.2.7纠正，不能作为后续模板规则。
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
