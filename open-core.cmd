@echo off
cd /d "%~dp0"
if not defined CORE_PORTAL_URL set /p CORE_PORTAL_URL=Your C.O.R.E. HTTPS URL (Enter for localhost): 
py -m client.web %*
if errorlevel 1 pause
