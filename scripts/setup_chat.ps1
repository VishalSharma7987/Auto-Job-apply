# First-time setup chat (Windows / PowerShell).
# Runs the bot in LISTEN mode: it answers your Telegram messages immediately (no GitHub run per message).
#   1. run this script           .\scripts\setup_chat.ps1
#   2. in Telegram: send your resume PDF, then /setup (fill the template and send it back in ONE message)
#   3. send /done (or press Ctrl+C) when finished
# Secrets are read from .env. If the Cloudflare relay webhook is set, it is paused during the session and put back on exit
# (needs TELEGRAM_WEBHOOK_SECRET in .env - the same value as the Worker's).
param(
  [double]$MaxSeconds = 0,   # optional time limit in seconds (0 = until /done or Ctrl+C)
  [switch]$Force             # continue even if the webhook secret is unknown
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Creating virtualenv and installing dependencies (one time)..."
  python -m venv (Join-Path $root ".venv")
  & $py -m pip install --upgrade pip
  & $py -m pip install -e ".[dev]"
}
if (-not (Test-Path (Join-Path $root ".env"))) { throw ".env not found - copy .env.example to .env and fill SUPABASE_*, TELEGRAM_* first." }

$argsList = @("-m", "jobagent", "telegram", "--listen")
if ($MaxSeconds -gt 0) { $argsList += @("--max-seconds", "$MaxSeconds") }
if ($Force) { $argsList += "--force" }
$env:PYTHONIOENCODING = "utf-8"
& $py @argsList
exit $LASTEXITCODE
