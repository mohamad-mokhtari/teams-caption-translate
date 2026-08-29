#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
[ -f .env ] || { echo "No .env — copy .env.example to .env and add a key."; exit 1; }
# 127.0.0.1 only: this listens next to the browser, and should not be on the network.
exec ./.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8100
