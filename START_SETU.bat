@echo off
setlocal enabledelayedexpansion

title SETU - Emergency Information Fusion Engine

echo ================================================================================
echo   SETU - Structured Emergency Bridge System
echo   SIH 2026 Disaster Management Demo
echo   Tagline: "Bridging Chaotic Emergency Reports to Structured Incident Intelligence"
echo ================================================================================
echo.

REM 1. Determine base directory
set "BASE_DIR=%~dp0"
set "BASE_DIR=%BASE_DIR:~0,-1%"

REM 2. Check required files
if exist "%BASE_DIR%\backend\main.py" goto :backend_files_ok
echo [SETU ERROR] Backend source files not found at:
echo   %BASE_DIR%\backend\main.py
echo Please make sure the demo package is extracted completely.
echo.
pause
exit /b 1
:backend_files_ok

if exist "%BASE_DIR%\frontend\dist\index.html" goto :frontend_files_ok
echo [SETU ERROR] Frontend production bundle not found at:
echo   %BASE_DIR%\frontend\dist\index.html
echo Please run "npm run build" in frontend or use the prepared release package.
echo.
pause
exit /b 1
:frontend_files_ok

REM 3. Detect Bundled Zero-Install Python Runtime
set "PYTHON_EXE=%BASE_DIR%\runtime\python.exe"

if exist "%PYTHON_EXE%" goto :runtime_ready

echo [SETU ERROR] Bundled portable Python runtime not found at:
echo   %PYTHON_EXE%
echo This zero-install judge package requires the bundled runtime directory.
echo Please ensure the release archive is extracted completely.
echo.
pause
exit /b 1

:runtime_ready
set "PYTHON_ARGS=-s -m uvicorn main:app --host 127.0.0.1 --port 8000"

REM 4. Detect and configure offline embedding model
set "HF_HOME=%BASE_DIR%\model"

if exist "%HF_HOME%\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2" goto :model_ready
if exist "%HF_HOME%\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2" goto :model_ready

echo [SETU ERROR] Offline embedding model not found in package.
echo Expected in:
echo   %HF_HOME%\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2
echo.
pause
exit /b 1

:model_ready

REM 5. Check whether port 8000 is occupied
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 1 } else { exit 0 }"
if not errorlevel 1 goto :port_clear

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = (Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2).status; if ($r -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 2 }"
if errorlevel 1 goto :port_in_use

echo [SETU] SETU is already running and ready on port 8000.
echo [SETU] Opening default browser at http://127.0.0.1:8000...
start http://127.0.0.1:8000
echo.
echo Press any key to exit launcher (the existing background instance will keep running).
pause >nul
exit /b 0

:port_in_use
echo [SETU] Port 8000 is already in use by another application.
echo Please close the conflicting application before launching SETU.
echo.
pause
exit /b 1

:port_clear

REM 6. Setup logging directory
if not exist "%BASE_DIR%\logs" mkdir "%BASE_DIR%\logs"
set "LOG_OUT=%BASE_DIR%\logs\backend.log"
set "LOG_ERR=%BASE_DIR%\logs\backend.err"
set "PID_FILE=%BASE_DIR%\logs\backend.pid"

if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>&1

REM 7. Launch FastAPI in judge mode
echo [SETU] Starting SETU Fusion Engine on http://127.0.0.1:8000...
set "SETU_JUDGE_MODE=1"
set "PYTHONUNBUFFERED=1"
set "FRONTEND_DIST_DIR=%BASE_DIR%\frontend\dist"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:HF_HOME = '%HF_HOME%'; $env:SETU_JUDGE_MODE = '1'; $env:FRONTEND_DIST_DIR = '%FRONTEND_DIST_DIR%'; $p = Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList '%PYTHON_ARGS%' -WorkingDirectory '%BASE_DIR%\backend' -RedirectStandardOutput '%LOG_OUT%' -RedirectStandardError '%LOG_ERR%' -PassThru; [System.IO.File]::WriteAllText('%PID_FILE%', $p.Id.ToString())"

if exist "%PID_FILE%" goto :pid_saved
echo [SETU] Backend failed to start.
echo See logs\backend.log for diagnostic details.
echo.
pause
exit /b 1

:pid_saved
set /p BACKEND_PID=<"%PID_FILE%"
echo [SETU] Engine started (PID: %BACKEND_PID%). Waiting for health check...

REM 8. Wait for health check readiness (up to 30 seconds)
set ATTEMPT=0

:health_check_loop
set /a ATTEMPT+=1

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = (Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 1).status; if ($r -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 2 }" >nul 2>&1
if not errorlevel 1 goto :backend_ready

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-Process -Id %BACKEND_PID% -ErrorAction SilentlyContinue; if ($p) { exit 0 } else { exit 1 }"
if errorlevel 1 goto :backend_crashed

if %ATTEMPT% geq 30 goto :backend_timeout

powershell -NoProfile -Command "Start-Sleep -Milliseconds 800" >nul 2>&1
goto :health_check_loop

:backend_crashed
echo [SETU] Backend process exited prematurely.
echo See logs\backend.log and logs\backend.err for details.
echo.
if exist "%LOG_ERR%" type "%LOG_ERR%"
if exist "%LOG_OUT%" type "%LOG_OUT%"
pause
exit /b 1

:backend_timeout
echo [SETU] Backend timed out while waiting for health check.
echo See logs\backend.log.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-Process -Id %BACKEND_PID% -Force -ErrorAction SilentlyContinue"
echo.
pause
exit /b 1

:backend_ready

echo.
echo ================================================================================
echo   [SUCCESS] SETU Emergency Fusion Engine is READY and SERVING!
echo.
echo   Local Address : http://127.0.0.1:8000
echo   Active PID    : %BACKEND_PID%
echo   Backend Log   : logs\backend.log
echo ================================================================================
echo.
echo [SETU] Opening default browser...
start http://127.0.0.1:8000

echo.
echo --------------------------------------------------------------------------------
echo   HOW TO TEST:
echo   1. In the browser, click "Process Intelligence Pipeline" in the top bar.
echo   2. Verify the clean benchmark:
echo      20 Reports fused into 13 Candidate Incidents + 1 Physical Contradiction
echo      across 5 Geographic Groupings (4 Named Zones + Outskirts/GPS).
echo.
echo   TO STOP SETU:
echo   Press [ENTER] in this window to cleanly terminate the backend engine.
echo --------------------------------------------------------------------------------
echo.

set /p "USER_STOP=Press [ENTER] to stop SETU: "

echo [SETU] Stopping SETU backend (PID %BACKEND_PID%)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-Process -Id %BACKEND_PID% -Force -ErrorAction SilentlyContinue"
echo [SETU] Stopped cleanly.
powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
exit /b 0
