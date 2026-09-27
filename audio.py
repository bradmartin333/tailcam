import glob
import os
import queue
import re
import shutil
import subprocess
import sys
import threading

from config import AUDIO_RATE


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
    """Owns one mic; ffmpeg runs only while someone is listening and its raw PCM output fans out to all listeners."""

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
                    [FFMPEG, "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", *self.input_args,
                     "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-flush_packets", "1", "-"],
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
                        pass  # slow listener; it hears a dropout instead of falling behind
        proc.wait()
        with self.lock:
            if self.proc is proc:
                print(f"audio {self.name}: ffmpeg exited ({proc.returncode})", flush=True)
                self.proc = None


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


def audio_finder():
    """Return a function mapping a camera index to its AudioSource (or None) on this platform."""
    if FFMPEG is None:
        print("warning: ffmpeg not found, audio disabled", flush=True)
        return lambda i: None
    if sys.platform == "darwin":
        devices = list_avfoundation_devices()
        return lambda i: find_avfoundation_audio(i, devices)
    return find_alsa_audio
