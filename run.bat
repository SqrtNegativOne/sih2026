@echo off
REM P7 fix: this used to call "uv run uvicorn ..." -- uv is not installed on
REM the target machine, so that failed outright. .venv already has uvicorn
REM installed directly (confirmed: .venv\Scripts\uvicorn.exe exists); this
REM calls it without going through uv at all.
REM NO --reload here, deliberately. The backend caches an expensive
REM historical replay and two tonnage snapshots in memory, and --reload
REM restarts the process on any .py file save -- which silently discards all
REM of them mid-demo. Use run-dev.bat while developing; this is the launcher
REM for showing the project to someone.
echo Starting backend...
start "Backend" cmd /k ".venv\Scripts\uvicorn.exe backend.main:app"

echo Starting frontend...
cd frontend
if not exist node_modules (
    echo Installing frontend dependencies...
    call npm install
)
start "Frontend" cmd /k "npm run dev"
cd ..

echo Project is starting in separate windows.
echo Backend:  http://127.0.0.1:8000
echo Frontend: http://127.0.0.1:5173 (see the Frontend window for the exact port)
echo.
echo TIP: run this once before a demo so the Replay screen is instant:
echo   .venv\Scripts\python.exe -m data_builders.build_replay_snapshot
