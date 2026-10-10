#!/usr/bin/env bash
# One-step local setup (macOS / Linux): virtualenv, avp with all optional providers, Chromium for diagrams.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
"$PY" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install --use-pep517 -e '.[anthropic,gemini,dev]'
python -m playwright install chromium
command -v ffmpeg >/dev/null || echo "!! ffmpeg not found — install it (brew install ffmpeg / apt install ffmpeg)"
[ -f .env ] || cp .env.example .env
echo
echo "done. next:"
echo "  source .venv/bin/activate"
echo "  edit .env (API keys for the providers you use)"
echo "  avp doctor channels/econ-history"
echo "  pytest            # offline, free"
