@echo off
set "ROOT=%~dp0"
set "PATH=%ROOT%.venv\Lib\site-packages\pxr\bin;%ROOT%.venv\Lib\site-packages\pxr\lib;%PATH%"
set "QUILTIX_PLUGIN_PATHS=%ROOT%sample_plugins\prism_asset_browser"
uv run python "%ROOT%tools\entry.py"
if errorlevel 1 (
    echo.
    echo QuiltiX exited with an error.
    pause
)
