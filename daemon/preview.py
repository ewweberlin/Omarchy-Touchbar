"""Render every strip state with fake data, for looking at before hardware.

    python3 -m daemon.preview [--out preview] [--scale 2]
    python3 -m daemon.preview --themes [--out preview/themes]

--themes renders the home strip once per installed theme, so a palette change
can be checked without switching the desktop theme 25 times.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from .config import CANVAS_H, CANVAS_W, load_settings
from .icons import IconResolver
from .render import Model, Renderer
from .stats import Sample
from .theme import Palette, load_palette

THEME_ROOTS = (
    Path("/usr/share/omarchy/themes"),
    Path.home() / ".config/omarchy/themes",
)

FAKE_STATS = Sample(
    cpu_pct=23.0,
    cpu_temp=71.0,
    gpu_pct=8.0,
    gpu_mhz=650.0,
    ram_pct=54.0,
    ram_used_gb=8.6,
    ram_total_gb=16.0,
    fan_rpm=4980.0,
    fan_max=6336.0,
    net_down=1.8 * 1024 * 1024,
    net_up=240 * 1024,
    disk_pct=62.0,
    disk_free_gb=145.0,
)

WORKSPACES = (
    {"id": 1, "active": False, "occupied": True},
    {"id": 2, "active": True, "occupied": True},
    {"id": 3, "active": False, "occupied": True},
    {"id": 4, "active": False, "occupied": False},
    {"id": 5, "active": False, "occupied": False},
)

WINDOWS = (
    "chromium",
    "foot",
    "org.gnome.Nautilus",
    "md.obsidian.Obsidian",
    "org.gnome.Calculator",
    "code",
    "spotify",
    "org.gnome.Loupe",
)


def window_cards(icons: IconResolver) -> tuple[dict, ...]:
    return tuple(
        {
            "address": f"0x{index:012x}",
            "app": icons.app_name(app_class),
            "active": index == 0,
            "icon": icons.data_uri(app_class),
        }
        for index, app_class in enumerate(WINDOWS)
    )


def models(icons: IconResolver) -> dict[str, Model]:
    stats = FAKE_STATS.as_dict()
    return {
        "home": Model(
            state="home",
            workspaces=WORKSPACES,
            stats=stats,
            brightness=72,
            volume=34,
        ),
        "controls": Model(
            state="controls",
            workspaces=WORKSPACES,
            stats=stats,
            brightness=72,
            volume=34,
            muted=False,
        ),
        "windows": Model(
            state="windows",
            workspaces=WORKSPACES,
            stats=stats,
            brightness=72,
            volume=34,
            windows=window_cards(icons),
            window_scroll=0.0,
        ),
        "vitals": Model(
            state="vitals",
            workspaces=WORKSPACES,
            stats=stats,
            brightness=72,
            volume=34,
        ),
    }


def theme_palettes() -> dict[str, Path]:
    """Slug -> colors.toml for every installed theme, user themes last."""
    found: dict[str, Path] = {}
    for root in THEME_ROOTS:
        for colors in sorted(root.glob("*/colors.toml")):
            if colors.is_file():
                found[colors.parent.name] = colors
    return dict(sorted(found.items()))


def make_renderer(palette: Palette, settings: dict) -> Renderer:
    """Renderer configured from the same settings the daemon uses."""
    return Renderer(
        palette,
        settings["font_family"],
        bool(settings.get("action_labels", True)),
        settings.get("action_icon_size", 34),
    )


def render_themes(out: Path, scale: float, settings: dict) -> int:
    home = models(IconResolver())["home"]
    out.mkdir(parents=True, exist_ok=True)
    palettes = theme_palettes()
    if not palettes:
        print("no themes found")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        for slug, colors in palettes.items():
            svg = make_renderer(load_palette(colors), settings).render(home)
            svg_path = Path(tmp) / f"{slug}.svg"
            svg_path.write_text(svg, encoding="utf-8")
            png_path = out / f"{slug}.png"
            try:
                subprocess.run(
                    [
                        "rsvg-convert",
                        "-w",
                        str(int(CANVAS_W * scale)),
                        "-h",
                        str(int(CANVAS_H * scale)),
                        "-o",
                        str(png_path),
                        str(svg_path),
                    ],
                    check=True,
                    capture_output=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"{slug}: skipped ({exc})")
                continue
            print(png_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="daemon.preview")
    parser.add_argument("--out", default=None)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument(
        "--themes",
        action="store_true",
        help="render the home strip once per installed theme",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.themes:
        return render_themes(Path(args.out or "preview/themes"), args.scale, settings)

    out = Path(args.out or "preview")
    out.mkdir(parents=True, exist_ok=True)
    palette = load_palette()
    renderer = make_renderer(palette, settings)
    icons = IconResolver()

    for name, model in models(icons).items():
        svg = renderer.render(model)
        svg_path = out / f"{name}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        png_path = out / f"{name}.png"
        width = int(CANVAS_W * args.scale)
        try:
            subprocess.run(
                [
                    "rsvg-convert",
                    "-w",
                    str(width),
                    "-h",
                    str(int(CANVAS_H * args.scale)),
                    "-o",
                    str(png_path),
                    str(svg_path),
                ],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"{name}: svg only ({exc})")
            continue
        print(f"{png_path} ({svg_path.stat().st_size} bytes of SVG)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
