"""Check a Moltskin Linux x86-64 build using two visible windows on a private X display."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

if __package__:
    from . import native
else:
    import native


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="path to the built Linux chrome executable")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        parser.error("run this acceptance check on Linux x86-64")
    if os.geteuid() == 0:
        parser.error("run as a regular user so Chromium can use its sandbox")
    for tool in ("xvfb-run", "Xvfb", "xauth", "xdotool"):
        if not shutil.which(tool):
            parser.error(f"{tool} is required (Debian/Ubuntu packages: xvfb, xauth, xdotool)")
    try:
        with args.binary.expanduser().open("rb") as executable:
            header = executable.read(20)
        if not header.startswith(b"\x7fELF\x02\x01") or header[18:20] != b"\x3e\x00":
            parser.error("the supplied chrome executable is not a Linux x86-64 ELF binary")
        binary, version, capabilities = native.probe_capabilities(args.binary)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    expected = json.loads((ROOT / "chromium/upstream.json").read_text())["version"]
    if version != expected:
        parser.error(f"expected pinned Chromium {expected}, got {version}")
    missing = native.DESKTOP_CAPABILITIES - capabilities
    if missing:
        parser.error("build lacks " + ", ".join(sorted(missing)))

    # xvfb-run allocates an authenticated display so the input checks never use a user's desktop.
    env = dict(os.environ, MOLTSKIN_CHROMIUM_BINARY=str(binary), MOLTSKIN_TEST_NATIVE_BROWSERS="1",
               MOLTSKIN_TEST_X11_INPUT="1", MOLTSKIN_HEADLESS="0", XDG_SESSION_TYPE="x11")
    env.pop("WAYLAND_DISPLAY", None)
    print(f"Checking Moltskin Chromium {version} from {binary}", flush=True)
    return subprocess.run([
        "xvfb-run", "-a", "-s", "-screen 0 1440x900x24 -nolisten tcp",
        sys.executable, "-m", "pytest", "-q", "tests/test_chromium_storage_isolation.py",
    ], cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
