"""App icon lookup for the window drawer.

Hyprland only gives us a window class. Resolve that to a desktop entry's Icon=
name, then to a file in the active icon theme, and inline it as a data URI so
librsvg never has to touch the filesystem while rendering.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

HOME = Path(os.environ.get("HOME", "~")).expanduser()

APP_DIRS = [
    HOME / ".local/share/applications",
    Path("/usr/share/applications"),
    HOME / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
]

ICON_DIRS = [
    HOME / ".local/share/icons",
    HOME / ".icons",
    Path("/usr/share/icons"),
]

THEME_FILE = HOME / ".local/state/omarchy/current/theme/icons.theme"

MAX_ICON_BYTES = 96 * 1024


def _icon_theme() -> str:
    try:
        name = THEME_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "Adwaita"
    return name or "Adwaita"


class IconResolver:
    def __init__(self) -> None:
        self.theme = _icon_theme()
        self._desktop: dict[str, str] | None = None
        self._by_wm_class: dict[str, str] | None = None
        self._cache: dict[str, str | None] = {}

    # -- desktop entries -----------------------------------------------------

    def _load_desktop_entries(self) -> None:
        by_id: dict[str, str] = {}
        by_class: dict[str, str] = {}
        for directory in APP_DIRS:
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.suffix != ".desktop":
                    continue
                icon, wm_class = self._parse_desktop(entry)
                if not icon:
                    continue
                stem = entry.stem
                by_id.setdefault(stem.lower(), icon)
                by_id.setdefault(stem.lower().replace("_", "-"), icon)
                if wm_class:
                    by_class.setdefault(wm_class.lower(), icon)
        self._desktop = by_id
        self._by_wm_class = by_class

    @staticmethod
    def _parse_desktop(path: Path) -> tuple[str | None, str | None]:
        icon = None
        wm_class = None
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for _ in range(120):
                    line = handle.readline()
                    if not line:
                        break
                    if line.startswith("Icon="):
                        icon = line[5:].strip()
                    elif line.startswith("StartupWMClass="):
                        wm_class = line[15:].strip()
                    elif line.startswith("[Desktop Action"):
                        break
        except OSError:
            return None, None
        return icon, wm_class

    def _icon_name_for(self, app_class: str) -> str:
        if self._desktop is None:
            self._load_desktop_entries()
        assert self._desktop is not None and self._by_wm_class is not None
        key = app_class.strip().lower()
        candidates = [key, key + ".desktop"]
        # org.gnome.Nautilus -> nautilus, md.obsidian.Obsidian -> obsidian
        tail = key.rsplit(".", 1)[-1]
        candidates.extend([tail, tail + ".desktop"])
        for candidate in candidates:
            if candidate in self._desktop:
                return self._desktop[candidate]
            if candidate in self._by_wm_class:
                return self._by_wm_class[candidate]
        # Many apps name their icon after the class or its last component.
        return self._by_wm_class.get(key) or tail or key

    # -- icon files ----------------------------------------------------------

    def _find_icon(self, name: str) -> Path | None:
        if not name:
            return None
        for theme in (self.theme, "Yaru", "Adwaita", "hicolor"):
            for base in ICON_DIRS:
                root = base / theme
                if not root.is_dir():
                    continue
                found = self._search_theme(root, name)
                if found:
                    return found
        # Flat fallback: some packages only ship one unthemed file.
        for base in ICON_DIRS:
            found = self._search_theme(base, name, max_depth=3)
            if found:
                return found
        return None

    @staticmethod
    def _search_theme(root: Path, name: str, max_depth: int = 2) -> Path | None:
        best: tuple[int, Path] | None = None
        try:
            for path in root.glob(f"{'*/' * max(max_depth - 1, 0)}apps/{name}.*"):
                suffix = path.suffix.lower()
                if suffix not in (".svg", ".png"):
                    continue
                if suffix == ".svg":
                    score = 1000
                else:
                    match = re.search(r"(\d+)x\d+", str(path))
                    size = int(match.group(1)) if match else 48
                    score = 100 - abs(size - 64)
                if best is None or score > best[0]:
                    best = (score, path)
        except OSError:
            return None
        return best[1] if best else None

    # -- public --------------------------------------------------------------

    def refresh_theme(self) -> None:
        """Colours and icons change together when Omarchy switches themes."""
        self.theme = _icon_theme()
        self._desktop = None
        self._by_wm_class = None
        self._cache.clear()

    def data_uri(self, app_class: str) -> str | None:
        key = (app_class or "").strip()
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]
        uri = None
        path = self._find_icon(self._icon_name_for(key))
        if path is not None:
            try:
                if path.stat().st_size <= MAX_ICON_BYTES:
                    payload = base64.b64encode(path.read_bytes()).decode("ascii")
                    mime = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
                    uri = f"data:{mime};base64,{payload}"
            except OSError:
                uri = None
        self._cache[key] = uri
        return uri
