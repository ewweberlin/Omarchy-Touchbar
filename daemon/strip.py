"""The strip's state machine: what is on screen and what a touch means.

All geometry comes from layout.py so the picture and the touch zones cannot
drift. Backends (brightness, audio, stats, Hyprland) are injected so the whole
machine is testable without hardware or a running compositor.
"""

from __future__ import annotations

import time

from . import layout as L
from .actions import run_action
from .icons import IconResolver
from .render import Model
from .stats import Stats

TAP_MS = 700
TAP_SLOP = 16.0
DICTATION_SECONDS = 300.0


class Strip:
    def __init__(
        self,
        settings: dict,
        stats: Stats,
        hypr,
        audio,
        brightness,
        icons: IconResolver | None = None,
    ) -> None:
        self.settings = settings
        self.stats = stats
        self.hypr = hypr
        self.audio = audio
        self.brightness = brightness
        self.icons = icons or IconResolver()

        self.state = "home"
        self.dirty = True
        self.last_touch = time.monotonic()
        self.brightness_pct = self.brightness.read()
        self.windows: tuple[dict, ...] = ()
        self.window_scroll = 0.0
        self.dictating_until = 0.0
        self.workspaces = tuple(self.hypr.workspaces)

        self._lay = L.home(int(settings.get("workspace_count") or 5))
        self._press: dict | None = None
        self._slider: tuple[str, L.Rect, float] | None = None
        self._scroll_origin = 0.0
        self._scroll_start = 0.0
        self._pending_brightness: int | None = None
        self._pending_volume: int | None = None
        self._brightness_ts = 0.0
        self._volume_ts = 0.0

    # -- model ---------------------------------------------------------------

    def model(self, now: float | None = None) -> Model:
        now = time.monotonic() if now is None else now
        return Model(
            state=self.state,
            workspaces=self.workspaces,
            workspace_count=len(self._lay.workspaces),
            stats=self.stats.sample.as_dict(),
            brightness=self.brightness_pct,
            volume=self.audio.volume,
            muted=self.audio.muted,
            windows=self.windows,
            window_scroll=self.window_scroll,
            dictating=now < self.dictating_until,
        )

    # -- external changes ----------------------------------------------------

    def sync_workspaces(self) -> None:
        workspaces = tuple(self.hypr.workspaces)
        if workspaces != self.workspaces:
            self.workspaces = workspaces
            self.dirty = True

    def refresh_windows(self, *, force: bool = False) -> None:
        icon_enabled = bool(self.settings.get("window_icons", True))
        windows = tuple(
            {
                "address": window.get("address") or "",
                "title": window.get("title") or window.get("class") or "Window",
                "active": bool(window.get("active")),
                "icon": self.icons.data_uri(window.get("class") or "")
                if icon_enabled
                else None,
            }
            for window in self.hypr.windows
        )
        if windows == self.windows and not force:
            return
        self.windows = windows
        self.window_scroll = L.clamp_window_scroll(self.window_scroll, len(windows))
        if self.state == "windows":
            self.dirty = True

    def notify_theme(self) -> None:
        self.icons.refresh_theme()
        self.refresh_windows(force=True)
        self.dirty = True

    # -- drawers -------------------------------------------------------------

    def open(self, state: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if state == "windows":
            self.refresh_windows(force=True)
        if state != self.state:
            self.state = state
            self.dirty = True
        self.last_touch = now

    def close(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self.state != "home":
            self.state = "home"
            self.dirty = True
        self.last_touch = now

    def tick(self, now: float) -> None:
        idle_ms = int(self.settings.get("idle_close_ms") or 0)
        if self.state != "home" and idle_ms > 0 and (now - self.last_touch) * 1000 > idle_ms:
            self.close(now)

    # -- touch ---------------------------------------------------------------

    def touch_down(self, x: float, y: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.last_touch = now
        if self.state != "home" and x < L.DRAWER_X:
            hit, outside = L.hit_home(self._lay, x, y), True
        else:
            hit, outside = self._drawer_hit(x, y), False
        self._press = {"hit": hit, "x": x, "y": y, "t": now, "outside": outside,
                       "scroll": self.window_scroll}

        if not hit:
            return
        kind, value = hit
        if kind == "drawer_brightness":
            self._slider = ("brightness", L.TRACK1, 0.0)
            self._set_brightness(float(value), now)
        elif kind == "drawer_volume":
            self._slider = ("volume", L.TRACK2, 0.0)
            self._set_volume(float(value), now)
        elif kind == "control" and value == "brightness":
            self._slider = ("brightness", self._lay.brightness, 2.0)
            self._set_brightness(L.clamped_fraction(self._lay.brightness, x, 2), now)
        elif kind == "control" and value == "volume":
            self._slider = ("volume", self._lay.volume, 2.0)
            self._set_volume(L.clamped_fraction(self._lay.volume, x, 2), now)
        elif kind == "window":
            self._slider = ("scroll", L.Rect(0, 0, 0, 0), 0.0)
            self._scroll_origin = x
            self._scroll_start = self.window_scroll

    def touch_move(self, x: float, y: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.last_touch = now
        if self._slider is None:
            return
        kind, rect, pad = self._slider
        if kind == "scroll":
            delta = self._scroll_origin - x
            scroll = L.clamp_window_scroll(self._scroll_start + delta, len(self.windows))
            if scroll != self.window_scroll:
                self.window_scroll = scroll
                self.dirty = True
            return
        fraction = L.clamped_fraction(rect, x, pad)
        if kind == "brightness":
            self._set_brightness(fraction, now)
        else:
            self._set_volume(fraction, now)

    def touch_up(self, x: float, y: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.last_touch = now
        press = self._press
        self._press = None
        if self._slider is not None:
            kind = self._slider[0]
            self._slider = None
            if kind == "brightness":
                self._apply_brightness(now, force=True)
                return
            if kind == "volume":
                self._apply_volume(now, force=True)
                return
            # A tap on a window card still selects it; only a real drag scrolls.
            if press and press["hit"] and press["hit"][0] == "window":
                moved = abs(x - press["x"]) + abs(y - press["y"])
                if moved <= TAP_SLOP and (now - press["t"]) * 1000 <= TAP_MS:
                    self._tap(press["hit"], False, now)
            return
        if not press or not press["hit"]:
            if press and press["outside"]:
                self.close(now)
            return
        moved = abs(x - press["x"]) + abs(y - press["y"])
        held_ms = (now - press["t"]) * 1000
        if moved <= TAP_SLOP and held_ms <= TAP_MS:
            self._tap(press["hit"], bool(press["outside"]), now)

    def flush(self, now: float) -> None:
        self._apply_brightness(now)
        self._apply_volume(now)

    # -- internals -----------------------------------------------------------

    def _drawer_hit(self, x: float, y: float):
        if self.state == "controls":
            return L.hit_controls(x, y)
        if self.state == "windows":
            return L.hit_windows(x, y, len(self.windows), self.window_scroll)
        if self.state == "vitals":
            return L.hit_vitals(x, y, 6)
        return L.hit_home(self._lay, x, y)

    def _tap(self, hit: tuple[str, object], outside: bool, now: float) -> None:
        kind, value = hit
        if kind == "menu":
            self.close(now)
            run_action("menu")
            return
        if kind == "workspace":
            index = int(value)
            workspace_id = int(self.settings.get("workspace_first") or 1) + index
            active = bool(
                index < len(self.workspaces) and self.workspaces[index].get("active")
            )
            if active and self.state == "windows":
                self.close(now)
            elif active:
                self.open("windows", now)
            else:
                self.hypr.switch_workspace(workspace_id)
                self.close(now)
            return
        if kind == "action":
            self.close(now)
            if value == "dictation":
                # Omarchy exposes dictation as a toggle, so the lit state has
                # to toggle too; the timeout only clears it if the user never
                # taps again to stop recording.
                if now < self.dictating_until:
                    self.dictating_until = 0.0
                else:
                    self.dictating_until = now + DICTATION_SECONDS
                self.dirty = True
            run_action(str(value))
            return
        if kind == "system":
            if self.state == "vitals":
                self.close(now)
            else:
                self.open("vitals", now)
            return
        if kind == "control":
            self.last_touch = now
            if value in ("sun", "expand"):
                if self.state == "controls":
                    self.close(now)
                else:
                    self.open("controls", now)
            elif value in ("speaker", "mute"):
                self.audio.toggle_mute()
                self.dirty = True
            return
        if kind == "drawer_close":
            self.close(now)
            return
        if kind == "drawer_mute":
            self.audio.toggle_mute()
            self.dirty = True
            return
        if kind == "window":
            index = int(value)
            if index < len(self.windows):
                self.hypr.focus_window(self.windows[index]["address"])
            self.close(now)
            return
        if kind == "vitals_card":
            self.last_touch = now
            return
        if kind == "drawer_background":
            self.close(now)
            return
        if outside:
            self.close(now)

    def _set_brightness(self, fraction: float, now: float) -> None:
        percent = max(1, min(100, round(fraction * 100)))
        if percent == self.brightness_pct:
            return
        self.brightness_pct = percent
        self._pending_brightness = percent
        self.dirty = True
        self._apply_brightness(now)

    def _set_volume(self, fraction: float, now: float) -> None:
        step = max(1, int(self.settings.get("slider_step") or 1))
        percent = max(0, min(100, round(fraction * 100 / step) * step))
        if percent == self.audio.volume:
            return
        self._pending_volume = percent
        self.dirty = True
        self._apply_volume(now)

    def _apply_brightness(self, now: float, force: bool = False) -> None:
        if self._pending_brightness is None:
            return
        if not force and now - self._brightness_ts < 0.04:
            return
        percent, self._pending_brightness = self._pending_brightness, None
        self._brightness_ts = now
        self.brightness.set(percent)

    def _apply_volume(self, now: float, force: bool = False) -> None:
        if self._pending_volume is None:
            return
        if not force and now - self._volume_ts < 0.06:
            return
        percent, self._pending_volume = self._pending_volume, None
        self._volume_ts = now
        self.audio.set_volume(percent)


__all__ = ["Strip", "TAP_MS", "TAP_SLOP"]
