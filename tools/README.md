# 本地构建工具

文档修订：2026-09-08，适用应用0.2.23。`tools/`只保存开发机受信任运行组件，二进制和登录资料不进入Git。

## 必需组件

| 目录 | 当前构建要求 |
|---|---|
| `tools/Codex/` | Codex CLI 0.149.1及配套`codex-code-mode-host.exe`；两个EXE均校验有效OpenAI签名和固定哈希 |
| `tools/Tesseract-OCR/` | Tesseract 5.5.3.20260724及所需运行文件/语言数据；校验EXE版本和固定哈希 |

完整SHA-256和构建门槛只在[构建清单](../packaging/构建清单.txt)维护；执行依据为[scripts/build-windows.ps1](../scripts/build-windows.ps1)。这些是当前构建使用的固定组件，不是推荐安装互联网上任意最新版本。

新电脑可先进行普通源码开发与自动化测试。制作Windows包前，从有权访问的受保护交接介质恢复完整组件目录，核对版本、哈希和签名；任一不符停止构建，调查来源，不修改校验值掩盖不一致。

本机pnpm 11的既有依赖缓存为`F:\.pnpm-store`。仅修改package.json版号也可能使工作区状态指纹过期，导致`pnpm run`自动尝试重新安装；先用`pnpm --dir frontend install --offline --frozen-lockfile --store-dir F:\.pnpm-store`同步状态，再运行构建。本轮确认依赖及锁文件无变化，不通过强制清空node_modules解决缓存路径不一致；其他电脑使用其实际已配置的缓存目录。

应用当前固定Sol识别，模型选择由服务实现约束，不由Codex目录名决定。升级内置组件后需重新验证登录、模型回执、协议隔离和人工确认；流程见[项目说明书](../docs/项目说明书.md#recognition)。

不要复制桌面`.codex`、`auth.json`、浏览器登录数据、令牌或个人配置到本目录。业务数据包也不携带系统专用ChatGPT登录，新电脑由本人重新授权。
