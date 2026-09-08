@echo off
setlocal
cd /d "%~dp0\..\..\backend"
echo ==========================================================
echo  Vissim COM smoke test
echo  Opens its OWN Vissim instance, loads the PTV example
echo  "Urban Intersection Beijing.CN", runs 60 s, checks read-back.
echo  CLOSE any Vissim window you have open before continuing.
echo ==========================================================
pause
.venv\Scripts\python.exe tools\com_smoke_test.py --duration 60 %*
echo.
echo If the last lines say the run finished and read-back matched, Vissim COM works.
echo Otherwise copy this window's text and send it to Yunha.
pause
