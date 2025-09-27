$BASE = "http://127.0.0.1:5005"
$H = @{ "X-API-Key" = "<A_TUA_GUID_AQUI>" }

Write-Host "Ping:"; Invoke-RestMethod "$BASE/ping" -Headers $H
Write-Host "`nAccount:"; Invoke-RestMethod "$BASE/account" -Headers $H | Format-Table -Auto

Write-Host "`nOHLCV EURUSD M15 (3 rows):"
Invoke-RestMethod "$BASE/ohlcv?symbol=EURUSD&tf=M15&limit=3" -Headers $H | Select-Object -First 3 | Format-List

Write-Host "`nBUY 0.01 EURUSD:"
$body = @{symbol="EURUSD"; side="buy"; volume=0.01; magic=2025; comment="smoke"} | ConvertTo-Json
Invoke-RestMethod "$BASE/order_market" -Method POST -Headers $H -Body $body -ContentType "application/json" | Format-List

Write-Host "`nPositions:"; Invoke-RestMethod "$BASE/positions" -Headers $H | Format-Table -Auto

Write-Host "`nCLOSE (SELL 0.01):"
$body2 = @{symbol="EURUSD"; side="sell"; volume=0.01; magic=2025; comment="close smoke"} | ConvertTo-Json
Invoke-RestMethod "$BASE/order_market" -Method POST -Headers $H -Body $body2 -ContentType "application/json" | Format-List