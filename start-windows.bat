@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  where py >nul 2>nul && set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
  echo.
  echo ERROR: Python was not found.
  echo Install Python 3.11 or 3.12 and enable "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo.
    echo ERROR: Failed to create the virtual environment.
    pause
    exit /b 1
  )
)

echo Installing dependencies into the virtual environment...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: Dependency installation failed.
  pause
  exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul

rem Pick a free port ourselves: see groovy_runner\port.py for why Streamlit's
rem own fallback doesn't work with the localhost-only binding on Windows.
set "PORT="
for /f "usebackq delims=" %%p in (`".venv\Scripts\python.exe" -m groovy_runner.port`) do set "PORT=%%p"
if not defined PORT (
  echo.
  echo ERROR: Could not find a free port.
  pause
  exit /b 1
)

echo Starting AEM Groovy Runner on http://localhost:%PORT% ...
".venv\Scripts\python.exe" -m streamlit run app.py --server.port %PORT%

endlocal
