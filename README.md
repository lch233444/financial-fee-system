# 金融计划收费计算系统

Windows 本地运行的财务管理 Web 系统，覆盖客户与账户资料、eMPF 文件分类及账单识别、余额与资金流水、季度高水位线结算、Excel/PDF 导出、Invoice/Payment 管理和本地备份。账单识别采用本地 Tesseract OCR，并可由用户主动调用本系统专用的 ChatGPT Pro/Codex 登录，以固定 `gpt-5.6-sol` 进行一次辅助识别。

当前源码版本与正式Windows单机运行版均为 **0.2.20**。正式版位于 `F:\财务系统\金融计划收费系统_Windows运行版_0.2.20_20260904\FinancialFeeSystem`，使用独立数据根 `F:\财务系统\财务数据`并仅监听 `127.0.0.1:8000`。本版在0.2.19季度边界规则上新增未Finalized资金流水的受控更正：财务可修改资金生效日期、Contribution/Withdrawal类型、金额和备注，必须填写更正原因；原凭证继续保留，系统写入更正前后内容和原因，已被Finalized Settlement使用或更正后会落入已锁定期间的流水仍不可改。完整状态和证据见[项目说明书](docs/项目说明书.md)。开始开发前请先阅读项目说明书、[变更记录](docs/CHANGELOG.md)和[协作规则](AGENTS.md)。

## 工程接手入口

本仓库把 [项目说明书](docs/项目说明书.md) 作为业务背景、目标规则、当前实现和工程交接的单一入口。文档不是简略功能介绍，也不能只描述当前代码；它必须明确区分用户已经确认的目标与尚待实现的差距，包含：

- 业务起因、角色职责、已确认目标、业务闭环、已实现范围和明确非目标；
- 运行架构、目录职责、领域模型和数据关系；
- Settlement、Invoice、Payment及账单导入状态机；
- HWM公式、金额类型、舍入和不可破坏的不变量；
- OCR/Sol交叉核验、API地图、前端页面和配置项；
- 数据迁移、备份恢复、测试矩阵、Windows构建和故障定位；
- 已知技术缺口、工程决策、Definition of Done和接手检查清单。

新工程师或新Agent应按 `AGENTS.md → docs/项目说明书.md → docs/CHANGELOG.md → 任务相关代码/专题文档` 的顺序阅读。历史交接文档只用于追溯，不能覆盖当前代码、测试或用户最新要求。

用户在对话中确认的业务背景或规则必须在同一次任务中写回GitHub交接资料，不能依赖对话历史或Agent记忆继续传递。

0.2.12按用户最新确认把Invoice编号第一段改为Company全名，格式为`Company全名-中介人名字缩写-Issue Date(YYYYMMDD)-连续号`；Company Code只作基础资料内部标识。连续号仍按同一Company+FC持续递增、不按日期重置，作废、失败或碰撞跳过的号码不复用，历史Invoice编号不改写。业务编号与Windows安全归档文件名分离：0.2.12新生成PDF统一使用确定性哈希归档名，旧版已记录路径及中断签发的安全原名仍可恢复。该批同时补强Sub Account可见性和账单入账追踪：人工确认余额账单后建立带StatementImport来源的BalanceSnapshot，资金页可按Client、Platform和Sub Account查看，季度结算的内部Excel候选及历史列表直接逐行显示Sub Account号码和Scheme，Invoice候选、已选组合和账户明细显示Fee Plan及`Scheme（当前资料）`；切换导入记录或凭证类型会清空旧选择。ACTIVE Sub Account必须有开始管理日期，Draft/Closed或缺少开始日期的账户不得结算；实际结束日期不得早于该账户任何未作废Settlement的Closing Date，其他变更仍会审计化重核未锁定Snapshot的Closing资格。系统不会把供款资料自动转成Contribution/Withdrawal，也不会自动Calculate或Finalize季度结算。

0.2.13补强季度结算和备份恢复的完整性：Finalize在同一SQLite写锁内按当前资料重算并拒绝陈旧Draft，验证物理凭证哈希，并用迁移`9d2f6a8c4b13`冻结Finalized结果及其资金、余额和证据关系；误建Draft可受控删除并保留审计。备份改为唯一临时ZIP严格校验后原子公布，恢复采用独占写入门闩、同盘事务目录切换、失败回滚、断电续作和排队后自动安全退出。后端226/226、前端生产构建、Windows候选合成验收、正式备份副本迁移预检及8000端口正式切换均已通过。Settlement页面仍没有Void入口，且既有自然键使Void后尚不能重建同组合替代Settlement；Payment二态、凭证硬前置、错单更正和退款仍是下一批高优先级工作。

0.2.14实现Settlement和Invoice的受控更正闭环：Settlement页面提供Finalized作废入口；同一自然键只允许一个非VOID活动版本，VOID历史按`version_no`和`replaces_settlement_id`形成不可覆盖的替代链。Payment业务状态收敛为`UNPAID/PAID`，逾期改为独立提示；一次付款确认必须上传可核验实体凭证，并满足“实际现金＋公司承担差额＝Invoice金额”，不再接受部分付款或重复付款。错单通过`OPEN → COMPLETED`的Invoice Correction处理：原Invoice、编号、PDF及现金事实永久保留，当前活动现金分配先以REVERSAL冲回，再按本轮可处置现金分配至完整Settlement替代链生成的新Invoice或登记有凭证退款；OPEN期间替代Invoice必须保持资金空白，连续更正须先完成上一轮，再按本轮REVERSAL和全链累计退款守恒处理，不会虚构新现金。迁移`7f3c2a91b6e4`会重建Settlement活动唯一约束、收紧Payment凭证外键并新增不可变分配/退款/差额/更正台账；旧Invoice仍未被Payment合计完整结清、缺少匹配付款凭证、共用凭证或存在半迁移结构时会在DDL前安全停止。历史上多笔Payment只有在合计恰好全额且每笔均有独立合规凭证时才无损保留，新版本不再允许新增多笔付款。

0.2.15新增Client、Sub Account和账单导入的受控删除。系统不会判断一条资料是不是“测试数据”；财务人员只能删除自己确认误建或不再需要、且没有任何业务或历史引用的Client/Sub Account，任一引用存在都会拒绝且不会级联清理。未确认账单仍可删除；已确认误入账只有在其Statement Import与唯一Balance Snapshot双向关系完整、尚未进入任何Settlement、没有附件/导出或重复导入后继引用、原件完整且SHA-256一致时，才可填写原因后连同该Snapshot、持仓、原件及OCR/AI结果一并删除，Client与Sub Account继续保留。Client、Sub Account、Statement Import和Balance Snapshot的ID使用持久高水位继续递增，删除过的ID不会分给以后新建资料，避免旧审计被误解为指向新记录。对升级前已经存在的历史重号，`c1a7d5e9b402`只在旧删除审计严格早于同ID存活账单、记录类型与数量唯一且无新版保留字段时自动消歧，并单独写入更正审计；任一证据不足仍停止升级。删除文件先在原目录原子暂存，数据库成功提交后才清理；新上传文件则先建立小型`.upload-pending`安全标记。备份同时对数据库Statement Import与归档原件做路径、文件全集和SHA-256交叉校验。

0.2.16把账单辅助识别固定切换为`gpt-5.6-sol`，继续使用低推理档位、一次调用、严格财务Schema、本地OCR独立结果和人工最终确认。用户明确授权的指定测试图片已通过Sol严格Schema检查，未输出识别字段、未自动入账。源码后端353/353、前端生产构建、Windows候选、正式备份严格验证、F盘隔离副本预检及正式运行版验收均通过；数据库继续为`c1a7d5e9b402`和57个Trigger，Excel母版SHA-256保持不变。

0.2.17把“完整备份恢复”升级为面向季度复核的“完整数据包导出/导入”。年度和季度只标记检查批次，数据包始终包含导出时的完整数据库、账单原件、附件、付款及退款凭证、Excel/PDF导出；Manifest记录精确系统版本，导入只接受相同版本。导入仍是整库覆盖而非合并，成功校验后安全退出并在重启时事务切换；跨电脑时会在临时副本中把Statement、Attachment、ExportRecord和Invoice PDF路径改为接收方数据根，复验哈希、数据库完整性、外键和57个Trigger后才提交。旧版完整备份仍可按原规则恢复，但不获得新的跨路径保证。该版已通过专项62/62、相关导出与数据包69/69、后端完整回归356/356、前端生产构建、Windows候选跨数据根实测、正式备份与数据包隔离预检及8000端口正式验收。

0.2.18只调整新签发Invoice的双语PDF模板，不改Settlement、金额、编号、付款、归档或数据库结构。新版按A+B融合方案采用白底、深蓝表头、香槟金分隔线和清晰的客户/账单资料、账户收费、应付总额及付款资料层级；Sub Account与收费计划仍保留，收费计划以服务说明显示，客户PDF不显示Trustee、Platform或Platform Code。既有已签发PDF继续按原文件和SHA-256保存，不会被新版重生成覆盖。

0.2.19把结算资金区间的下边界统一为实际选择或继承的Beginning Snapshot日期。自然季度首日开始时可使用上一季末快照，流水从翌日（即季度首日）起计；若使用Starting Date当天快照，则当天资金视为已经包含在Beginning中，不重复计入。Calculate、Finalize、数据库Trigger和凭证保护使用同一口径。

0.2.20为“资金与余额”的最近资金流水增加“更正”入口。未被Finalized结算锁定的记录可以在同一ID上更正资金生效日期、类型、金额和备注，并强制填写2至500字符原因；原有凭证关联不变，AuditEvent保存更正前后值。更正当前日期或目标日期属于已Finalized期间时服务端返回409，页面显示对应Settlement已锁定。已有Draft不会被静默改写，财务须重新Calculate，以当前流水生成新的Draft结果。

2026-09-01最终Windows包通过8001端口纯合成HTTP及浏览器验收，实际完成Settlement v1/v2/v3的120.00/100.00/90.00收费链，以及原120.00现金经20.00和10.00两次有凭证退款后由最终Invoice保持PAID 90.00；原PDF哈希不变，400/409/422异常路径、严格备份、页面无横向溢出和无控制台告警均通过。正式切换前由0.2.13建立完整备份，新代码严格验证后在F盘隔离副本完成`9d2f6a8c4b13 → 7f3c2a91b6e4`迁移；正式0.2.14随后接管8000端口，数据库`integrity_check=ok`、外键违规0、53个Trigger及49条OpenAPI路径均通过。Excel母版未修改，验收未读取或输出真实客户业务内容、未打开真实客户文件，也未调用Luna处理真实资料。

## 开发环境

- Python 3.12+
- Node.js 22+
- Tesseract OCR（英文、繁体中文、简体中文语言包）
- Codex CLI 原生程序（Windows发布包已内置；开发构建从 `tools/Codex` 复制）

验收过的Windows/Python依赖精确版本记录在 `backend/requirements-dev-lock.txt`，Node依赖由 `frontend/pnpm-lock.yaml` 锁定。Codex和Tesseract二进制不进入Git；需要构建Windows一键版时，按 [本地构建工具说明](tools/README.md) 从受保护的交接介质恢复并校验签名及SHA-256。

ChatGPT Pro 辅助识别不使用 OpenAI API Key。应用通过发布包内的 Codex App Server及本地 `stdio` 协议启动独立ChatGPT登录流程，并在每次识别前检查登录状态及模型可用性。Sol使用专用、干净的 `CODEX_HOME`，不会继承桌面Codex的全局AGENTS、插件、Hook或MCP配置，也不会复制桌面Codex登录凭据。为延续已经完成的系统专用登录，目录名仍保留历史兼容名`LunaCodexHome`，但实际请求模型只允许`gpt-5.6-sol`。用户首次使用时在浏览器为本系统单独完成本人ChatGPT账号授权；未登录、订阅额度不足或Sol不可用时，账单会直接进入人工复核，本地OCR和其他财务功能仍可使用。

该接入当前定位为本地MVP预览功能。OpenAI官方仍将 `codex app-server` 命令标记为实验性能力，因此每次升级内置Codex版本后都必须重新完成登录、模型列表、样例识别及人工复核边界验收；未来迁移到公司服务器前应重新评估认证、数据治理和正式支持方案。

## 本地启动

```powershell
git clone https://github.com/lch233444/financial-fee-system.git
cd financial-fee-system
./scripts/setup-dev.ps1
./scripts/start-dev.ps1
```

前端开发地址为 `http://127.0.0.1:5173`，后端 API 为 `http://127.0.0.1:8000/api`。

## 生产构建

```powershell
./scripts/build-windows.ps1
```

构建完成后，运行 `release/FinancialFeeSystem/FinancialFeeSystem.exe`。首次启动会弹窗要求选择数据保存目录；开发或自动化验收时也可通过 `FINANCIAL_DATA_ROOT` 指定目录。

日常关闭请点击左侧栏底部的“安全退出系统”。系统会先关闭自己启动的Sol识别进程，再停止本地服务；无需打开任务管理器。仅关闭浏览器标签不会停止后台服务。

## 自动化测试

```powershell
./.venv/Scripts/python.exe -m pytest backend/tests -q
```

详细操作见 [用户操作手册](docs/用户操作手册.md)，AI 安全边界见 [ChatGPT Pro辅助识别说明](docs/ChatGPT%20Pro辅助识别说明.md)，技术规则见 [技术与验收说明](docs/技术与验收说明.md)。

对于项目每一次修改都要同步修改项目说明书。凡修改源码、测试、数据库、UI、配置、依赖、构建、发布、脚本或其他项目文件，都必须在同一次提交中更新 `docs/项目说明书.md`；行为或交付状态发生变化时还必须更新 `docs/CHANGELOG.md`。提交前运行 `python scripts/check-project-docs.py --base <基准提交>`，Pull Request模板也会要求逐项确认。

## 数据安全

- 服务只监听 `127.0.0.1`。
- 客户账单、凭证、数据库及导出文件均存放在数据目录，不进入源码仓库。
- 本地 OCR 不上传客户资料；只有用户主动点击“Sol辅助识别”时，该账单图片及识别提示才会发送给 OpenAI。
- AI 结果单独保存为待复核数据，不能直接生成余额快照、资金流水、季度结算或 Invoice。
