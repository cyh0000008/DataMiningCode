param(
    [string]$Python = "D:\Data_Mining\CompareKPI_United\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python not found: $Python"
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name BadCaseImageReport `
    --add-data "config.json;." `
    bad_case_image_report.py

$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    throw "PyInstaller failed with exit code $ExitCode"
}

$ExePath = Join-Path $ProjectRoot "dist\BadCaseImageReport.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "PyInstaller finished but EXE was not found: $ExePath"
}

Write-Host ""
Write-Host "EXE generated:" -ForegroundColor Green
Write-Host $ExePath
