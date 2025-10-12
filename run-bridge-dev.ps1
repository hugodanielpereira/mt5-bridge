param(
  [int]$Port = 5005,
  [string]$HttpHost = "127.0.0.1"
)

# --- Logging env (defaults if not set) ---
if (-not $env:LOG_ROTATE)  { $env:LOG_ROTATE  = "time" }
if (-not $env:LOG_WHEN)    { $env:LOG_WHEN    = "midnight" }
if (-not $env:LOG_BACKUPS) { $env:LOG_BACKUPS = "14" }
if (-not $env:LOG_LEVEL)   { $env:LOG_LEVEL   = "INFO" }

Write-Host "`n=== Ensuring hot-reload deps ===" -ForegroundColor Cyan
# Use python -c so it's PowerShell-friendly (no heredocs)
python -c "import sys,subprocess; subprocess.check_call([sys.executable,'-m','pip','install','--disable-pip-version-check','-q','uvicorn[standard]','watchfiles'])" 2>$null

Write-Host "`n=== Starting bridge with hot reload on $HttpHost`:$Port ===`n" -ForegroundColor Cyan
# IMPORTANT: run this from the bridge root (folder that contains the 'app' package)
& python -m uvicorn app.main:app `
  --host $HttpHost `
  --port $Port `
  --reload `
  --reload-dir app `
  --reload-include "*.py" `
  --reload-exclude "logs/*" `
  --reload-exclude "*.log"