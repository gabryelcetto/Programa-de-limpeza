@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente virtual nao encontrado. Rode primeiro:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -e .
    pause
    exit /b 1
)

echo ============================================
echo  Zelador Files - limpeza da pasta Downloads
echo ============================================
echo.
echo [1/2] Simulacao: o que seria enviado para a Lixeira (nada e apagado agora)
echo.
".venv\Scripts\python.exe" -m zelador limpar --dry-run
echo.

choice /c SN /n /m "Enviar esses arquivos para a Lixeira de verdade? [S/N] "
if errorlevel 2 (
    echo.
    echo Cancelado. Nada foi alterado.
    pause
    exit /b 0
)

echo.
echo [2/2] Limpando...
echo.
".venv\Scripts\python.exe" -m zelador limpar
echo.
echo Pronto. Os arquivos estao na Lixeira e podem ser restaurados.
pause
