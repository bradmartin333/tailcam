import ctypes
import fcntl
import os
import sys
import threading
from contextlib import contextmanager

# Linux-only V4L2 control access via ioctl, so the image needs no v4l-utils. Elsewhere (macOS dev)
# controls_for() returns None and the page simply shows no controls.

VIDIOC_G_CTRL = 0xC008561B
VIDIOC_S_CTRL = 0xC008561C
VIDIOC_QUERYCTRL = 0xC0445624
VIDIOC_QUERYMENU = 0xC02C5625

CTRL_FLAG_NEXT_CTRL = 0x80000000
CTRL_FLAG_DISABLED = 0x0001
CTRL_TYPE_MENU = 3
CTRL_TYPE_CTRL_CLASS = 6

CID_FOCUS_ABSOLUTE = 0x009A090A
CID_FOCUS_AUTO = 0x009A090C


class QueryCtrl(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint32), ("type", ctypes.c_uint32), ("name", ctypes.c_char * 32),
        ("minimum", ctypes.c_int32), ("maximum", ctypes.c_int32), ("step", ctypes.c_int32),
        ("default_value", ctypes.c_int32), ("flags", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 2),
    ]


class Control(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("value", ctypes.c_int32)]


class QueryMenu(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("id", ctypes.c_uint32), ("index", ctypes.c_uint32), ("name", ctypes.c_char * 32),
                ("reserved", ctypes.c_uint32)]


class Controls:
    """The V4L2 controls of /dev/video<index>, plus the values we've set, so they can be reapplied
    if the device is reopened. Values live only in memory, i.e. for the container's uptime."""

    def __init__(self, index):
        self.path = f"/dev/video{index}"
        self.lock = threading.Lock()
        self.wanted = {}  # cid -> value, in the order they were set
        with self._open() as fd:
            self.available = {q.id: q for q in self._enumerate(fd)}
            # Status LEDs aren't a standard V4L2 control. They show up only when the driver or a
            # host-side mapping (uvcdynctrl for Logitech) exposes one, named like "LED1 Mode".
            self.leds = {cid: self._off_value(fd, q) for cid, q in self.available.items() if b"led" in q.name.lower()}

    @contextmanager
    def _open(self):
        fd = os.open(self.path, os.O_RDWR)
        try:
            yield fd
        finally:
            os.close(fd)

    def _enumerate(self, fd):
        q = QueryCtrl(id=CTRL_FLAG_NEXT_CTRL)
        while True:
            try:
                fcntl.ioctl(fd, VIDIOC_QUERYCTRL, q)
            except OSError:
                return
            if q.type != CTRL_TYPE_CTRL_CLASS and not q.flags & CTRL_FLAG_DISABLED:
                yield QueryCtrl.from_buffer_copy(q)
            q = QueryCtrl(id=q.id | CTRL_FLAG_NEXT_CTRL)

    def _off_value(self, fd, q):
        """The menu entry named "Off" if there is one, otherwise the control's minimum."""
        if q.type == CTRL_TYPE_MENU:
            for i in range(q.minimum, q.maximum + 1):
                m = QueryMenu(id=q.id, index=i)
                try:
                    fcntl.ioctl(fd, VIDIOC_QUERYMENU, m)
                except OSError:
                    continue
                if m.name.lower() == b"off":
                    return i
        return q.minimum

    def names(self):
        return [q.name.decode(errors="replace") for q in self.available.values()]

    def get(self, cid):
        with self._open() as fd:
            c = Control(id=cid)
            fcntl.ioctl(fd, VIDIOC_G_CTRL, c)
            return c.value

    def set(self, cid, value):
        with self.lock:
            with self._open() as fd:
                fcntl.ioctl(fd, VIDIOC_S_CTRL, Control(id=cid, value=value))
            self.wanted.pop(cid, None)
            self.wanted[cid] = value

    def apply(self):
        """Turn the LEDs off and reapply every value set so far, e.g. after the device was reopened."""
        with self.lock:
            settings = {**self.leds, **self.wanted}
        for cid, value in settings.items():
            try:
                with self._open() as fd:
                    fcntl.ioctl(fd, VIDIOC_S_CTRL, Control(id=cid, value=value))
            except OSError as e:
                print(f"{self.path}: setting control {cid:#x} failed: {e}", flush=True)

    def focus(self):
        """{min, max, step, value, auto} for the focus slider, or None if the camera can't focus manually."""
        q = self.available.get(CID_FOCUS_ABSOLUTE)
        if q is None:
            return None
        try:
            value = self.get(CID_FOCUS_ABSOLUTE)
            auto = bool(self.get(CID_FOCUS_AUTO)) if CID_FOCUS_AUTO in self.available else None
        except OSError:
            return None
        return {"min": q.minimum, "max": q.maximum, "step": q.step or 1, "value": value, "auto": auto}

    def set_focus(self, value):
        q = self.available[CID_FOCUS_ABSOLUTE]
        value = min(max(int(value), q.minimum), q.maximum)
        if CID_FOCUS_AUTO in self.available:
            self.set(CID_FOCUS_AUTO, 0)  # manual focus is ignored (or refused) while autofocus is on
        self.set(CID_FOCUS_ABSOLUTE, value)

    def set_autofocus(self, on):
        self.set(CID_FOCUS_AUTO, 1 if on else 0)
        if on:
            with self.lock:
                self.wanted.pop(CID_FOCUS_ABSOLUTE, None)  # don't reapply a stale manual value over autofocus


def controls_for(index):
    if not sys.platform.startswith("linux"):
        return None
    try:
        return Controls(index)
    except OSError as e:
        print(f"camera {index}: can't read controls: {e}", flush=True)
        return None
