$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "No existe .venv. Sigue primero los pasos de instalacion del README.md."
}

Push-Location $projectRoot
try {
    & $python -m pip install -r .\requirements-build.txt
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudieron instalar las dependencias de compilacion."
    }

    & $python -m PyInstaller --noconfirm --clean --onefile --windowed --name Sonometro .\sonometro.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller no pudo generar el ejecutable."
    }
}
finally {
    Pop-Location
}
