# ============================================
# test-mlsl-e2e.ps1  —  MLSL end-to-end smoke
# - Sem necessidade obrigatória de API key
# - Faz smoke via API (order_market)
# - Faz smoke via Executor (drop de sinal)
# - Mostra posições e histórico normalizado
# ============================================

# --- PARÂMETROS BÁSICOS (ajusta à vontade) ---
$BridgeUrl = $env:BRIDGE_URL
if (-not $BridgeUrl) { $BridgeUrl = "http://127.0.0.1:5005" }

# Se tiveres key, põe-a aqui ou exporta BRIDGE_API_KEY / X_API_KEY no ambiente
$ApiKey = $env:BRIDGE_API_KEY
if (-not $ApiKey) { $ApiKey = $env:X_API_KEY }   # fallback

# Caminho da tua árvore MLSL (para a inbox do executor)
$RepoRoot = (Resolve-Path ".").Path   # ou "C:\Trading\apps\ml-strategy-lab"

# Símbolo e volume para o smoke
$Symbol = "BTCUSD"
$Vol    = 0.01
$Magic  = 7701
$Comment= "mlsl-exec smoke $(Get-Date -Format s)"

# --- HEADERS (só envia se tiveres chave) ---
if ($ApiKey) { $Global:BRIDGE_HEADERS = @{ "X-API-Key" = $ApiKey } } else { $Global:BRIDGE_HEADERS = @{} }

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Json($obj) { ($obj | ConvertTo-Json -Depth 8) -replace "\\u002B","+ " | Write-Host }

function Invoke-Bridge {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [ValidateSet("GET","POST")] [string]$Method = "GET",
        $Body = $null
    )
    $uri = "$BridgeUrl$Path"
    try {
        if ($Method -eq "GET") {
            return Invoke-RestMethod -Uri $uri -Headers $Global:BRIDGE_HEADERS -Method GET -TimeoutSec 30
        } else {
            if ($Body -is [string]) {
                return Invoke-RestMethod -Uri $uri -Headers $Global:BRIDGE_HEADERS -Method POST -TimeoutSec 30 `
                    -ContentType "application/json" -Body $Body
            } else {
                $json = ($Body | ConvertTo-Json -Depth 8)
                return Invoke-RestMethod -Uri $uri -Headers $Global:BRIDGE_HEADERS -Method POST -TimeoutSec 30 `
                    -ContentType "application/json" -Body $json
            }
        }
    } catch {
        Write-Warning "Falha na chamada: $uri"
        if ($_.Exception.Response -and $_.Exception.Response.ContentLength -gt 0) {
            try {
                $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $errBody = $sr.ReadToEnd()
                Write-Host $errBody
            } catch { }
        } else {
            Write-Host $_
        }
        return $null
    }
}

# ------------------------------------------------
# 0) Ping + Health
# ------------------------------------------------
Write-Step "0) Bridge /ping e /health"
$ping = Invoke-Bridge -Path "/ping"
Write-Host "ping:"; Write-Json $ping

$health = Invoke-Bridge -Path "/health?verbose=1"
Write-Host "health:"; Write-Json $health

# ------------------------------------------------
# 1) Smoke via API: /order_market (BUY)
# ------------------------------------------------
Write-Step "1) Smoke via API: /order_market BUY $Symbol $Vol"
$respOrder = Invoke-Bridge -Path "/order_market" -Method POST -Body @{
    symbol  = $Symbol
    side    = "buy"
    volume  = [double]$Vol
    magic   = [int]$Magic
    comment = $Comment
}
if ($respOrder) {
    Write-Host "order_market response:"; Write-Json $respOrder
} else {
    Write-Warning "order_market falhou (vê logs do bridge/MT5)."
}

# ------------------------------------------------
# 2) Posições correntes
# ------------------------------------------------
Write-Step "2) Posições Correntes (/positions)"
$pos = Invoke-Bridge -Path "/positions"
if ($pos) {
    $pos | Select-Object ticket,symbol,type,volume,price_open,price_current,magic,comment | Format-Table -AutoSize
} else {
    Write-Host "(sem posições ou erro na chamada)"
}

# ------------------------------------------------
# 3) Smoke via EXECUTOR: criar sinal na inbox
# ------------------------------------------------
Write-Step "3) Smoke via EXECUTOR: gerar sinal na inbox"
$Inbox = Join-Path $RepoRoot "outputs\live\signals\inbox"
$Archive = Join-Path $RepoRoot "outputs\live\signals\archive"
$LogExec = Join-Path $RepoRoot "outputs\live\logs\executor.log"
New-Item -ItemType Directory -Force -Path $Inbox | Out-Null
New-Item -ItemType Directory -Force -Path $Archive | Out-Null

$sig = @{
  symbol    = $Symbol
  side      = "buy"
  volume    = [double]$Vol
  timeframe = "H2"
  comment   = "smoke-auto $(Get-Date -Format s)"
}

$sigFile = Join-Path $Inbox ("{0}_smoke_{1}.json" -f $Symbol, (Get-Date -Format "yyyyMMdd-HHmmss"))
$sig | ConvertTo-Json -Depth 4 | Out-File -FilePath $sigFile -Encoding utf8
Write-Host "Criado sinal: $sigFile"

# (Opcional) Espera alguns segundos e mostra rasto no archive + log
Start-Sleep -Seconds 3
Write-Host "`nArchive recente:" -ForegroundColor DarkGray
Get-ChildItem $Archive | Sort-Object LastWriteTime -Desc | Select-Object -First 5 Name,LastWriteTime

if (Test-Path $LogExec) {
    Write-Host "`nexecutor.log (tail 30):" -ForegroundColor DarkGray
    Get-Content $LogExec -Tail 30
} else {
    Write-Host "executor.log não encontrado em $LogExec"
}

# ------------------------------------------------
# 4) Histórico: /orders_history e /deals_history
# ------------------------------------------------
Write-Step "4) Histórico (ISO) + filtros"
$orders = Invoke-Bridge -Path "/orders_history?days=14&symbol=$Symbol"
$deals  = Invoke-Bridge -Path "/deals_history?days=14&magic=$Magic"

if ($orders) {
    Write-Host "orders (top 5):"
    $orders | Select-Object -First 5 time_done,symbol,magic,comment | Format-Table -AutoSize
    Write-Host "`norders JSON (top 3):"
    Write-Json ($orders | Select-Object -First 3)
}

if ($deals) {
    Write-Host "`ndeals (top 5):"
    $deals | Select-Object -First 5 time,symbol,profit,magic,comment | Format-Table -AutoSize
    Write-Host "`ndeals JSON (top 3):"
    Write-Json ($deals | Select-Object -First 3)
}

# ------------------------------------------------
# 5) (Opcional) Fechar tudo do símbolo
#     — comenta esta secção se quiseres deixar posições abertas
# ------------------------------------------------
Write-Step "5) (Opcional) Fechar posições do símbolo"
$closeResp = Invoke-Bridge -Path "/close_symbol?symbol=$Symbol" -Method POST
if ($closeResp) {
    Write-Host "close_symbol response:"; Write-Json $closeResp
} else {
    Write-Host "(não foi possível fechar — possivelmente sem posições)"
}

Write-Host "`n✅ Fim do teste end-to-end." -ForegroundColor Green