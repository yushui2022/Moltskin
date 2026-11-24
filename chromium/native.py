"""Locate the Moltskin Chromium build and inspect its native capabilities."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

CAPABILITY = "moltskin-disable-local-font-access"


def default_binary() -> Path:
    configured = os.environ.get("MOLTSKIN_CHROMIUM_BINARY")
    if configured:
        return Path(configured).expanduser()
    out = Path.home() / "chromium-baseline" / "src" / "out"
    executable = "Chromium.app" if sys.platform == "darwin" else "chrome"
    panel_build = out / "MoltskinPanel" / executable
    return panel_build if panel_build.exists() else out / "MoltskinFont" / executable


def probe(path: Path, *, capability: str = CAPABILITY) -> tuple[Path, str]:
    path = path.expanduser().resolve()
    if path.suffix == ".app":
        path = path / "Contents" / "MacOS" / "Chromium"
    result = subprocess.run(
        [path, "--version", "--moltskin-capabilities"], capture_output=True,
        timeout=15, check=True, text=True, errors="replace",
    )
    lines = result.stdout.splitlines()
    if CAPABILITY not in lines:
        raise ValueError(f"this binary does not report the Moltskin Local Font Access capability: {path}")
    if capability not in lines:
        raise ValueError(f"this binary does not report the {capability} capability: {path}")
    version = re.search(r"\b\d+(?:\.\d+){3}\b", lines[0])
    return path, version.group() if version else lines[0].strip()


def chromium_owns_directory(user_data: Path) -> bool:
    # Chromium also creates a lock when launched directly, or if our launcher exits first.
    try:
        owner = os.readlink(user_data / "SingletonLock")
    except FileNotFoundError:
        return False
    host, _, pid = owner.rpartition("-")
    if host == socket.gethostname() and pid.isascii() and pid.isdigit() and 0 < int(pid) <= 2**31 - 1:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False  # Chromium cleans its own stale lock on startup.
        except PermissionError:
            pass
    return True
