# 金融计划收费计算系统

Windows 本地运行的财务管理 Web 系统，覆盖客户与账户资料、eMPF 文件分类及账单识别、余额与资金流水、季度高水位线结算、Excel/PDF 导出、Invoice/Payment 管理和本地备份。账单识别采用本地 Tesseract OCR，并可由用户主动调用本系统专用的 ChatGPT Pro/Codex 登录，以固定 `gpt-5.6-luna` 进行一次辅助识别。

当前源码版本为 **0.2.8**；Windows单机运行版切换状态见[项目说明书](docs/项目说明书.md)。开始开发前请先阅读项目说明书、[变更记录](docs/CHANGELOG.md)和[协作规则](AGENTS.md)；它们共同保证不同电脑和不同开发代理可以延续同一套业务与安全边界。

## 工程接手入口

本仓库把 [项目说明书](docs/项目说明书.md) 作为业务背景、目标规则、当前实现和工程交接的单一入口。文档不是简略功能介绍，也不能只描述当前代码；它必须明确区分用户已经确认的目标与尚待实现的差距，包含：

- 业务起因、角色职责、已确认目标、业务闭环、已实现范围和明确非目标；
- 运行架构、目录职责、领域模型和数据关系；
- Settlement、Invoice、Payment及账单导入状态机；
- HWM公式、金额类型、舍入和不可破坏的不变量；
- OCR/Luna交叉核验、API地图、前端页面和配置项；
- 数据迁移、备份恢复、测试矩阵、Windows构建和故障定位；
- 已知技术缺口、工程决策、Definition of Done和接手检查清单。

新工程师或新Agent应按 `AGENTS.md → docs/项目说明书.md → docs/CHANGELOG.md → 任务相关代码/专题文档` 的顺序阅读。历史交接文档只用于追溯，不能覆盖当前代码、测试或用户最新要求。

用户在对话中确认的业务背景或规则必须在同一次任务中写回GitHub交接资料，不能依赖对话历史或Agent记忆继续传递。

当前0.2.8延续账户级HWM、Snapshot凭证、Luna隔离和导入复核边界，并在“季度结算”提供公司内部财务Excel批量筛选、选择及模板导出。公司模板直接以用户重新提供的原始工作簿为母版，完整保留文档属性、计算链、主题、主表第3、4行示例及17个原公式，只删除主表第5行及以下18个个人备注单元格；Defer页原始数据和36个公式不变。跨Platform客户Invoice、二态Payment/差额退款和精简FC统计仍在后续批次。

## 开发环境

- Python 3.12+
- Node.js 22+
- Tesseract OCR（英文、繁体中文、简体中文语言包）
- Codex CLI 原生程序（Windows发布包已内置；开发构建从 `tools/Codex` 复制）

验收过的Windows/Python依赖精确版本记录在 `backend/requirements-dev-lock.txt`，Node依赖由 `frontend/pnpm-lock.yaml` 锁定。Codex和Tesseract二进制不进入Git；需要构建Windows一键版时，按 [本地构建工具说明](tools/README.md) 从受保护的交接介质恢复并校验签名及SHA-256。

ChatGPT Pro 辅助识别不使用 OpenAI API Key。应用通过发布包内的 Codex App Server及本地 `stdio` 协议启动独立ChatGPT登录流程，并在每次识别前检查登录状态及模型可用性。Luna使用专用、干净的 `CODEX_HOME`，不会继承桌面Codex的全局AGENTS、插件、Hook或MCP配置，也不会复制桌面Codex登录凭据。用户首次使用时在浏览器为本系统单独完成本人ChatGPT账号授权；未登录、订阅额度不足或 Luna 不可用时，账单会直接进入人工复核，本地 OCR 和其他财务功能仍可使用。

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

日常关闭请点击左侧栏底部的“安全退出系统”。系统会先关闭自己启动的Luna识别进程，再停止本地服务；无需打开任务管理器。仅关闭浏览器标签不会停止后台服务。

## 自动化测试

```powershell
./.venv/Scripts/python.exe -m pytest backend/tests -q
```

详细操作见 [用户操作手册](docs/用户操作手册.md)，AI 安全边界见 [ChatGPT Pro辅助识别说明](docs/ChatGPT%20Pro辅助识别说明.md)，技术规则见 [技术与验收说明](docs/技术与验收说明.md)。

对于项目每一次修改都要同步修改项目说明书。凡修改源码、测试、数据库、UI、配置、依赖、构建、发布、脚本或其他项目文件，都必须在同一次提交中更新 `docs/项目说明书.md`；行为或交付状态发生变化时还必须更新 `docs/CHANGELOG.md`。提交前运行 `python scripts/check-project-docs.py --base <基准提交>`，Pull Request模板也会要求逐项确认。

## 数据安全

- 服务只监听 `127.0.0.1`。
- 客户账单、凭证、数据库及导出文件均存放在数据目录，不进入源码仓库。
- 本地 OCR 不上传客户资料；只有用户主动点击“Luna辅助识别”时，该账单图片及识别提示才会发送给 OpenAI。
- AI 结果单独保存为待复核数据，不能直接生成余额快照、资金流水、季度结算或 Invoice。
