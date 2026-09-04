# 变更记录

本项目采用持续更新记录。尚未发布的改动写在“未发布”；正式发布时再移动到对应版本。

## 0.2.19 - 2026-09-04（Windows运行版）

### 自然季度期初边界修正

- Q1/Q2/Q3/Q4的Starting Date继续显示自然季度首日；首次账户结算在该日开始时，Beginning Snapshot除同日快照外，也可选择紧邻的上一季末快照。非季度首日开始管理的账户仍只接受Starting Date当天快照。
- 资金流水计算和凭证检查改为以实际Beginning Snapshot日期为下边界。选择上一季末快照时，季度首日生效的Contribution/Withdrawal会进入本季；选择Starting Date当天快照时，当天资金视为已包含在Beginning中，不会重复计算。
- Calculate、Finalize当前态重算、SQLite Finalize Trigger及Finalized凭证保护统一使用上述口径；新增Alembic revision `d4f8a1c73b29`，在不改变业务数据行的前提下替换3个相关Trigger，总数保持57。
- “新增资金流水”把日期明确为“资金生效日期”，提示按公司确认的实际入账／基金分配口径填写，不要把供款所属月份截止日当成资金发生日。系统不会自动改写既有资金流水。
- 新增首次自然季度及后续继承季度的边界回归，覆盖上一季末Beginning、季度首日资金、收费结果、Finalize和凭证冻结；迁移兼容已版本化及完整未版本化的0.2.18数据库。

### 测试与发布

- 季度边界针对性24/24、后端完整回归360/360及Windows构建内第二轮360/360通过；前端TypeScript/Vite 7.3.6生产构建通过，最终资源为`index-BPfXgcpZ.js`和`index-DAkh9ICn.css`；项目文档检查通过。
- Windows包于2026-09-04 15:10:58 +08:00构建；写入`构建结果.txt`前405个文件、614348825字节。主EXE SHA-256为`AF7F152FE59568EF36FBC51B314070B7CE3349895E0135C19DC2235F7980A865`，ProductVersion/FileVersion为0.2.19/0.2.19.0；Excel母版SHA-256保持`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。
- 8001最终候选在F盘纯合成数据根完成Q2上一季末Beginning、季度首日Contribution、Closing、20%收费及Finalize闭环；最终前端提示、49条OpenAPI、入口禁缓存、head `d4f8a1c73b29`、完整性、外键0、57个Trigger和季度边界精确契约通过。
- 正式切换前0.2.18创建2026/Q3完整数据包`financial_system_data_package_2026_Q3_20260904_151249_073978_6u9oqirh.zip`，887752字节，SHA-256为`4AF30D7CCC44D217DCE810425ABF074ECD60C165103F43DE2E4F0D9486DB931A`，API副本一致；该包在F盘隔离数据根完成0.2.18导入恢复和0.2.19迁移预检。
- 正式0.2.19已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.19_20260904\FinancialFeeSystem`接管`127.0.0.1:8000`及既有数据根；版本、进程、EXE、前端、OpenAPI、head、完整性、外键0、57个Trigger和无恢复残留均通过。升级后同版本数据包`financial_system_data_package_2026_Q3_20260904_151627_786418_xg_p2tk6.zip`为887773字节，SHA-256为`E6FA3EC310F621440ADA308710E1D1A25DE4A6EAC34A99F85366A56801693C93`，API副本一致，并在另一F盘隔离数据根完成0.2.19导入、自动退出、重启和路径复验。
- 本批未输出客户业务内容、未打开客户文件、未调用Sol/Luna，也未修改Excel母版；旧0.2.18运行目录及候选/预检临时目录在正式验收后移入Windows回收站，可恢复。

## 0.2.18 - 2026-09-04（Windows运行版）

### Invoice PDF A+B融合版式

- 客户Invoice中英文PDF升级为用户确认的A+B融合设计：保留A版白底、清晰分区和易核对表格，同时采用B版深蓝、香槟金与更精致的标题、汇总和付款资料区域。装饰图形为中性双菱形，不冒充Company官方Logo。
- 客户PDF只展示Company、Client、Sub Account、Settlement Period、Fee Plan服务说明、HKD金额、Invoice No、Issue/Due Date和付款资料；不显示Trustee、Platform或Platform Code。签发前内部页面仍保留Platform、Fee Plan和Scheme供财务人员辨认，不删改底层冻结来源及跨Platform合并校验。
- 中英文PDF分别使用繁体中文和英文标签；银行转账、划线支票、收款人、银行、户口号码、付款邮箱和邮寄地址继续来自签发时冻结资料。已签发归档PDF保持不可变，只有新签发Invoice使用新模板。
- 本批不修改财务金额、编号、结算、付款、数据包、数据库结构或Excel母版；Alembic head继续为`c1a7d5e9b402`，Trigger继续为57个。
- Windows构建仍执行全部后端测试，但先运行内存压力最大的备份/恢复测试，再运行其余测试文件，避免低可用内存机器在测试进程后段一次读取合成恢复包时出现环境性`MemoryError`；没有跳过、放宽或改写测试。

### 测试与发布

- Invoice PDF专项20/20、后端完整回归357/357及构建内第二轮357/357通过；前端TypeScript/Vite 7.3.6生产构建通过，最终资源为`index-CEC_S9AS.js`和`index-DAkh9ICn.css`，项目文档检查通过。
- Windows包于2026-09-04 13:35:17 +08:00构建；写入`构建结果.txt`前403个文件、614322908字节，写入后404个文件、614323609字节。主EXE SHA-256为`D1885972A71C6FE6A96A8F559A1286F13BA878613B30A4C7A21695742FB68AD4`，ProductVersion/FileVersion为0.2.18/0.2.18.0；Excel母版SHA-256保持`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。
- 8001候选版在F盘纯合成数据根完成两个内部Platform、两个Sub Account的结算及合并Invoice签发；中英文PDF均为单页A4，文字、尺寸、付款资料、视觉无重叠/裁切及禁止字段不外显均通过。候选数据库为head `c1a7d5e9b402`、`integrity_check=ok`、外键0、57个Trigger，49条OpenAPI路径和入口禁缓存通过。
- 正式切换前0.2.17创建2026/Q3完整数据包`financial_system_data_package_2026_Q3_20260904_133808_455462_30riaqax.zip`，639648字节，SHA-256为`B83290FEC8FEA46104CCD4DB452862BB858E9F9F05318AE500BC7A79E1A6641C`，API副本一致；同版程序在F盘隔离数据根完成导入、自动退出和重启，再由0.2.18候选接管，head、完整性、外键0、57个Trigger及无恢复残留全部通过。
- 正式0.2.18已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.18_20260904\FinancialFeeSystem`接管`127.0.0.1:8000`和既有数据根；版本、进程路径、EXE、49条OpenAPI路径、前端资源、禁缓存、Sol精确模型可用性、数据库head/完整性/外键0/57个Trigger及无恢复残留均通过。验收未查询或输出客户业务行、未打开客户文件、未调用Sol/Luna，也未修改Excel母版。
- 旧0.2.17正式目录、固定`release`候选、构建产物、纯合成候选、隔离预检、PDF渲染及本批测试临时目录均已移入Windows回收站，可恢复；本机活动运行目录只保留0.2.18，正式数据根、既有备份和本次完整数据包继续保留。

## 0.2.17 - 2026-09-04（Windows运行版）

### 完整数据包交接

- “数据与系统”页面把完整备份恢复升级为面向季度复核的完整数据包导出/导入。年度和季度只作为交接批次标签写入数据包，不筛选资料；每次导出仍包含当时系统中的全部数据库记录、账单原件、附件、付款/退款凭证以及系统生成的Excel/PDF。
- 数据包Manifest新增精确系统版本和复核年度/季度。新格式只允许完全相同的系统版本及当前数据库revision导入，避免不同程序结构误读同一份资料；旧版`financial-fee-system-backup`格式仍按旧规则兼容，但不获得新的跨电脑路径保证。
- 导入明确为完整覆盖，不是追加、去重或合并。页面在提交前提示接收方当前全部资料将被替换；验证成功后继续使用既有写入门闩、安全退出、下次启动事务切换、失败回滚及断电恢复机制。
- 新格式支持发送方与接收方使用不同盘符或数据目录。系统只在待导入数据库副本中重定向Statement Import、Attachment、ExportRecord及Invoice PDF路径；随后重新核对SQLite完整性、外键、57个Trigger、数据库哈希及全部物理文件，全部通过后才原子替换接收方资料。
- 严格文件核验扩展到数据库中的全部Attachment、ExportRecord和Invoice中英文PDF；任何缺失、越界、非普通文件、大小或SHA-256不一致均在生成或导入前拒绝。
- 修正内部Excel与Settlement PDF重复生成时可能复用文件名、覆盖旧导出文件的问题；每次生成改用时间微秒和随机段组成的唯一归档名，既有导出记录不再因后续重复导出失去对应文件。

### 测试、数据包与正式发布

- 完整数据包专项62/62、相关导出与数据包69/69、后端完整回归356/356通过，覆盖Manifest精确字段、季度标签、版本不匹配拒绝、旧格式兼容、不同数据根路径重定向、完整性/外键/57个Trigger、唯一导出文件名和失败回滚；前端TypeScript/Vite生产构建通过，最终资源为`index-CEC_S9AS.js`和`index-DAkh9ICn.css`；项目文档检查通过。
- Windows包于2026-09-04 11:47:08 +08:00构建；写入`构建结果.txt`前403个文件、614301097字节，写入后404个文件、614301798字节。主EXE SHA-256为`13F03E9739915F8C1BFA64BAE5DBE7B6F27B42257E97322C7C98A5B91A69104E`，ProductVersion/FileVersion为0.2.17/0.2.17.0；Excel母版SHA-256保持`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。
- 候选版在两个不同F盘纯合成数据根完成2026/Q3数据包生成、完整覆盖导入、自动退出和重启；客户、账户及附件转移、附件哈希、四类路径重定向、当前head、完整性、外键0、57个Trigger及无恢复残留均通过。浏览器确认同版本、不合并提示和646像素无横向溢出。
- 正式切换前由0.2.16创建`financial_system_backup_20260904_115132_097175_b0icn7wa.zip`，639599字节，SHA-256为`6D61A97291E9AF781463FDD44D976100A4A8F183FCE87CC38A264DDA18631B0F`，API副本一致；0.2.17随后创建2026/Q3完整数据包`financial_system_data_package_2026_Q3_20260904_115224_264023_kg42jhv6.zip`，639647字节，SHA-256为`73DB05797C5C04BAF00B20058C307FCA48DB8F8F384EE72728A22D58310BD4F0`，API副本一致。数据包在F盘隔离数据根完成导入、自动退出和重启预检，所有结构、文件路径及恢复状态均通过。
- 正式0.2.17已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.17_20260904\FinancialFeeSystem`接管`127.0.0.1:8000`和既有数据根；版本、进程路径、EXE、49条OpenAPI路径、前端资源、禁缓存、Sol精确模型可用性、数据库head/完整性/外键/57个Trigger及正式浏览器页面均通过。旧0.2.16、固定候选、构建产物、纯合成数据根、隔离预检和精确测试临时目录已移入Windows回收站，可恢复；本机只保留0.2.17正式运行目录，正式数据根、备份及数据包继续保留。
- 本批不修改Alembic结构及Excel母版。正式浏览器只从默认首页进入“数据与系统”页面，未打开客户文件或业务详情，也未把客户字段写入发布记录；不调用Sol/Luna处理真实文件。

## 0.2.16 - 2026-09-03（Windows运行版）

### 账单辅助识别固定切换至Sol

- 用户主动触发的ChatGPT Pro辅助识别从固定`gpt-5.6-luna`切换为固定`gpt-5.6-sol`；模型可用性、Thread及Turn回执均继续按精确模型ID核对，任何改路由或第二模型调用仍立即停止并转人工复核。
- 保持原有低推理档位、单次调用、严格结构化财务Schema、OCR独立结果、逐字段差异清单和人工最终确认。Sol结果仍只能保存为待复核候选，不能直接创建或修改Balance Snapshot、Transaction、Settlement、Invoice或Payment。
- 后端、账单导入页、数据与系统页及安全退出提示统一显示Sol。新审计来源改为`SOL_SELECTED`和`SOL_HUMAN_CONFIRMED`；既有历史记录不改写。对外确认字段`luna_document_type_reviewed`、审计摘要键`luna_count`以及专用登录目录`LunaCodexHome`仅为兼容既有客户端、历史审计与现有登录继续保留，不再代表实际调用Luna。
- 隐私受控的实时冒烟脚本新增`--summary-only`，仅输出模型、严格Schema是否通过及差异数量，不输出识别字段。用户明确授权的指定测试图片已由Sol完成一次识别并通过严格财务字段Schema；该过程未生成财务入账。
- 本批不改数据库结构，因此Alembic head继续为`c1a7d5e9b402`，Trigger仍为57个；不修改已确认Excel母版。

### 测试、备份与正式发布

- 后端完整回归353/353及前端TypeScript/Vite生产构建通过，最终资源为`index-CfnBQxXk.js`和`index-BoYfTAme.css`。Windows包于2026-09-03 16:45:01 +08:00构建；写入`构建结果.txt`前403个文件、613863716字节，写入后404个文件、613864417字节。主EXE SHA-256为`CE12DBE8B7BAC8BA30D1257D8C1596D4215FFE5C3271660E8D02F42D58DDE5A7`，ProductVersion/FileVersion为0.2.16/0.2.16.0；Excel母版SHA-256仍为`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。
- 候选EXE在8001端口和F盘隔离数据根通过健康、49条OpenAPI路径、最终资源、禁缓存、Sol精确模型/登录/可用性及浏览器验收；“数据与系统”和“账单导入”只显示Sol，1280宽度无横向溢出，控制台无告警错误。候选数据库为head `c1a7d5e9b402`、`integrity_check=ok`、外键违规0、57个Trigger，严格备份创建及恢复验证通过。
- 正式切换前由0.2.15创建`financial_system_backup_20260903_164924_726172_v0x_wd3d.zip`，554659字节，SHA-256为`3FF6B716D6599DFFBCD696A88F9F0B00E7FD84CA2C33E01CBFBBC32153D6109E`；API副本哈希一致。新代码严格验证归档后，在F盘隔离副本启动0.2.16，确认head、完整性、外键、57个Trigger及Sol状态均正常，才停止旧版。
- 0.2.16已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.16_20260903\FinancialFeeSystem`接管`127.0.0.1:8000`和既有数据根。正式验收通过版本、进程路径、EXE版本/哈希、49条OpenAPI路径、最新前端资源、入口`no-store`、Sol精确模型且`ready`、数据库head/完整性/外键/57个Trigger及浏览器页面无溢出/无告警；旧0.2.15目录已整体移入Windows回收站，可恢复。
- 云端识别仅使用用户明确授权的指定测试图片，且只验证Schema成功状态；没有输出识别字段或生成财务入账。未打开或输出其他客户业务内容，Excel母版未修改。

## 0.2.15 - 2026-09-01（Windows运行版）

### Client与Sub Account受控删除

- “客户与账户”页面为Client和每个Sub Account增加删除按钮及不可撤销二次确认。系统不会判断某条资料是不是测试数据；删除仅用于财务人员确认误建或不再需要、且没有任何业务或历史引用的资料。
- Client存在Sub Account、Settlement、Invoice、规范化附件或导出引用时返回409；Sub Account存在资金流水、Balance Snapshot、已确认Statement Import、任意Settlement账户行、规范化`ACCOUNT/SUB_ACCOUNT`附件或导出引用时返回409。删除在`BEGIN IMMEDIATE`内完成应用全引用检查和Core SQL `DELETE`，不调用ORM级联、不把业务外键置空；成功后以`entity_id=NULL`记录原ID和最小识别字段。
- Client、Sub Account、Statement Import和Balance Snapshot的新建ID改由`app_settings`中的四项持久高水位分配；迁移会按现存记录及相关删除审计播种，手工Snapshot及账单确认Snapshot均显式取号。记录即使被删除，其ID也不会分配给以后新建资料，避免旧AuditEvent中的已删ID与新资料混淆。对升级前已发生的Statement ID复用，迁移只会自动消歧“一条`STATEMENT_IMPORT_DELETED`、实体类型为Statement Import、没有新版保留字段、同ID只有一个冲突，且删除审计时间严格早于当前账单建立时间”的组合。原审计details全部保留，旧ID改存`deleted_import_id`并清空`entity_id`，同时写入版本标记及独立`LEGACY_STATEMENT_DELETE_ID_REUSE_DISAMBIGUATED`更正审计。迁移、既有head启动和备份均交叉验证该证据；任一数量、时序、类型或标记不可严格证明时仍停止。当前同ID账单后续按正常流程删除后会产生新的严格删除审计，该ID以后不得再复用。
- 前端同一时间只允许一个Client/Sub Account删除请求，处理中锁定其他删除按钮；不存在返回404，引用冲突和数据库忙返回明确错误。

### 未确认导入及已确认误入账更正

- 未确认Statement Import继续允许受控删除；异常确认字段、异常Snapshot关系、`duplicate_of`后继、相同路径或同一物理文件被其他导入记录共用时拒绝。现存原件必须在受控目录中、为普通文件且SHA-256与数据库一致；未确认原件原本不存在时可删除数据库记录并明确返回文件未删除。
- 已确认误入账删除要求填写去除首尾空格后2至500字符原因并二次确认。只有Statement Import与唯一Balance Snapshot的`confirmed_account_id/confirmed_snapshot_id/statement_import_id/source_type/account_id`双向关系完整一致、该Snapshot未被任意Settlement账户行使用、Statement Import/Snapshot没有规范化Attachment/ExportRecord引用、没有`duplicate_of`后继，且确认原件存在、非空、为受控普通文件、SHA-256一致时才放行。
- 放行后在同一写事务删除该Statement Import、由它生成的Balance Snapshot及持仓，并删除原件和OCR/Luna结果；Client与Sub Account继续保留。审计使用`entity_id=NULL`并保存删除原因、原ID、Snapshot摘要和文件校验信息，不复制持仓内容或OCR/Luna文本。
- 原件先在同目录原子改名为唯一`.delete-pending`暂存文件，数据库提交成功后才清理；事务失败恢复原件，提交后清理失败则明确返回待清理状态并追加审计。应用启动会按数据库引用和已提交删除审计恢复或清理待删除文件；根目录、路径、哈希或证明关系不明确时停止启动，不猜测覆盖或删除。
- 上传/OCR、reparse、Luna、confirm和delete共享进程锁；异步上传的文件保存、重复检查、OCR与数据库提交在线程worker中完整持锁。原件在OCR前及OCR结束、取得数据库写锁后各校验一次普通文件身份和SHA-256，识别期间被外部替换时不会写入旧哈希记录。新上传原件先建立唯一小型`.upload-pending`旁证；写锁持续繁忙、提交结果不确定、二次校验失败或标记清理失败时保留旁证并明确提示安全重启，启动按数据库精确路径/同一物理文件及SHA-256清理无引用原件或确认已入库原件，不把“同SHA但不同路径”的旧记录误当作当前文件引用。前端同步禁止上传、Luna和删除相互交错，并防止重复点击。

### 迁移、备份与发布状态

- 新Alembic revision `c1a7d5e9b402`从精确`7f3c2a91b6e4`升级，增加Client/Account防级联和Statement/Snapshot防孤儿四个SQLite Trigger，总数从53增至57。应用层先给出具体引用原因；Trigger再独立检查核心外键、规范化Attachment/ExportRecord及Statement Import的`duplicate_of`后继，阻止绕过API和并发窗口。迁移前后核对完整Trigger集合、外键及SQLite完整性，原地downgrade明确拒绝。
- 备份创建和恢复校验新增Statement Import数据库-归档交叉检查：数据库`stored_path`必须唯一映射到`statement_imports/**`普通文件，数据库SHA-256、Manifest SHA-256和实际内容一致，且数据库引用集合与归档文件集合完全相等。缺失、篡改、孤儿、`.delete-pending`/`.upload-pending`文件及数据库副本/文件复制竞态均拒绝；结构校验同时支持正式`7f3c2a91b6e4`和目标`c1a7d5e9b402`。
- Windows构建测试临时根缩短为项目所在盘根下的`.ffsys-build-tmp`，避免备份复验目录与较长付款凭证安全文件名叠加后触发Windows路径上限；构建内352项回归仍全部执行，不以跳过测试规避路径问题。
- 文件SHA-256改用固定64KiB复用缓冲区流式计算，不改变哈希结果；在机器可用内存较低时，备份创建、恢复验证及凭证核验不再为每个读取块反复申请1MiB内存。
- 后端352/352完整回归与前端TypeScript/Vite 7.3.6生产构建通过，最终资源为`index-CKCifoas.js`和`index-BoYfTAme.css`。Windows候选于23:26:35 +08:00构建，写入结果文件前403个文件、613852656字节，写入后404个文件、613853357字节；主EXE SHA-256为`42442BFCF26693E5BEFD722BB146C697B6734A071BA6618A1E297B2DA847DC51`，ProductVersion/FileVersion为0.2.15/0.2.15.0。
- 8001纯合成HTTP及浏览器验收通过Client/Sub Account和未确认/已确认账单删除、200/404/409/422、跨删除ID递增、Snapshot与文件清理、严格备份、57个Trigger、页面无横向溢出及控制台无告警；候选已安全退出并清理合成数据。
- 正式切换前由0.2.14创建`financial_system_backup_20260901_233123_452757_tvghcclt.zip`，978172字节，SHA-256为`121D882F259F619C5095B8B4D7B1CB53E5C6FBB937E736CF99D7D83703F88519`，API副本一致。最终Windows候选在F盘隔离副本完成`7f3c2a91b6e4 → c1a7d5e9b402`，且仅消歧到用户确认的2条可严格证明历史重号；两条当前账单均通过受控DELETE删除，删除后严格备份通过，才允许停止旧0.2.14。
- 0.2.15已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.15_20260901\FinancialFeeSystem`接管`127.0.0.1:8000`及既有数据根。用户授权的2条历史测试误导入账单已在正式库受控删除；目标记录为0、新删除审计恰好2条、pending为0。正式验收通过版本、进程路径、49条OpenAPI路径、三类DELETE、最新前端资源、`no-store`、head `c1a7d5e9b402`、完整性、外键0、57个Trigger及无恢复/文件pending。删除后又创建并严格验证`financial_system_backup_20260901_233658_829621_tnoatd06.zip`，55272字节，SHA-256为`854706D90A7D7569CDFF220B1CB10142C5E82E2C9948FA1F2BA14567DD4FB842`。旧0.2.14目录共403个文件、613708412字节，正式验收通过后已整体移入Windows回收站，可恢复；活动运行目录只保留0.2.15。
- 固定`release`候选、`backend/build`、`backend/FinancialFeeSystem.spec`、`frontend/dist`、纯合成验收与本批精确测试临时目标共12个位置、2152个文件、941778417字节已永久清理。源码、0.2.15正式运行目录、正式数据根和完整备份不在清理范围。
- 本批不修改已确认Excel母版，不读取或输出真实客户业务内容，也不调用Luna处理真实文件。

## 0.2.14 - 2026-09-01（Windows运行版）

### Settlement版本与受控作废

- `QuarterlySettlement`新增`version_no`与`replaces_settlement_id`。自然键加版本永久唯一；SQLite部分唯一索引只约束`status != 'VOID'`记录，使同一Client、Platform、Fee Plan、年度和季度只有一个活动版本，同时完整保留VOID历史。
- 首次Settlement为v1；最新版本VOID后再次Calculate会在`BEGIN IMMEDIATE`写锁内建立下一连续版本并直接指向被替代版本。应用和Trigger共同拒绝跨自然键替代、替代非VOID、跳号、同一旧版本被多次直接替代及并发双活动版本。
- 季度结算页面新增Finalized Settlement作废入口，要求2至500字符原因及二次确认；活动Invoice来源或后续账户HWM依赖仍会阻止Void。结果区和历史表显示版本、直接替代来源及VOID原因，VOID记录不再提供导出或删除操作。
- Invoice Correction的替代Invoice必须使用与原Invoice来源一一对应、沿完整`replaces_settlement_id`链到达的Finalized Settlement版本；不能遗漏原来源或混入无替代链来源。

### 二态Payment、凭证和不可变账本

- Payment业务状态收敛为`UNPAID/PAID`；Due Date逾期改为独立`is_overdue`提示，不再返回`PARTIALLY_PAID/OVERDUE`付款状态。
- 日常付款只允许对Issued且资金账本为空的Invoice确认一次。请求必须提交正数实际现金、去空格后非空的方式及未被占用的`PAYMENT`凭证；服务端校验受控路径、普通非空文件、大小和SHA-256，再由数据库在同一Payment INSERT中原子认领凭证并建立初始APPLY。凭证占用按Attachment关系和凭证外键判断，SHA-256只证明物理完整性，相同哈希不自动合并业务凭证。Schema与SQLite Trigger都拒绝空白付款/退款方式；部分付款、重复付款及并发第二次确认均拒绝。
- 支持`COMPANY_BORNE_DIFFERENCE`：`实际现金 + 公司承担差额 = Invoice金额`必须精确成立，差额大于0必须有原因且差额为0时拒绝填写差额原因。Payment冻结差额与原因，AFTER INSERT先自动建立唯一Payment绑定的InvoiceAdjustment，再建立APPLY；普通差额不能脱离Payment独立插入，触发器任一步失败会回滚整笔Payment。差额不计作现金。
- 新增PaymentAllocation、PaymentRefund、InvoiceAdjustment及对应AuditEvent。Payment是原始现金事实；现金在Invoice间的去向只通过追加式APPLY/REVERSAL表达。Payment、分配、退款、差额及已认领付款/退款凭证均由RESTRICT外键和Trigger保护，不可更新或删除；Correction只允许从OPEN受控补齐唯一替代关系并转为COMPLETED，其他改写及删除均拒绝。

### Invoice错单更正与退款

- 新增`OPEN/COMPLETED` InvoiceCorrection。只有Issued原Invoice可以开启；有活动现金时原单必须已完整平账。开启操作原子作废原Invoice、退役来源，并为当前活动APPLY逐笔写等额且唯一的REVERSAL；原编号、中英文归档PDF、Payment现金事实和审计永久保留。
- Void、Correction及Refund原因会去除首尾空格并要求至少2字符；Invoice转VOID必须同时写入2至500字符原因和作废时间，进入VOID后两字段由Trigger冻结。Correction开启/完成过程的中间flush若触发约束会统一回滚并返回409，不暴露未捕获数据库错误。
- OPEN后须先按HWM顺序处理原Settlement、建立并Finalize完整替代版本，再建立并Issue同Client、年度、季度及Fee Plan的替代Invoice。替代Invoice在Correction完成前必须保持资金空白；后端拒绝对其登记新Payment或再次发起Correction。REVERSAL在OPEN立即生效，correction-linked APPLY、退款和差额只有COMPLETED后才进入会计值，跨事务pending行不能提前把替代单标为PAID；连续更正只能在上一轮COMPLETED后由当前Issued替代单开启。
- 完成更正以本轮Correction实际REVERSAL为处置边界：每笔Payment均须满足`本轮保留APPLY + 本轮退款 = 本轮REVERSAL`，全部保留现金加可选公司差额必须精确结清替代Invoice。退款逐笔要求正金额、日期、方式、原因及唯一`PAYMENT_REFUND`实体凭证。
- 连续更正继续校验`Payment原始现金 = 当前净分配 + 累计退款`，下一轮只能处置上一轮仍活动的分配，已经退款的现金不能再次使用。已确认的120→100+20口径现在表达为原120 Payment永久保留、100分配到替代Invoice及20有凭证退款，不登记虚假100新收款。
- 原Invoice没有现金也可更正；完成态Trigger要求本轮不存在APPLY、退款或公司差额，只建立唯一原/替代Invoice关系，替代Invoice保持UNPAID。
- Invoice页面新增实际现金/公司差额/未结及付款凭证展示、付款凭证上传、发起Correction、替代Invoice选择、逐Payment保留/退款、退款凭证上传和完成记录。直接Void只用于没有活动现金且无需替代关系的场景。

### 迁移、安全停止与发布交付

- 新Alembic revision `7f3c2a91b6e4`从精确`9d2f6a8c4b13`升级，在显式事务内重建Settlement版本表和Payment约束，新增四张追加账本表，将存量Settlement回填为v1并将合格旧Payment无损回填为初始APPLY；替代Draft自然键冻结，Finalize重新核对替代链和最近前期`previous_settlement_id`，避免直接SQL冻结跨组合或错误父链；原地downgrade明确拒绝，须从升级前完整备份恢复。
- 迁移要求完整旧head、35个既有Trigger、无外键违规且不存在半迁移新表。旧Invoice的Payment合计未精确结清（包括仍停留在部分付款状态）、Payment缺少一一匹配且元数据完整的`PAYMENT`凭证、多个Payment共用凭证时，在任何DDL前安全停止；不猜测分配、不伪造凭证、不误标新head。历史上多笔Payment若合计恰好全额且每笔都有独立合规凭证，则逐笔无损回填为初始APPLY，保留既有现金事实；这不重新开放日常多笔付款。
- 备份数据库结构校验识别旧正式`9d2f6a8c4b13`和新`7f3c2a91b6e4`各自契约，并严格核验Settlement、活动Invoice/InvoiceSource及新付款账本的关键唯一/部分索引、RESTRICT外键和Trigger清单。旧9d head现要求Trigger名称集合精确等于35项且每项SQL非空，删除任一旧Trigger的备份会在恢复暂存前被拒绝。创建及恢复校验还把数据库Payment/PaymentRefund凭证引用与归档实体逐笔交叉核对归属、唯一受控路径、非空普通文件、大小、数据库SHA-256和Manifest SHA-256；旧9d Payment缺证、篡改或连同Manifest记录删除实体文件均拒绝。正式切换前仍必须由当前正式版创建完整备份，并在F盘隔离副本完成高风险迁移预检。
- 0.2.14后端最终完整回归260/260通过，其中数据库核心专项49/49、备份专项52/52通过；前端TypeScript及Vite 7.3.6生产构建通过，最终资源为`index-gxMgisA1.js`与`index-Bb_qOqzJ.css`。最终Windows包于2026-09-01 04:08:59 +08:00构建，写入`构建结果.txt`前401个文件、613697621字节；主EXE SHA-256为`B0DE1AE4439CFB8ACDB773D127F4A1FBDE8F230DAC82970839CF3B67834CC73B`，ProductVersion/FileVersion为0.2.14/0.2.14.0。正式目录加入配置和结果文件后共403个文件、613708412字节。
- 候选EXE在8001端口和F盘纯合成数据根完成HTTP与浏览器验收：Settlement v1/v2/v3收费依次为120.00/100.00/90.00；原120.00现金经20.00和10.00两次有凭证退款后，最终Invoice保持PAID 90.00。异常400/409/422、OPEN替代单资金保护、原PDF哈希不变、严格备份校验、页面无横向溢出及控制台无告警均通过；候选数据库为`7f3c2a91b6e4`、`integrity_check=ok`、外键违规0且53个Trigger与精确合同一致。
- 正式切换前由0.2.13创建`F:\财务系统\财务数据\backups\financial_system_backup_20260901_041300_780215_2mlboy8f.zip`，共1517367字节，SHA-256为`0EF541673DB0FFAE2377DAFE678B0805D42BAC00C929256C50BC878F2D59C538`，API下载副本哈希一致。0.2.14新代码严格验证Manifest format v1、4个payload文件和旧head精确35个Trigger；备份在F盘隔离副本完成受控绝对路径重定向及`9d2f6a8c4b13 → 7f3c2a91b6e4`迁移，结果为`integrity_check=ok`、外键违规0、53个Trigger且无恢复残留，全程未输出客户业务内容。
- 正式0.2.14已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.14_20260901\FinancialFeeSystem`接管`127.0.0.1:8000`及既有数据根。健康检查、实际进程路径、EXE版本/哈希、49条OpenAPI路径、Settlement Void端点及Invoice Correction四项端点（合计5条相关新路径）、最新前端资源、入口`no-store`、数据库head/完整性/外键/53个Trigger精确合同和无恢复残留均通过。旧0.2.13已停止并整体移入Windows回收站；固定`release`、`backend/build`、`backend/FinancialFeeSystem.spec`、`frontend/dist`、候选/隔离预检和本批精确测试临时目录共31个目标、2549个文件、1132227932字节已永久清理，正式运行目录、真实数据根及完整备份不在清理范围内。
- Windows一键构建的pytest临时根从深层`backend/test-tmp`移到项目所在盘根下的固定短路径；这避免长源码路径、退款凭证安全文件名和恢复事务子目录叠加触发Windows路径限制，构建仍必须完整重跑全部后端测试。pytest失败时脚本立即清理，完整构建成功时在脚本尾部清理；PyInstaller或组件校验等中后段失败时可能遗留，须按该固定路径精确清理。
- 本批不修改已确认Excel母版，不读取或输出真实客户业务内容，也不调用Luna处理真实文件。

## 0.2.13 - 2026-08-31（Windows运行版）

### Settlement Finalize与数据库完整性

- Calculate与Finalize改用同一套当前数据库计算服务。Finalize的第一条SQL取得SQLite `BEGIN IMMEDIATE`写锁，再重查Client/账户状态、Company/FC、Fee Plan费率、Snapshot、Transaction、前序HWM及同账户同季度冲突；与Draft任一字段不一致即409要求重新Calculate，不在Finalize中静默更新。
- 同一Sub Account同一年度季度不得跨Platform或Fee Plan重复出现在非VOID Settlement；应用、迁移预检及SQLite Trigger共同保护。DRAFT误建可由财务确认删除，账户行同事务级联清理，审计以`entity_id=NULL`并在details记录原Settlement ID及组合字段，避免SQLite复用物理ID后审计混淆；FINALIZED/VOID仍不可删除。
- Finalize逐项验证StatementImport/Attachment物理文件仍在受控数据根、为非空普通文件且SHA-256（及Attachment大小）一致；Finalized引用的Transaction、Snapshot及其Attachment/StatementImport关键证据关系由Trigger冻结，VOID后才允许纠正。
- 新Alembic revision `9d2f6a8c4b13`在升级前拒绝外键破损、同账户同季度重复、错误前序HWM链、核心整数公式、父子合计、收益率、公式版本或关键证据关系，再建立新的生命周期与不可变Trigger。新ACCOUNT_HWM强制`HWM-2.0-ACCOUNT`；收益率以纯整数`ROUND_HALF_UP`等价规则核对。Beginning、Net Contribution或Gain/Loss任一绝对值超过`9,000,000,000,000` cents（HKD 90bn）时，为避免SQLite 64位整数校验溢出而安全停止；这是技术上限而非业务额度。
- 起始日流水继续不重复计入Net Contribution（计算范围`> start && <= closing`），但因其应已进入Beginning Snapshot，冻结范围保持`>= start && <= closing`，Finalize后不得增删改。
- 当前VOID历史仍占用`Client + Platform + Fee Plan + 年度 + 季度`自然键；VOID后可以纠正底层资料，但尚不能直接建立同组合替代Settlement。移除该限制需要重建带自引用及多重Invoice外键的核心表并定义替代Invoice关系，本版不宣称完成纠错闭环。

### 备份恢复与前端并发保护

- 备份Manifest改为严格字段、唯一JSON键、受控路径、精确文件全集和逐文件SHA-256；数据库副本必须通过系统核心表/列、已知Alembic版本、`integrity_check`及`foreign_key_check`，无关或未来版本SQLite即使自身可打开也拒绝。创建侧拒绝数据库及业务文件树中的symlink/junction，并使用唯一临时ZIP完整自校验后在备份目录原子改名，避免同秒并发请求覆盖或下载半成品。
- 恢复在数据根同一卷预制并再次校验`database/attachments/statement_imports/output`，以`preparing/applying/rolling_back/committed`事务journal和同盘目录原子替换提交。普通错误逆序回滚，进程中断下次启动先恢复一致状态；committed后的marker、pending、旧目录及上传ZIP清理由幂等收尾重试，后排队marker不会被旧事务误删。
- 所有API财务写入进入进程级恢复读写门闩：普通写入仍可按既有并发契约运行并计数，恢复只有在活动写入为0时才能独占进入，进入后拒绝新的财务写入；恢复API另从读取上传前取得专用互斥。因此恢复校验期间不会有已越过检查的并发财务写入，同一时刻也只接受一份恢复请求。校验成功写入pending marker后，全局中间件立即拒绝除安全退出外的所有财务写入，并在响应送达后自动执行与“安全退出系统”相同的优雅停机；避免等待人工重启期间的新数据被旧备份覆盖。旧暂存或上传ZIP清理失败作为明确warning返回，前端直接展示；启动会收敛未被当前marker/journal引用的安全前缀临时副本。
- 资金流水、手工Snapshot、Calculate、Finalize、Draft删除、备份生成和备份恢复提交增加即时ref级防双击；对应请求期间锁定输入。Settlement输入变化会使旧结果失效，恢复成功会清除旧错误提示，避免后台处理旧选择而页面显示新参数或成功/失败并存。
- 本批不修改Excel母版，不读取真实客户资料，不调用Luna处理真实文件。二态Payment、付款凭证硬前置、差额、Invoice更正/退款、Settlement VOID替代版本及其前端作废入口仍属后续范围；现有受保护Void后端接口不等于普通用户已具备完整更正闭环。

### 验收与发布状态

- 源码最终完整回归226/226通过，前端TypeScript及生产构建通过；备份/恢复与既有Invoice并发专项最终68/68通过。构建脚本于2026-08-31 20:45:42 +08:00生成候选包：写入`构建结果.txt`前399个文件、613,409,752字节，写入后400个文件、613,410,453字节。主EXE SHA-256为`6A6B88DDEAA185D31E2F86A12EC19E1F3AB352FFB25505B8F785D0C51AA15280`，ProductVersion/FileVersion为0.2.13/0.2.13.0；最终前端资源为`index-Do53jYCv.js`与`index-Bi8r7y5B.css`，Excel母版SHA-256保持不变。
- 候选EXE在8001端口和F盘纯合成数据根完成0.2.13闭环验收：陈旧Draft Finalize返回409、正确Settlement Service Fee为40.00、Finalized期间资料改写返回409、误建Draft删除返回200、同轮备份名称唯一且严格校验通过、恢复排队后自动安全退出；重启后pending marker和事务临时目录均收敛，数据库为Alembic head `9d2f6a8c4b13`、`integrity_check=ok`、外键违规0且35个Trigger齐全。候选最后通过安全退出并释放8001端口。
- 正式切换前由0.2.12系统接口创建`financial_system_backup_20260831_205840.zip`，共1,513,851字节，SHA-256为`526C6DB77950F21F7B8C397BD98C8F379A3C91D8F7AB458C7AB1A44E877DA0B5`；API下载副本与正式备份哈希一致。新恢复服务在F盘隔离副本上严格验证归档、Manifest、路径全集、逐文件哈希和系统数据库身份，再由0.2.13候选把副本从`c4b7f1d92e60`安全迁移到`9d2f6a8c4b13`；完整性、外键和35个Trigger均通过后才允许停旧版。
- 0.2.13已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.13_20260831\FinancialFeeSystem`接管8000端口及既有独立数据根。正式健康检查返回0.2.13且仅监听127.0.0.1；实际进程路径、45条OpenAPI路径、Settlement Draft DELETE、前端资源`index-Do53jYCv.js`、入口`no-store, max-age=0`、EXE版本/哈希、数据库head/完整性/外键及35个Trigger均通过，pending restore和恢复事务残留均为0。
- 旧0.2.12正式目录已移入Windows回收站。固定`release`候选、PyInstaller/前端构建产物、F盘合成验收和预检副本、精确测试临时目录及本批误落C盘的13个测试目录已永久清理；共清理77个临时/缓存目标、4,374个文件、971,230,064字节。0.2.13发布完成时本机活动运行目录只保留该正式版，独立业务数据及正式完整备份继续保留；0.2.13后来已被0.2.14替换并回收。
- 本批未修改Excel母版；正式数据仅执行备份、迁移及数据库结构完整性检查，没有查询或输出客户业务行、打开客户文件，也未调用Luna处理真实资料。

## 0.2.12 - 2026-08-29（Windows运行版）

### Invoice编号与安全归档

- 用户于2026-08-29纠正Invoice编号第一段：必须使用Company全名，不使用Company Code；Company Code继续保留为基础资料内部标识。新编号格式为`Company全名-中介人名字缩写-Issue Date(YYYYMMDD)-连续号`。
- 连续号继续按同一Company+FC组合持续递增，不按日期重置；切换格式不会重置现有InvoiceSequence，历史Issued、Void和签发尝试编号不改写、不批量迁移。
- 编号预留同时检查Invoice及InvoiceIssueAttempt中的历史号码。若新全名格式与既有正式号或失败预留号碰撞，系统跳过该序号、写入`INVOICE_NUMBER_COLLISION_SKIPPED`审计，再分配下一号码；被跳过号码不复用。
- 业务Invoice编号不再直接作为内部PDF路径。0.2.12新签发及旧Issued缺档补建统一以完整业务号的SHA-256作为内部归档名，避免Windows大小写不敏感路径碰撞；旧版已登记PDF路径照常读取，遗留ISSUING的安全原名仍可完成或清理，若哈希/原名两套归档同时完整则拒绝自动判定。下载文件名另做Windows安全化并包含记录ID，PDF正文和数据库仍保留完整业务编号。
- 中英文Invoice均使用可显示中文的Unicode字体渲染动态业务资料；Company全名或编号含中文时，英文版PDF仍须完整显示，不得出现缺字替代符。
- 本批不改变数据库Schema，也不新增Alembic迁移。正式运行仍为SQLite；现有Invoice编号列具TEXT affinity，不会按声明的`VARCHAR(100)`截断本批全名编号。若未来更换数据库，必须先重新评估并扩宽编号字段，不能直接沿用当前长度声明。

### Sub Account、账单入账与季度结算追踪

- “客户与账户”改为逐Client完整显示每个Sub Account的Platform、Account Number、Scheme、Fee Plan、管理期间、状态和备注；下拉选择统一显示`Client · Platform · Account Number · Scheme`，避免不同Platform使用相同Account Number时无法区分。
- 已确认Statement Import页面显示关联Client、Platform、Sub Account、Scheme、Trustee、Fee Plan、Snapshot日期/金额、Closing资格、持仓及Snapshot审计编号，并提示DRAFT资料补全或下一步人工结算。
- “资金与余额”增加Client、Platform和Sub Account三级筛选；余额快照显示来源、持仓、Closing资格和原账单入口，资金流水与快照均显示完整账户身份。季度结算的内部Excel候选及历史Settlement列表逐行显示Sub Account号码和Scheme；Invoice候选、已选组合和明细显示Fee Plan及`Scheme（当前资料）`。跨Platform同号账户不再只显示无法区分的Account Number。
- 账单复核的已有Sub Account改为受控选择；每次切换导入记录均强制清空，确认请求同时提交并核对所选账户的Platform ID。凭证目标同样改为受控选择，切换Snapshot/Transaction或任一账户筛选时清空旧目标，避免不同实体相同数字ID造成错挂凭证。
- 公开只读列表增强：`GET /api/transactions`增加`client_id/client_name`、`platform_id/platform_name`、`fee_plan_id/fee_plan_name`和`scheme_name`；`GET /api/balance-snapshots`增加同组账户身份字段以及`statement_import_id`和`holdings`。写入结构和数据库Schema不变。
- 明确数据链：余额账单经财务确认后保留StatementImport原件、OCR/Luna候选、人工确认值和审计记录，并建立`source_type=STATEMENT_IMPORT`的BalanceSnapshot；它随后出现在资金页和季度结算Snapshot选项中，但不会自动建立Contribution/Withdrawal、不会自动Calculate或Finalize Settlement。供款记录只能保留为凭证/摘要，资金流水仍须财务在具体Sub Account下人工登记并关联凭证。
- 非季末且非实际退出日的导入快照只作普通余额记录，不能作为Closing；新建的DRAFT Client/Sub Account须先补全Company、FC、Platform、Fee Plan和管理日期并激活，ACTIVE Sub Account缺少开始管理日期时服务端直接拒绝。补全Sub Account时可录入实际结束日期；结束日期不得早于该账户任何未作废Settlement的Closing Date，服务端同时重核未被Settlement引用的导入Snapshot资格并留审计，会改变已引用Snapshot时同样拒绝修改。Calculate与Finalize均再次强制Client及全部Sub Account为ACTIVE且具备开始管理日期。

### 验收与发布状态

- 新增编号归档、全名/特殊字符、碰撞跳号、双语Unicode PDF、账单确认追踪、真实合成原件下载、退出日Snapshot→Settlement闭环、ACTIVE/开始日期的Calculate及Finalize状态门槛、结算后结束日期保护、跨Platform同号及只读API字段测试；源码完整回归168/168通过，前端TypeScript及生产构建通过。
- Windows最终候选包于2026-08-29 16:41:44 +08:00构建；写入`构建结果.txt`前共397个文件、613,244,167字节，写入后共398个文件、613,244,868字节。主EXE SHA-256为`3B042DD705453BA75DA6A45427CCC3CC13CD50B81D953254C7CFBC0DE90369C9`，ProductVersion/FileVersion为0.2.12/0.2.12.0；Excel母版SHA-256保持不变。
- 候选EXE在8001端口及F盘隔离合成数据根完成完整流程：同一Company全名/FC先后签发的编号证明Issue Date正确且连续号不按日期重置；跨Platform同号Sub Account在导入、资金、结算和Invoice界面均可区分，Statement确认只生成可追溯BalanceSnapshot且不自动生成Transaction或Settlement。补充验收确认ACTIVE账户缺少开始日期返回400、已Finalized季度后把结束日期提前到Closing Date之前返回409，Invoice返回并显示Fee Plan及当前Scheme。中英文PDF的中文全名及换行编号完成渲染检查；8个页面无横向溢出、浏览器控制台无告警错误。
- 正式切换前由0.2.11创建并验证`financial_system_backup_20260829_154800.zip`，共1,513,851字节，SHA-256为`149CF48FF11B945A1BCCD0B2E1485A0F95A6F268B07E80A52901E7ED4049306D`；0.2.12在其F盘副本上启动并安全退出后数据库哈希仍与Manifest一致。最终构建替换前又由当时运行的0.2.12创建`financial_system_backup_20260829_162909.zip`，共1,513,852字节，SHA-256为`0C2C1DA5E30AED9030CB6FD72EE9710243F4879059A652C71F5AE45D00B655AA`；两份备份的ZIP、Manifest、4个文件记录及逐文件哈希均通过。数据库结构校验为Alembic head `c4b7f1d92e60`、`integrity_check=ok`、外键违规0、Trigger 19个；校验没有输出客户字段或文件内容。
- 0.2.12已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.12_20260829\FinancialFeeSystem`接管8000端口及既有独立数据根。正式健康检查返回0.2.12，进程路径、45条OpenAPI路径、最新前端资源、入口禁缓存、EXE版本/哈希、数据库head/完整性/外键及19个Trigger均通过。
- 旧0.2.11正式目录的399个文件、613,185,497字节已移入Windows回收站；固定`release`候选、`backend/build`、`backend/FinancialFeeSystem.spec`、`frontend/dist`及本批合成测试、PDF渲染、备份预检和Luna隔离临时目标均已清理。本机活动运行目录只保留0.2.12正式版，独立业务数据和完整备份继续保留。
- 本批不修改Excel母版，不读取真实客户资料，也不调用Luna处理真实文件。

## 0.2.11 - 2026-08-28（Windows运行版）

### Invoice编号与基础设置

- 用户确认Invoice编号格式改为`Company缩写-中介人名字缩写-Issue Date(YYYYMMDD)-连续号`，例如`AAA-TW-20260828-1`。连续号继续按Company+FC组合持续递增，不按日期重置；Void、签发失败或ISSUING退回号码不复用，历史Invoice编号不改写。
- 基础设置将FC的`Initial Code`更正为“中介人名字缩写”，现有数据库`code`字段保持兼容；删除输入框灰色`TW`示例，列表列名同步更正。
- Invoice编号日期不再取Client Management Start Date月份，改取正式签发时录入的Issue Date，并由`YYYYMM`改为`YYYYMMDD`；流水号取消三位补零。现有Company+FC序列表继续使用，无需数据库迁移。
- 用户确认Excel母版正式改名为`新收费计划计算纯净版模板.xlsx`；配置、构建、测试和文档引用同步改名，工作簿内容不改，SHA-256保持`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。
- 版本提升为0.2.11；本批不读取真实客户资料，也不调用Luna处理真实文件。

### 构建、验收与切换

- 编号/母版专项回归12/12、后端完整回归146/146和前端TypeScript/Vite生产构建通过。Windows候选包生成结果文件前共397个文件、613,184,743字节；主EXE SHA-256为`434C25C6311A5EB08090B9F021962DBB8AE938DD86BE73E85E70AF9C3FC73395`，ProductVersion/FileVersion为0.2.11/0.2.11.0。
- 候选EXE在8001端口及全新F盘合成数据根实际建立两名同Company/FC客户并签发Invoice，先后生成`AAA-TW-20260828-1`与`AAA-TW-20260829-2`，证明Issue Date进入日期段且跨日不重置；序列表`last_number=2`，数据库完整性/外键正常，候选版随后安全退出。
- 浏览器验收确认FC页显示“中介人名字缩写”和“中介人缩写”、输入框无`TW` placeholder、页面无横向溢出且控制台无警告错误；Invoice清单显示上述两条新编号。该验收只使用合成资料。
- 正式切换前由0.2.10创建并校验`financial_system_backup_20260828_162126.zip`，共931,160字节，SHA-256为`0BDF546E477F08AC5706FB6211586F97A0ABF9D91BFE3ED154BED4BA1EF16932`；ZIP CRC、Manifest、3个文件项、唯一数据库条目和逐文件SHA-256通过，未查看客户字段或附件内容。
- 0.2.11已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.11_20260828\FinancialFeeSystem`接管8000端口和既有独立数据根。正式健康检查、进程路径、45条OpenAPI路径、Invoice Issue/FC DELETE、最新前端、禁缓存、母版新文件名/哈希、数据库head `c4b7f1d92e60`、`integrity_check=ok`及外键违规为0均通过。
- 旧0.2.10正式目录已移入回收站；7个候选、构建和合成测试目标已永久清理。本机活动路径只保留0.2.11正式运行版，独立数据根及完整备份保留。

## 0.2.10 - 2026-08-28（Windows运行版）

### 新增与保护

- 基础设置为Company、FC、Platform和Fee Plan增加DELETE接口和删除按钮；删除前必须明确确认，提交期间防重复操作，成功后联动刷新四组基础资料和下拉选项。
- 只允许删除从未被业务或历史记录引用的误建资料。Company检查FC、Fee Plan、Client、Settlement、Invoice和InvoiceSequence；FC检查Client、Settlement、Invoice和InvoiceSequence；Platform检查Sub Account、Settlement和InvoiceLine；Fee Plan检查Sub Account、Settlement和Invoice。
- 任一引用存在时返回409并展示具体引用类型，不级联删除、不把可空关系置空；不存在返回404。成功删除写入不含客户内容的AuditEvent，数据库`RESTRICT`外键继续承担并发兜底。
- 版本提升为0.2.10；本批未增加数据库迁移，不修改Excel母版，不读取真实客户资料，也不调用Luna处理真实文件。
- 后端完整回归146/146、基础资料删除专项10/10及前端TypeScript/Vite生产构建通过；独立审查发现的旧式历史`entity_type`大小写/空格兼容问题已修复并纳入回归。

### 构建、验收与切换

- Windows候选包共398个文件、613,177,651字节；主EXE SHA-256为`3E94EB05E6252CF49E26DCF1C16922DD6735CAB71DCDA747AA0AF1401791001A`，ProductVersion/FileVersion为0.2.10/0.2.10.0，Excel母版SHA-256保持`16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145`。
- 候选EXE在8001端口和全新F盘合成数据根完成四类删除200、404、引用409、本地写标记403、6条删除审计、OpenAPI、数据库完整性/外键、四页签删除入口、无横向溢出、无控制台告警错误和安全退出验收。
- 正式切换前由0.2.9创建并校验`financial_system_backup_20260828_154422.zip`，共399,013字节，SHA-256为`FE317254AEDD4492BE676407C66F602AE32A6DA42EDF69AF905B6D27FEDBB687`；ZIP CRC、Manifest、3个文件项、唯一数据库条目和逐文件SHA-256通过。0.2.10在其F盘副本上启动后，数据库仍为`c4b7f1d92e60`，`integrity_check=ok`且外键违规为0。
- 0.2.10已从`F:\财务系统\金融计划收费系统_Windows运行版_0.2.10_20260828\FinancialFeeSystem`接管8000端口和原数据根；正式健康检查、进程路径、45条OpenAPI路径、4个新增DELETE、最新前端、禁缓存、数据库完整性/外键和母版哈希均通过。
- 旧0.2.9运行目录已移入回收站，其余候选、预检、构建及测试临时产物均已永久删除；手工回归曾误落C盘的本批11个测试目录也已按精确路径清理。合计23个原位置、2,933个文件、1,322,190,706字节不再占用项目活动路径，F盘正式运行目录只保留0.2.10，完整备份继续保留。全程未读取真实客户字段或文件、未调用Luna处理真实资料，也未修改Excel母版。

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
