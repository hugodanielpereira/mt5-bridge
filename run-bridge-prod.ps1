param(
  [int]$Port = 5005,
  [string]$HttpHost = "127.0.0.1",
  [int]$Workers = 1,
  [switch]$StrictStartup  # if set, app raises on MT5 init failure (uses BRIDGE_STRICT_STARTUP=1)
)

# --- Resolve Python (prefer local venv) ---
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) { $python = $VenvPython } else { $python = "python" }

# --- Logging env with sensible defaults (your app writes to logs\bridge.log) ---
if (-not $env:LOG_ROTATE)   { $env:LOG_ROTATE   = "time" }      # "time" or "size"
if (-not $env:LOG_WHEN)     { $env:LOG_WHEN     = "midnight" }  # only used for time-rotation
if (-not $env:LOG_BACKUPS)  { $env:LOG_BACKUPS  = "14" }        # keep 14 daily logs
if (-not $env:LOG_LEVEL)    { $env:LOG_LEVEL    = "INFO" }
if (-not $env:X_API_KEY)    { $env:X_API_KEY    = "CHANGE-ME" } # set a real key later

# Optional strict startup (fail if MT5 not initialized)
if ($StrictStartup) { $env:BRIDGE_STRICT_STARTUP = "1" }

# --- Ensure deps (quiet) ---
& $python -m pip install --disable-pip-version-check -q uvicorn[standard] > $null 2>&1

# --- Ensure logs dir exists (your app writes there) ---
$logDir = Join-Path $ScriptRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

Write-Host "`n=== Starting bridge (prod) on $HttpHost`:$Port (workers=$Workers) ===`n" -ForegroundColor Green

# NOTE: no --reload in prod
$uvicornArgs = @(
  "app.main:app",
  "--host", $HttpHost,
  "--port", $Port,
  "--workers", "$Workers",
  "--proxy-headers"               # safe default if you later reverse-proxy
)

# Launch
& $python -m uvicorn @uvicornArgs