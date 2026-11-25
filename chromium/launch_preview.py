"""Open a disposable Moltskin Chromium preview, or a persistent one by name."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__:
    from . import native
else:  # Direct invocation: python3 chromium/launch_preview.py
    import native

MODES = ("standard", "restricted")
MEDIA_MODES = ("standard", "blocked")


def profile_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError(
            "use 1–64 lowercase letters, digits, hyphens or underscores, starting with a letter or digit"
        )
    return value


def run_browser(binary: Path, user_data: Path, mode: str, media_capture: str, *, disposable: bool = False) -> int:
    args = [str(binary), f"--user-data-dir={user_data}", "--no-first-run", "--no-default-browser-check"]
    if mode == "restricted":
        args.append("--" + native.CAPABILITY)
    if media_capture == "blocked":
        args.append("--" + native.MEDIA_CAPTURE_CAPABILITY)
    if disposable and sys.platform == "darwin":
        args.append("--use-mock-keychain")
    with subprocess.Popen(args) as browser:
        try:
            return browser.wait()
        except KeyboardInterrupt:
            browser.terminate()
            return browser.wait()


def check_media_support(capabilities: frozenset[str], media_capture: str) -> None:
    if media_capture == "blocked" and native.MEDIA_CAPTURE_CAPABILITY not in capabilities:
        raise ValueError(f"this binary does not report the {native.MEDIA_CAPTURE_CAPABILITY} capability")


def launch(profile: Path, binary: Path, capabilities: frozenset[str],
           requested_mode: str | None, requested_media: str | None) -> int:
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    busy = f"profile {profile.name!r} is already open; quit its browser before relaunching"
    with (profile / "preview.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(busy) from None
        user_data = profile / "browser"
        if native.chromium_owns_directory(user_data):
            raise ValueError(busy)

        settings = profile / "permissions.json"
        try:
            saved = json.loads(settings.read_text(encoding="utf-8"))
        except FileNotFoundError:
            legacy = profile / "local-fonts"
            saved = {"local_fonts": legacy.read_text(encoding="utf-8").strip() if legacy.exists() else "standard",
                     "media_capture": "standard"}
        if (not isinstance(saved, dict) or saved.get("local_fonts") not in MODES
                or saved.get("media_capture") not in MEDIA_MODES):
            raise ValueError(f"invalid Chromium preview permissions for {profile.name!r}")
        mode = requested_mode or saved["local_fonts"]
        media_capture = requested_media or saved["media_capture"]
        check_media_support(capabilities, media_capture)
        updated = {"local_fonts": mode, "media_capture": media_capture}
        if updated != saved or not settings.exists():
            temporary = settings.with_suffix(".tmp")
            temporary.write_text(json.dumps(updated) + "\n", encoding="utf-8")
            temporary.replace(settings)

        print(f"Opening {profile.name} with local fonts {mode} and media capture {media_capture}.", flush=True)
        return run_browser(binary, user_data, mode, media_capture)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", type=profile_name, help="environment to save; omit for a disposable preview")
    parser.add_argument("--local-fonts", choices=MODES, help="font access policy; new profiles default to standard")
    parser.add_argument("--media-capture", choices=MEDIA_MODES, help="camera and microphone policy (default: standard)")
    parser.add_argument(
        "--binary", type=Path, default=native.default_binary(),
        help="Moltskin Chromium executable or .app bundle (default: newest supported local build)",
    )
    args = parser.parse_args()
    try:
        binary, _, capabilities = native.probe_capabilities(args.binary)
        if args.profile is None:
            media_capture = args.media_capture or "standard"
            check_media_support(capabilities, media_capture)
            with TemporaryDirectory(prefix="moltskin-preview-") as temporary:
                print("Opening a disposable preview. Its data will be removed when Chromium exits.", flush=True)
                return run_browser(binary, Path(temporary), args.local_fonts or "standard", media_capture, disposable=True)
        repo = Path(__file__).resolve().parents[1]
        data = Path(os.environ.get("MOLTSKIN_DATA_DIR", repo / "data")).expanduser().resolve()
        return launch(data / "chromium-preview" / args.profile, binary, capabilities, args.local_fonts, args.media_capture)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Cannot launch Chromium preview: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
