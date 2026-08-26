[CmdletBinding()]
param(
    [string]$PythonCommand = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    if ($PythonCommand) {
        & $PythonCommand -m venv (Join-Path $ProjectRoot ".venv")
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv (Join-Path $ProjectRoot ".venv")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv (Join-Path $ProjectRoot ".venv")
    } else {
        throw "未找到Python 3.12或以上版本。"
    }
}

& $VenvPython -m pip install --upgrade pip
$LockedRequirements = Join-Path $ProjectRoot "backend\requirements-dev-lock.txt"
if (Test-Path -LiteralPath $LockedRequirements) {
    & $VenvPython -m pip install -r $LockedRequirements
} else {
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "backend\requirements-dev.txt")
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "未找到Node.js 22或以上版本。"
}
if (Get-Command pnpm -ErrorAction SilentlyContinue) {
    & pnpm --dir (Join-Path $ProjectRoot "frontend") install --frozen-lockfile
} elseif (Get-Command corepack -ErrorAction SilentlyContinue) {
    & corepack pnpm --dir (Join-Path $ProjectRoot "frontend") install --frozen-lockfile
} else {
    throw "未找到pnpm或corepack。请安装Node.js 22后重试。"
}

$Tesseract = Join-Path $ProjectRoot "tools\Tesseract-OCR\tesseract.exe"
if (-not (Test-Path -LiteralPath $Tesseract)) {
    Write-Warning "未找到本地Tesseract。账单可保存及人工复核，但无法自动识别。"
}

Write-Host "开发环境准备完成。"
