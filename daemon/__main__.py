"""Entry point for both the supervisor and `python3 -m daemon`."""

from __future__ import annotations

import os
import sys

# The shell watches the plugin folder and reloads the plugin when files change.
# Importing our own modules would write daemon/__pycache__, so keep bytecode out
# of the installed plugin (Service.qml also passes -B, but this holds even when
# the daemon is started by hand with `python3 -m daemon`).
sys.dont_write_bytecode = True

if __package__ in (None, ""):
    # Service.qml launches this file directly, so there is no package context.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from daemon.daemon import main
else:
    from .daemon import main

if __name__ == "__main__":
    raise SystemExit(main())
