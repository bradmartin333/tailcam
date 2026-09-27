import threading
import time

import cv2

from audio import audio_finder
from config import ALWAYS_ON, IDLE_GRACE, JPEG_QUALITY, MAX_CAMERAS


class Camera:
    """Owns one capture device; a single reader thread fans frames out to all viewers.

    Unless ALWAYS_ON, the device is released once nobody has watched for IDLE_GRACE seconds
    and reopened when the next viewer arrives, so an unwatched camera costs no CPU.
    """

    def __init__(self, index, cap, audio=None):
        self.index = index
        self.cap = cap
        self.audio = audio
        self.frame = None
        self.viewers = 0
        self.idle_since = time.monotonic()
        self.cond = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def watch(self):
        with self.cond:
            self.viewers += 1
            self.cond.notify_all()

    def unwatch(self):
        with self.cond:
            self.viewers -= 1
            if self.viewers == 0:
                self.idle_since = time.monotonic()

    def _sleep_if_idle(self):
        with self.cond:
            if self.viewers or time.monotonic() - self.idle_since < IDLE_GRACE:
                return
            if self.cap is not None:
                print(f"camera {self.index}: no viewers, idling", flush=True)
                self.cap.release()
                self.cap = None
                self.frame = None  # so the next viewer waits for a live frame, not a stale one
            self.cond.wait_for(lambda: self.viewers > 0)
        print(f"camera {self.index}: viewer connected, waking", flush=True)
        self.cap = cv2.VideoCapture(self.index)

    def _run(self):
        failures = 0
        while True:
            if not ALWAYS_ON:
                self._sleep_if_idle()
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


def detect_cameras():
    find_audio = audio_finder()
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
