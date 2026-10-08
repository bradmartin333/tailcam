import json
import queue
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlsplit

from .config import AUDIO_RATE, HOSTS

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
        if self.path == "/healthz":  # the container's own healthcheck uses 127.0.0.1
            self._send(200, "text/plain", b"ok\n")
        elif not self._host_allowed():
            self.send_error(403)
        elif self.path == "/":
            self._index()
        elif self.path in STATIC:
            self._send(200, *STATIC[self.path])
        else:
            route, cam_id = self._route()
            handler = {"stream": self._stream, "audio": self._audio, "focus": self._focus_state}.get(route)
            if handler:
                handler(cam_id)
            else:
                self.send_error(404)

    def do_POST(self):
        route, cam_id = self._route()
        if not self._host_allowed():
            self.send_error(403)
        elif route == "focus":
            self._focus(cam_id)
        else:
            self.send_error(404)

    def _host_allowed(self):
        return not HOSTS or (self.headers.get("Host") or "").lower() in HOSTS

    def _route(self):
        """("stream", "0") for /stream/0?t=123."""
        route, _, cam_id = self.path.split("?", 1)[0].lstrip("/").partition("/")
        return route, cam_id

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _camera(self, cam_id):
        return self.cameras.get(int(cam_id)) if cam_id.isascii() and cam_id.isdigit() else None

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

    def _focus_controls(self, cam_id):
        cam = self._camera(cam_id)
        controls = cam.controls if cam else None
        if controls is None or controls.focus_limits() is None:
            self.send_error(404, "Focus control not found")
            return None
        return controls

    def _focus_state(self, cam_id):
        """Current focus as JSON; under autofocus, value is wherever the camera has moved the lens."""
        controls = self._focus_controls(cam_id)
        if controls is None:
            return
        try:
            focus = controls.focus()
        except OSError as e:
            self.send_error(503, f"Reading focus failed: {e}")
            return
        self._send(200, "application/json", json.dumps(focus).encode())

    def _focus(self, cam_id):
        """Form body with either value=<n> (manual focus) or auto=0|1."""
        controls = self._focus_controls(cam_id)
        if controls is None:
            return
        # The body is a CORS "simple request", so any page could send it from a viewer's browser.
        # Browsers always send Origin on a POST; only accept our own.
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.send_error(403)
            return
        length = self.headers.get("Content-Length", "0")
        if not (length.isascii() and length.isdigit() and int(length) <= 1024):
            self.send_error(400)
            return
        try:
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(length)).decode()).items()}
            if "auto" in form and controls.has_autofocus:
                controls.set_autofocus(form["auto"] == "1")
            elif "value" in form:
                controls.set_focus(int(form["value"]))
            else:
                self.send_error(400)
                return
        except (UnicodeDecodeError, ValueError):
            self.send_error(400)
            return
        except OSError as e:
            self.send_error(503, f"Setting focus failed: {e}")
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


def focus_bar(cam):
    """The focus slider (and autofocus toggle, if the camera has one), shown under the selected feed."""
    limits = cam.controls.focus_limits() if cam.controls else None
    if limits is None:
        return ""
    try:
        focus = cam.controls.focus()
    except OSError:
        # Still show the bar; the page polls the real state once it's selected.
        focus = {**limits, "value": limits["min"], "auto": False if cam.controls.has_autofocus else None}
    auto = ""
    if focus["auto"] is not None:
        checked = " checked" if focus["auto"] else ""
        auto = f'<label><input type="checkbox" class="af"{checked}> auto</label>'
    return (
        f'<div class="focus"><input type="range" aria-label="Focus" min="{focus["min"]}" max="{focus["max"]}"'
        f' step="{focus["step"]}" value="{focus["value"]}">{auto}</div>'
    )
