[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "开发环境尚未安装，请先运行scripts/setup-dev.ps1。"
}
if (-not $env:FINANCIAL_DATA_ROOT) {
    $env:FINANCIAL_DATA_ROOT = Join-Path $ProjectRoot "data"
}

$Backend = Start-Process -FilePath $VenvPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory (Join-Path $ProjectRoot "backend") -WindowStyle Hidden -PassThru

if (Get-Command pnpm -ErrorAction SilentlyContinue) {
    $PnpmCommand = (Get-Command pnpm).Source
    $Frontend = Start-Process -FilePath $PnpmCommand `
        -ArgumentList @("--dir", (Join-Path $ProjectRoot "frontend"), "dev") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
} elseif (Get-Command corepack -ErrorAction SilentlyContinue) {
    $Frontend = Start-Process -FilePath (Get-Command corepack).Source `
        -ArgumentList @("pnpm", "--dir", (Join-Path $ProjectRoot "frontend"), "dev") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
} else {
    Stop-Process -Id $Backend.Id -Force -ErrorAction SilentlyContinue
    throw "未找到pnpm或corepack。"
}

try {
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        Start-Sleep -Milliseconds 400
        try {
            Invoke-WebRequest "http://127.0.0.1:5173" -UseBasicParsing | Out-Null
            $Ready = $true
            break
        } catch {}
    }
    if (-not $Ready) { throw "开发服务未能在预期时间内启动。" }
    Start-Process "http://127.0.0.1:5173"
    Write-Host "系统已启动。关闭此窗口或按Ctrl+C可结束开发服务。"
    Wait-Process -Id $Backend.Id
} finally {
    Stop-Process -Id $Backend.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $Frontend.Id -Force -ErrorAction SilentlyContinue
}
