@echo off
setlocal
cd /d "%~dp0\..\.."
echo ==========================================================
echo  Prism (vissimDashboard) - one-time setup
echo  Folder: %CD%
echo ==========================================================
echo.

where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>&1 || (
  echo [X] Python was not found. Install Python 3.11 or 3.12 from python.org
  echo     and tick "Add python.exe to PATH" in the installer. Then run this again.
  goto :fail
)
where node >nul 2>&1 || (
  echo [X] Node.js was not found. Install Node.js 22 LTS from nodejs.org, then run this again.
  goto :fail
)
for /f "delims=" %%v in ('%PY% --version') do echo Found %%v
for /f "delims=" %%v in ('node --version') do echo Found Node %%v
echo.

echo [1/6] Python virtual environment (backend\.venv)
if not exist backend\.venv\Scripts\python.exe (
  %PY% -m venv backend\.venv || goto :fail
) else (
  echo       already exists, reusing it
)

echo [2/6] Backend packages (this takes a few minutes the first time)
backend\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt pytest || goto :fail

echo [3/6] Frontend packages
pushd frontend
call npm.cmd install --no-audit --no-fund || (popd & goto :fail)
popd

echo [4/6] Synthetic demo data (data\ folder - runs without Vissim)
backend\.venv\Scripts\python.exe backend\tools\make_demo_data.py || goto :fail
backend\.venv\Scripts\python.exe backend\tools\make_showcase_network.py || goto :fail
backend\.venv\Scripts\python.exe backend\tools\make_demo_trajectories.py || goto :fail

echo [5/6] Environment file (backend\.env)
if not exist backend\.env (
  copy backend\.env.example backend\.env >nul
  echo       created backend\.env from .env.example
  echo       -^> open backend\.env in Notepad and paste the ANTHROPIC_API_KEY you were given
) else (
  echo       backend\.env already exists, left untouched
)

echo [6/6] Accounts
backend\.venv\Scripts\python.exe backend\tools\manage_users.py list-users
echo       No account yet? Run 06-create-admin.cmd (there is no default login).
echo.
echo ==========================================================
echo  Setup complete.
echo  Next: 02-start-backend.cmd  then  03-start-frontend.cmd
echo ==========================================================
pause
exit /b 0

:fail
echo.
echo [X] Setup stopped at the step above. Copy ALL the text in this window
echo     (right-click the title bar - Edit - Select All, then Enter) and send it to Yunha.
pause
exit /b 1
