# One-step local setup (Windows PowerShell): virtualenv, avp with all optional providers, Chromium for diagrams.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
py -3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'"
py -3 -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install --use-pep517 -e ".[anthropic,gemini,dev]"
python -m playwright install chromium
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Write-Host "!! ffmpeg not found - install it (winget install ffmpeg)" }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host ""
Write-Host "done. next:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  edit .env (API keys for the providers you use)"
Write-Host "  avp doctor channels/econ-history"
Write-Host "  pytest            # offline, free"
