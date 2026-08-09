@echo off
chcp 65001 >nul 2>nul
title FireClip

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

set "PYTHON_EXE="

if exist "python-embed\python.exe" (
    set "PYTHON_EXE=%PROJECT_ROOT%python-embed\python.exe"
    goto :found_python
)

if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=%PROJECT_ROOT%venv\Scripts\python.exe"
    goto :found_python
)

where python >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('where python') do (
        set "PYTHON_EXE=%%i"
        goto :found_python
    )
)

echo [ERROR] Python not found
echo Please run setup.bat first or install Python 3.10+
echo.
pause
exit /b 1

:found_python
echo [INFO] Python: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" -c "import PyQt6" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Dependencies not installed, run setup.bat first
    echo.
    set /p run_setup="Install now? (y/n): "
    if /i "%run_setup%"=="y" (
        call setup.bat
    ) else (
        exit /b 1
    )
)

set "PYTHONPATH=%PROJECT_ROOT%"
set "PYTHONIOENCODING=utf-8"

if not exist "models_cache" mkdir "models_cache"
set "HF_HOME=%PROJECT_ROOT%models_cache\huggingface"
set "TRANSFORMERS_CACHE=%PROJECT_ROOT%models_cache\huggingface"
set "XDG_CACHE_HOME=%PROJECT_ROOT%models_cache"

echo [INFO] Starting FireClip...
echo.
"%PYTHON_EXE%" main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Program exited with code %errorlevel%
    echo Check logs in: logs\
    pause
)
