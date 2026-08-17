@echo off
setlocal enabledelayedexpansion
title PyConfer - Instalacao
cd /d "%~dp0"

echo ==========================================================
echo   PyConfer - instalacao das dependencias
echo ==========================================================
echo.
echo Este script instala tudo o que a aplicacao precisa.
echo Os programas podem ser instalados em qualquer pasta: a
echo aplicacao procura sozinha onde eles estao.
echo.
echo E necessario conexao com a internet.
echo.
pause
echo.

REM ---------------------------------------------------------
REM 1. winget (gerenciador de pacotes do Windows)
REM ---------------------------------------------------------
echo [1/6] Procurando o winget...
where winget >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERRO: o winget nao foi encontrado.
    echo.
    echo   Ele vem no Windows 10 e 11 atualizados, dentro do
    echo   aplicativo "Instalador de Aplicativo" da Microsoft Store.
    echo   Atualize a Microsoft Store e rode este script de novo.
    echo.
    pause
    exit /b 1
)
echo   OK
echo.

REM ---------------------------------------------------------
REM 2. Python
REM ---------------------------------------------------------
echo [2/6] Procurando o Python...
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo   Nao encontrado. Instalando...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo   ==================================================
    echo   Python instalado.
    echo.
    echo   FECHE esta janela e execute o instalar.bat de novo.
    echo   Isso e necessario para o Windows reconhecer o Python.
    echo   ==================================================
    echo.
    pause
    exit /b 0
)
echo   OK (%PY%)
echo.

REM ---------------------------------------------------------
REM 3. Tesseract (motor de OCR)
REM ---------------------------------------------------------
echo [3/6] Procurando o Tesseract-OCR...
where tesseract >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
        echo   OK ^(ja instalado^)
    ) else (
        echo   Nao encontrado. Instalando...
        winget install -e --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
    )
) else (
    echo   OK
)
echo.

REM ---------------------------------------------------------
REM 4. Poppler (converte PDF em imagem)
REM ---------------------------------------------------------
echo [4/6] Procurando o Poppler...
where pdftoppm >nul 2>&1
if errorlevel 1 (
    echo   Nao encontrado. Instalando...
    winget install -e --id oschwartz10612.Poppler --accept-source-agreements --accept-package-agreements
) else (
    echo   OK
)
echo.

REM ---------------------------------------------------------
REM 5. Ambiente virtual + bibliotecas Python
REM ---------------------------------------------------------
echo [5/6] Preparando o ambiente Python...
if not exist ".venv\Scripts\python.exe" (
    echo   Criando o ambiente virtual...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo   ERRO ao criar o ambiente virtual.
        pause
        exit /b 1
    )
)
echo   Instalando as bibliotecas...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo   ERRO ao instalar as bibliotecas. Verifique a internet.
    pause
    exit /b 1
)
echo   OK
echo.

REM ---------------------------------------------------------
REM 6. Conferencia final
REM ---------------------------------------------------------
echo [6/6] Conferindo se esta tudo no lugar...
echo.
".venv\Scripts\python.exe" -c "from core import engine; d=engine.diagnosticar(); print('   Tesseract:', 'OK  ' + d['tesseract'] if d['tesseract_ok'] else 'NAO ENCONTRADO'); print('   Poppler:  ', 'OK  ' + d['poppler'] if d['poppler_ok'] else 'NAO ENCONTRADO'); raise SystemExit(0 if d['tesseract_ok'] and d['poppler_ok'] else 1)"

if errorlevel 1 (
    echo.
    echo   ==================================================
    echo   Alguma dependencia nao foi encontrada.
    echo.
    echo   Quase sempre isso se resolve fechando esta janela
    echo   e rodando o instalar.bat mais uma vez: o Windows
    echo   so reconhece programas recem-instalados em janelas
    echo   abertas depois da instalacao.
    echo   ==================================================
    echo.
    pause
    exit /b 1
)

echo.
echo ==========================================================
echo   Instalacao concluida.
echo.
echo   Execute o arquivo  iniciar.bat  para abrir a aplicacao.
echo ==========================================================
echo.
pause
