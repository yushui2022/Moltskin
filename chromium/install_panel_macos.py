"""Install a Finder launcher for this Moltskin checkout and its Python environment."""
from __future__ import annotations

import fcntl
import importlib.util
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "org.moltskin.local-panel"


def install(applications: Path, python: str) -> Path:
    applications.mkdir(parents=True, exist_ok=True)
    app = applications / "Moltskin.app"
    with (applications / ".moltskin-panel-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.lexists(app):
            if not app.is_symlink() and app.is_dir():
                try:
                    old = plistlib.loads((app / "Contents/Info.plist").read_bytes())
                except (OSError, plistlib.InvalidFileException):
                    old = {}
                ours = (isinstance(old, dict) and old.get("CFBundleIdentifier") == IDENTIFIER
                        and old.get("MoltskinCheckout") == str(ROOT))
            else:
                ours = False
            if not ours:
                raise FileExistsError(f"Another application already exists at {app}")
        with tempfile.TemporaryDirectory(dir=applications) as tmp:
            staged = Path(tmp) / "Moltskin.app"
            contents = staged / "Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            (contents / "MacOS").mkdir()
            executable = contents / "MacOS/Moltskin"
            env_lines = []
            for key in ("MOLTSKIN_DATA_DIR", "MOLTSKIN_CHROMIUM_BINARY", "MOLTSKIN_CHROMIUM_BUILDS_DIR", "MOLTSKIN_PORT"):
                value = os.environ.get(key)
                if value:
                    if key != "MOLTSKIN_PORT":
                        value = str(Path(value).expanduser().resolve())
                    env_lines.append(f"export {key}={shlex.quote(value)}\n")
            executable.write_text("#!/bin/sh\n" + "".join(env_lines) + "exec " + shlex.join([
                python, str(ROOT / "chromium/open_panel.py"), "--show-errors",
            ]) + "\n", encoding="utf-8")
            executable.chmod(0o755)
            (contents / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": IDENTIFIER, "CFBundleExecutable": "Moltskin",
                "CFBundleName": "Moltskin", "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1", "CFBundleIconFile": "Moltskin.icns",
                "LSUIElement": True, "MoltskinCheckout": str(ROOT),
            }))
            icons = Path(tmp) / "Moltskin.iconset"
            icons.mkdir()
            for size in (16, 32, 64, 128, 256, 512):
                target = icons / f"icon_{size}x{size}.png"
                subprocess.run(["/usr/bin/sips", "-z", str(size), str(size), str(ROOT / "web/logo.png"),
                                "--out", str(target)], check=True, stdout=subprocess.DEVNULL)
                if size in (32, 64, 256, 512):
                    half = size // 2
                    os.link(target, icons / f"icon_{half}x{half}@2x.png")
                if size == 64:
                    target.unlink()  # iconutil accepts 32@2x but does not define a 64pt slot.
            subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(icons),
                            "-o", str(resources / "Moltskin.icns")], check=True)
            if app.exists():
                for name in ("Contents/Resources/Moltskin.icns", "Contents/MacOS/Moltskin", "Contents/Info.plist"):
                    destination = app / name
                    if destination.parent.resolve() != destination.parent:
                        raise ValueError(f"Unexpected symlink inside {app}")
                    os.replace(staged / name, destination)
            else:
                staged.rename(app)
    return app


def main() -> int:
    if sys.platform != "darwin":
        print("The Finder launcher requires macOS.", file=sys.stderr)
        return 1
    if any(importlib.util.find_spec(mod) is None for mod in ("uvicorn", "camoufox", "fastapi")):
        print("Run this installer with the Python environment used for run.py.", file=sys.stderr)
        return 1
    try:
        print(install(Path.home() / "Applications", sys.executable))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
