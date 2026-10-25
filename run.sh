#!/bin/sh
cd "$(dirname "$0")"
# Settings such as TAILCAM_LED_OFF can live in .env next to this script.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
# tailcam.service sets UV to uv's full path, because systemd's PATH doesn't include where uv lives.
exec "${UV:-uv}" run --with-requirements requirements.txt -m tailcam
