@echo off
rem Run the CLI, e.g.  cli.bat --folder D:\code --repo my-project --yes
setlocal
set "PY=F:\Python311\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
"%PY%" -m gitpusher.cli %*
