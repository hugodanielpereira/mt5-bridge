# ===============================================
# Stop ALL MLSL + Bridge Services (PowerShell)
# ===============================================
Write-Host "==== A encerrar todos os serviços MLSL / Bridge ====" -ForegroundColor Cyan

# Define o diretório base (ajusta se necessário)
$base = "C:\Trading"
$apps = "$base\apps\ml-strategy-lab"
$bridge = "$base\bridges\mt5-bridge"

# ----------------------------
# Encerrar processos Python
# ----------------------------
# Filtra apenas instâncias MLSL/Bridge com cwd conhecido
$targets = @(
    "uvicorn", 
    "ml-strategy-lab", 
    "mt5-bridge", 
    "retrain_incremental", 
    "executor", 
    "scheduler_retrain"
)

foreach ($t in $targets) {
    $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like "*python*" -and $_.CommandLine -match $t
    }
    foreach ($p in $procs) {
        Write-Host ("Parando: {0,-25} PID={1}" -f $t, $p.Id) -ForegroundColor Yellow
        try {
            Stop-Process -Id $p.Id -Force
            Write-Host ("✅ {0} encerrado." -f $t) -ForegroundColor Green
        } catch {
            Write-Host ("⚠️ Falha a encerrar {0}: {1}" -f $t, $_.Exception.Message) -ForegroundColor Red
        }
    }
}

# ----------------------------
# Fecha janelas PowerShell abertas por Start-Process
# ----------------------------
Write-Host "A encerrar janelas PowerShell MLSL..." -ForegroundColor Cyan
$procsPwsh = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowTitle -match "MLSL|Bridge|Executor|Scheduler"
}
foreach ($p in $procsPwsh) {
    Write-Host ("Fechando janela: {0,-20} PID={1}" -f $p.MainWindowTitle, $p.Id) -ForegroundColor DarkGray
    try {
        Stop-Process -Id $p.Id -Force
    } catch {}
}

# ----------------------------
# Limpa pids residuais
# ----------------------------
$pidsDir = Join-Path $apps "outputs\live\pids"
if (Test-Path $pidsDir) {
    Remove-Item "$pidsDir\*.pid" -ErrorAction SilentlyContinue
    Write-Host "🧹 PID files limpos em $pidsDir" -ForegroundColor DarkCyan
}

Write-Host "`n✅ Todos os serviços MLSL foram encerrados com sucesso." -ForegroundColor Green