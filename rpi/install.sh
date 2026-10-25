#!/bin/sh
# Installs tailcam.service and tailcam-gate.service for this checkout, running tailcam as you.
# Run it as the user tailcam should run as (not with sudo; it asks for sudo itself):
#   rpi/install.sh gate     # run tailcam only while the door sensor's circuit is closed
#   rpi/install.sh always   # run tailcam all the time, without the sensor
# Rerun it after moving the checkout or reinstalling uv, or to switch modes.
set -eu

mode=${1:-}
case $mode in gate | always) ;; *) echo "usage: $0 gate|always" >&2; exit 2 ;; esac
if [ "$(id -u)" -eq 0 ]; then
    echo "run this as the user tailcam should run as, not root; it uses sudo where needed" >&2
    exit 1
fi
uv=$(command -v uv) || { echo "uv isn't on your PATH; install it first" >&2; exit 1; }
dir=$(cd "$(dirname "$0")/.." && pwd)

for unit in tailcam tailcam-gate; do
    sed -e "s|@DIR@|$dir|g" -e "s|@USER@|$(id -un)|g" -e "s|@UV@|$uv|g" "$dir/rpi/$unit.service" |
        sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload

if [ "$mode" = gate ]; then
    sudo systemctl disable --now tailcam.service  # the gate starts it, not boot
    sudo systemctl enable tailcam-gate.service
    sudo systemctl restart tailcam-gate.service  # restart, not start, in case it ran the old unit
else
    sudo systemctl disable --now tailcam-gate.service
    sudo systemctl enable tailcam.service
    sudo systemctl restart tailcam.service
fi
echo "installed for $dir as $(id -un) ($mode mode)"
