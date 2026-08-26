# ChatGPT Pro辅助识别说明

## 1. 模式定位

本功能用来辅助财务人员复核 eMPF 账单，不是自动记账机器人。系统先完成文档分类和本地 Tesseract OCR，用户再决定是否调用本系统专用的 ChatGPT Pro/Codex 登录。AI只产生第二份待复核识别结果，最终数据仍由财务人员确认。

本功能不使用 OpenAI API Key。它通过本机 Codex CLI 的 App Server及ChatGPT管理登录工作，使用量受该账号当前订阅额度和模型可用性限制。ChatGPT Pro不能作为普通API Key使用，本系统也不会读取、显示或保存ChatGPT密码。

本接入只作为第一版本地MVP使用。OpenAI官方目前仍把 `codex app-server` 命令列为实验性能力；这不影响当前已锁定版本的本机验收，但不应把它直接视为未来公司服务器部署的长期稳定接口。升级Codex或改变部署方式前必须重新验收并评估数据治理。

## 2. 固定处理规则

```text
上传账单
  -> 文档分类（余额页 / 供款记录 / 未知）
  -> 本地OCR或PDF原生文字提取
  -> 用户主动点击Luna辅助识别
  -> 固定gpt-5.6-luna识别一次
  -> 逐字段比较与格式校验
       一致：预填待确认值
       冲突/异常：标记人工复核
  -> 财务人员确认
  -> 才可生成账户余额快照
```

供款记录、资产转入记录及未知文件不能进入余额确认流程。它们只保留原始凭证、分类和可读摘要，须在资金与余额模块人工登记。

系统不得：

- 自动改用 Terra、Sol 或任何其他模型；
- 因模型目录提供升级建议而切换模型；
- 在 Luna 失败后调用第二个模型；
- 让AI结果静默覆盖本地OCR或人工修订值；
- 通过AI识别接口直接创建余额、流水、结算、Invoice或付款记录；
- 对已经确认入账的Statement Import再次运行AI识别。

## 3. 登录和状态

Luna使用应用专用的 `CODEX_HOME` 和独立ChatGPT登录。它不会继承桌面Codex的全局 `AGENTS.md`、插件、Hook或MCP配置；退出Luna登录也不会退出Codex桌面应用。每次识别在发送图片前仍会检查安全配置和 `instructionSources`，发现非空来源即停止并转人工复核。

AI状态可能显示：

- `ready`：ChatGPT已登录，且当前账号可使用 `gpt-5.6-luna`。
- `signed_out`：尚未登录或登录已失效；点击登录并在浏览器完成授权。
- `unavailable`：发布包中的Codex运行程序缺失、损坏或无法启动；本地OCR仍可用。
- `model_unavailable`：账号当前模型列表没有精确的 `gpt-5.6-luna`；直接人工复核。
- `quota_exhausted`：ChatGPT/Codex当前额度窗口已用完；不调用其他模型，直接人工复核或等待额度重置。
- `wrong_auth`：当前为API Key等非ChatGPT管理登录；本模式拒绝使用，避免产生API账单。
- `error`：协议、超时或其他错误；保留本地OCR并人工复核。

额度不足、网络失败或服务暂时不可用都不应循环重试模型。财务人员可稍后再次手动尝试，也可直接完成本次人工复核。

## 4. 冲突与确认

系统对姓名、账户号、Trustee、Scheme、币种、As-of Date、Total Balance、累计净供款、累计投资盈亏和持仓字段分别比较。比较前只允许可审计的标准化，例如去除金额千位逗号、统一日期格式和清理多余空格；标准化后的值不同即为冲突。

冲突页面必须同时保留：

- 原图；
- 本地OCR原值和置信度；
- Luna原值；
- 冲突原因；
- 人工最终值；
- 操作时间及解析器/模型版本。

即使所有字段一致，也只是降低复核工作量，不代表允许自动入账。账户匹配、重复检测、日期资格和关键字段校验仍须通过。

## 5. 隐私说明

- 仅使用本地OCR：账单图片留在用户选择的数据目录。
- 点击“Luna辅助识别”：当前账单图片及识别提示会发送给OpenAI处理。
- 数据库继续只监听和保存在本机，但这不代表AI识别过程完全离线。
- 上传前应确认公司允许使用相应ChatGPT账号处理客户个人及财务资料。

## 6. 安装与打包

Windows一键版内置启动 `codex app-server` 所需的原生程序，不要求目标电脑安装Node.js或另行安装Codex CLI。应用按以下顺序查找：

1. 发布目录中的 `Codex/codex.exe`；
2. 源码开发目录中的 `tools/Codex/codex.exe`；
3. 仅作为开发回退，系统 `PATH` 中可调用的 `codex.exe`。

构建脚本要求 `tools/Codex/codex.exe` 和 `tools/Codex/codex-code-mode-host.exe` 均存在，并把整个目录复制进发布包。构建和复制过程不会复制任何用户登录令牌；最终用户仍须在自己的Windows账号下完成一次ChatGPT浏览器授权。

发布验收必须在目标Windows账号下实际执行登录、模型状态读取和一张非敏感测试图片识别。只看到文件存在，不能证明ChatGPT登录或AI识别可用。

## 7. 官方资料

- [Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

这些能力、订阅额度和模型可用性可能变化，因此系统以运行时状态检查为准，不把文档中的示例账号或计划类型当作授权证明。
