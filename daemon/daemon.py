"""Main loop: input, sampling, theme reloads and publishing the SVG.

The expensive part of a repaint is not drawing (that is a few milliseconds of
string building); it is tiny-dfr re-reading /etc/tiny-dfr/config.toml and
re-parsing the SVG with librsvg. So repaints are coalesced, skipped when the
document did not change, and suppressed while Fn is held.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from . import config, theme
from .actions import Audio, Brightness
from .hypr import Hyprland
from .icons import IconResolver
from .input import InputHub
from .render import Renderer
from .stats import Stats
from .strip import Strip

KEEPALIVE_SECONDS = 5.0
PUBLISH_RETRY_SECONDS = 30.0
PAINT_RETRY_SECONDS = 2.0
BRIGHTNESS_POLL_SECONDS = 2.0
TOUCH_BACKLIGHT = Path("/sys/class/backlight/appletb_backlight")
LID_STATE = Path("/proc/acpi/button/lid")


def _stat_sig(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _write_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Write via tmp + os.replace.

    tiny-dfr watches the config path with IN_MOVED_TO, so an in-place write
    would never be seen. The SVG stays world-readable because tiny-dfr renders
    it as `nobody`.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


class Daemon:
    def __init__(self, managed: bool = False, verbose: bool = False) -> None:
        self.managed = managed
        self.verbose = verbose
        self.running = True
        self.force_theme = False
        # Remember the launching process. QML is not always PID 1's child after
        # a killed supervisor, and a user-session subreaper would defeat a
        # getppid() == 1 check.
        self._parent_pid = os.getppid()

        self.settings = config.load_settings()
        self.palette = theme.load_palette()
        self.theme_sig = theme.theme_signature()
        self.refresh_sig = _stat_sig(config.REFRESH_FILE)
        self.renderer = Renderer(
            self.palette,
            self.settings["font_family"],
            bool(self.settings.get("action_labels", False)),
            self.settings.get("action_icon_size", 34),
        )

        self.stats = Stats()
        self.stats.refresh()
        self.hypr = Hyprland(
            int(self.settings.get("workspace_count") or 5),
            int(self.settings.get("workspace_first") or 1),
        )
        self.hypr.refresh()
        self.audio = Audio()
        self.audio.refresh(force=True)
        self.brightness = Brightness()
        self.icons = IconResolver()
        self.strip = Strip(
            self.settings,
            self.stats,
            self.hypr,
            self.audio,
            self.brightness,
            self.icons,
        )
        self.strip.sync_workspaces()
        self.strip.refresh_windows(force=True)
        self.input = InputHub(self.settings)

        self.last_svg = ""
        self.next_paint = 0.0
        self.retry_at = 0.0
        self.next_keepalive = 0.0
        self.next_brightness_poll = 0.0
        self.next_parent_check = 0.0

        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGUSR1, self._on_signal)

    # -- lifecycle -----------------------------------------------------------

    def _on_signal(self, signum, _frame) -> None:
        if signum == signal.SIGUSR1:
            self.force_theme = True
            self.strip.dirty = True
        else:
            self.running = False

    def run(self) -> None:
        config.ensure_dirs()
        self.hypr.start()
        self.input.rescan()
        print(
            f"touchbar: started, canvas {config.CANVAS_W}x{config.CANVAS_H} "
            f"at panel x={config.CANVAS_X0}",
            flush=True,
        )
        try:
            while self.running:
                now = time.monotonic()
                events = self.input.poll(self._timeout(now))
                if events:
                    self._handle(events, now)
                self._maybe_theme()
                self._maybe_parent(now)
                self._sample(now)
                self.strip.tick(now)
                self.strip.flush(now)
                self._keepalive(now)
                if self.strip.dirty and now >= self.next_paint:
                    self._paint(now)
        finally:
            self.input.close()
            self.hypr.stop()
            print("touchbar: stopped", flush=True)

    # -- input ---------------------------------------------------------------

    def _handle(self, events: list[dict], now: float) -> None:
        for event in events:
            kind = event.get("type")
            if kind == "fn":
                if not event.get("down"):
                    # tiny-dfr is back on the media layer; make sure it shows
                    # our latest state rather than a frame from before Fn.
                    self.strip.dirty = True
                continue
            if self.input.fn_down:
                # The F-key layer is up and tiny-dfr is handling touches itself.
                continue
            self._wake(now)
            if self.verbose:
                # The most useful line when calibrating: what the kernel
                # reported and where the strip thinks that lands.
                print(
                    f"touchbar: {kind} x={event['x']:.0f} y={event['y']:.0f}",
                    flush=True,
                )
            if kind == "down":
                self.strip.touch_down(event["x"], event["y"], now)
            elif kind == "move":
                self.strip.touch_move(event["x"], event["y"], now)
            elif kind == "up":
                self.strip.touch_up(event.get("x", 0.0), event.get("y", 0.0), now)

    def _wake(self, now: float) -> None:
        """A touch means the user is looking: refresh everything before drawing."""
        if now - self.strip.last_touch > 5.0:
            self.stats.refresh()
            if self.hypr.refresh():
                self.strip.sync_workspaces()
                self.strip.refresh_windows(force=True)
            self.audio.refresh(force=True)
            self.strip.brightness_pct = self.brightness.read()
        self.strip.dirty = True

    def _timeout(self, now: float) -> float:
        idle = now - self.strip.last_touch
        if self.strip.state != "home" or idle < self._busy_seconds():
            return 0.02
        return 0.15

    # -- periodic work -------------------------------------------------------

    def _busy_seconds(self) -> float:
        return float(self.settings.get("busy_seconds") or 30)

    def _sleep_seconds(self) -> float:
        return float(self.settings.get("sleep_seconds") or 300)

    def _sample(self, now: float) -> None:
        idle = now - self.strip.last_touch
        if idle > self._sleep_seconds() and self.strip.state == "home":
            return

        if idle < self._busy_seconds():
            interval = 1.0
        else:
            interval = 5.0
        self.stats.interval = interval
        if self.stats.maybe_refresh(now):
            self.strip.dirty = True

        max_age = 4.0 if idle < self._busy_seconds() else 60.0
        if self.hypr.maybe_refresh(now, max_age=max_age):
            self.strip.sync_workspaces()
            self.strip.refresh_windows()
            self.strip.dirty = True

        if idle < self._busy_seconds():
            self.audio.refresh()

        if now >= self.next_brightness_poll:
            self.next_brightness_poll = now + BRIGHTNESS_POLL_SECONDS
            if idle < self._busy_seconds():
                percent = self.brightness.read()
                if percent != self.strip.brightness_pct:
                    self.strip.brightness_pct = percent
                    self.strip.dirty = True

    def _maybe_theme(self) -> None:
        sig = theme.theme_signature()
        refresh = _stat_sig(config.REFRESH_FILE)
        if not self.force_theme and sig == self.theme_sig and refresh == self.refresh_sig:
            return
        self.force_theme = False
        self.theme_sig = sig
        self.refresh_sig = refresh
        self.palette = theme.load_palette()
        self.renderer = Renderer(
            self.palette,
            self.settings["font_family"],
            bool(self.settings.get("action_labels", False)),
            self.settings.get("action_icon_size", 34),
        )
        self.strip.notify_theme()
        print("touchbar: theme changed", flush=True)

    def _maybe_parent(self, now: float) -> None:
        if not self.managed or now < self.next_parent_check:
            return
        self.next_parent_check = now + 2.0
        if os.getppid() != self._parent_pid:
            print("touchbar: supervisor exited", flush=True)
            self.running = False

    def _keepalive(self, now: float) -> None:
        """Stop tiny-dfr's idle blanking from eating the first tap.

        tiny-dfr dims after 30s without input and turns the bar off after 60s.
        It only writes the backlight when its own idea of the value changes, so
        resetting the sysfs value behind its back sticks until the next
        transition. The display belongs to the user while the lid is open.
        """
        if now < self.next_keepalive:
            return
        self.next_keepalive = now + KEEPALIVE_SECONDS
        if not self._lid_open():
            return
        current = _read_int(TOUCH_BACKLIGHT / "brightness")
        maximum = _read_int(TOUCH_BACKLIGHT / "max_brightness")
        if current is None or maximum is None or current >= maximum:
            return
        try:
            subprocess.run(
                ["brightnessctl", "-d", "appletb_backlight", "set", str(maximum)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    @staticmethod
    def _lid_open() -> bool:
        try:
            states = list(LID_STATE.glob("*/state"))
        except OSError:
            return True
        if not states:
            return True
        try:
            return "open" in states[0].read_text(encoding="ascii")
        except OSError:
            return True

    # -- painting ------------------------------------------------------------

    def _paint(self, now: float, force: bool = False) -> None:
        if self.input.fn_down and not force:
            return
        svg = self.renderer.render(self.strip.model(now))
        if svg == self.last_svg and not force:
            self.strip.dirty = False
            return
        if not self._publish(svg, now):
            # Keep the frame pending, but do not rerender and retry the atomic
            # writes on every 20ms loop pass when, say, /etc/tiny-dfr is not
            # writable yet.
            self.next_paint = now + PAINT_RETRY_SECONDS
            return
        self.last_svg = svg
        self.strip.dirty = False
        self.next_paint = now + 1.0 / max(1, int(self.settings.get("max_fps") or 12))
        if self.verbose:
            print(f"touchbar: painted {len(svg)} bytes", flush=True)

    def _publish(self, svg: str, now: float) -> bool:
        try:
            _write_atomic(config.SVG_PATH, svg.encode("utf-8"), 0o644)
            _write_atomic(config.TINY_DFR_CONFIG, config.config_text().encode("utf-8"), 0o644)
        except OSError as exc:
            if now >= self.retry_at:
                self.retry_at = now + PUBLISH_RETRY_SECONDS
                print(
                    f"touchbar: cannot publish to {config.TINY_DFR_DIR}: {exc}\n"
                    "touchbar: run bin/install to fix permissions",
                    flush=True,
                )
            return False
        return True


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="omarchy-touchbar")
    parser.add_argument("--managed", action="store_true", help="exit when the supervisor does")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    daemon = Daemon(managed=args.managed, verbose=args.verbose)
    daemon._paint(time.monotonic(), force=True)
    daemon.run()
    return 0
