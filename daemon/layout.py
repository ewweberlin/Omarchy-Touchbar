"""Geometry for every Touch Bar state.

This module is the only place that knows pixel positions. The renderer draws
from these rects and the daemon hit-tests against the same rects, so a visual
and its touch zone cannot drift apart.

Canvas space is 2079x60, anchored at panel x = 91 (tiny-dfr's forced esc cell
plus its 16px gap).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CANVAS_H, CANVAS_W, MAX_WORKSPACES

# --- horizontal skeleton -----------------------------------------------------

PAD = 8.0
MENU_W = 56.0
MENU_GAP = 6.0

WS_W = 64.0
WS_GAP = 4.0

ACT_GAP = 6.0
ACT_W = 984.0

SYS_W = 236.0
SYS_GAP = 8.0
STACK_GAP = 12.0
STACK_W = (SYS_W - STACK_GAP) / 2

MENU_X = PAD
WS_X = MENU_X + MENU_W + MENU_GAP
ACT_X = 412.0
SYS_X = ACT_X + ACT_W + SYS_GAP
CTRL_X = SYS_X + SYS_W + SYS_GAP
CTRL_W = CANVAS_W - PAD - CTRL_X

DRAWER_X = ACT_X
DRAWER_W = CANVAS_W - PAD - DRAWER_X

# --- vertical skeleton -------------------------------------------------------

ROWS_Y = 0.0
ROWS_H = CANVAS_H

# --- action cells ------------------------------------------------------------

ACTION_ORDER = (
    "terminal",
    "browser",
    "files",
    "screenshot",
    "clipboard",
    "dictation",
    "theme",
    "backgrounds",
)

ACTION_LABELS = {
    "terminal": "Terminal",
    "browser": "Browser",
    "files": "Files",
    "screenshot": "Shot",
    "clipboard": "Clipboard",
    "dictation": "Dictate",
    "theme": "Theme",
    "backgrounds": "Wallpaper",
}

# --- control strip -----------------------------------------------------------

CTRL_PAD = 8.0
CTRL_ICON_W = 24.0
CTRL_BAR_W = 112.0
CTRL_MUTE_W = 48.0
CTRL_CHEV_W = 45.0


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def inset(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)


def clamped_fraction(rect: Rect, x: float, pad: float = 0.0) -> float:
    span = max(1.0, rect.w - 2 * pad)
    return max(0.0, min(1.0, (x - rect.x - pad) / span))


@dataclass(frozen=True)
class HomeLayout:
    menu: Rect
    workspaces: tuple[Rect, ...]
    actions: tuple[tuple[str, Rect], ...]
    system: Rect
    stack_left: Rect
    stack_right: Rect
    left_rows: tuple[Rect, ...]
    right_rows: tuple[Rect, ...]
    control: Rect
    sun: Rect
    brightness: Rect
    speaker: Rect
    volume: Rect
    mute: Rect
    chevron: Rect


def _build_home(count: int) -> HomeLayout:
    count = max(1, min(MAX_WORKSPACES, count))
    menu = Rect(MENU_X, 0, MENU_W, ROWS_H)

    ws_y = 8.0
    ws_h = ROWS_H - 2 * ws_y
    workspaces = tuple(
        Rect(WS_X + i * (WS_W + WS_GAP), ws_y, WS_W, ws_h) for i in range(count)
    )

    # More than five workspaces push the quick actions right; the system and
    # control blocks stay anchored to the right edge so the strip always ends
    # at the same pixel.
    act_x = max(ACT_X, workspaces[-1].right + ACT_GAP)
    act_w = SYS_X - ACT_GAP - act_x
    act_y = 5.0
    act_h = ROWS_H - 2 * act_y
    cell_w = (act_w - ACT_GAP * (len(ACTION_ORDER) - 1)) / len(ACTION_ORDER)
    actions = tuple(
        (name, Rect(act_x + i * (cell_w + ACT_GAP), act_y, cell_w, act_h))
        for i, name in enumerate(ACTION_ORDER)
    )

    system = Rect(SYS_X, 0, SYS_W, ROWS_H)
    stack_left = Rect(SYS_X, 0, STACK_W, ROWS_H)
    stack_right = Rect(SYS_X + STACK_W + STACK_GAP, 0, STACK_W, ROWS_H)

    left_rows = (
        Rect(stack_left.x, 2, STACK_W, 27),
        Rect(stack_left.x, 31, STACK_W, 27),
    )
    right_rows = (
        Rect(stack_right.x, 2, STACK_W, 18),
        Rect(stack_right.x, 21, STACK_W, 18),
        Rect(stack_right.x, 40, STACK_W, 18),
    )

    control = Rect(CTRL_X, 0, CTRL_W, ROWS_H)
    cursor = CTRL_X + CTRL_PAD
    sun = Rect(cursor, 0, CTRL_ICON_W, ROWS_H)
    cursor = sun.right + 6
    brightness = Rect(cursor, 0, CTRL_BAR_W, ROWS_H)
    cursor = brightness.right + 12
    speaker = Rect(cursor, 0, CTRL_ICON_W, ROWS_H)
    cursor = speaker.right + 6
    volume = Rect(cursor, 0, CTRL_BAR_W, ROWS_H)
    cursor = volume.right + 12
    mute = Rect(cursor, 0, CTRL_MUTE_W, ROWS_H)
    cursor = mute.right + 6
    chevron = Rect(cursor, 0, CTRL_CHEV_W, ROWS_H)

    return HomeLayout(
        menu=menu,
        workspaces=workspaces,
        actions=actions,
        system=system,
        stack_left=stack_left,
        stack_right=stack_right,
        left_rows=left_rows,
        right_rows=right_rows,
        control=control,
        sun=sun,
        brightness=brightness,
        speaker=speaker,
        volume=volume,
        mute=mute,
        chevron=chevron,
    )


HOME = _build_home(5)


def home(count: int | None = None) -> HomeLayout:
    if count is None or count == len(HOME.workspaces):
        return HOME
    return _build_home(count)


# --- drawers -----------------------------------------------------------------

CONTROL_ICON = 24.0
CONTROL_TRACK_W = 600.0
CONTROL_TRACK_H = 22.0
CONTROL_TRACK_Y = (CANVAS_H - CONTROL_TRACK_H) / 2
CONTROL_CLOSE = Rect(2023.0, 14, 40, 32)
CONTROL_MUTE = Rect(1948.0, 16, 40, 28)

# Brightness and volume sit side by side, not stacked: the digitizer reports
# only X (see input.py), so two rows would be indistinguishable to a finger.
# Each column starts at its icon (the 0 % end, like the macOS slider) and ends
# where the next control begins, so the whole half is a forgiving drag zone.
CONTROL_DIVIDER_X = 1178.0
CONTROL_ICON1_CX = 438.0
CONTROL_ICON2_CX = 1206.0
CONTROL_VALUE1_X = 1140.0
CONTROL_VALUE2_X = 1912.0
TRACK1 = Rect(464.0, CONTROL_TRACK_Y, CONTROL_TRACK_W, CONTROL_TRACK_H)
TRACK2 = Rect(1232.0, CONTROL_TRACK_Y, CONTROL_TRACK_W, CONTROL_TRACK_H)
CONTROL_ZONE1 = Rect(DRAWER_X, 0, CONTROL_DIVIDER_X - DRAWER_X, CANVAS_H)
CONTROL_ZONE2 = Rect(CONTROL_DIVIDER_X, 0, CONTROL_MUTE.x - CONTROL_DIVIDER_X, CANVAS_H)

WINDOW_CARD_W = 168.0
WINDOW_CARD_H = 48.0
WINDOW_CARD_Y = 6.0
WINDOW_CARD_X = 420.0
WINDOW_CARD_GAP = 8.0
WINDOW_CLOSE = Rect(2023.0, 14, 40, 32)
WINDOW_VISIBLE = 9

VITALS_Y = 2.0
VITALS_H = 56.0
VITALS_X = 424.0
VITALS_W = 258.5
VITALS_GAP = 8.0
VITALS_CLOSE = Rect(2023.0, 14, 40, 32)


def window_card(index: int, scroll: float = 0.0) -> Rect:
    return Rect(
        WINDOW_CARD_X + index * (WINDOW_CARD_W + WINDOW_CARD_GAP) - scroll,
        WINDOW_CARD_Y,
        WINDOW_CARD_W,
        WINDOW_CARD_H,
    )


def vitals_card(index: int) -> Rect:
    return Rect(
        VITALS_X + index * (VITALS_W + VITALS_GAP), VITALS_Y, VITALS_W, VITALS_H
    )


# --- hit testing -------------------------------------------------------------


def hit_home(layout: HomeLayout, x: float, y: float) -> tuple[str, object] | None:
    if layout.menu.contains(x, y):
        return ("menu", None)
    for index, rect in enumerate(layout.workspaces):
        if rect.contains(x, y):
            return ("workspace", index)
    for name, rect in layout.actions:
        if rect.contains(x, y):
            return ("action", name)
    if layout.system.contains(x, y):
        return ("system", None)
    for name, rect in (
        ("sun", layout.sun),
        ("brightness", layout.brightness),
        ("speaker", layout.speaker),
        ("volume", layout.volume),
        ("mute", layout.mute),
        ("expand", layout.chevron),
    ):
        if rect.contains(x, y):
            return ("control", name)
    return None


def hit_controls(x: float, y: float) -> tuple[str, object] | None:
    if CONTROL_CLOSE.contains(x, y):
        return ("drawer_close", None)
    if CONTROL_MUTE.contains(x, y):
        return ("drawer_mute", None)
    if CONTROL_ZONE1.contains(x, y):
        return ("drawer_brightness", clamped_fraction(TRACK1, x))
    if CONTROL_ZONE2.contains(x, y):
        return ("drawer_volume", clamped_fraction(TRACK2, x))
    return ("drawer_background", None)


def hit_windows(
    x: float, y: float, count: int, scroll: float
) -> tuple[str, object] | None:
    if WINDOW_CLOSE.contains(x, y):
        return ("drawer_close", None)
    for index in range(count):
        if window_card(index, scroll).contains(x, y):
            return ("window", index)
    return ("drawer_background", None)


def hit_vitals(x: float, y: float, count: int) -> tuple[str, object] | None:
    if VITALS_CLOSE.contains(x, y):
        return ("drawer_close", None)
    for index in range(count):
        if vitals_card(index).contains(x, y):
            return ("vitals_card", index)
    return ("drawer_background", None)


def hit(state: str, x: float, y: float, *, windows: int = 0, scroll: float = 0.0):
    if state == "controls":
        return hit_controls(x, y)
    if state == "windows":
        return hit_windows(x, y, windows, scroll)
    if state == "vitals":
        return hit_vitals(x, y, 6)
    return hit_home(HOME, x, y)


def max_window_scroll(count: int) -> float:
    if count <= WINDOW_VISIBLE:
        return 0.0
    content = count * (WINDOW_CARD_W + WINDOW_CARD_GAP) - WINDOW_CARD_GAP
    viewport = WINDOW_VISIBLE * (WINDOW_CARD_W + WINDOW_CARD_GAP) - WINDOW_CARD_GAP
    return max(0.0, content - viewport)


def clamp_window_scroll(value: float, count: int) -> float:
    return max(0.0, min(max_window_scroll(count), value))
