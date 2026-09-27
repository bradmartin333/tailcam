import glob
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

PORT = int(os.environ.get("TAILCAM_PORT", "8080"))
MAX_CAMERAS = int(os.environ.get("TAILCAM_MAX_CAMERAS", "10"))
JPEG_QUALITY = int(os.environ.get("TAILCAM_JPEG_QUALITY", "80"))
BOUNDARY = "frame"


def find_ffmpeg():
    """Prefer a system ffmpeg (Docker installs one with ALSA); fall back to the pip-bundled build for macOS dev."""
    if exe := shutil.which("ffmpeg"):
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


FFMPEG = find_ffmpeg()


class AudioSource:
    """Owns one mic; ffmpeg runs only while someone is listening and its MP3 output fans out to all listeners."""

    def __init__(self, name, input_args):
        self.name = name
        self.input_args = input_args
        self.listeners = set()
        self.proc = None
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=64)
        with self.lock:
            self.listeners.add(q)
            if self.proc is None:
                self.proc = subprocess.Popen(
                    [FFMPEG, "-hide_banner", "-loglevel", "error", *self.input_args,
                     "-ac", "1", "-b:a", "64k", "-f", "mp3", "-"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                )
                threading.Thread(target=self._run, args=(self.proc,), daemon=True).start()
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.listeners.discard(q)
            if not self.listeners and self.proc is not None:
                self.proc.terminate()
                self.proc = None

    def _run(self, proc):
        while chunk := proc.stdout.read1(4096):
            with self.lock:
                for q in self.listeners:
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:
                        pass  # slow listener; MP3 decoders resync on the next frame
        proc.wait()
        with self.lock:
            if self.proc is proc:
                print(f"audio {self.name}: ffmpeg exited ({proc.returncode})", flush=True)
                self.proc = None


class Camera:
    """Owns one capture device; a single reader thread fans frames out to all viewers."""

    def __init__(self, index, cap, audio=None):
        self.index = index
        self.cap = cap
        self.audio = audio
        self.frame = None
        self.cond = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        failures = 0
        while True:
            ok, img = self.cap.read()
            if not ok:
                failures += 1
                time.sleep(0.1)
                if failures >= 50:
                    print(f"camera {self.index}: no frames, reopening", flush=True)
                    self.cap.release()
                    time.sleep(2)
                    self.cap = cv2.VideoCapture(self.index)
                    failures = 0
                continue
            failures = 0
            ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                with self.cond:
                    self.frame = jpeg.tobytes()
                    self.cond.notify_all()

    def wait_frame(self, last, timeout=5):
        with self.cond:
            self.cond.wait_for(lambda: self.frame is not None and self.frame is not last, timeout)
            return self.frame


def find_alsa_audio(index):
    """Linux: the ALSA card on the same USB device as /dev/video<index>, if any."""
    try:
        usb_dev = os.path.dirname(os.path.realpath(f"/sys/class/video4linux/video{index}/device"))
        for card in glob.glob("/sys/class/sound/card*"):
            if os.path.dirname(os.path.realpath(f"{card}/device")) == usb_dev:
                n = int(card.rsplit("card", 1)[1])
                return AudioSource(f"card {n}", ["-f", "alsa", "-i", f"plughw:{n},0"])
    except (OSError, ValueError):
        pass
    return None


def list_avfoundation_devices():
    """macOS: ({index: name} for video, {index: name} for audio) as ffmpeg's AVFoundation input sees them."""
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    ).stderr
    video, audio, current = {}, {}, None
    for line in out.splitlines():
        if "video devices:" in line:
            current = video
        elif "audio devices:" in line:
            current = audio
        elif current is not None and (m := re.search(r"\] \[(\d+)\] (.+)$", line)):
            current[int(m.group(1))] = m.group(2)
    return video, audio


def device_base_name(name):
    return re.sub(r"\s+(camera|microphone|mic|audio)$", "", name.strip(), flags=re.I).lower()


def find_avfoundation_audio(index, devices):
    """macOS: pair camera <index> with the mic sharing its name, e.g. "MacBook Air Camera" -> "MacBook Air Microphone"."""
    video, audio = devices
    if index not in video:
        return None
    base = device_base_name(video[index])
    for a, name in audio.items():
        if device_base_name(name) == base:
            return AudioSource(name, ["-f", "avfoundation", "-i", f":{a}"])
    return None


def detect_cameras():
    if FFMPEG is None:
        print("warning: ffmpeg not found, audio disabled", flush=True)
        find_audio = lambda i: None
    elif sys.platform == "darwin":
        devices = list_avfoundation_devices()
        find_audio = lambda i: find_avfoundation_audio(i, devices)
    else:
        find_audio = find_alsa_audio

    cameras = {}
    for i in range(MAX_CAMERAS):
        cap = cv2.VideoCapture(i)
        # Many USB webcams expose a second metadata-only /dev/video node; reading a frame filters those out.
        if cap.isOpened() and cap.read()[0]:
            audio = find_audio(i)
            cameras[i] = Camera(i, cap, audio)
            print(f"found camera {i} (audio: {audio.name if audio else 'none'})", flush=True)
        else:
            cap.release()
    if not cameras:
        print("warning: no cameras detected", flush=True)
    return cameras


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tailcam</title>
<style>
  body {{ margin: 0; background: #111; color: #eee; font-family: system-ui, sans-serif; }}
  header {{ padding: 12px 16px; font-size: 18px; }}
  main {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 480px), 1fr)); gap: 12px; padding: 0 12px 12px; }}
  figure {{ margin: 0; background: #000; border-radius: 8px; overflow: hidden; cursor: pointer; -webkit-tap-highlight-color: transparent; }}
  figure.sel {{ box-shadow: 0 0 0 3px #2ecc40; }}
  figure.sel.muted {{ box-shadow: 0 0 0 3px rgba(46, 204, 64, 0.35); }}
  figure.sel.noaudio {{ box-shadow: 0 0 0 3px #e74c3c; }}
  img {{ display: block; width: 100%; height: auto; }}
  figcaption {{ padding: 6px 10px; font-size: 14px; color: #aaa; }}
  p {{ padding: 0 16px; }}
</style>
</head>
<body>
<header>tailcam &middot; {count} camera(s)</header>
{body}
<script>{script}</script>
</body>
</html>
"""

SCRIPT = """
const audio = new Audio();
const figs = [...document.querySelectorAll("figure[data-cam]")];
let selected = null;
let muted = true;

function render() {
  for (const f of figs) f.classList.remove("sel", "muted", "noaudio");
  if (!selected) return;
  selected.classList.add("sel");
  if (selected.dataset.audio !== "1") selected.classList.add("noaudio");
  else if (muted) selected.classList.add("muted");
}

function stop() {
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
}

function apply() {
  if (muted || selected.dataset.audio !== "1") {
    stop();
  } else {
    // Fresh URL each time so playback starts live rather than from stale buffered audio.
    audio.src = `/audio/${selected.dataset.cam}?t=${Date.now()}`;
    audio.play().catch(() => { muted = true; render(); });
  }
  render();
}

// The server ends the stream if the mic stalls; show that as muted so a tap retries.
for (const ev of ["ended", "error"]) {
  audio.addEventListener(ev, () => {
    if (!muted && audio.getAttribute("src")) { muted = true; render(); }
  });
}

for (const f of figs) {
  f.addEventListener("click", () => {
    if (f === selected) muted = !muted;
    else { selected = f; muted = false; }
    apply();
  });
}

selected = figs.find(f => f.dataset.cam === "0") || figs[0] || null;
render();
"""


class Handler(BaseHTTPRequestHandler):
    cameras = {}

    def do_GET(self):
        if self.path == "/":
            self._index()
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
        html = PAGE.format(count=len(self.cameras), body=body, script=SCRIPT)
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
        self.send_header("Content-Type", "audio/mpeg")
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
