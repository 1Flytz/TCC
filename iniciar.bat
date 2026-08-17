@echo off
title PyConfer
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   O ambiente ainda nao foi preparado.
    echo   Execute primeiro o arquivo  instalar.bat
    echo.
    pause
    exit /b 1
)

echo ==========================================================
echo   PyConfer
echo ==========================================================
echo.
echo   A aplicacao vai abrir no navegador, em:
echo.
echo       http://localhost:8000
echo.
echo   Para encerrar, feche esta janela ou pressione Ctrl+C.
echo ==========================================================
echo.

start "" http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn api.main:app --port 8000

pause
