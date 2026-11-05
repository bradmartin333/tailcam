import os
import threading
from http.server import ThreadingHTTPServer

from .camera import detect_cameras
from .config import PORT
from .server import Handler
from .thermal import log_cpu_temperature


def main():
    log_cpu_temperature()
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
