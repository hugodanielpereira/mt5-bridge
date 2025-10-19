param(
  [string]$HttpHost = "127.0.0.1",
  [int]$Port = 5005
)

# Ir para a pasta do script
Set-Location -Path $PSScriptRoot

# Usa o Python do venv se existir
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# Logging defaults
if (-not $env:LOG_ROTATE)  { $env:LOG_ROTATE  = "time" }
if (-not $env:LOG_WHEN)    { $env:LOG_WHEN    = "midnight" }
if (-not $env:LOG_BACKUPS) { $env:LOG_BACKUPS = "14" }
if (-not $env:LOG_LEVEL)   { $env:LOG_LEVEL   = "INFO" }

# Define o título da janela
$Host.UI.RawUI.WindowTitle = "MLSL - BRIDGE"

Write-Host "`n=== Starting bridge on http://$HttpHost`:$Port ===`n" -ForegroundColor Cyan

# Executa o Uvicorn (sem hot reload)
& $py -m uvicorn app.main:app --host $HttpHost --port $Port