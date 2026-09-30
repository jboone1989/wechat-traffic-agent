#!/usr/bin/env bash
set -euo pipefail

VENV="${CONTENT_BROWSER_USE_VENV:-/home/ubuntu/browser-use-venv}"
PYTHON="${CONTENT_BROWSER_USE_BOOTSTRAP_PYTHON:-python3}"

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install "browser-use==0.13.10"
"$VENV/bin/browser-use" --doctor
