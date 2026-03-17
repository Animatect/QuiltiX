@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0"

echo.
echo ============================================
echo   QuiltiX Installer
echo ============================================
echo.

:: -------------------------------------------
:: Step 1: Check / Install uv
:: -------------------------------------------
echo [1/6] Checking for uv...
where uv >nul 2>&1
if errorlevel 1 (
    echo  uv not found. Installing uv...
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 (
        echo  ERROR: Failed to install uv. Please install manually from https://docs.astral.sh/uv/
        goto :error
    )
    echo  uv installed. You may need to restart your terminal for PATH changes.
    echo  Trying to continue...
) else (
    for /f "tokens=*" %%v in ('uv --version') do echo  Found %%v
)

:: -------------------------------------------
:: Step 2: Install Python 3.11 via uv
:: -------------------------------------------
echo.
echo [2/6] Installing Python 3.11 via uv...
uv python install 3.11
if errorlevel 1 (
    echo  ERROR: Failed to install Python 3.11
    goto :error
)
echo  Python 3.11 ready.

:: -------------------------------------------
:: Step 3: Create virtual environment
:: -------------------------------------------
echo.
echo [3/6] Creating virtual environment...
if exist "%ROOT%.venv" (
    echo  Removing existing .venv...
    rmdir /s /q "%ROOT%.venv"
)
uv venv --python cpython-3.11 --python-preference managed
if errorlevel 1 (
    echo  ERROR: Failed to create virtual environment
    goto :error
)

:: Verify configparser works (catches broken Python installs)
"%ROOT%.venv\Scripts\python.exe" -c "import configparser" >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python installation is broken (missing configparser).
    echo  This usually means a non-standard Python was picked up.
    echo  Try: uv venv --python cpython-3.11 --python-preference only-managed
    goto :error
)
echo  Virtual environment created.

:: -------------------------------------------
:: Step 4: Install build tools + QuiltiX
:: -------------------------------------------
echo.
echo [4/6] Installing QuiltiX and dependencies...
uv pip install setuptools setuptools-scm
if errorlevel 1 (
    echo  ERROR: Failed to install build tools
    goto :error
)
uv pip install -e . --no-build-isolation
if errorlevel 1 (
    echo  ERROR: Failed to install QuiltiX
    goto :error
)
echo  QuiltiX installed.

:: -------------------------------------------
:: Step 5: Install OpenUSD
:: -------------------------------------------
echo.
echo [5/6] Installing OpenUSD...
uv pip install "git+https://github.com/PrismPipeline/OpenUSD_build.git@25.05.01-win-mtlx-1.39.3"
if errorlevel 1 (
    echo  ERROR: Failed to install OpenUSD. You may need to install it manually.
    echo  See README.md for instructions.
    goto :error
)
echo  OpenUSD installed.

:: -------------------------------------------
:: Step 6: Set up Arnold delegate
:: -------------------------------------------
echo.
echo [6/6] Setting up Arnold render delegate...

set "ARNOLD_DST=%ROOT%delegates\Arnold"

:: Check if Arnold is already in the local delegates folder
if exist "%ARNOLD_DST%\Arnold-*" (
    echo  Arnold delegate already set up locally.
    goto :arnold_done
)

:: Arnold not found locally — ask the user
echo  Arnold delegate not found in: %ARNOLD_DST%
echo.
echo  If you have Arnold installed elsewhere, enter the path to the Arnold
echo  delegates folder (the folder containing "Arnold-x.x.x-windows" and "hdArnold").
echo.
set "ARNOLD_USER_PATH="
set /p "ARNOLD_USER_PATH=  Arnold path (or press Enter to skip): "

if "%ARNOLD_USER_PATH%"=="" (
    goto :arnold_skip
)

:: Validate the user-provided path
if not exist "%ARNOLD_USER_PATH%" (
    echo  Path does not exist: %ARNOLD_USER_PATH%
    goto :arnold_skip
)

:: Copy from user-provided path
echo  Copying Arnold delegate from: %ARNOLD_USER_PATH%
if not exist "%ARNOLD_DST%" mkdir "%ARNOLD_DST%"
xcopy "%ARNOLD_USER_PATH%" "%ARNOLD_DST%" /e /i /q /y >nul
if errorlevel 1 (
    echo  WARNING: Failed to copy Arnold delegate. You can still run QuiltiX without Arnold.
    goto :arnold_skip
)
echo  Arnold delegate copied.
goto :arnold_done

:arnold_skip
echo.
echo  Skipping Arnold setup. QuiltiX will run without Arnold rendering.
echo  To add Arnold support later, place the SDK in:
echo    %ARNOLD_DST%\Arnold-7.x.x-windows\
echo  And the hdArnold plugin in:
echo    %ARNOLD_DST%\hdArnold\
echo.

:arnold_done

:: -------------------------------------------
:: Done
:: -------------------------------------------
echo.
echo ============================================
echo   Installation complete!
echo ============================================
echo.
echo   To launch QuiltiX, run:
echo     launch_quiltix.bat
echo.
goto :end

:error
echo.
echo Installation failed. See errors above.
echo.

:end
pause
endlocal
