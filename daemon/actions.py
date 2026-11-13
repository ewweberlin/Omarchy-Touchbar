"""Shell actions and the two sliders' backends.

Every launch is detached: the theme/background pickers block until the user
picks something, and the Touch Bar must stay responsive.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

def _pick_backlight() -> str:
    # The panel backlight name differs between T2 models (acpi_video0 on some,
    # intel_backlight on others). Skip the Touch Bar's own appletb_backlight.
    base = Path("/sys/class/backlight")
    for name in ("acpi_video0", "intel_backlight", "gmux_backlight"):
        if (base / name).is_dir():
            return name
    try:
        for entry in sorted(base.iterdir()):
            if not entry.name.startswith("appletb"):
                return entry.name
    except OSError:
        pass
    return "acpi_video0"


BRIGHTNESS_DEVICE = _pick_backlight()
BRIGHTNESS_SYS = Path("/sys/class/backlight") / BRIGHTNESS_DEVICE


def _spawn(argv: list[str]) -> None:
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:  # pragma: no cover - depends on the host
        print(f"spawn failed: {argv!r}: {exc}", flush=True)


# Omarchy's pickers do not apply what they show: `omarchy-theme-switcher`
# prints the chosen theme name and `omarchy-theme-bg-switcher` prints the chosen
# wallpaper path. Both are meant to be run with their stdout captured, and the
# caller applies the result. Spawning them bare therefore opens a picker whose
# selection is thrown away, which is why these two entries are the exact
# commands Omarchy's own menu runs (omarchy-menu.jsonc / Background.qml)
# rather than the `omarchy theme switcher` wrapper.
ACTIONS: dict[str, list[str]] = {
    "menu": ["omarchy", "menu", "toggle"],
    "terminal": ["omarchy", "launch", "terminal"],
    "browser": ["omarchy", "launch", "browser"],
    "files": ["omarchy", "launch", "nautilus"],
    "screenshot": ["omarchy-capture-screenshot"],
    "clipboard": ["omarchy", "menu", "clipboard"],
    "dictation": ["omarchy", "dictation", "toggle"],
    "theme": [
        "omarchy-shell",
        "shell",
        "summon",
        "omarchy.image-picker",
        '{"source":"themes"}',
    ],
    "backgrounds": [
        "bash",
        "-c",
        'background=$(omarchy-theme-bg-switcher); [[ -n $background ]] && omarchy-theme-bg-set "$background"',
    ],
    "lock": ["omarchy-system-lock"],
}


def run_action(name: str) -> None:
    command = ACTIONS.get(name)
    if command:
        _spawn(command)


def _run(argv: list[str], timeout: float = 2.0) -> bool:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class Brightness:
    """Internal panel backlight through brightnessctl.

    Writing sysfs directly is not an option: /sys/class/backlight/*/brightness
    is root-only here and brightnessctl is the sanctioned path (it goes through
    logind, which is also why it works for the Touch Bar backlight).
    """

    def __init__(self, device: str = BRIGHTNESS_DEVICE) -> None:
        self.device = device

    def read(self) -> int:
        try:
            current = int((BRIGHTNESS_SYS / "brightness").read_text().strip())
            maximum = int((BRIGHTNESS_SYS / "max_brightness").read_text().strip())
        except (OSError, ValueError):
            return 75
        if maximum <= 0:
            return 75
        return max(0, min(100, round(100 * current / maximum)))

    def set(self, percent: int) -> None:
        percent = max(1, min(100, int(percent)))
        if not _run(["brightnessctl", "-d", self.device, "set", f"{percent}%"]):
            _run(["omarchy", "brightness", "display", "--no-osd", f"{percent}%"])


class Audio:
    """Default output volume and mute, resolved through Omarchy's sink helper.

    The sink is re-resolved every few seconds because switching outputs (or the
    DSP tuning sink appearing) changes which node actually holds the volume.
    """

    def __init__(self) -> None:
        self._sink = ""
        self._sink_ts = 0.0
        self._volume = 30
        self._muted = False
        self._read_ts = 0.0

    def _resolve_sink(self, force: bool = False) -> str:
        now = time.monotonic()
        if not force and self._sink and now - self._sink_ts < 20:
            return self._sink
        try:
            result = subprocess.run(
                ["omarchy", "audio", "output", "sink"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            sink = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        except (OSError, subprocess.SubprocessError):
            sink = ""
        self._sink = sink or self._sink
        self._sink_ts = now
        return self._sink

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._read_ts < 1.0:
            return
        self._read_ts = now
        sink = self._resolve_sink()
        if not sink:
            return
        try:
            volume = subprocess.run(
                ["pactl", "get-sink-volume", sink],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            ).stdout
            mute = subprocess.run(
                ["pactl", "get-sink-mute", sink],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        for token in volume.split():
            if token.endswith("%"):
                try:
                    self._volume = max(0, min(100, int(token.rstrip("%"))))
                except ValueError:
                    pass
                break
        self._muted = "yes" in mute.lower()

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    def set_volume(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        sink = self._resolve_sink()
        if not sink:
            return
        # Setting volume does not unmute: the mute button is its own control.
        _run(["pactl", "set-sink-volume", sink, f"{percent}%"])
        self._volume = percent

    def toggle_mute(self) -> None:
        sink = self._resolve_sink()
        if not sink:
            return
        _run(["pactl", "set-sink-mute", sink, "toggle"])
        self._muted = not self._muted
