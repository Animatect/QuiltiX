@echo off
set "ROOT=%~dp0"
set "PATH=%ROOT%.venv\Lib\site-packages\pxr\bin;%ROOT%.venv\Lib\site-packages\pxr\lib;%PATH%"
uv run python "%ROOT%tools\entry.py"
if errorlevel 1 (
    echo.
    echo QuiltiX exited with an error.
    pause
)
