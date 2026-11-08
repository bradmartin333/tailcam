#!/bin/sh
cd "$(dirname "$0")"
# Settings such as TAILCAM_LED_OFF can live in .env next to this script.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

# Copy everything tailcam prints into logs/, one file per run, keeping the newest TAILCAM_LOG_KEEP runs
# (0 turns this off). It still goes to stdout too, i.e. the journal under tailcam.service. The file is
# written as tailcam runs, so a run cut short by pulling the Pi's power keeps its log, which a volatile
# journal doesn't.
keep=${TAILCAM_LOG_KEEP:-10}
case $keep in '' | *[!0-9]*) echo "TAILCAM_LOG_KEEP must be a whole number, using 10" >&2; keep=10 ;; esac
if [ "$keep" -gt 0 ] && mkdir -p logs; then
    # Make room for this run by mtime, before its file exists: a Pi has no clock battery, so a run that
    # starts before NTP syncs can get a name that sorts earlier than the last run's.
    ls -1t logs/tailcam-*.log 2>/dev/null | tail -n "+$keep" | while read -r old; do rm -f "$old"; done
    log=logs/tailcam-$(date +%Y-%m-%d_%H-%M-%S).log
    # tee reads from a FIFO rather than a pipe so that uv can still be exec'd below: it stays the
    # service's main process, and systemd sees its exit status (a pipeline's would be tee's). tee ignores
    # SIGTERM, which systemd sends the whole service on stop, so it copies tailcam's last lines and exits
    # when they end.
    fifo=$(mktemp -d) && mkfifo "$fifo/out" && {
        (trap '' TERM; exec tee -a "$log") <"$fifo/out" &
        exec >"$fifo/out" 2>&1
        rm -r "$fifo"
    }
fi

# tailcam.service sets UV to uv's full path, because systemd's PATH doesn't include where uv lives.
exec "${UV:-uv}" run --with-requirements requirements.txt -m tailcam
