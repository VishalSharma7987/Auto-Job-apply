# Local runner (Windows / PowerShell). Usage:
#   .\scripts\run_local.ps1                 # full run using .env (DRY_RUN default true)
#   .\scripts\run_local.ps1 -Mode status
#   .\scripts\run_local.ps1 -Fake           # offline demo: fake jobs, fake LLM, sqlite, dry run
param(
  [string]$Mode = "full",
  [switch]$Fake,
  [switch]$Setup
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Creating virtualenv..."
  python -m venv $venv
  $Setup = $true
}
if ($Setup) {
  & $py -m pip install --upgrade pip
  & $py -m pip install -e ".[dev]"
  & $py -m playwright install chromium
}
if ($Fake) {
  $env:FAKE_MODE = "true"; $env:DRY_RUN = "true"; $env:DB_BACKEND = "sqlite"
}
$env:PYTHONIOENCODING = "utf-8"
& $py -m jobagent run --mode $Mode
exit $LASTEXITCODE
