@echo off
setlocal
cd /d "%~dp0"
set "FINANCIAL_DATA_ROOT=%~dp0test-data"
set "FINANCIAL_CODEX_HOME=%~dp0test-login"
set "FINANCIAL_HOST=127.0.0.1"
set "FINANCIAL_PORT=18180"
set "FINANCIAL_TESTING=0"
start "" "%~dp0FinancialFeeSystem\FinancialFeeSystem.exe"
endlocal
