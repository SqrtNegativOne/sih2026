# P7 fix: this used to call "uv run uvicorn ..." -- uv is not installed on
# the target machine, so that failed outright. .venv already has uvicorn
# installed directly (confirmed: .venv\Scripts\uvicorn.exe exists); this
# calls it without going through uv at all.
Write-Host "Starting backend..."
Start-Process -FilePath "cmd" -ArgumentList "/k", ".venv\Scripts\uvicorn.exe backend.main:app --reload" -WindowStyle Normal

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
