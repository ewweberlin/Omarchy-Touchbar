"""SVG builder for the Touch Bar canvas.

Everything is flat: solid rounded rects, 1px hairlines and stroked icons. No
filters, no gradients, no blur -- tiny-dfr re-parses this document on every
repaint, and librsvg cost scales with document complexity.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from . import layout as L
from .config import CANVAS_H, CANVAS_W
from .theme import Palette, alpha, mix

# --- text metrics ------------------------------------------------------------

_NARROW = set("iljtfrI.,:;'|!()[]{}`/\\")
_WIDE = set("mwMW@")
_TALL = set("ABCDEFGHJKLMNOPQRSTUVWXYZ")


def text_width(text: str, size: float, weight: int = 400) -> float:
    """Approximate advance width. Only used for truncation and centring."""
    total = 0.0
    for ch in text:
        if ch in _NARROW:
            factor = 0.33
        elif ch in _WIDE:
            factor = 0.85
        elif ch in _TALL:
            factor = 0.68
        elif ch.isdigit():
            factor = 0.58
        elif ch == " ":
            factor = 0.29
        else:
            factor = 0.55
        total += factor
    if weight >= 600:
        total *= 1.03
    return total * size


def ellipsize(text: str, size: float, max_width: float, weight: int = 400) -> str:
    if text_width(text, size, weight) <= max_width:
        return text
    ellipsis = "…"
    budget = max_width - text_width(ellipsis, size, weight)
    out = []
    used = 0.0
    for ch in text:
        step = text_width(ch, size, weight)
        if used + step > budget:
            break
        out.append(ch)
        used += step
    return "".join(out).rstrip() + ellipsis


# --- primitives --------------------------------------------------------------


def rect(
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str,
    radius: float = 0.0,
    opacity: float = 1.0,
) -> str:
    if w <= 0 or h <= 0:
        return ""
    extra = f' opacity="{opacity:g}"' if opacity < 1.0 else ""
    if radius:
        return (
            f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" '
            f'rx="{radius:g}" ry="{radius:g}" fill="{fill}"{extra}/>'
        )
    return f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" fill="{fill}"{extra}/>'


def text(
    x: float,
    y: float,
    value: str,
    size: float,
    fill: str,
    weight: int = 400,
    anchor: str = "start",
    opacity: float = 1.0,
    spacing: float = 0.0,
) -> str:
    if not value:
        return ""
    extra = f' opacity="{opacity:g}"' if opacity < 1.0 else ""
    spacing_attr = f' letter-spacing="{spacing:g}"' if spacing else ""
    return (
        f'<text x="{x:g}" y="{y:g}" font-family="{{FAMILY}}" font-size="{size:g}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{spacing_attr}{extra}>'
        f"{escape(value)}</text>"
    )


# Icons authored on a 24x24 grid.
_STROKE_ICONS: dict[str, tuple[str, ...]] = {
    "menu": ("M4 7.5h16", "M4 12h16", "M4 16.5h16"),
    "terminal": ("M5.2 8.2l3.8 3.8-3.8 3.8", "M11.6 16.2h7"),
    # Holder arc and stand around the filled capsule in _FILL_ICONS.
    "dictation": ("M6.6 11.4a5.4 5.4 0 0010.8 0", "M12 16.8v3.6", "M9 20.4h6"),
    "browser": (
        "M12 3.6a8.4 8.4 0 100 16.8 8.4 8.4 0 000-16.8Z",
        "M3.8 12h16.4",
        "M12 3.6c2.8 3 2.8 13.8 0 16.8",
        "M12 3.6c-2.8 3-2.8 13.8 0 16.8",
    ),
    "screenshot": (
        "M4 9.4A1.8 1.8 0 015.8 7.6h2.4l1.5-2h4.6l1.5 2h2.4A1.8 1.8 0 0120 9.4v8.2"
        "a1.8 1.8 0 01-1.8 1.8H5.8A1.8 1.8 0 014 17.6Z",
        "M12 12.1a2.9 2.9 0 100 5.8 2.9 2.9 0 000-5.8Z",
    ),
    "clipboard": (
        "M9 4.6h6v2.8H9z",
        "M8.6 5.4H7A1.7 1.7 0 005.3 7.1v11.5A1.7 1.7 0 007 20.3h10a1.7 1.7 0 001.7-1.7V7.1"
        "A1.7 1.7 0 0017 5.4h-1.6",
    ),
    "backgrounds": (
        "M4.4 6.4h15.2v11.2H4.4z",
        "M4.4 15l4.3-4.3 2.9 2.9 3.4-3.4 4.6 4.6",
    ),
    "sun": (
        "M12 7.4a4.6 4.6 0 100 9.2 4.6 4.6 0 000-9.2Z",
        "M12 2.4v2",
        "M12 19.6v2",
        "M2.4 12h2",
        "M19.6 12h2",
        "M5.2 5.2l1.4 1.4",
        "M17.4 17.4l1.4 1.4",
        "M18.8 5.2l-1.4 1.4",
        "M6.6 17.4l-1.4 1.4",
    ),
    "speaker": ("M15 9.2a4 4 0 010 5.6", "M17.6 6.8a7.4 7.4 0 010 10.4"),
    "mute": ("M15.4 10l4.2 4.2", "M19.6 10l-4.2 4.2"),
    "chevron-up": ("M8 14.2l4-4 4 4",),
    "chevron-down": ("M8 9.8l4 4 4-4",),
    "close": ("M7.8 7.8l8.4 8.4", "M16.2 7.8l-8.4 8.4"),
    "wifi": (
        "M4.4 9.2a11 11 0 0115.2 0",
        "M7.2 12.2a7 7 0 019.6 0",
        "M10 15.2a3.2 3.2 0 014 0",
    ),
}

_FILL_ICONS: dict[str, tuple[str, ...]] = {
    "files": (
        "M3.4 7.6A2.2 2.2 0 015.6 5.4h3.3l1.9 2.1h7.6a2.2 2.2 0 012.2 2.2v7.7"
        "a2.2 2.2 0 01-2.2 2.2H5.6a2.2 2.2 0 01-2.2-2.2Z",
    ),
    "dictation": (
        "M12 3.4a3.2 3.2 0 013.2 3.2v5.2a3.2 3.2 0 11-6.4 0V6.6A3.2 3.2 0 0112 3.4Z",
    ),
    "theme": ("M12 3.1c3.5 3.9 6 6.8 6 9.7a6 6 0 11-12 0c0-2.9 2.5-5.8 6-9.7Z",),
    "speaker-body": ("M4.2 9.3h3.2l3.9-3.2v11.8l-3.9-3.2H4.2Z",),
    "dot": ("M12 8.6a3.4 3.4 0 100 6.8 3.4 3.4 0 000-6.8Z",),
}


def icon(name: str, cx: float, cy: float, size: float, color: str, width: float = 1.8) -> str:
    scale = size / 24.0
    parts = [f'<g transform="translate({cx - size / 2:g} {cy - size / 2:g}) scale({scale:g})">']
    if name in _STROKE_ICONS:
        for d in _STROKE_ICONS[name]:
            parts.append(
                f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width:g}" '
                f'stroke-linecap="round" stroke-linejoin="round"/>'
            )
    if name in _FILL_ICONS:
        for d in _FILL_ICONS[name]:
            parts.append(f'<path d="{d}" fill="{color}"/>')
    if name == "speaker" or name == "mute":
        for d in _FILL_ICONS["speaker-body"]:
            parts.append(f'<path d="{d}" fill="{color}"/>')
    parts.append("</g>")
    return "".join(parts)


# --- renderer ----------------------------------------------------------------


@dataclass
class Model:
    state: str = "home"
    workspaces: tuple = ()
    # The strip's configured cell count. The renderer must use it even when a
    # Hyprland refresh fails and leaves `workspaces` empty, otherwise the drawn
    # cells and the hit-test layout disagree.
    workspace_count: int = 0
    stats: dict | None = None
    brightness: int = 75
    volume: int = 30
    muted: bool = False
    windows: tuple = ()
    window_scroll: float = 0.0
    dictating: bool = False


def _fmt_temp(value) -> str:
    return f"{value:.0f}°" if value is not None else "--°"


def _fmt_pct(value) -> str:
    return f"{value:.0f}%" if value is not None else "--%"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "--"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f}M"
    if value >= 1024:
        return f"{value / 1024:.0f}K"
    return f"{value:.0f}B"


def _fmt_clock(mhz: float | None) -> str:
    if not mhz:
        return "--G"
    return f"{mhz / 1000:.1f}G" if mhz >= 1000 else f"{mhz:.0f}M"


class Renderer:
    def __init__(
        self,
        palette: Palette,
        font_family: str,
        action_labels: bool = False,
        action_icon_size: float = 34,
    ):
        self.p = palette
        self.font = font_family
        self.action_labels = action_labels
        # Cells are 50 px tall; the glyphs fill ~75% of their box, so 50 still fits.
        self.action_icon_size = max(16.0, min(50.0, float(action_icon_size)))

    # -- shared pieces -------------------------------------------------------

    def render(self, model: Model) -> str:
        body = [self._home(model)]
        if model.state == "controls":
            body.append(self._controls(model))
        elif model.state == "windows":
            body.append(self._windows(model))
        elif model.state == "vitals":
            body.append(self._vitals(model))
        doc = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{CANVAS_H}" '
            f'viewBox="0 0 {CANVAS_W} {CANVAS_H}">'
            + rect(0, 0, CANVAS_W, CANVAS_H, self.p.strip_bg)
            + "".join(body)
            + "</svg>"
        )
        return doc.replace("{FAMILY}", self.font)

    # -- home ----------------------------------------------------------------

    def _home(self, model: Model) -> str:
        p = self.p
        lay = L.home(model.workspace_count or len(model.workspaces) or 5)
        out: list[str] = []

        # Menu
        cell = lay.menu.inset(0, 4)
        out.append(rect(cell.x, cell.y, cell.w, cell.h, p.cell, 10))
        out.append(icon("menu", cell.cx, cell.cy, 20, p.fg))

        # Workspaces
        for i, rect_ in enumerate(lay.workspaces):
            info = model.workspaces[i] if i < len(model.workspaces) else None
            active = bool(info and info.get("active"))
            occupied = bool(info and info.get("occupied"))
            fill = p.accent if active else p.cell
            out.append(rect(rect_.x, rect_.y, rect_.w, rect_.h, fill, 10))
            number = str(info.get("id")) if info else str(i + 1)
            color = p.on_accent if active else (p.fg_bright if occupied else p.text_dim)
            out.append(
                text(rect_.cx, rect_.cy + 6.5, number, 18, color, 600, "middle")
            )
            if occupied and not active:
                out.append(
                    rect(rect_.cx - 8, rect_.bottom - 9, 16, 2.5, p.accent, 1.25)
                )

        # Quick actions
        for name, rect_ in lay.actions:
            pressed = name == "dictation" and model.dictating
            fill = p.accent if pressed else p.cell
            out.append(rect(rect_.x, rect_.y, rect_.w, rect_.h, fill, 10))
            fg = p.on_accent if pressed else p.fg
            if not self.action_labels:
                # No label: the icon gets the whole cell. Thinner strokes from
                # 40 px up keep the outline glyphs from looking heavy.
                size = self.action_icon_size
                stroke = 1.7 if size < 40 else 1.5
                out.append(icon(name, rect_.cx, rect_.cy, size, fg, stroke))
                continue
            out.append(icon(name, rect_.cx, rect_.y + 17, 21, fg, 1.9))
            label = L.ACTION_LABELS[name]
            out.append(
                text(
                    rect_.cx,
                    rect_.y + 42,
                    ellipsize(label, 8.5, rect_.w - 10),
                    8.5,
                    p.on_accent if pressed else p.text_dim,
                    500,
                    "middle",
                )
            )

        # Hairlines between groups
        for x in (lay.actions[0][1].x - 4, L.SYS_X - 4, L.CTRL_X - 4):
            out.append(rect(x, 14, 1, 32, p.hairline))

        # System block
        out.append(self._system(model))

        # Control strip
        out.append(self._control_strip(model))
        return "".join(out)

    def _system(self, model: Model) -> str:
        p = self.p
        s = model.stats or {}
        lay = L.HOME
        out: list[str] = []

        def row(
            rect_: L.Rect,
            label: str,
            prefix: str,
            value: str,
            fraction: float,
            size_label: float,
            size_value: float,
            bar_y: float,
        ) -> str:
            color = p.fg_bright
            if fraction > 0.9:
                color = p.danger
            elif fraction > 0.75:
                color = p.warning
            parts = [
                text(rect_.x, rect_.y + size_label + 1.5, label, size_label,
                     p.muted, 600, spacing=0.4),
            ]
            if prefix:
                parts.append(
                    text(
                        rect_.x + text_width(label, size_label, 600) + 6,
                        rect_.y + size_label + 1.5,
                        prefix,
                        size_label + 0.5,
                        p.text_dim,
                        400,
                    )
                )
            parts.append(
                text(rect_.right, rect_.y + size_value + 0.5, value,
                     size_value, color, 600, "end")
            )
            parts.append(rect(rect_.x, rect_.y + bar_y, rect_.w, 2.5, p.track, 1.25))
            parts.append(
                rect(rect_.x, rect_.y + bar_y, rect_.w * fraction, 2.5, p.accent, 1.25)
            )
            return "".join(parts)

        left = lay.left_rows
        out.append(
            row(left[0], "CPU", _fmt_temp(s.get("cpu_temp")),
                _fmt_pct(s.get("cpu_pct")), (s.get("cpu_pct") or 0) / 100, 8.5, 12, 20)
        )
        out.append(
            row(left[1], "GPU", _fmt_clock(s.get("gpu_mhz")),
                _fmt_pct(s.get("gpu_pct")), (s.get("gpu_pct") or 0) / 100, 8.5, 12, 20)
        )

        right = lay.right_rows
        out.append(
            row(right[0], "RAM", "", _fmt_pct(s.get("ram_pct")),
                (s.get("ram_pct") or 0) / 100, 8, 10, 13.5)
        )
        fan_max = s.get("fan_max") or 7000
        out.append(
            row(right[1], "FAN", "", f"{s.get('fan_rpm') or 0:.0f}",
                min(1.0, (s.get("fan_rpm") or 0) / fan_max), 8, 10, 13.5)
        )
        net = f"↓{_fmt_rate(s.get('net_down'))} ↑{_fmt_rate(s.get('net_up'))}"
        out.append(
            row(right[2], "NET", "", net, 0.0, 8, 10, 13.5)
        )
        return "".join(out)

    def _bar(self, rect_: L.Rect, fraction: float, height: float = 6.0) -> str:
        p = self.p
        y = rect_.cy - height / 2
        inner = rect_.w - 4
        out = [rect(rect_.x, y, rect_.w, height, p.track, height / 2)]
        knob_x = rect_.x + 2 + inner * max(0.0, min(1.0, fraction))
        out.append(rect(rect_.x + 2, y, inner * fraction, height, p.accent, height / 2))
        out.append(
            f'<circle cx="{knob_x:g}" cy="{rect_.cy:g}" r="{height / 2 + 2:g}" '
            f'fill="{p.fg_bright}"/>'
        )
        return "".join(out)

    def _control_strip(self, model: Model) -> str:
        p = self.p
        lay = L.HOME
        out: list[str] = []

        out.append(icon("sun", lay.sun.cx, lay.sun.cy, 21, p.fg))
        out.append(self._bar(lay.brightness, model.brightness / 100))

        spk = "mute" if model.muted else "speaker"
        out.append(icon(spk, lay.speaker.cx, lay.speaker.cy, 21, p.fg))
        out.append(self._bar(lay.volume, model.volume / 100))

        mute_rect = lay.mute.inset(0, 10)
        fill = p.accent if model.muted else p.cell
        out.append(rect(mute_rect.x, mute_rect.y, mute_rect.w, mute_rect.h, fill, 10))
        out.append(
            icon(
                "mute" if model.muted else "speaker",
                mute_rect.cx,
                mute_rect.cy,
                17,
                p.on_accent if model.muted else p.fg,
            )
        )

        out.append(icon("chevron-up", lay.chevron.cx, lay.chevron.cy, 20, p.text_dim))
        return "".join(out)

    # -- drawers -------------------------------------------------------------

    def _drawer_shell(self) -> list[str]:
        p = self.p
        return [
            rect(L.DRAWER_X, 0, L.DRAWER_W, CANVAS_H, p.strip_bg),
            rect(L.DRAWER_X, 14, 1, 32, p.hairline),
        ]

    def _close_button(self, rect_: L.Rect) -> str:
        p = self.p
        return rect(rect_.x, rect_.y, rect_.w, rect_.h, p.cell, 10) + icon(
            "close", rect_.cx, rect_.cy, 17, p.text_dim
        )

    def _controls(self, model: Model) -> str:
        p = self.p
        out = self._drawer_shell()
        mid = CANVAS_H / 2

        for icon_cx, track, value_x, value, glyph in (
            (L.CONTROL_ICON1_CX, L.TRACK1, L.CONTROL_VALUE1_X, model.brightness, "sun"),
            (L.CONTROL_ICON2_CX, L.TRACK2, L.CONTROL_VALUE2_X, model.volume, "speaker"),
        ):
            out.append(icon(glyph, icon_cx, mid, 22, p.fg))
            out.append(self._big_track(track, value / 100))
            out.append(
                text(
                    value_x,
                    mid + 7,
                    f"{value}%",
                    18,
                    p.fg_bright,
                    600,
                    "end",
                )
            )

        out.append(rect(L.CONTROL_DIVIDER_X, 14, 1, 32, p.hairline))

        mute_rect = L.CONTROL_MUTE
        fill = p.accent if model.muted else p.cell
        out.append(rect(mute_rect.x, mute_rect.y, mute_rect.w, mute_rect.h, fill, 10))
        out.append(
            icon(
                "mute" if model.muted else "speaker",
                mute_rect.cx,
                mute_rect.cy,
                17,
                p.on_accent if model.muted else p.fg,
            )
        )

        out.append(self._close_button(L.CONTROL_CLOSE))
        return "".join(out)

    def _big_track(self, track: L.Rect, fraction: float) -> str:
        p = self.p
        fraction = max(0.0, min(1.0, fraction))
        out = [rect(track.x, track.y, track.w, track.h, p.track, track.h / 2)]
        # Ticks every 10% so the slider reads as a scale, not a progress bar.
        for step in range(1, 10):
            tick_x = track.x + track.w * step / 10
            out.append(rect(tick_x, track.y + 2.5, 1, track.h - 5, alpha(p.fg, 0.10)))
        fill_w = max(track.h, track.w * fraction)
        out.append(rect(track.x, track.y, fill_w, track.h, p.accent, track.h / 2))
        knob_x = track.x + track.w * fraction
        out.append(
            f'<circle cx="{knob_x:g}" cy="{track.cy:g}" r="{track.h / 2 + 2:g}" fill="{p.fg_bright}"/>'
        )
        return "".join(out)

    def _windows(self, model: Model) -> str:
        p = self.p
        out = self._drawer_shell()
        windows = model.windows
        out.append(
            text(
                L.DRAWER_X + 12,
                26,
                f"Windows · {len(windows)}",
                10,
                p.text_dim,
                600,
                spacing=0.5,
            )
        )
        visible = L.WINDOW_VISIBLE
        for index, window in enumerate(windows[: visible + 1]):
            card = L.window_card(index, model.window_scroll)
            if card.right < L.DRAWER_X or card.x > L.WINDOW_CLOSE.x:
                continue
            active = bool(window.get("active"))
            out.append(rect(card.x, card.y, card.w, card.h, p.cell, 10))
            if active:
                out.append(rect(card.x, card.y + 8, 2.5, card.h - 16, p.accent, 1.25))
            icon_x = card.x + 26
            if window.get("icon"):
                out.append(
                    f'<image x="{icon_x - 14:g}" y="{card.cy - 14:g}" width="28" height="28" '
                    f'xlink:href="{window["icon"]}" preserveAspectRatio="xMidYMid meet" '
                    f'xmlns:xlink="http://www.w3.org/1999/xlink"/>'
                )
            else:
                out.append(rect(icon_x - 14, card.cy - 14, 28, 28, p.raised, 7))
                initial = (window.get("app") or "?").strip()[:1].upper()
                out.append(
                    text(icon_x, card.cy + 5, initial, 15, p.fg_bright, 600, "middle")
                )
            label_x = card.x + 48
            out.append(
                text(
                    label_x,
                    card.cy + 4,
                    ellipsize(window.get("app") or "", 11, card.right - label_x - 10),
                    11,
                    p.fg_bright if active else p.fg,
                    500,
                )
            )
        out.append(self._close_button(L.WINDOW_CLOSE))
        return "".join(out)

    def _vitals(self, model: Model) -> str:
        p = self.p
        s = model.stats or {}
        out = self._drawer_shell()

        cards = [
            (
                "CPU",
                _fmt_pct(s.get("cpu_pct")),
                _fmt_temp(s.get("cpu_temp")),
                (s.get("cpu_pct") or 0) / 100,
                p.accent,
            ),
            (
                "GPU",
                _fmt_pct(s.get("gpu_pct")),
                _fmt_clock(s.get("gpu_mhz")),
                (s.get("gpu_pct") or 0) / 100,
                p.accent,
            ),
            (
                "RAM",
                _fmt_pct(s.get("ram_pct")),
                f"{(s.get('ram_used_gb') or 0):.1f} / {(s.get('ram_total_gb') or 0):.0f} GB",
                (s.get("ram_pct") or 0) / 100,
                p.good if (s.get("ram_pct") or 0) < 80 else p.warning,
            ),
            (
                "FAN",
                f"{(s.get('fan_rpm') or 0):.0f}",
                "RPM",
                min(1.0, (s.get("fan_rpm") or 0) / (s.get("fan_max") or 7000)),
                p.accent,
            ),
            (
                "NET",
                f"↓{_fmt_rate(s.get('net_down'))}",
                f"↑{_fmt_rate(s.get('net_up'))}",
                0.0,
                p.cyan,
            ),
            (
                "DISK",
                _fmt_pct(s.get("disk_pct")),
                f"{(s.get('disk_free_gb') or 0):.0f} GB free",
                (s.get("disk_pct") or 0) / 100,
                p.accent,
            ),
        ]

        for index, (label, value, sub, fraction, color) in enumerate(cards):
            card = L.vitals_card(index)
            out.append(rect(card.x, card.y, card.w, card.h, p.cell, 10))
            out.append(text(card.x + 12, card.y + 17, label, 8.5, p.muted, 600, spacing=0.6))
            out.append(text(card.right - 12, card.y + 17, sub, 10, p.text_dim, 500, "end"))
            out.append(text(card.x + 12, card.y + 40, value, 19, p.fg_bright, 600))
            if fraction > 0:
                out.append(rect(card.x + 12, card.bottom - 9, card.w - 24, 3, p.track, 1.5))
                out.append(
                    rect(card.x + 12, card.bottom - 9, (card.w - 24) * fraction, 3, color, 1.5)
                )

        out.append(self._close_button(L.VITALS_CLOSE))
        return "".join(out)
