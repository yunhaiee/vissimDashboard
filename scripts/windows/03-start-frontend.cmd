@echo off
setlocal
cd /d "%~dp0\..\..\frontend"
if not exist node_modules (
  echo [X] frontend\node_modules not found. Run 01-setup.cmd first.
  pause & exit /b 1
)
echo Frontend starting on http://localhost:3000
echo Keep this window open. Close it (or press Ctrl+C) to stop the frontend.
echo.
call npm.cmd run dev
echo.
echo Frontend stopped.
pause
