import queue
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from string import Template
from urllib.parse import parse_qs

from .config import AUDIO_RATE

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
        elif self.path == "/healthz":
            self._send(200, "text/plain", b"ok\n")
        elif self.path in STATIC:
            self._send(200, *STATIC[self.path])
        elif self.path.startswith("/stream/"):
            self._stream(self.path[len("/stream/"):].split("?", 1)[0])
        elif self.path.startswith("/audio/"):
            self._audio(self.path[len("/audio/"):].split("?", 1)[0])
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/focus/"):
            self._focus(self.path[len("/focus/"):].split("?", 1)[0])
        else:
            self.send_error(404)

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _camera(self, cam_id):
        return self.cameras.get(int(cam_id)) if cam_id.isdigit() else None

    def _index(self):
        if self.cameras:
            figures = "".join(
                f'<figure data-cam="{i}" data-audio="{1 if cam.audio else 0}">'
                f'<img src="/stream/{i}" alt="Camera {i}">{focus_bar(cam)}</figure>'
                for i, cam in self.cameras.items()
            )
            body = f"<main>{figures}</main>"
        else:
            body = "<p>No cameras detected.</p>"
        html = PAGE.substitute(body=body, audio_rate=AUDIO_RATE)
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
        empty = 0
        cam.watch()
        try:
            while True:
                frame = cam.wait_frame(frame)
                if frame is None:
                    # Nothing is written while the camera has no frames, so a departed client would
                    # never be noticed and would keep the camera awake. Give up; the page can reload.
                    empty += 1
                    if empty >= 3:
                        return
                    continue
                empty = 0
                self.wfile.write(
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode()
                    + frame
                    + b"\r\n"
                )
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            cam.unwatch()

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

    def _focus(self, cam_id):
        """Form body with either value=<n> (manual focus) or auto=0|1."""
        cam = self._camera(cam_id)
        if cam is None or cam.controls is None or cam.controls.focus() is None:
            self.send_error(404, "Focus control not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        try:
            if "auto" in form and cam.controls.focus()["auto"] is not None:
                cam.controls.set_autofocus(form["auto"] == "1")
            elif form.get("value", "").lstrip("-").isdigit():
                cam.controls.set_focus(int(form["value"]))
            else:
                self.send_error(400)
                return
        except OSError as e:
            self.send_error(500, f"Setting focus failed: {e}")
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


def focus_bar(cam):
    """The focus slider (and autofocus toggle, if the camera has one), shown under the selected feed."""
    focus = cam.controls.focus() if cam.controls else None
    if focus is None:
        return ""
    auto = ""
    if focus["auto"] is not None:
        checked = " checked" if focus["auto"] else ""
        auto = f'<label><input type="checkbox" class="af"{checked}> auto</label>'
    return (
        f'<div class="focus"><input type="range" aria-label="Focus" min="{focus["min"]}" max="{focus["max"]}"'
        f' step="{focus["step"]}" value="{focus["value"]}">{auto}</div>'
    )
