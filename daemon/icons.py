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


def _prettify_class(app_class: str) -> str:
    """firefox -> Firefox, google-chrome -> Google Chrome, org.gnome.Foo -> Foo."""
    tail = app_class.rsplit(".", 1)[-1]
    tail = re.sub(r"[-_]+", " ", tail).strip()
    if not tail:
        return "Window"
    return tail.title() if tail.islower() else tail


class IconResolver:
    def __init__(self) -> None:
        self.theme = _icon_theme()
        self._desktop: dict[str, str] | None = None
        self._by_wm_class: dict[str, str] | None = None
        self._app_names: dict[str, str] = {}
        self._cache: dict[str, str | None] = {}
        self._name_cache: dict[str, str] = {}

    # -- desktop entries -----------------------------------------------------

    def _load_desktop_entries(self) -> None:
        by_id: dict[str, str] = {}
        by_class: dict[str, str] = {}
        names: dict[str, str] = {}
        for directory in APP_DIRS:
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.suffix != ".desktop":
                    continue
                name, icon, wm_class = self._parse_desktop(entry)
                stem = entry.stem.lower()
                if name:
                    names.setdefault(stem, name)
                    names.setdefault(stem.replace("_", "-"), name)
                    if wm_class:
                        names.setdefault(wm_class.lower(), name)
                if not icon:
                    continue
                by_id.setdefault(stem, icon)
                by_id.setdefault(stem.replace("_", "-"), icon)
                if wm_class:
                    by_class.setdefault(wm_class.lower(), icon)
        self._desktop = by_id
        self._by_wm_class = by_class
        self._app_names = names

    @staticmethod
    def _parse_desktop(path: Path) -> tuple[str | None, str | None, str | None]:
        """(Name, Icon, StartupWMClass) from a desktop entry's main section."""
        name = None
        icon = None
        wm_class = None
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for _ in range(120):
                    line = handle.readline()
                    if not line:
                        break
                    if line.startswith("Name="):
                        name = line[5:].strip()
                    elif line.startswith("Icon="):
                        icon = line[5:].strip()
                    elif line.startswith("StartupWMClass="):
                        wm_class = line[15:].strip()
                    elif line.startswith("[Desktop Action"):
                        break
        except OSError:
            return None, None, None
        return name, icon, wm_class

    @staticmethod
    def _candidates(app_class: str) -> list[str]:
        key = app_class.strip().lower()
        # org.gnome.Nautilus -> nautilus, md.obsidian.Obsidian -> obsidian
        tail = key.rsplit(".", 1)[-1]
        return [key, key + ".desktop", tail, tail + ".desktop"]

    def _icon_name_for(self, app_class: str) -> str:
        if self._desktop is None:
            self._load_desktop_entries()
        assert self._desktop is not None and self._by_wm_class is not None
        candidates = self._candidates(app_class)
        for candidate in candidates:
            if candidate in self._desktop:
                return self._desktop[candidate]
            if candidate in self._by_wm_class:
                return self._by_wm_class[candidate]
        # Many apps name their icon after the class or its last component.
        key = app_class.strip().lower()
        tail = key.rsplit(".", 1)[-1]
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
        self._app_names = {}
        self._cache.clear()
        self._name_cache.clear()

    def app_name(self, app_class: str) -> str:
        """Human-readable app name for a window class.

        The drawer paints this instead of the window title: titles can contain
        document names and message subjects, and the rendered SVG is published
        world-readable under /etc/tiny-dfr. Desktop-entry Name= and the class
        itself are public app identity, so they are safe to paint.
        """
        key = (app_class or "").strip()
        if not key:
            return "Window"
        if key in self._name_cache:
            return self._name_cache[key]
        if self._desktop is None:
            self._load_desktop_entries()
        name = ""
        for candidate in self._candidates(key):
            found = self._app_names.get(candidate)
            if found:
                name = found
                break
        if not name:
            name = _prettify_class(key)
        self._name_cache[key] = name
        return name

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
