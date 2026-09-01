@echo off
REM Start the translation companion on Windows. Same job as run.sh.
setlocal
cd /d "%~dp0"

if not exist .env (
  echo No .env yet. Do this first:
  echo.
  echo     copy server\.env.example server\.env
  echo     rem then edit it: OPENAI_API_KEY=sk-...   and TARGET_LANG=^<your language^>
  echo.
  exit /b 1
)

if not exist .venv (
  echo First run: creating the virtual environment...
  python -m venv .venv || (echo Could not create a virtual environment. Is Python installed? & exit /b 1)
)

.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\python -m pip install --quiet -r requirements.txt

echo Companion running. Leave this window open; Ctrl-C stops it.
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
