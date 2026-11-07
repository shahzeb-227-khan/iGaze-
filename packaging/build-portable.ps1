param([string]$Python)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $project '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
$env:PYINSTALLER_CONFIG_DIR = Join-Path $project 'build\pyinstaller-cache'
Push-Location -LiteralPath $project
try {
    & $Python -m PyInstaller --noconfirm --onedir --windowed --name iGAZE `
        --specpath build --workpath build\portable --distpath dist `
        --collect-all mediapipe --add-data 'face_landmarker.task:.' `
        --add-data 'igaze_cal.json:.' main.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

    $app = Join-Path $project 'dist\iGAZE\iGAZE.exe'
    $check = Start-Process -FilePath $app -ArgumentList '--check-install' -WindowStyle Hidden -Wait -PassThru
    if ($check.ExitCode -ne 0) { throw "Portable runtime check failed: $($check.ExitCode)" }

    $output = Join-Path $project 'dist\iGAZE-Windows-x64-Portable.zip'
    if (Test-Path -LiteralPath $output) {
        throw "Archive already exists. Move it before rebuilding: $output"
    }
    Compress-Archive -LiteralPath (Join-Path $project 'dist\iGAZE') -DestinationPath $output
    Get-FileHash -LiteralPath $output -Algorithm SHA256
} finally {
    Pop-Location
}
