# C:\Trading\bridges\mt5-bridge\scripts\warmup-demo.ps1
param(
  [Parameter(Mandatory = $true)][string]$Term,
  [int]$TimeoutSec = 20
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Term)) {
  throw "terminal nao existe: $Term"
}

$dir = Split-Path $Term
$portableFlag = Join-Path $dir "portable"
if (-not (Test-Path $portableFlag)) {
  New-Item -ItemType File -Path $portableFlag -Force | Out-Null
}

# Ja esta a correr exatamente este executavel?
$running = @(Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" | Where-Object {
  $_.ExecutablePath -eq $Term
})

# Se nao existir nenhuma instancia, arranca em modo /portable
if ($running.Count -eq 0) {
  Write-Host "[WARMUP] start portable: $Term /portable"
  Start-Process -FilePath $Term -ArgumentList "/portable" -WorkingDirectory $dir
  Start-Sleep -Seconds 2
}

# Espera por um estado aceitavel:
#   Preferido: so 1 instancia e em /portable
#   Aceitavel: existe pelo menos 1 /portable (nao falha o run-all)
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
  $procs = @(Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" | Where-Object {
    $_.ExecutablePath -eq $Term
  })

  $hasPortable = $false
  $hasNon      = $false

  foreach ($p in $procs) {
    $cmd = ""
    if ($null -ne $p.CommandLine) { $cmd = [string]$p.CommandLine }
    if ($cmd -match "/portable") { $hasPortable = $true } else { $hasNon = $true }
  }

  if ($hasPortable -and -not $hasNon) {
    Write-Host "[WARMUP] OK - only portable instance is running."
    exit 0
  }

  if ($hasPortable -and $hasNon) {
    Write-Warning "[WARMUP] portable + non-portable em simultaneo (continuo assim mesmo)."
    exit 0
  }

  Start-Sleep -Milliseconds 700
}

Write-Warning "[WARMUP] timeout - nao consegui ver o portable exclusivo, continuo mesmo assim."
exit 0