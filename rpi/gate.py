"""Runs tailcam.service only while the door sensor's circuit is closed, i.e. while a Raspberry Pi GPIO is
pulled to ground by a hall effect sensor's output or a reed switch.

It talks to the kernel's GPIO character device through ioctls, so it needs nothing beyond python3. It sleeps
in poll() until the pin changes, so it uses no CPU while the door stays put. After a change it waits until
the pin has been steady for TAILCAM_GATE_SETTLE seconds, then reads the pin's level (not the edges it saw)
and starts or stops the service to match. A door opened and closed quickly therefore causes at most one
start or stop, and the service always ends up matching the door, however the edges arrived.

Runs as root (see tailcam-gate.service), since it calls systemctl and opens /dev/gpiochip*.
"""
import ctypes
import fcntl
import glob
import os
import select
import subprocess

UNIT = "tailcam.service"
# Set in tailcam-gate.service, not tailcam's .env: they're the gate's settings, not the app's.
PIN = int(os.environ.get("TAILCAM_GATE_PIN", "17"))  # BCM number, e.g. 17 is physical pin 11
SETTLE = float(os.environ.get("TAILCAM_GATE_SETTLE", "2"))
RETRY = 30  # seconds between attempts after a start or stop fails
# Kernel-side debounce, which drops a reed switch's contact bounce and short noise spikes on a long wire
# before they wake us. The settle time above handles anything slower.
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
                    raise SystemExit(f"gate: can't read {path}'s lines ({e}); the gate needs Linux 5.10 or newer")
                if info.name == name:
                    return path, offset
        finally:
            os.close(fd)
    raise SystemExit(f"gate: no GPIO line named {name.decode()}")


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


def systemctl(verb):
    """Run `systemctl <verb> UNIT`, logging anything it says on stderr (e.g. a daemon-reload warning).
    True if it worked."""
    result = subprocess.run(["systemctl", verb, UNIT], stderr=subprocess.PIPE, text=True)
    err = result.stderr.strip()
    if result.returncode:
        print(f"gate: systemctl {verb} {UNIT} failed (exit {result.returncode}): {err or 'no message'}", flush=True)
    elif err:
        print(f"gate: systemctl {verb} {UNIT}: {err}", flush=True)
    return result.returncode == 0


def run(line, settle, control):
    """Start or stop the service to match the pin (`control` is systemctl, or a fake in tests), then sleep
    until the pin has changed and settled. Starting a running service or stopping a stopped one does
    nothing, so this is safe to repeat. A failed start or stop is retried every RETRY seconds; otherwise
    nothing happens until the door moves, so a service stopped by hand stays stopped until then (and
    tailcam.service's own Restart= covers crashes). Edges that arrive while systemctl runs wake the next
    wait() at once."""
    while True:
        closed = line.closed()
        print(f"gate: circuit {'closed, starting' if closed else 'open, stopping'} {UNIT}", flush=True)
        ok = control("start" if closed else "stop")
        if line.wait(None if ok else RETRY):
            while line.wait(settle):
                pass


def main():
    run(Line(*find_line(f"GPIO{PIN}".encode())), SETTLE, systemctl)


if __name__ == "__main__":
    main()
