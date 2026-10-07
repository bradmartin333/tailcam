from http.server import ThreadingHTTPServer

from .camera import detect_cameras
from .config import PORT
from .server import Handler


def main():
    Handler.cameras = detect_cameras()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"tailcam listening on :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
