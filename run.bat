@echo off
rem Launch the GitPusher GUI without a console window.
setlocal
set "PYW=F:\Python311\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0GitPusher.pyw"
