@echo off
set "ROOT=%~dp0"
set "PATH=%ROOT%.venv\Lib\site-packages\pxr\bin;%ROOT%.venv\Lib\site-packages\pxr\lib;%PATH%"
uv run python -m QuiltiX
