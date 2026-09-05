@echo off
setlocal
cd /d "%~dp0\..\..\backend"
if not exist .venv\Scripts\python.exe (
  echo [X] backend\.venv not found. Run 01-setup.cmd first.
  pause & exit /b 1
)
set QWEN_WORKER_AUTO_START=0
echo Backend starting on http://localhost:8001  (API docs: http://localhost:8001/docs)
echo Keep this window open. Close it (or press Ctrl+C) to stop the backend.
echo.
.venv\Scripts\python.exe -m uvicorn main:app --port 8001
echo.
echo Backend stopped.
pause
