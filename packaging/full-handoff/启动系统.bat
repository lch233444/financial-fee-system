@echo off
setlocal
cd /d "%~dp0"
set "FINANCIAL_DATA_ROOT=%~dp0data"
set "FINANCIAL_CODEX_HOME=%~dp0private-login"
set "FINANCIAL_FRONTEND_DIST="
set "FINANCIAL_HOST=127.0.0.1"
set "FINANCIAL_PORT=18181"
set "FINANCIAL_TESTING=0"
start "" "%~dp0FinancialFeeSystem\FinancialFeeSystem.exe"
endlocal
