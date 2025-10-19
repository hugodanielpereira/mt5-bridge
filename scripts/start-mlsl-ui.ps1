# scripts/start-mlsl-ui.ps1
param(
  [switch]$Detach,            # deixa as janelas abertas e devolve controlo
  [switch]$StartWatcher = $true
)

$ErrorActionPreference = 'Stop'

function Write-Section($text, $color="Cyan") {
  Write-Host ("=" * 88) -ForegroundColor DarkGray
  Write-Host $text -ForegroundColor $color
  Write-Host ("=" * 88) -ForegroundColor DarkGray
}
function _Resolve([string]$p){ try { (Resolve-Path -LiteralPath $p -ErrorAction Stop).ProviderPath } catch { $null } }

# --- Descobrir raízes -----------------------------------------------------------------
# Este script vive dentro do repo LAB (…\apps\ml-strategy-lab\scripts\start-mlsl-ui.ps1)
$ScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
$LAB = _Resolve (Join-Path $ScriptRoot '..')           # …\apps\ml-strategy-lab
if (-not $LAB) { throw "Não encontrei o diretório do LAB a partir de $ScriptRoot" }

$AppsRoot = Split-Path -Parent $LAB                    # …\apps
$TopRoot  = Split-Path -Parent $AppsRoot               # …\ (ex.: C:\Trading)

# Bridge pode estar em monorepo (…\apps\bridges\mt5-bridge) ou como irmão (…\bridges\mt5-bridge)
$BRIDGE = _Resolve (Join-Path $AppsRoot 'bridges\mt5-bridge')
if (-not $BRIDGE) { $BRIDGE = _Resolve (Join-Path $TopRoot 'bridges\mt5-bridge') }
if (-not $BRIDGE) { throw "Não encontrei o repositório do BRIDGE (procurei em `$AppsRoot\bridges\mt5-bridge e `$TopRoot\bridges\mt5-bridge)" }

# --- Executáveis Python ---------------------------------------------------------------
$PyLab    = Join-Path $LAB    '.venv\Scripts\python.exe'
$PyBridge = Join-Path $BRIDGE '.venv\Scripts\python.exe'
if (-not (Test-Path $PyBridge)) { throw "Venv do BRIDGE não encontrado: $PyBridge" }
if (-not (Test-Path $PyLab))    { Write-Warning "Venv do LAB não encontrado: $PyLab (algumas partes podem falhar)"; $PyLab = 'python' }

# --- Ports/ENV ------------------------------------------------------------------------
$env:BRIDGE_HOST = $env:BRIDGE_HOST  ? $env:BRIDGE_HOST  : '127.0.0.1'
$env:BRIDGE_PORT = $env:BRIDGE_PORT  ? $env:BRIDGE_PORT  : '5005'
$env:UI_PORT     = $env:UI_PORT      ? $env:UI_PORT      : '5000'

# Reload opcional para uvicorn
$__reloadFlag = ''
if ($env:UVICORN_RELOAD -in @('1','true','on','yes')) { $__reloadFlag = ' --reload' }

# --- Comandos -------------------------------------------------------------------------
$BackendCmd = "$PyBridge -m uvicorn app.main:app --host $env:BRIDGE_HOST --port $env:BRIDGE_PORT$__reloadFlag"
# UI principal do bridge (FastAPI/Starlette que serve os ficheiros estáticos da UI)
# Se tiveres um app específico em app/ui/ui_app.py, arrancamos com ele; senão tentamos Flask como fallback.
$UiAppPath   = Join-Path $BRIDGE 'app\ui\ui_app.py'
if (Test-Path $UiAppPath) {
  $UiCmd = "$PyBridge -m uvicorn app.ui.ui_app:app --host 127.0.0.1 --port $env:UI_PORT$__reloadFlag"
} else {
  # Fallback com Flask (só se existir o módulo)
  $UiCmd = "$PyBridge -m flask --app app.ui.ui_app run --host 127.0.0.1 --port $env:UI_PORT"
}

# Watcher opcional (no LAB)
$WatcherPath = Join-Path $LAB 'tools\watch_signals.py'
$WatcherCmd  = "$PyLab `"$WatcherPath`""
$HasWatcher  = (Test-Path $WatcherPath)

Write-Section "Launching ML Strategy Lab — UI & Backend"

# --- Backend (Bridge FastAPI) ---------------------------------------------------------
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING='utf-8'
if ($Detach) {
  Start-Process powershell -ArgumentList "-NoExit","-Command","Write-Host '=== MLSL Backend (Bridge) ===' -ForegroundColor Green; cd '$BRIDGE'; $BackendCmd" -WindowStyle Normal
} else {
  Write-Host "=== MLSL Backend (Bridge) ===" -ForegroundColor Green
  Push-Location $BRIDGE
  Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$BRIDGE'; $BackendCmd" -WindowStyle Normal | Out-Null
  Pop-Location
}

# --- UI (se existir) ------------------------------------------------------------------
if ($Detach) {
  Start-Process powershell -ArgumentList "-NoExit","-Command","Write-Host '=== MLSL UI ===' -ForegroundColor Yellow; cd '$BRIDGE'; $UiCmd" -WindowStyle Normal
} else {
  Write-Host "=== MLSL UI ===" -ForegroundColor Yellow
  Push-Location $BRIDGE
  Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$BRIDGE'; $UiCmd" -WindowStyle Normal | Out-Null
  Pop-Location
}

# --- Watcher (opcional) ---------------------------------------------------------------
if ($StartWatcher -and $HasWatcher) {
  if ($Detach) {
    Start-Process powershell -ArgumentList "-NoExit","-Command","Write-Host '=== MLSL Watcher ===' -ForegroundColor Magenta; cd '$LAB'; $WatcherCmd" -WindowStyle Normal
  } else {
    Write-Host "=== MLSL Watcher ===" -ForegroundColor Magenta
    Push-Location $LAB
    Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$LAB'; $WatcherCmd" -WindowStyle Normal | Out-Null
    Pop-Location
  }
} elseif ($StartWatcher) {
  Write-Warning "Watcher não encontrado: $WatcherPath"
}

Write-Section ("A abrir UI: http://127.0.0.1:{0}/dashboard" -f $env:UI_PORT) 'Green'