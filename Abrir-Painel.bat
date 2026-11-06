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

echo Abrindo o painel do Zelador no navegador...
echo Deixe esta janela aberta enquanto usa o painel. Para fechar, feche esta janela.
echo.
".venv\Scripts\python.exe" -m zelador painel
pause
