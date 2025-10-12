$ErrorActionPreference = "Stop"
$Repo = "C:\Trading\bridges\mt5-bridge"
$Py   = "$Repo\.venv\Scripts\python.exe"

Set-Location $Repo
& $Py -m uvicorn app.main:app --host 127.0.0.1 --port 5005