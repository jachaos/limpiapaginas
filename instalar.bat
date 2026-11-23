@echo off
rem Instala LimpiaPaginas en Windows. Usa "instalar.bat -Cpu" para no instalar la parte de GPU.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
pause
