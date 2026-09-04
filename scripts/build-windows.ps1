[CmdletBinding()]
param(
    [string]$PnpmExecutable = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$ReleaseApp = Join-Path $ReleaseRoot "FinancialFeeSystem"
$ProjectDriveRoot = [System.IO.Path]::GetPathRoot($ProjectRoot)
$TestTempRoot = Join-Path $ProjectDriveRoot ".ffsys-build-tmp"
$ConfigText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $ProjectRoot "backend\app\config.py")
$VersionMatch = [regex]::Match($ConfigText, 'APP_VERSION\s*=\s*"(?<version>\d+\.\d+\.\d+)"')
if (-not $VersionMatch.Success) {
    throw "无法从backend/app/config.py读取APP_VERSION。"
}
$AppVersion = $VersionMatch.Groups["version"].Value
$VersionParts = @($AppVersion.Split(".") | ForEach-Object { [int]$_ })
$ExpectedFileVersion = "$AppVersion.0"
$FrontendPackage = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $ProjectRoot "frontend\package.json") | ConvertFrom-Json
if ($FrontendPackage.version -ne $AppVersion) {
    throw "前后端版本不一致：backend=$AppVersion，frontend=$($FrontendPackage.version)。"
}
$VersionInfoText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $ProjectRoot "packaging\version_info.txt")
$FixedVersionPattern = "filevers=\(\s*$($VersionParts[0])\s*,\s*$($VersionParts[1])\s*,\s*$($VersionParts[2])\s*,\s*0\s*\)"
$FixedProductPattern = "prodvers=\(\s*$($VersionParts[0])\s*,\s*$($VersionParts[1])\s*,\s*$($VersionParts[2])\s*,\s*0\s*\)"
if (
    $VersionInfoText -notmatch $FixedVersionPattern -or
    $VersionInfoText -notmatch $FixedProductPattern -or
    $VersionInfoText -notmatch "StringStruct\('FileVersion',\s*'$([regex]::Escape($ExpectedFileVersion))'\)" -or
    $VersionInfoText -notmatch "StringStruct\('ProductVersion',\s*'$([regex]::Escape($AppVersion))'\)"
) {
    throw "packaging/version_info.txt的数值或字符串版本与APP_VERSION不一致。"
}

$ExpectedTemplateHash = "16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145"
$TemplatePath = Join-Path $ProjectRoot "新收费计划计算纯净版模板.xlsx"
$ActualTemplateHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TemplatePath).Hash
if ($ActualTemplateHash -ne $ExpectedTemplateHash) {
    throw "公司Excel母版SHA-256与0.2.8确认基线不一致，构建已停止。"
}

if (Test-Path -LiteralPath $ReleaseRoot) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "开发环境尚未安装，请先运行scripts/setup-dev.ps1。"
}
if ($PnpmExecutable) {
    & $PnpmExecutable --dir (Join-Path $ProjectRoot "frontend") run build
} elseif (Get-Command pnpm -ErrorAction SilentlyContinue) {
    & pnpm --dir (Join-Path $ProjectRoot "frontend") run build
} elseif (Get-Command corepack -ErrorAction SilentlyContinue) {
    & corepack pnpm --dir (Join-Path $ProjectRoot "frontend") run build
} else {
    throw "未找到pnpm或corepack。"
}
if ($LASTEXITCODE -ne 0) {
    throw "前端生产构建失败，Windows发布已停止。"
}

if (Test-Path -LiteralPath $TestTempRoot) {
    Remove-Item -LiteralPath $TestTempRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $TestTempRoot -Force | Out-Null
$env:TEMP = $TestTempRoot
$env:TMP = $TestTempRoot
$BackendTests = Join-Path $ProjectRoot "backend\tests"
$BackupTests = Join-Path $BackendTests "test_system_backup.py"
$RemainingTests = @(
    Get-ChildItem -LiteralPath $BackendTests -File -Filter "test_*.py" |
        Where-Object { $_.FullName -ne $BackupTests } |
        Sort-Object Name |
        ForEach-Object FullName
)
$OrderedTests = @($BackupTests) + $RemainingTests
& $VenvPython -m pytest $OrderedTests -q -p no:cacheprovider --basetemp (Join-Path $TestTempRoot "basetemp")
$TestExitCode = $LASTEXITCODE
if ($TestExitCode -ne 0) {
    Remove-Item -LiteralPath $TestTempRoot -Recurse -Force
    throw "后端测试失败，Windows发布已停止。"
}

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name FinancialFeeSystem `
    --version-file (Join-Path $ProjectRoot "packaging\version_info.txt") `
    --distpath $ReleaseRoot `
    --workpath (Join-Path $ProjectRoot "backend\build") `
    --specpath (Join-Path $ProjectRoot "backend") `
    --paths (Join-Path $ProjectRoot "backend") `
    --add-data "$(Join-Path $ProjectRoot 'frontend\dist');frontend\dist" `
    --add-data "$(Join-Path $ProjectRoot '新收费计划计算纯净版模板.xlsx');." `
    --add-data "$(Join-Path $ProjectRoot 'backend\alembic.ini');." `
    --add-data "$(Join-Path $ProjectRoot 'backend\alembic');alembic" `
    --collect-submodules uvicorn `
    --collect-data reportlab `
    --collect-data alembic `
    (Join-Path $ProjectRoot "backend\run_financial_system.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller构建失败，Windows发布已停止。"
}

$BundledOcr = Join-Path $ProjectRoot "tools\Tesseract-OCR"
$TesseractPath = Join-Path $BundledOcr "tesseract.exe"
if (-not (Test-Path -LiteralPath $TesseractPath)) {
    throw "缺少tools/Tesseract-OCR，不能生成含OCR环境的一键版。"
}
$ExpectedTesseractHash = "C66F0F12ED76F6AA455DAC97684BBC86756D6A732380BEE09122454CFDA3F420"
$ActualTesseractHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TesseractPath).Hash
if ($ActualTesseractHash -ne $ExpectedTesseractHash) {
    throw "tools/Tesseract-OCR/tesseract.exe与已审计的SHA-256不一致，构建已停止。"
}
$TesseractVersionOutput = (& $TesseractPath --version 2>&1 | Select-Object -First 1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $TesseractVersionOutput -notmatch '^tesseract v5\.5\.3\.20260724$') {
    throw "Tesseract版本与已审计的5.5.3.20260724不一致。实际输出：$TesseractVersionOutput"
}
Copy-Item -LiteralPath $BundledOcr -Destination (Join-Path $ReleaseApp "Tesseract-OCR") -Recurse -Force

# 只复制发布方已核验的官方Codex CLI；不在构建期间下载程序，
# 也绝不复制用户登录令牌。
$BundledCodex = Join-Path $ProjectRoot "tools\Codex"
$ExpectedCodexHashes = @{
    "codex.exe" = "A395030B56B126F608F2403036DDDB654A9C063213E9C2B5F85D954CF490EBE6"
    "codex-code-mode-host.exe" = "8F98CC7AA079B51DBFBB16A8E655A468A9C37C1CD23E22422C10CDFD6CACE543"
}
foreach ($CodexFile in @("codex.exe", "codex-code-mode-host.exe")) {
    $CodexPath = Join-Path $BundledCodex $CodexFile
    if (-not (Test-Path -LiteralPath $CodexPath)) {
        throw "缺少tools/Codex/$CodexFile，不能生成含ChatGPT Pro辅助识别的一键版。"
    }
    $CodexSignature = Get-AuthenticodeSignature -LiteralPath $CodexPath
    if (
        $CodexSignature.Status -ne "Valid" -or
        -not $CodexSignature.SignerCertificate -or
        $CodexSignature.SignerCertificate.Subject -notmatch 'O="OpenAI OpCo, LLC"'
    ) {
        throw "tools/Codex/$CodexFile 未通过OpenAI Authenticode签名校验，构建已停止。"
    }
    $ActualCodexHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $CodexPath).Hash
    if ($ActualCodexHash -ne $ExpectedCodexHashes[$CodexFile]) {
        throw "tools/Codex/$CodexFile 与已审计的SHA-256不一致，构建已停止。"
    }
}
$CodexVersionOutput = (& (Join-Path $BundledCodex "codex.exe") --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $CodexVersionOutput -notmatch 'codex-cli\s+0\.149\.1') {
    throw "Codex CLI版本与已审计的0.149.1不一致，构建已停止。实际输出：$CodexVersionOutput"
}
Copy-Item -LiteralPath $BundledCodex -Destination (Join-Path $ReleaseApp "Codex") -Recurse -Force
Write-Host "已校验并包含OpenAI签名的Codex CLI 0.149.1；最终用户仍需使用自己的ChatGPT账号完成一次浏览器登录。"
Copy-Item -LiteralPath (Join-Path $ProjectRoot "docs") -Destination (Join-Path $ReleaseApp "docs") -Recurse -Force
$BuildAuditDir = Join-Path $ReleaseApp "docs\build-dependencies"
New-Item -ItemType Directory -Path $BuildAuditDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "backend\requirements-dev-lock.txt") -Destination $BuildAuditDir -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "frontend\pnpm-lock.yaml") -Destination $BuildAuditDir -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\启动金融计划收费系统.bat") -Destination $ReleaseApp -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\发布说明.txt") -Destination $ReleaseApp -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\构建清单.txt") -Destination $ReleaseApp -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") -Destination $ReleaseApp -Force

$ReleaseFiles = @(Get-ChildItem -LiteralPath $ReleaseApp -Recurse -File)
$ReleaseBytes = [long](($ReleaseFiles | Measure-Object -Property Length -Sum).Sum)
$ExecutablePath = Join-Path $ReleaseApp "FinancialFeeSystem.exe"
$ExecutableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExecutablePath).Hash
$ExecutableVersion = (Get-Item -LiteralPath $ExecutablePath).VersionInfo
if (
    $ExecutableVersion.ProductVersion -ne $AppVersion -or
    $ExecutableVersion.FileVersion -ne $ExpectedFileVersion -or
    $ExecutableVersion.FileMajorPart -ne $VersionParts[0] -or
    $ExecutableVersion.FileMinorPart -ne $VersionParts[1] -or
    $ExecutableVersion.FileBuildPart -ne $VersionParts[2] -or
    $ExecutableVersion.FilePrivatePart -ne 0 -or
    $ExecutableVersion.ProductMajorPart -ne $VersionParts[0] -or
    $ExecutableVersion.ProductMinorPart -ne $VersionParts[1] -or
    $ExecutableVersion.ProductBuildPart -ne $VersionParts[2] -or
    $ExecutableVersion.ProductPrivatePart -ne 0
) {
    throw "构建后的EXE数值或字符串版本与APP_VERSION不一致。"
}
$BuildResult = @"
金融计划收费计算系统 Windows构建结果
构建时间：$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))
输出目录：$ReleaseApp
主EXE SHA-256：$ExecutableHash
发布文件数（不含本结果文件）：$($ReleaseFiles.Count)
发布总字节数（不含本结果文件）：$ReleaseBytes
Windows ProductVersion：$AppVersion
Windows FileVersion：$ExpectedFileVersion
Excel母版 SHA-256：$ActualTemplateHash
Tesseract SHA-256：$ActualTesseractHash
后端完整测试和前端生产构建已由本脚本先行通过。
"@
Set-Content -LiteralPath (Join-Path $ReleaseApp "构建结果.txt") -Value $BuildResult -Encoding UTF8
Remove-Item -LiteralPath $TestTempRoot -Recurse -Force

Write-Host "Windows一键版已生成：$ReleaseApp"
