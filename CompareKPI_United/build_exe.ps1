param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($Python)) {
    $LocalPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $LocalPython) {
        $Python = $LocalPython
    } else {
        $Python = "python"
    }
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name CompareKPI_UI `
    --add-data "templates;templates" `
    compare_kpi_gui.py

$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    throw "PyInstaller failed with exit code $ExitCode. Try: python -m pip install pyinstaller"
}

$ExePath = Join-Path $ProjectRoot "dist\CompareKPI_UI.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "PyInstaller finished but EXE was not found: $ExePath"
}

Write-Host ""
Write-Host "EXE generated:" -ForegroundColor Green
Write-Host $ExePath
Write-Host ""
Write-Host "When launched from this dist folder, reports will be generated under the project reports folder."
Write-Host "If you copy the EXE elsewhere, reports will be generated under a reports folder next to that EXE."
