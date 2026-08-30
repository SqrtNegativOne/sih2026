@echo off
REM P7 fix: this used to call "uv run uvicorn ..." -- uv is not installed on
REM the target machine, so that failed outright. .venv already has uvicorn
REM installed directly (confirmed: .venv\Scripts\uvicorn.exe exists); this
REM calls it without going through uv at all.
echo Starting backend...
start "Backend" cmd /k ".venv\Scripts\uvicorn.exe backend.main:app --reload"

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
