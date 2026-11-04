# Gera dist\zelador.exe (um único arquivo, sem precisar ter Python instalado para usar).
# Ícone: assets\zelador.ico (gerado por scripts\gerar-icone.py)
# Requer: pip install pyinstaller   (e as dependências do projeto: pip install -e .)
# Uso:    powershell -File scripts\gerar-exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
# caminho absoluto: o PyInstaller resolve os relativos a partir da pasta do .spec (build\)
$icone = (Resolve-Path "assets\zelador.ico").Path

python -m PyInstaller `
    --onefile `
    --console `
    --name zelador `
    --icon $icone `
    --noconfirm --clean `
    --specpath build `
    --paths . `
    --collect-submodules send2trash.win `
    scripts\zelador_exe.py

if ($LASTEXITCODE -ne 0) { throw "O PyInstaller falhou (código $LASTEXITCODE)." }

$exe = Resolve-Path "dist\zelador.exe"
Write-Host ""
Write-Host "Pronto: $exe"
Write-Host ("SHA256: " + (Get-FileHash $exe -Algorithm SHA256).Hash)
