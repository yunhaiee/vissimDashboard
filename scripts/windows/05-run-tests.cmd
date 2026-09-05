@echo off
setlocal
cd /d "%~dp0\..\.."
echo === Backend tests (expect: 0 failed; "skipped" is normal) ===
pushd backend
.venv\Scripts\python.exe -m pytest -q
popd
echo.
echo === Frontend typecheck + contract suite (expect: no errors, "fail 0") ===
pushd frontend
call npm.cmd run typecheck
call npm.cmd run test:dashboard-contract
popd
pause
