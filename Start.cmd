@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0runtime\electron\electron.exe" (
  echo [Error] Missing runtime\electron\electron.exe
  echo Please use the complete Portable package or read README.md.
  pause
  exit /b 1
)

if not exist "%~dp0runtime\python\python.exe" (
  echo [Error] Missing runtime\python\python.exe
  echo Please use the complete Portable package or read README.md.
  pause
  exit /b 1
)

start "Qwen PDF Layout Translator" "%~dp0runtime\electron\electron.exe" "%~dp0gui"
endlocal
