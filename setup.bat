@echo off
chcp 65001 >nul 2>nul
title FireClip - Setup

echo ============================================
echo   FireClip Portable Environment Setup
echo ============================================
echo.

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

echo [1/5] Checking Python environment...
echo.

if exist "python-embed\python.exe" (
    echo [OK] Embedded Python found: python-embed\python.exe
    set "PYTHON_EXE=%PROJECT_ROOT%python-embed\python.exe"
    goto :check_pip
)

if exist "venv\Scripts\python.exe" (
    echo [OK] Virtual env found: venv\Scripts\python.exe
    set "PYTHON_EXE=%PROJECT_ROOT%venv\Scripts\python.exe"
    goto :check_pip
)

where python >nul 2>&1
if %errorlevel%==0 (
    echo [OK] System Python found
    for /f "delims=" %%i in ('where python') do (
        set "PYTHON_EXE=%%i"
        goto :found_python
    )
)

:found_python
echo.
echo [!] No Python environment detected
echo.
echo Please choose installation method:
echo   1. Download embedded Python (recommended, ~25MB)
echo   2. Use system Python (requires Python 3.10+)
echo   3. Exit
echo.
set /p choice="Enter option (1/2/3): "

if "%choice%"=="1" goto :download_embedded
if "%choice%"=="2" goto :use_system_python
if "%choice%"=="3" exit /b 0
goto :invalid_choice

:download_embedded
echo.
echo [2/5] Downloading embedded Python...
echo.

if not exist "python-embed" mkdir "python-embed"

set "PYTHON_VERSION=3.11.9"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip"
set "PYTHON_ZIP=python-embed\python-embed.zip"

echo Downloading Python %PYTHON_VERSION%...
echo URL: %PYTHON_URL%
echo.

powershell -Command "& {$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_ZIP%'}"

if not exist "%PYTHON_ZIP%" (
    echo [ERROR] Download failed, check network connection
    pause
    exit /b 1
)

echo Extracting...
powershell -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath 'python-embed' -Force"
del "%PYTHON_ZIP%"

if not exist "python-embed\python.exe" (
    echo [ERROR] Extraction failed
    pause
    exit /b 1
)

set "PYTHON_EXE=%PROJECT_ROOT%python-embed\python.exe"
echo [OK] Python installed to: python-embed\

echo.
echo [3/5] Installing pip...
echo.

for %%f in (python-embed\python*._pth) do (
    echo Modifying %%f to enable site...
    powershell -Command "(Get-Content '%%f') -replace '#import site', 'import site' | Set-Content '%%f'"
)

echo Downloading get-pip.py...
powershell -Command "& {$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'get-pip.py'}"

if not exist "get-pip.py" (
    echo [ERROR] Failed to download get-pip.py
    pause
    exit /b 1
)

echo Installing pip...
"%PYTHON_EXE%" get-pip.py --no-warn-script-location

if %errorlevel% neq 0 (
    echo [ERROR] pip installation failed
    pause
    exit /b 1
)

echo [OK] pip installed successfully
goto :install_requirements

:check_pip
echo.
echo [2/5] Checking pip...

"%PYTHON_EXE%" -m pip --version >nul 2>&1
if %errorlevel%==0 (
    echo [OK] pip available
    goto :install_requirements
)

echo pip not available, installing...

if exist "python-embed\python.exe" (
    for %%f in (python-embed\python*._pth) do (
        powershell -Command "(Get-Content '%%f') -replace '#import site', 'import site' | Set-Content '%%f'"
    )
    
    if not exist "get-pip.py" (
        powershell -Command "& {$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'get-pip.py'}"
    )
    
    "%PYTHON_EXE%" get-pip.py --no-warn-script-location
)

goto :install_requirements

:use_system_python
echo.
echo Using system Python: %PYTHON_EXE%
goto :install_requirements

:install_requirements
echo.
echo [4/5] Installing dependencies...
echo.

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found
    pause
    exit /b 1
)

echo Installing dependencies (this may take a few minutes)...
"%PYTHON_EXE%" -m pip install -r requirements.txt --no-warn-script-location

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Some dependencies failed to install
    echo Check network connection or install manually
    pause
    exit /b 1
)

echo [OK] Dependencies installed successfully

echo.
echo [5/5] Configuring FFmpeg...
echo.

if exist "ffmpeg\bin\ffmpeg.exe" (
    echo [OK] FFmpeg configured: ffmpeg\bin\ffmpeg.exe
    goto :setup_complete
)

if exist "..\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe" (
    echo FFmpeg detected: ..\ffmpeg-8.1.1-essentials_build\
    echo Copy to project directory? (recommended)
    set /p copy_ffmpeg="Enter y to copy, any other key to skip: "
    
    if /i "%copy_ffmpeg%"=="y" (
        if not exist "ffmpeg\bin" mkdir "ffmpeg\bin"
        copy "..\ffmpeg-8.1.1-essentials_build\bin\*.exe" "ffmpeg\bin\" >nul
        echo [OK] FFmpeg copied to: ffmpeg\bin\
    )
    goto :setup_complete
)

echo [WARNING] FFmpeg not detected
echo Please download FFmpeg and place in:
echo   - %PROJECT_ROOT%ffmpeg\bin\ffmpeg.exe
echo   - %PROJECT_ROOT%..\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe
echo Or add FFmpeg to system PATH
goto :setup_complete

:setup_complete
echo.
echo ============================================
echo   Setup Complete!
echo ============================================
echo.
echo Python: %PYTHON_EXE%
echo.
echo Run run.bat to start FireClip
echo.
pause
exit /b 0

:invalid_choice
echo Invalid option
pause
exit /b 1
