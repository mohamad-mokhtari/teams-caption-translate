@echo off
REM Build CaptionTranslator.exe on Windows. Needs Python 3.10+ on PATH.
REM Result: desktop\dist\CaptionTranslator.exe and desktop\dist\CaptionTranslator-<version>-windows.zip
REM (CI does the same on every push -- see .github/workflows/ci.yml.)
setlocal
cd /d "%~dp0"

if not exist .build-venv (
  echo Creating a build environment...
  python -m venv .build-venv || (echo Could not create a virtual environment. Is Python installed? & exit /b 1)
)
.build-venv\Scripts\python -m pip install --quiet --upgrade pip
.build-venv\Scripts\python -m pip install --quiet -r requirements.txt || exit /b 1
.build-venv\Scripts\python build.py || exit /b 1

echo.
echo Done: %~dp0dist
