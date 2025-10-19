@echo off
setlocal enableextensions

REM Ir para a pasta deste .bat
pushd %~dp0

REM Usar Python do venv se existir; senão, usar o Python do sistema
set PYEXE=python
if exist ".venv\Scripts\python.exe" (
  set "PYEXE=.venv\Scripts\python.exe"
)

REM (opcional) logging defaults
if "%LOG_ROTATE%"=="" set LOG_ROTATE=time
if "%LOG_WHEN%"==""   set LOG_WHEN=midnight
if "%LOG_BACKUPS%"=="" set LOG_BACKUPS=14
if "%LOG_LEVEL%"==""  set LOG_LEVEL=INFO

echo.
echo === Starting bridge on http://127.0.0.1:5005 ===
echo.

"%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 5005

if errorlevel 1 (
  echo.
  echo [ERRO] Falha a iniciar o bridge. Verifica se o Python/uvicorn estao instalados
  echo ou se a porta 5005 nao esta em uso.
  echo.
  pause
)
endlocal