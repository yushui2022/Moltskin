"""Open a disposable Moltskin Chromium preview, or a persistent one by name."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

CAPABILITY = "moltskin-disable-local-font-access"
MODES = ("standard", "restricted")


def profile_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError(
            "use 1–64 lowercase letters, digits, hyphens or underscores, starting with a letter or digit"
        )
    return value


def checked_binary(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.suffix == ".app":
        path = path / "Contents" / "MacOS" / "Chromium"
    probe = subprocess.run(
        [path, "--version", "--moltskin-capabilities"], stdout=subprocess.PIPE, timeout=15, check=True
    )
    if CAPABILITY.encode() not in probe.stdout.splitlines():
        raise ValueError(f"this binary does not report the Moltskin Local Font Access capability: {path}")
    return path


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


def run_browser(binary: Path, user_data: Path, mode: str, *, disposable: bool = False) -> int:
    args = [str(binary), f"--user-data-dir={user_data}", "--no-first-run", "--no-default-browser-check"]
    if mode == "restricted":
        args.append("--" + CAPABILITY)
    if disposable and sys.platform == "darwin":
        args.append("--use-mock-keychain")
    with subprocess.Popen(args) as browser:
        try:
            return browser.wait()
        except KeyboardInterrupt:
            browser.terminate()
            return browser.wait()


def launch(profile: Path, binary: Path, requested_mode: str | None) -> int:
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    busy = f"profile {profile.name!r} is already open; quit its browser before relaunching"
    with (profile / "preview.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(busy) from None
        user_data = profile / "browser"
        if chromium_owns_directory(user_data):
            raise ValueError(busy)

        settings = profile / "local-fonts"
        try:
            saved = settings.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            saved = None
        if saved is not None and saved not in MODES:
            raise ValueError(f"invalid Local Font Access setting in {settings}")
        mode = requested_mode or saved or "standard"
        if mode != saved:
            temporary = settings.with_suffix(".tmp")
            temporary.write_text(mode + "\n", encoding="utf-8")
            temporary.replace(settings)

        print(f"Opening {profile.name} with Local Font Access set to {mode}.", flush=True)
        return run_browser(binary, user_data, mode)


def main() -> int:
    build = Path.home() / "chromium-baseline" / "src" / "out" / "MoltskinFont"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", type=profile_name, help="environment to save; omit for a disposable preview")
    parser.add_argument("--local-fonts", choices=MODES, help="font access policy; new profiles default to standard")
    parser.add_argument(
        "--binary", type=Path, default=build / ("Chromium.app" if sys.platform == "darwin" else "chrome"),
        help="Moltskin Chromium executable or .app bundle (default: local out/MoltskinFont build)",
    )
    args = parser.parse_args()
    try:
        binary = checked_binary(args.binary)
        if args.profile is None:
            with TemporaryDirectory(prefix="moltskin-preview-") as temporary:
                print("Opening a disposable preview. Its data will be removed when Chromium exits.", flush=True)
                return run_browser(binary, Path(temporary), args.local_fonts or "standard", disposable=True)
        repo = Path(__file__).resolve().parents[1]
        data = Path(os.environ.get("MOLTSKIN_DATA_DIR", repo / "data")).expanduser().resolve()
        return launch(data / "chromium-preview" / args.profile, binary, args.local_fonts)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Cannot launch Chromium preview: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
