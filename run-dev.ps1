# The DEVELOPMENT launcher: --reload on, so editing Python restarts the
# backend automatically.
#
# Do not use this for a demo. --reload restarts the process on every .py save,
# and the backend keeps expensive things in memory -- a historical replay that
# costs about 22 minutes to rebuild, plus two tonnage snapshots. A stray save
# mid-demo throws all of it away and the next click pays for it in front of
# whoever is watching. run.ps1 is the demo one.
Write-Host "Starting backend (dev, auto-reload)..."
Start-Process -FilePath "cmd" -ArgumentList "/k", ".venv\Scripts\uvicorn.exe backend.main:app --reload" -WindowStyle Normal

Write-Host "Starting frontend..."
Set-Location -Path "frontend"
if (!(Test-Path "node_modules")) { npm install }
Start-Process -FilePath "cmd" -ArgumentList "/k", "npm run dev" -WindowStyle Normal
Set-Location -Path ".."

Write-Host "Dev servers starting in separate windows."
