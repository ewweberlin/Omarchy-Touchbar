"""Raw evdev input for the Touch Bar digitizer and the Fn key.

tiny-dfr renders for us but its own touch handling is deliberately unused: the
stretched button carries no action key, so a config reload (which clears
tiny-dfr's touch map) can never strand a pressed key. All hit-testing happens
here, which also means we see touches on every part of the canvas, including
the sliders.

The digitizer is the T2 USB device named "Apple Inc. Touch Bar Display
Touchpad". udev puts it on its own seat (seat-touchbar) so Hyprland ignores it,
which also means logind never grants a normal session an ACL for it -- bin/install
installs a rule that hands the node to the logged-in user instead.
"""

from __future__ import annotations

import fcntl
import glob
import os
import select
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CANVAS_H, CANVAS_X0, PANEL_W

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0
SYN_DROPPED = 3

ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39

KEY_FN = 464

INPUT_ABSINFO = struct.Struct("iiiiii")
INPUT_EVENT = struct.Struct("qqHHi")


def _ioc_read(kind: int, number: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (kind << 8) | number


def _eviocgname(length: int = 256) -> int:
    return _ioc_read(ord("E"), 0x06, length)


def _eviocgbit(event_type: int, length: int) -> int:
    return _ioc_read(ord("E"), 0x20 + event_type, length)


def _eviocgabs(axis: int) -> int:
    return _ioc_read(ord("E"), 0x40 + axis, INPUT_ABSINFO.size)


def _device_name(fd: int) -> str:
    buffer = bytearray(256)
    try:
        fcntl.ioctl(fd, _eviocgname(len(buffer)), buffer, True)
    except OSError:
        return ""
    return buffer.split(b"\x00", 1)[0].decode("utf-8", "replace")


def _sysfs_name(path: str) -> str:
    """The device name without opening the node.

    Most /dev/input/event* nodes belong to other devices and are not readable
    by the session; reading the name from sysfs lets discovery skip them
    silently instead of printing an error for every keyboard and power button.
    """
    name_path = Path("/sys/class/input") / Path(path).name / "device/name"
    try:
        return name_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _has_bit(fd: int, event_type: int, code: int) -> bool:
    buffer = bytearray((code // 8) + 2)
    try:
        fcntl.ioctl(fd, _eviocgbit(event_type, len(buffer)), buffer, True)
    except OSError:
        return False
    return bool(buffer[code // 8] & (1 << (code % 8)))


def _abs_info(fd: int, axis: int) -> tuple[int, int]:
    buffer = bytearray(INPUT_ABSINFO.size)
    try:
        fcntl.ioctl(fd, _eviocgabs(axis), buffer, True)
    except OSError:
        return (0, 0)
    values = INPUT_ABSINFO.unpack(buffer)
    return (values[1], values[2])


@dataclass
class Device:
    path: str
    fd: int
    name: str
    role: str
    abs_x: tuple[int, int] = (0, 0)
    abs_y: tuple[int, int] = (0, 0)


class InputHub:
    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self.digitizer: Device | None = None
        self.keyboard: Device | None = None
        self.fn_down = False
        self._last_scan = 0.0
        self._slot = 0
        self._active_slot: int | None = None
        self._tracking = False
        self._raw_x = 0
        self._raw_y = 0
        self._x = 0.0
        self._y = 0.0
        self._pending_down = False
        self._dirty_pos = False
        # True once the current contact reports any Y at all. The T2 Touch Bar
        # digitizer is a single row: it emits ABS_X and never a Y coordinate,
        # so a contact without Y is pinned to the row centre instead of the
        # raw 0 that would fall outside every padded row rect.
        self._saw_y = False
        self._warned: set[str] = set()

    # -- discovery -----------------------------------------------------------

    def rescan(self) -> None:
        self._last_scan = time.monotonic()
        if self.digitizer is None:
            found = self._find_digitizer()
            if found:
                self.digitizer = found
                print(f"touch: digitizer {found.path} ({found.name})", flush=True)
        if self.keyboard is None:
            found = self._find_keyboard()
            if found:
                self.keyboard = found
                print(f"touch: keyboard {found.path} ({found.name})", flush=True)

    def _find_digitizer(self) -> Device | None:
        for path in sorted(glob.glob("/dev/input/event*")):
            # Touch Bar nodes are the only ones worth opening here; everything
            # else is skipped before the permission-denied noise starts.
            hint = _sysfs_name(path).lower()
            if hint and "touch bar" not in hint:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                # The digitizer is the actionable failure; the Touch Bar's
                # keyboard-mode node is root-only by design and never needed.
                if "touchpad" in hint:
                    self._warn_once(path, f"cannot open {path}: {exc}")
                continue
            name = _device_name(fd)
            if "touch bar" in name.lower() and (
                _has_bit(fd, EV_ABS, ABS_MT_POSITION_X) or _has_bit(fd, EV_ABS, ABS_X)
            ):
                # The firmware advertises ABS_MT_POSITION_X/Y but only ever
                # emits ABS_X (see _handle); take each range from whichever
                # axis actually exists so calibration still works on both
                # kinds of digitizer.
                abs_x = _abs_info(fd, ABS_MT_POSITION_X)
                if abs_x[1] <= abs_x[0]:
                    abs_x = _abs_info(fd, ABS_X)
                abs_y = _abs_info(fd, ABS_MT_POSITION_Y)
                if abs_y[1] <= abs_y[0]:
                    abs_y = _abs_info(fd, ABS_Y)
                return Device(
                    path=path,
                    fd=fd,
                    name=name,
                    role="digitizer",
                    abs_x=abs_x,
                    abs_y=abs_y,
                )
            os.close(fd)
        return None

    def _find_keyboard(self) -> Device | None:
        best: tuple[int, Device] | None = None
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                # Only the built-in keyboard is actionable; the rest are other
                # users' or other seats' devices.
                if "internal keyboard" in _sysfs_name(path).lower():
                    self._warn_once(path, f"cannot open {path}: {exc}")
                continue
            name = _device_name(fd)
            has_fn = _has_bit(fd, EV_KEY, KEY_FN)
            # Some Apple firmwares emit KEY_FN without advertising it in the
            # capability bitmap, so an internal keyboard is worth keeping even
            # without the bit.
            internal = "internal keyboard" in name.lower()
            if not has_fn and not internal:
                os.close(fd)
                continue
            score = (2 if has_fn else 0) + (1 if internal else 0)
            device = Device(path=path, fd=fd, name=name, role="keyboard")
            if best is None or score > best[0]:
                if best:
                    os.close(best[1].fd)
                best = (score, device)
            else:
                os.close(fd)
        return best[1] if best else None

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        print(f"touch: {message} (run bin/install to fix permissions)", flush=True)

    def _drop(self, role: str) -> None:
        device = self.digitizer if role == "digitizer" else self.keyboard
        if device is None:
            return
        try:
            os.close(device.fd)
        except OSError:
            pass
        if role == "digitizer":
            self.digitizer = None
            self._tracking = False
            self._active_slot = None
        else:
            self.keyboard = None
        self._last_scan = 0.0

    def close(self) -> None:
        for device in (self.digitizer, self.keyboard):
            if device:
                try:
                    os.close(device.fd)
                except OSError:
                    pass
        self.digitizer = None
        self.keyboard = None

    # -- coordinate mapping --------------------------------------------------

    def _map(
        self, raw_x: int, raw_y: int, has_y: bool = True
    ) -> tuple[float, float]:
        s = self.settings
        ax = self.digitizer.abs_x if self.digitizer else (0, 1)
        ay = self.digitizer.abs_y if self.digitizer else (0, 1)
        span_x = max(1, ax[1] - ax[0])
        span_y = max(1, ay[1] - ay[0])
        fx = (raw_x - ax[0]) / span_x
        # A one-row panel has no meaningful Y; the middle of the canvas keeps
        # every line of the strip reachable.
        fy = (raw_y - ay[0]) / span_y if has_y else 0.5
        if s.get("swap_xy"):
            fx, fy = fy, fx
        if s.get("invert_x"):
            fx = 1.0 - fx
        if s.get("invert_y"):
            fy = 1.0 - fy
        scale = float(s.get("x_scale") or 1.0)
        x = fx * PANEL_W * scale - CANVAS_X0 + float(s.get("x_offset") or 0)
        y = fy * CANVAS_H + float(s.get("y_offset") or 0)
        return x, max(0.0, min(CANVAS_H - 0.001, y))

    # -- event pump ----------------------------------------------------------

    def poll(self, timeout: float = 0.02) -> list[dict]:
        now = time.monotonic()
        if (self.digitizer is None or self.keyboard is None) and now - self._last_scan > 3.0:
            self.rescan()
        fds = [d.fd for d in (self.digitizer, self.keyboard) if d]
        if not fds:
            time.sleep(timeout)
            return []
        try:
            ready, _, _ = select.select(fds, [], [], timeout)
        except (OSError, ValueError):
            self._drop_dead()
            return []
        events: list[dict] = []
        for fd in ready:
            role = None
            if self.digitizer and fd == self.digitizer.fd:
                role = "digitizer"
            elif self.keyboard and fd == self.keyboard.fd:
                role = "keyboard"
            if role is None:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            except OSError:
                self._drop(role)
                continue
            if not data:
                self._drop(role)
                continue
            events.extend(self._handle(data, role))
        return events

    def _drop_dead(self) -> None:
        for role in ("digitizer", "keyboard"):
            device = self.digitizer if role == "digitizer" else self.keyboard
            if device is None:
                continue
            try:
                os.fstat(device.fd)
            except OSError:
                self._drop(role)

    def _handle(self, data: bytes, role: str) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while offset + INPUT_EVENT.size <= len(data):
            _, _, etype, code, value = INPUT_EVENT.unpack_from(data, offset)
            offset += INPUT_EVENT.size
            if role == "keyboard":
                if etype == EV_KEY and code == KEY_FN:
                    self.fn_down = value != 0
                    out.append({"type": "fn", "down": self.fn_down})
                continue

            if etype == EV_ABS:
                if code == ABS_MT_SLOT:
                    self._slot = value
                elif code == ABS_MT_TRACKING_ID:
                    if value == -1:
                        if self._active_slot == self._slot:
                            self._tracking = False
                    elif not self._tracking:
                        self._tracking = True
                        self._active_slot = self._slot
                        self._pending_down = True
                        self._saw_y = False
                        self._raw_x = self._raw_y = 0
                elif self._tracking and code in (ABS_X, ABS_Y):
                    # Single-touch axes are not slot-tagged, but they mirror
                    # the contact the firmware considers primary, which is the
                    # one this hub tracks. The T2 panel only sends ABS_X.
                    if code == ABS_X:
                        self._raw_x = value
                    else:
                        self._raw_y = value
                        self._saw_y = True
                    self._dirty_pos = True
                elif self._slot == self._active_slot:
                    if code == ABS_MT_POSITION_X:
                        self._raw_x = value
                        self._dirty_pos = True
                    elif code == ABS_MT_POSITION_Y:
                        self._raw_y = value
                        self._saw_y = True
                        self._dirty_pos = True
                continue

            if etype != EV_SYN:
                continue
            if code == SYN_DROPPED:
                # Lost the stream: forget the current contact rather than
                # reporting a jump from stale coordinates.
                if self._tracking:
                    self._tracking = False
                    out.append({"type": "up", "x": self._x, "y": self._y})
                self._active_slot = None
                self._pending_down = False
                self._dirty_pos = False
                self._saw_y = False
                continue
            if code != SYN_REPORT:
                continue

            if self._tracking and (self._pending_down or self._dirty_pos):
                self._x, self._y = self._map(self._raw_x, self._raw_y, self._saw_y)
                if self._pending_down:
                    self._pending_down = False
                    self._dirty_pos = False
                    out.append({"type": "down", "x": self._x, "y": self._y})
                else:
                    self._dirty_pos = False
                    out.append({"type": "move", "x": self._x, "y": self._y})
            elif not self._tracking and self._active_slot is not None:
                self._active_slot = None
                out.append({"type": "up", "x": self._x, "y": self._y})
        return out
