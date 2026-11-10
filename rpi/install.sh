#!/bin/sh
# Installs tailcam.service for this checkout, running tailcam as you, all the time.
# Run it as the user tailcam should run as (not with sudo; it asks for sudo itself):
#   rpi/install.sh gate     # serve the feeds only while the door sensor's circuit is closed
#   rpi/install.sh always   # serve them all the time, without the sensor
# Rerun it after moving the checkout or reinstalling uv, or to switch modes.
set -eu

mode=${1:-}
case $mode in gate | always) ;; *) echo "usage: $0 gate|always" >&2; exit 2 ;; esac
if [ "$(id -u)" -eq 0 ]; then
    echo "run this as the user tailcam should run as, not root; it uses sudo where needed" >&2
    exit 1
fi
if [ "$(getconf LONG_BIT)" != 64 ]; then
    echo "tailcam needs a 64-bit OS (e.g. 64-bit Raspberry Pi OS): OpenCV has no 32-bit ARM build on PyPI" >&2
    exit 1
fi
uv=$(command -v uv) || { echo "uv isn't on your PATH; install it first" >&2; exit 1; }
dir=$(cd "$(dirname "$0")/.." && pwd)
# These get pasted into sed and unit files, where characters like & | % $ and spaces mean something.
case $dir$uv in *[!A-Za-z0-9/._+-]*)
    echo "the checkout ($dir) and uv ($uv) paths may only contain letters, digits and / . _ + -" >&2
    exit 1 ;;
esac
command -v ffmpeg >/dev/null || echo "warning: ffmpeg isn't installed, so tailcam will have no audio (sudo apt install ffmpeg)" >&2
if [ "$mode" = gate ] && ! getent group gpio >/dev/null; then
    echo "there's no gpio group, which tailcam needs to read the door sensor (Raspberry Pi OS has one)" >&2
    exit 1
fi

# Earlier versions ran a root service, tailcam-gate, that started and stopped tailcam with the door. Starting
# the cameras on every door close sometimes left the IR Arducam stuck, so tailcam now runs all the time and
# reads the sensor itself. Stop the old service first, since it holds the sensor's pin.
if [ -f /etc/systemd/system/tailcam-gate.service ]; then
    sudo systemctl disable --now tailcam-gate.service
    sudo rm /etc/systemd/system/tailcam-gate.service
    sudo rm -rf /usr/local/lib/tailcam
    if [ -d /etc/systemd/system/tailcam-gate.service.d ]; then
        echo "warning: /etc/systemd/system/tailcam-gate.service.d has old gate settings; set them with" \
            "'sudo systemctl edit tailcam.service' instead, then delete that directory" >&2
    fi
fi
# Keep the webcams out of USB autosuspend (see the rule file for why). The trigger applies it to
# cameras that are already plugged in; later ones get it as they connect.
sudo install -o root -g root -m 0644 "$dir/rpi/50-tailcam-cameras.rules" /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=usb --attr-match=bInterfaceClass=0e
sed -e "s|@DIR@|$dir|g" -e "s|@USER@|$(id -un)|g" -e "s|@UV@|$uv|g" "$dir/rpi/tailcam.service" |
    sudo tee /etc/systemd/system/tailcam.service >/dev/null
# The gate's settings go in a drop-in, so `sudo systemctl edit tailcam.service` (override.conf, read after
# this file) can change them and keep the change across reinstalls.
dropin=/etc/systemd/system/tailcam.service.d/gate.conf
if [ "$mode" = gate ]; then
    sudo mkdir -p "$(dirname "$dropin")"
    sudo tee "$dropin" >/dev/null <<'UNIT'
# Written by rpi/install.sh gate. Change these with `sudo systemctl edit tailcam.service`.
[Service]
# The door sensor's BCM GPIO (17 is physical pin 11), and how long it must hold steady before feeds come back.
Environment=TAILCAM_GATE_PIN=17
Environment=TAILCAM_GATE_SETTLE=2
# To read the pin through /dev/gpiochip*.
SupplementaryGroups=gpio
UNIT
else
    sudo rm -f "$dropin"
fi
sudo systemctl daemon-reload
sudo systemctl enable tailcam.service
sudo systemctl restart tailcam.service
echo "installed for $dir as $(id -un) ($mode mode)"
