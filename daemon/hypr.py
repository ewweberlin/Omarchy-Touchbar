"""Hyprland state: workspaces and the active workspace's windows.

A background thread tails the Hyprland event socket so the strip reacts to
window changes immediately; a slow poll covers a missed socket reconnect.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

from .config import HYPR_INSTANCE, HYPR_SOCKET_DIR

INTERESTING = (
    "workspace",
    "openwindow",
    "closewindow",
    "movewindow",
    "activewindow",
    "focusedmon",
    "createworkspace",
    "destroyworkspace",
)


def _hyprctl(*args: str) -> object | None:
    try:
        result = subprocess.run(
            ["hyprctl", *args],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


_ADDRESS = re.compile(r"^0x[0-9a-fA-F]+$")


def _eval(code: str) -> None:
    try:
        subprocess.run(
            ["hyprctl", "eval", code],
            capture_output=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def dispatch_workspace(workspace_id: int) -> None:
    """Switch workspaces through Hyprland's Lua dispatcher.

    This Omarchy build has no legacy `hyprctl dispatch <name>`; the Lua
    shorthand is `hl.dispatch(hl.dsp.focus({ workspace = "N" }))`.
    """
    _eval(f'hl.dispatch(hl.dsp.focus({{ workspace = "{int(workspace_id)}" }}))')


def focus_window(address: str) -> None:
    if not _ADDRESS.match(address or ""):
        return
    # A bare hex address is not a selector; Hyprland's Lua focus dispatcher
    # wants `address:0x...`. bring_to_top() then acts on the newly focused
    # window, raising it above floating siblings.
    _eval(f'hl.dispatch(hl.dsp.focus({{ window = "address:{address}" }}))')
    _eval("hl.dispatch(hl.dsp.window.bring_to_top())")


class Hyprland:
    def __init__(self, workspace_count: int = 5, first: int = 1) -> None:
        self.workspace_count = workspace_count
        self.first = first
        self.workspaces: list[dict] = []
        self.windows: list[dict] = []
        self.active_workspace: int | None = None
        self._pending = True
        self._last = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- socket --------------------------------------------------------------

    def start(self) -> None:
        if not HYPR_INSTANCE:
            return
        self._thread = threading.Thread(target=self._watch, name="hypr-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        path = HYPR_SOCKET_DIR / HYPR_INSTANCE / ".socket2.sock"
        while not self._stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(2.0)
                    sock.connect(str(path))
                    buffer = b""
                    while not self._stop.is_set():
                        try:
                            chunk = sock.recv(4096)
                        except socket.timeout:
                            continue
                        except OSError:
                            break
                        if not chunk:
                            break
                        buffer += chunk
                        while b"\n" in buffer:
                            line, _, buffer = buffer.partition(b"\n")
                            name = line.split(b">>", 1)[0].decode("ascii", "replace")
                            if name in INTERESTING:
                                self._pending = True
            except (OSError, ValueError):
                pass
            self._stop.wait(1.0)

    # -- polling -------------------------------------------------------------

    def maybe_refresh(self, now: float, max_age: float = 4.0) -> bool:
        if not self._pending and now - self._last < max_age:
            return False
        self._pending = False
        self._last = now
        return self.refresh()

    def refresh(self) -> bool:

        workspaces = _hyprctl("-j", "workspaces")
        clients = _hyprctl("-j", "clients")
        active = _hyprctl("-j", "activeworkspace")
        if not isinstance(workspaces, list) or not isinstance(clients, list):
            return False

        active_id = None
        if isinstance(active, dict):
            try:
                active_id = int(active.get("id"))
            except (TypeError, ValueError):
                active_id = None
        self.active_workspace = active_id

        by_id = {}
        for workspace in workspaces:
            try:
                by_id[int(workspace.get("id"))] = workspace
            except (TypeError, ValueError):
                continue

        rows = []
        for offset in range(self.workspace_count):
            workspace_id = self.first + offset
            info = by_id.get(workspace_id)
            rows.append(
                {
                    "id": workspace_id,
                    "active": workspace_id == active_id,
                    "occupied": bool(info and int(info.get("windows") or 0) > 0),
                }
            )
        self.workspaces = rows

        windows = []
        for client in clients:
            workspace = client.get("workspace") or {}
            try:
                workspace_id = int(workspace.get("id"))
            except (TypeError, ValueError):
                continue
            if workspace_id != active_id:
                continue
            if client.get("hidden"):
                continue
            windows.append(
                {
                    "address": client.get("address") or "",
                    "class": client.get("class") or client.get("initialClass") or "",
                    "title": client.get("title") or "",
                    "active": False,
                    "history": client.get("focusHistoryID", 999),
                }
            )
        windows.sort(key=lambda w: w.get("history", 999))
        if windows:
            windows[0]["active"] = True
        self.windows = windows
        return True

    # -- actions -------------------------------------------------------------

    def switch_workspace(self, workspace_id: int) -> None:
        self.active_workspace = int(workspace_id)
        dispatch_workspace(workspace_id)

    def focus_window(self, address: str) -> None:
        focus_window(address)
