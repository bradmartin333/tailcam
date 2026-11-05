"""CPU temperature for the logs. The IR Arducam got stuck when the Pi ran hot without its fan, so tailcam
logs the temperature at each start and whenever a camera stops streaming."""
import glob

# Linux thermal zone types that measure the CPU itself: the Pi's, and an Intel package's. On other hosts
# zone 0 can be a different sensor (acpitz, a wifi card), so the log names the zone it read.
CPU_ZONES = ("cpu-thermal", "x86_pkg_temp")


def cpu_temperature():
    """The CPU temperature as e.g. '48.2 C (cpu-thermal)', or None where Linux has no thermal zones
    (macOS, or a container without /sys). Falls back to the first zone, named, if none is a known CPU type."""
    zones = []
    for path in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        try:
            with open(f"{path}/type") as f:
                kind = f.read().strip()
            with open(f"{path}/temp") as f:
                millidegrees = int(f.read())
        except (OSError, ValueError):
            continue
        zones.append((kind, millidegrees))
    if not zones:
        return None
    kind, millidegrees = next((z for z in zones if z[0] in CPU_ZONES), zones[0])
    return f"{millidegrees / 1000:.1f} C ({kind})"


def log_cpu_temperature():
    if temp := cpu_temperature():
        print(f"cpu temperature: {temp}", flush=True)
