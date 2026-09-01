#!/usr/bin/env bash
# Start the translation companion. Creates the venv and installs dependencies on
# first run, so this is the only command anyone needs.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Checked BEFORE the install, not after. Without a key there is nothing to start,
# and finding that out at the end of a two-minute pip install is a poor welcome.
if [ ! -f .env ]; then
  echo "No .env yet. Do this first:"
  echo
  echo "    cp server/.env.example server/.env"
  echo "    # then edit it: OPENAI_API_KEY=sk-...   and TARGET_LANG=<your language>"
  echo
  exit 1
fi
if ! grep -qE '^OPENAI_API_KEY=.+' .env && ! grep -qE '^PROVIDER=ollama' .env; then
  echo "server/.env has no OPENAI_API_KEY. Add one, or set PROVIDER=ollama to run locally."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: creating the virtual environment..."
  # Debian and Ubuntu ship python3 without venv; the error it gives on its own is
  # not obviously actionable.
  python3 -m venv .venv 2>/dev/null || {
    echo "Could not create a virtual environment."
    echo "On Debian/Ubuntu:  sudo apt install python3-venv"
    exit 1
  }
fi

./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

echo "Companion running. Leave this window open; Ctrl-C stops it."
# 127.0.0.1 only: this listens next to the browser and should not be on the network.
exec ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100
