"""Theme palette derived from Omarchy's live colors.toml.

Omarchy writes every theme's palette to
~/.local/state/omarchy/current/theme/colors.toml and swaps that file atomically
on every theme change, so re-reading it on demand is enough to follow the theme
without watching anything.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import config

# Fallback: Tokyo Night, the Omarchy default, in case the state file is missing
# (fresh boot before the theme is applied, or a preview run on another machine).
FALLBACK = {
    "accent": "#7aa2f7",
    "selection": "#292e42",
    "muted": "#414868",
    "background": "#1a1b26",
    "dark_background": "#13141c",
    "darker_background": "#0e0e14",
    "lighter_background": "#24283b",
    "foreground": "#a9b1d6",
    "dark_foreground": "#565f89",
    "light_foreground": "#b4bee6",
    "bright_foreground": "#c0caf5",
    "red": "#f7768e",
    "yellow": "#e0af68",
    "orange": "#eb927b",
    "green": "#9ece6a",
    "cyan": "#449dab",
    "blue": "#7aa2f7",
    "magenta": "#ad8ee6",
}


def _parse_hex(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) < 6:
        raise ValueError(f"bad colour {value!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    return _parse_hex(value)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """Linear mix between two hex colours; t=0 is a, t=1 is b."""
    ar, ag, ab = _parse_hex(a)
    br, bg, bb = _parse_hex(b)
    return rgb_to_hex((ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t))


def alpha(value: str, a: float) -> str:
    """#rrggbb + alpha as an #rrggbbaa string (librsvg supports 8-digit hex)."""
    r, g, b = _parse_hex(value)
    return "#%02x%02x%02x%02x" % (r, g, b, max(0, min(255, int(round(a * 255)))))


@dataclass(frozen=True)
class Palette:
    accent: str
    on_accent: str
    background: str
    sunken: str
    surface: str
    raised: str
    fg: str
    fg_bright: str
    muted: str
    danger: str
    warning: str
    good: str
    cyan: str

    # Derived tones used by the renderer so the SVG stays flat and simple.
    @property
    def strip_bg(self) -> str:
        return self.sunken

    @property
    def cell(self) -> str:
        return alpha(self.surface, 0.72)

    @property
    def cell_quiet(self) -> str:
        return alpha(self.surface, 0.34)

    @property
    def hairline(self) -> str:
        return alpha(self.fg, 0.14)

    @property
    def track(self) -> str:
        return alpha(self.fg, 0.18)

    @property
    def text_dim(self) -> str:
        return alpha(self.fg, 0.62)


def palette_from_colors(colors: dict) -> Palette:
    def get(key: str) -> str:
        value = colors.get(key) or FALLBACK.get(key)
        try:
            _parse_hex(value)
        except (TypeError, ValueError):
            value = FALLBACK[key]
        return value

    accent = get("accent")
    return Palette(
        accent=accent,
        on_accent=get("dark_background"),
        background=get("background"),
        sunken=get("darker_background"),
        surface=get("lighter_background"),
        raised=get("selection"),
        fg=get("foreground"),
        fg_bright=get("bright_foreground"),
        muted=get("muted"),
        danger=get("red"),
        warning=get("yellow"),
        good=get("green"),
        cyan=get("cyan"),
    )


def load_palette(path: Path | None = None) -> Palette:
    path = path or config.THEME_COLORS
    try:
        with path.open("rb") as handle:
            colors = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        colors = {}
    if not isinstance(colors, dict):
        colors = {}
    return palette_from_colors(colors)


def theme_signature(path: Path | None = None) -> tuple[int, int]:
    """(mtime_ns, size) so the daemon can detect a theme swap cheaply."""
    path = path or config.THEME_COLORS
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)
