@echo off
setlocal
REM Encaminha para o PowerShell (passa argumentos como dev|prod)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-bridge.ps1" %*
endlocal