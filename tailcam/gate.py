"""The door sensor gate: while TAILCAM_GATE_PIN is set, feeds and mics are served only while the sensor's
circuit is closed, i.e. while a Raspberry Pi GPIO is pulled to ground by a hall effect sensor's output or a
reed switch. It works like a shutter over the lenses: the cameras keep running, but nothing is sent.

It talks to the kernel's GPIO character device through ioctls, so it needs nothing beyond the standard
library, and access to /dev/gpiochip* (the gpio group on Raspberry Pi OS). A thread sleeps in poll() until
the pin changes, so it uses no CPU while the door stays put.
"""
import ctypes
import fcntl
import glob
import os
import select
import threading
import time

from .config import GATE_PIN, GATE_SETTLE

RETRY = 30  # seconds between attempts to open the pin, or to read it after a failed read
# Kernel-side debounce, which drops a reed switch's contact bounce and short noise spikes on a long wire
# before they wake us. The settle time handles anything slower.
DEBOUNCE_US = 20_000

# Linux GPIO uAPI v2, from include/uapi/linux/gpio.h.
FLAG_ACTIVE_LOW = 1 << 1
FLAG_INPUT = 1 << 2
FLAG_EDGE_RISING = 1 << 4
FLAG_EDGE_FALLING = 1 << 5
FLAG_BIAS_PULL_UP = 1 << 8
ATTR_ID_DEBOUNCE = 3
EVENT_SIZE = 48  # struct gpio_v2_line_event


class ChipInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 32), ("label", ctypes.c_char * 32), ("lines", ctypes.c_uint32)]


class LineAttribute(ctypes.Structure):
    # `value` is a union of flags, output values and debounce_period_us (a u32 in the low bytes).
    _fields_ = [("id", ctypes.c_uint32), ("padding", ctypes.c_uint32), ("value", ctypes.c_uint64)]


class LineConfigAttribute(ctypes.Structure):
    _fields_ = [("attr", LineAttribute), ("mask", ctypes.c_uint64)]


class LineConfig(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("num_attrs", ctypes.c_uint32), ("padding", ctypes.c_uint32 * 5),
                ("attrs", LineConfigAttribute * 10)]


class LineRequest(ctypes.Structure):
    _fields_ = [("offsets", ctypes.c_uint32 * 64), ("consumer", ctypes.c_char * 32), ("config", LineConfig),
                ("num_lines", ctypes.c_uint32), ("event_buffer_size", ctypes.c_uint32),
                ("padding", ctypes.c_uint32 * 5), ("fd", ctypes.c_int32)]


class LineInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 32), ("consumer", ctypes.c_char * 32), ("offset", ctypes.c_uint32),
                ("num_attrs", ctypes.c_uint32), ("flags", ctypes.c_uint64), ("attrs", LineAttribute * 10),
                ("padding", ctypes.c_uint32 * 4)]


class LineValues(ctypes.Structure):
    _fields_ = [("bits", ctypes.c_uint64), ("mask", ctypes.c_uint64)]


assert (ctypes.sizeof(ChipInfo), ctypes.sizeof(LineRequest), ctypes.sizeof(LineInfo)) == (68, 592, 256)


def _ioc(direction, nr, struct):
    return direction << 30 | ctypes.sizeof(struct) << 16 | 0xB4 << 8 | nr


GPIO_GET_CHIPINFO_IOCTL = _ioc(2, 0x01, ChipInfo)
GPIO_V2_GET_LINEINFO_IOCTL = _ioc(3, 0x05, LineInfo)
GPIO_V2_GET_LINE_IOCTL = _ioc(3, 0x07, LineRequest)
GPIO_V2_LINE_GET_VALUES_IOCTL = _ioc(3, 0x0E, LineValues)


def find_line(name):
    """(chip path, offset) of the GPIO line called `name`, e.g. b"GPIO17". Every Pi names its header pins
    this way, but which chip they're on differs (gpiochip0, or gpiochip4 on a Pi 5 with an older kernel)."""
    for path in sorted(glob.glob("/dev/gpiochip*")):
        fd = os.open(path, os.O_RDONLY)
        try:
            chip = ChipInfo()
            fcntl.ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, chip)
            for offset in range(chip.lines):
                info = LineInfo(offset=offset)
                try:
                    fcntl.ioctl(fd, GPIO_V2_GET_LINEINFO_IOCTL, info)
                except OSError as e:
                    raise OSError(e.errno, f"can't read {path}'s lines ({e.strerror}); the gate needs Linux 5.10 or newer")
                if info.name == name:
                    return path, offset
        finally:
            os.close(fd)
    raise LookupError(f"no GPIO line named {name.decode()}")


class Line:
    """One input pin with the internal pull-up on, so the sensor only has to pull it to ground. It reads
    as active (closed) while low."""

    def __init__(self, chip, offset):
        req = LineRequest(num_lines=1, consumer=b"tailcam-gate")
        req.offsets[0] = offset
        req.config.flags = FLAG_INPUT | FLAG_ACTIVE_LOW | FLAG_BIAS_PULL_UP | FLAG_EDGE_RISING | FLAG_EDGE_FALLING
        req.config.num_attrs = 1
        req.config.attrs[0].attr.id = ATTR_ID_DEBOUNCE
        req.config.attrs[0].attr.value = DEBOUNCE_US
        req.config.attrs[0].mask = 1  # applies to the request's first (only) line
        fd = os.open(chip, os.O_RDONLY)
        try:
            fcntl.ioctl(fd, GPIO_V2_GET_LINE_IOCTL, req)
        finally:
            os.close(fd)  # the line's own fd stays valid
        self.fd = req.fd
        self.poll = select.poll()
        self.poll.register(self.fd, select.POLLIN)

    def closed(self):
        values = LineValues(mask=1)
        fcntl.ioctl(self.fd, GPIO_V2_LINE_GET_VALUES_IOCTL, values)
        return bool(values.bits & 1)

    def wait(self, timeout=None):
        """True once the pin has changed, False if `timeout` seconds pass first. Discards the events."""
        if not self.poll.poll(None if timeout is None else timeout * 1000):
            return False
        os.read(self.fd, EVENT_SIZE * 16)
        return True


class Gate:
    """Whether feeds may be served (`closed`), following one pin.

    It errs toward off: `closed` stays False until the pin has been read, while the pin can't be opened or
    read, and from the first edge after a closed circuit. Feeds come back only once the pin has held steady
    for `settle` seconds and reads closed. So a door opened even briefly cuts the feeds at once, and a
    bouncing or flaky sensor keeps them off rather than on."""

    def __init__(self, open_line, settle):
        self.closed = False
        self._open_line = open_line  # returns a Line; a fake in tests
        self._settle = settle
        threading.Thread(target=self._run, daemon=True).start()

    def _set(self, closed, why):
        if closed != self.closed:
            self.closed = closed
            print(f"gate: circuit {why}, feeds {'on' if closed else 'off'}", flush=True)

    def _line(self):
        error = None
        while True:
            try:
                return self._open_line()
            except (OSError, LookupError) as e:
                if str(e) != error:  # log once per reason, not every RETRY seconds
                    print(f"gate: can't open GPIO{GATE_PIN} ({e}), feeds off; retrying every {RETRY} s", flush=True)
                    error = str(e)
            time.sleep(RETRY)

    def _run(self):
        line = self._line()
        while True:
            try:
                closed = line.closed()
            except OSError as e:
                print(f"gate: can't read GPIO{GATE_PIN} ({e})", flush=True)
                self._set(False, "unreadable")
                line.wait(RETRY)
                continue
            self._set(closed, "closed" if closed else "open")
            line.wait()
            self._set(False, "changed")
            while line.wait(self._settle):
                pass


def start_gate():
    """The Gate for TAILCAM_GATE_PIN, or None if it's unset (feeds are always served)."""
    if GATE_PIN is None:
        return None
    print(f"gate: feeds follow GPIO{GATE_PIN}", flush=True)
    return Gate(lambda: Line(*find_line(f"GPIO{GATE_PIN}".encode())), GATE_SETTLE)
