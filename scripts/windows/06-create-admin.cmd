@echo off
setlocal
cd /d "%~dp0\..\..\backend"
echo Create the FIRST admin account for this machine's Prism.
echo (Refuses if an enabled admin already exists - that is by design.)
echo.
set /p USERNAME_IN=Username (letters/numbers, e.g. minsu): 
set /p DISPLAY_IN=Display name (e.g. Kim Minsu): 
echo You will be asked to type the password twice; it is not shown while typing.
.venv\Scripts\python.exe tools\manage_users.py create-admin --username "%USERNAME_IN%" --display-name "%DISPLAY_IN%"
echo.
.venv\Scripts\python.exe tools\manage_users.py list-users
pause
