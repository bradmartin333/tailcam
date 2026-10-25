#!/bin/sh
cd "$(dirname "$0")"
# Settings such as TAILCAM_LED_OFF can live in .env next to this script.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
# systemd's PATH leaves out ~/.local/bin, where uv's installer puts it.
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
exec "$UV" run --with-requirements requirements.txt -m tailcam
