@echo off
REM ===============================================
REM  Run script para MT5-Bridge (Windows / VPS)
REM ===============================================

REM ---- Configuração ----
setlocal
REM Ativar virtualenv (se existir)
IF EXIST ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM Carregar .env se existir
IF EXIST ".env" (
    echo [INFO] A carregar .env...
    for /F "usebackq tokens=1,* delims==" %%a in (".env") do (
        set %%a=%%b
    )
)

REM ---- Lançar Uvicorn ----
echo [INFO] A iniciar MT5-Bridge em http://%HOST%:%PORT% ...
uvicorn app:app --host %HOST% --port %PORT% --reload

REM Fim
endlocal
pause