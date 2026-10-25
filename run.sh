#!/bin/sh
<<<<<<< HEAD
cd "$(dirname "$0")"
# Settings such as TAILCAM_LED_OFF can live in .env next to this script.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec /home/brad/.local/bin/uv run --with-requirements requirements.txt -m tailcam
