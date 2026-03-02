#!/usr/bin/env bash
# start.sh — Start the PT Generator web app
# Usage: bash start.sh [port] [--no-reload]

set -e

PORT=${1:-8000}
RELOAD="--reload"

for arg in "$@"; do
  case $arg in
    --no-reload) RELOAD="" ;;
  esac
done

if [ ! -f ".env" ]; then
  echo "Warning: .env file not found."
  echo "Create one with: echo 'ANTHROPIC_API_KEY=your_key_here' > .env"
  exit 1
fi

echo "Starting PT Generator on http://127.0.0.1:${PORT}"
echo "Press Ctrl+C to stop."
echo

python -m uvicorn app.web.server:app --host 127.0.0.1 --port "$PORT" $RELOAD
