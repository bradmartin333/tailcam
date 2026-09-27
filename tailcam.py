import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template

from camera import detect_cameras
from config import AUDIO_RATE, PORT

BOUNDARY = "frame"
WEB = Path(__file__).parent / "web"
PAGE = Template((WEB / "index.html").read_text())
STATIC = {
    "/style.css": ("text/css; charset=utf-8", (WEB / "style.css").read_bytes()),
    "/app.js": ("text/javascript; charset=utf-8", (WEB / "app.js").read_bytes()),
}


class Handler(BaseHTTPRequestHandler):
    cameras = {}

    def do_GET(self):
        if self.path == "/":
            self._index()
        elif self.path in STATIC:
            self._send(200, *STATIC[self.path])
        elif self.path == "/healthz":
            self._send(200, "text/plain", b"ok\n")
        elif self.path.startswith("/stream/"):
            self._stream(self.path[len("/stream/"):])
        elif self.path.startswith("/audio/"):
            self._audio(self.path[len("/audio/"):].split("?", 1)[0])
        else:
            self.send_error(404)

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _camera(self, cam_id):
        return self.cameras.get(int(cam_id)) if cam_id.isdigit() else None

    def _index(self):
        if self.cameras:
            figures = "".join(
                f'<figure data-cam="{i}" data-audio="{1 if cam.audio else 0}">'
                f'<img src="/stream/{i}" alt="Camera {i}">'
                f'<figcaption>Camera {i}{"" if cam.audio else " &middot; no audio"}</figcaption></figure>'
                for i, cam in self.cameras.items()
            )
            body = f"<main>{figures}</main>"
        else:
            body = "<p>No cameras detected.</p>"
        html = PAGE.substitute(count=len(self.cameras), body=body, audio_rate=AUDIO_RATE)
        self._send(200, "text/html; charset=utf-8", html.encode())

    def _stream(self, cam_id):
        cam = self._camera(cam_id)
        if cam is None:
            self.send_error(404, "Camera not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        frame = None
        try:
            while True:
                frame = cam.wait_frame(frame)
                if frame is None:
                    continue
                self.wfile.write(
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode()
                    + frame
                    + b"\r\n"
                )
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _audio(self, cam_id):
        cam = self._camera(cam_id)
        if cam is None or cam.audio is None:
            self.send_error(404, "Audio not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = cam.audio.subscribe()
        try:
            while True:
                try:
                    self.wfile.write(q.get(timeout=10))
                except queue.Empty:
                    # ffmpeg stalled or died. Without writes we'd never notice the client leaving,
                    # so end the stream; the page shows muted and the next tap retries.
                    return
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            cam.audio.unsubscribe(q)

    def log_message(self, fmt, *args):
        pass


def main():
    Handler.cameras = detect_cameras()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"tailcam listening on :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
