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
echo  Zelador Files - desfazer a ultima limpeza
echo ============================================
echo.
echo Simulacao do que seria restaurado:
echo.
".venv\Scripts\python.exe" -m zelador desfazer --dry-run
echo.

choice /c SN /n /m "Restaurar esses arquivos? [S/N] "
if errorlevel 2 (
    echo.
    echo Cancelado. Nada foi alterado.
    pause
    exit /b 0
)

echo.
".venv\Scripts\python.exe" -m zelador desfazer
echo.
pause
