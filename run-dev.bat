@echo off
REM The DEVELOPMENT launcher: --reload on, so editing Python restarts the
REM backend automatically.
REM
REM Do not use this for a demo. --reload restarts the process on every .py
REM save, and the backend keeps expensive things in memory -- a historical
REM replay that costs about 22 minutes to rebuild, plus two tonnage
REM snapshots. A stray save mid-demo throws all of it away and the next click
REM pays for it in front of whoever is watching. run.bat is the demo one.
echo Starting backend (dev, auto-reload)...
start "Backend (dev)" cmd /k ".venv\Scripts\uvicorn.exe backend.main:app --reload"

echo Starting frontend...
cd frontend
if not exist node_modules (
    echo Installing frontend dependencies...
    call npm install
)
start "Frontend" cmd /k "npm run dev"
cd ..

echo Dev servers starting in separate windows.
echo Backend:  http://127.0.0.1:8000
echo Frontend: http://127.0.0.1:5173
