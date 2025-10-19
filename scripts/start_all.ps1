# ===============================================
# Start ALL MLSL + Bridge Services (PowerShell)
# ===============================================

$base = "C:\Trading"
$apps = "$base\apps\ml-strategy-lab"
$bridge = "$base\bridges\mt5-bridge"

Write-Host "==== Iniciando todos os serviços MLSL ====" -ForegroundColor Cyan

# --- UI ---
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$bridge'; Write-Host '🚀 UI Bridge a arrancar...' -ForegroundColor Yellow; uvicorn app.main:app --host 127.0.0.1 --port 5005" `
    -WindowStyle Normal -WorkingDirectory $bridge -Verb RunAs

# --- Backend / MLSL Lab ---
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$apps'; Write-Host '⚙️ MLSL Backend (Lab) a arrancar...' -ForegroundColor Cyan; .\.venv\Scripts\activate; python -m tools.retrain_incremental --no-emit" `
    -WindowStyle Normal -WorkingDirectory $apps

# --- Executor ---
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$apps'; Write-Host '📈 Executor ativo...' -ForegroundColor Green; .\.venv\Scripts\activate; python -m tools.executor" `
    -WindowStyle Normal -WorkingDirectory $apps

# --- Scheduler / Retrain ---
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$apps'; Write-Host '⏰ Scheduler ativo...' -ForegroundColor Magenta; .\.venv\Scripts\activate; python -m tools.scheduler_retrain" `
    -WindowStyle Normal -WorkingDirectory $apps

# --- Bridge MT5 Connector ---
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$bridge'; Write-Host '🔌 MT5 Bridge ativo...' -ForegroundColor Blue; .\.venv\Scripts\activate; python -m app.main" `
    -WindowStyle Normal -WorkingDirectory $bridge

Write-Host "`n✅ Todos os serviços foram lançados (UI + Backend + Bridge + Executor + Scheduler)" -ForegroundColor Green