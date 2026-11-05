import os
import threading
from http.server import ThreadingHTTPServer

from .camera import detect_cameras
from .config import PORT
from .server import Handler


def log_temperature():
    """Log the CPU temperature, which Linux exposes on a Pi and most other boards. The IR Arducam got stuck
    on afternoons when the Pi had hit its 60 °C soft limit, so each start records it next to the camera lines."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            millidegrees = int(f.read())
    except (OSError, ValueError):
        return
    print(f"cpu temperature: {millidegrees / 1000:.1f} °C", flush=True)


def main():
    log_temperature()
    Handler.cameras = detect_cameras()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    # shutdown() waits for serve_forever() to return, so it can't run on the request's own thread.
    Handler.on_restart = lambda: threading.Thread(target=server.shutdown, daemon=True).start()
    print(f"tailcam listening on :{PORT}", flush=True)
    server.serve_forever()
    # Only reached through POST /restart. tailcam.service's Restart=always, or Docker's restart policy,
    # starts it again. os._exit because the camera threads sit in blocking OpenCV calls, which could hold
    # up a normal interpreter shutdown; the OS closes the devices either way.
    print("tailcam: exiting to restart", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
