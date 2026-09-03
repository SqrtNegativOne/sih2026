# P7 fix: this used to call "uv run uvicorn ..." -- uv is not installed on
# the target machine, so that failed outright. .venv already has uvicorn
# installed directly (confirmed: .venv\Scripts\uvicorn.exe exists); this
# calls it without going through uv at all.
# NO --reload here, deliberately. The backend caches an expensive historical
# replay and two tonnage snapshots in memory, and --reload restarts the
# process on any .py file save -- which silently discards all of them
# mid-demo. Use run-dev.ps1 while developing; this is the demo launcher.
Write-Host "Starting backend..."
Start-Process -FilePath "cmd" -ArgumentList "/k", ".venv\Scripts\uvicorn.exe backend.main:app" -WindowStyle Normal

Write-Host "Starting frontend..."
Set-Location -Path "frontend"
if (!(Test-Path "node_modules")) {
    Write-Host "Installing frontend dependencies..."
    npm install
}
Start-Process -FilePath "cmd" -ArgumentList "/k", "npm run dev" -WindowStyle Normal
Set-Location -Path ".."

Write-Host "Project is starting in separate windows."
Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend: http://127.0.0.1:5173 (see the Frontend window for the exact port)"
Write-Host ""
Write-Host "TIP: run this once before a demo so the Replay screen is instant:"
Write-Host "  .venv\Scripts\python.exe -m data_builders.build_replay_snapshot"
