import ctypes
import fcntl
import os
import sys
import threading
import uuid
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

# UVC extension unit access, for vendor controls the driver doesn't map to V4L2 controls.
UVC_SET_CUR = 0x01
UVC_GET_CUR = 0x81
UVC_GET_LEN = 0x85

# Newer Logitech webcams (C920, CrystalCam, ...) keep the status LED in their "peripheral" extension
# unit, the one libwebcam/uvcdynctrl maps as "LED1 Mode". Selector 9 is 5 bytes and byte 1 is the
# mode: 0 off, 1 on, 2 blink, 3 auto (lit while streaming, the default).
LOGITECH_PERIPHERAL = uuid.UUID("ffe52d21-8030-4e2c-82d9-f587d00540bd")
LOGITECH_LED = 9
LOGITECH_LED_LEN = 5


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


class XuQuery(ctypes.Structure):
    _fields_ = [("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8), ("query", ctypes.c_uint8),
                ("size", ctypes.c_uint16), ("data", ctypes.c_void_p)]


UVCIOC_CTRL_QUERY = 0xC0000000 | (ctypes.sizeof(XuQuery) << 16) | (ord("u") << 8) | 0x21


def xu_query(fd, unit, selector, query, data):
    """Run one UVC extension unit request; `data` (a ctypes byte array) is sent and/or filled in."""
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, XuQuery(unit, selector, query, len(data), ctypes.addressof(data)))


def extension_units(index):
    """{guid: unit id} for the UVC extension units of /dev/video<index>'s USB device, read from its raw descriptors."""
    usb_dev = os.path.dirname(os.path.realpath(f"/sys/class/video4linux/video{index}/device"))
    with open(f"{usb_dev}/descriptors", "rb") as f:
        d = f.read()
    units = {}
    i = 0
    while i + 20 <= len(d) and d[i]:
        # CS_INTERFACE / VC_EXTENSION_UNIT: bLength, type, subtype, bUnitID, guidExtensionCode[16], ...
        if d[i + 1] == 0x24 and d[i + 2] == 0x06 and d[i] >= 24:
            units[uuid.UUID(bytes_le=d[i + 4:i + 20])] = d[i + 3]
        i += d[i]
    return units


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
        # Without such a mapping, Logitech LEDs are still reachable through the extension unit directly.
        try:
            self.logitech_led = extension_units(index).get(LOGITECH_PERIPHERAL)
        except OSError:
            self.logitech_led = None

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

    def has_led(self):
        return bool(self.leds) or self.logitech_led is not None

    def _logitech_led_off(self, fd):
        length = (ctypes.c_uint8 * 2)()
        xu_query(fd, self.logitech_led, LOGITECH_LED, UVC_GET_LEN, length)
        if int.from_bytes(bytes(length), "little") != LOGITECH_LED_LEN:
            raise OSError(f"unexpected LED control size {bytes(length).hex()}")
        led = (ctypes.c_uint8 * LOGITECH_LED_LEN)()
        xu_query(fd, self.logitech_led, LOGITECH_LED, UVC_GET_CUR, led)
        if led[1] != 0:
            led[1] = 0
            xu_query(fd, self.logitech_led, LOGITECH_LED, UVC_SET_CUR, led)

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
        if self.logitech_led is not None:
            try:
                with self._open() as fd:
                    self._logitech_led_off(fd)
            except OSError as e:
                print(f"{self.path}: turning the Logitech LED off failed: {e}", flush=True)

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
