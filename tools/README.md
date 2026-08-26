# 本地构建工具

`tools/` 只用于保存开发机本地的受信任运行组件。二进制不进入Git仓库，也不得包含ChatGPT/Codex登录目录或令牌。

构建0.2.2候选版时使用：

- `tools/Codex/codex.exe`：Codex CLI 0.149.1，SHA-256 `A395030B56B126F608F2403036DDDB654A9C063213E9C2B5F85D954CF490EBE6`
- `tools/Codex/codex-code-mode-host.exe`：SHA-256 `8F98CC7AA079B51DBFBB16A8E655A468A9C37C1CD23E22422C10CDFD6CACE543`
- `tools/Tesseract-OCR/tesseract.exe`：Tesseract OCR 5.5.3.20260724，SHA-256 `C66F0F12ED76F6AA455DAC97684BBC86756D6A732380BEE09122454CFDA3F420`

在新电脑上可先完成普通源码开发和自动化测试；需要制作Windows一键包时，再从受保护的原交接介质复制对应工具，并逐项核对 `packaging/构建清单.txt`。Codex两个可执行文件还必须通过OpenAI OpCo, LLC的Authenticode签名校验，构建脚本会再次强制检查。

不要从不明镜像下载这些程序，也不要把 `.codex`、`auth.json`、浏览器登录数据或任何个人令牌复制到本目录。

