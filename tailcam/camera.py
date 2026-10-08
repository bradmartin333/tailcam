import sys
import threading
import time

import cv2

from .audio import audio_finder
from .config import IDLE_GRACE, JPEG_QUALITY, MAX_CAMERAS
from .v4l2 import controls_for


class Camera:
    """Owns one capture device; a single reader thread fans frames out to all viewers.

    The device stays open and streaming for the container's whole life, because switching a webcam
    on makes it click and flash its LED. Once nobody has watched for IDLE_GRACE seconds, frames are
    only grabbed (dequeued and dropped), which costs next to no CPU. The grace period covers page reloads.
    """

    def __init__(self, index, cap, audio=None, controls=None):
        self.index = index
        self.cap = cap
        self.audio = audio
        self.controls = controls
        self.frame = None
        self.viewers = 0
        self.last_viewer = time.monotonic()
        self.cond = threading.Condition()
        if controls:
            controls.apply()
        threading.Thread(target=self._run, daemon=True).start()

    def watch(self):
        with self.cond:
            self.viewers += 1

    def unwatch(self):
        with self.cond:
            self.viewers -= 1
            self.last_viewer = time.monotonic()

    def _idle(self):
        return not self.viewers and time.monotonic() - self.last_viewer > IDLE_GRACE

    def _run(self):
        failures = 0
        was_idle = False
        while True:
            with self.cond:
                idle = self._idle()
                if idle:
                    self.frame = None  # so the next viewer waits for a fresh frame, not a stale one
            if idle != was_idle:
                print(f"camera {self.index}: {'idle' if idle else 'waking'}", flush=True)
                was_idle = idle
            ok = self.cap.grab()
            if ok and not idle:
                ok, img = self.cap.retrieve()
                jpeg = to_jpeg(img) if ok else None
                ok = jpeg is not None  # unusable frames count as failures, so a bad camera gets reopened
            if not ok:
                failures += 1
                time.sleep(0.1)
                if failures >= 50:
                    print(f"camera {self.index}: no frames, reopening", flush=True)
                    self.cap.release()
                    time.sleep(2)
                    self.cap = open_capture(self.index)
                    if self.controls:
                        self.controls.apply()
                    failures = 0
                continue
            failures = 0
            if idle:
                continue
            with self.cond:
                self.frame = jpeg
                self.cond.notify_all()

    def wait_frame(self, last, timeout=5):
        with self.cond:
            self.cond.wait_for(lambda: self.frame is not None and self.frame is not last, timeout)
            return self.frame


def open_capture(index):
    """Ask for MJPEG. Uncompressed YUYV makes Logitech webcams reserve most of a USB 2.0 bus, so a second
    camera on the same hub fails with "Not enough bandwidth", and with cameras always on both stream at once."""
    cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    # If the camera agreed, hand out its JPEGs as they are instead of decoding and re-encoding every
    # frame. Elsewhere (YUYV-only cameras, macOS) frames stay decoded and to_jpeg() encodes them.
    if int(cap.get(cv2.CAP_PROP_FOURCC)) == cv2.VideoWriter_fourcc(*"MJPG"):
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    return cap


def to_jpeg(img):
    """JPEG bytes for a retrieved frame: a raw MJPEG buffer (one row of bytes) or a decoded image."""
    if img.ndim == 3 or img.shape[0] > 1:  # decoded, color or grayscale
        ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return jpeg.tobytes() if ok else None
    jpeg = img.tobytes()
    if not jpeg.startswith(b"\xff\xd8"):
        return None
    if jpeg.endswith(b"\xff\xd9"):
        return jpeg
    # Some drivers zero-pad the buffer, and some firmware (the CrystalCam's) leaves off the
    # end-of-image marker that strict decoders want. A truncated frame looks the same as the
    # latter and gets through too; telling them apart would mean decoding every frame.
    jpeg = jpeg.rstrip(b"\0")
    return jpeg if jpeg.endswith(b"\xff\xd9") else jpeg + b"\xff\xd9"


def detect_cameras():
    find_audio = audio_finder(sys.platform)
    cameras = {}
    for i in range(MAX_CAMERAS):
        cap = open_capture(i)
        # Many USB webcams expose a second metadata-only /dev/video node; reading a frame filters those out.
        if cap.isOpened() and cap.read()[0]:
            audio = find_audio(i)
            controls = controls_for(i)
            cameras[i] = Camera(i, cap, audio, controls)
            print(f"found camera {i} (audio: {audio.name if audio else 'none'})", flush=True)
            if controls:
                # Logged so it's easy to see from `docker logs` what a camera can do, e.g. whether it has an LED control.
                print(f"camera {i} controls: {', '.join(controls.names()) or 'none'}", flush=True)
                if not controls.has_led():
                    print(f"camera {i}: no LED control found, LED left as is", flush=True)
        else:
            cap.release()
    if not cameras:
        print("warning: no cameras detected", flush=True)
    return cameras
