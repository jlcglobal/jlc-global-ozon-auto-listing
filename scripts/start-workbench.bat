@echo off
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
netstat -ano | findstr "LISTENING" | findstr ":8765" >nul
if %errorlevel%==0 (
  echo ???????????????
  start "" "http://127.0.0.1:8765/workbench"
  exit /b 0
)
echo ???? JLC ???...
start "" /b ".venv\Scripts\python.exe" -m uvicorn app:app --app-dir collector/local-ingest --host 127.0.0.1 --port 8765 >> "logs\workbench-service.out.log" 2>&1
timeout /t 6 /nobreak >nul
start "" "http://127.0.0.1:8765/workbench"
echo ???: http://127.0.0.1:8765/workbench
