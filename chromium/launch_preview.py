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

# The non-default value and native switch for each environment permission.
POLICIES = {
    "local_fonts": ("restricted", native.CAPABILITY),
    "media_capture": ("blocked", native.MEDIA_CAPTURE_CAPABILITY),
    "geolocation": ("blocked", native.GEOLOCATION_CAPABILITY),
    "canvas_readback": ("blocked", native.CANVAS_READBACK_CAPABILITY),
    "webgl_debug_info": ("blocked", native.WEBGL_DEBUG_INFO_CAPABILITY),
}


def profile_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError(
            "use 1–64 lowercase letters, digits, hyphens or underscores, starting with a letter or digit"
        )
    return value


def permission_switches(settings: dict[str, str], capabilities: frozenset[str]) -> list[str]:
    switches = []
    for key, (restricted, switch) in POLICIES.items():
        value = settings.get(key, "standard")
        if value == "standard":
            continue
        if value != restricted:
            raise ValueError(f"invalid preview setting for {key}: {value!r}")
        if switch not in capabilities:
            raise ValueError(f"this binary does not report the {switch} capability")
        switches.append("--" + switch)
    return switches


def run_browser(binary: Path, user_data: Path, switches: list[str], *, disposable: bool = False) -> int:
    args = [str(binary), f"--user-data-dir={user_data}", "--no-first-run", "--no-default-browser-check", *switches]
    if disposable and sys.platform == "darwin":
        args.append("--use-mock-keychain")
    with subprocess.Popen(args) as browser:
        try:
            return browser.wait()
        except KeyboardInterrupt:
            browser.terminate()
            return browser.wait()


def launch(profile: Path, binary: Path, capabilities: frozenset[str], overrides: dict[str, str]) -> int:
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
        if not isinstance(saved, dict) or not {"local_fonts", "media_capture"} <= saved.keys():
            raise ValueError(f"invalid Chromium preview permissions for {profile.name!r}")
        settings_for_launch = dict.fromkeys(POLICIES, "standard") | saved | overrides
        switches = permission_switches(settings_for_launch, capabilities)
        if settings_for_launch != saved or not settings.exists():
            temporary = settings.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings_for_launch) + "\n", encoding="utf-8")
            temporary.replace(settings)

        description = ", ".join(f"{key}={settings_for_launch[key]}" for key in POLICIES)
        print(f"Opening {profile.name} with {description}.", flush=True)
        return run_browser(binary, user_data, switches)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", type=profile_name, help="environment to save; omit for a disposable preview")
    parser.add_argument("--local-fonts", choices=("standard", "restricted"), help="font access policy; new profiles default to standard")
    parser.add_argument("--media-capture", choices=("standard", "blocked"), help="camera and microphone policy (default: standard)")
    parser.add_argument("--geolocation", choices=("standard", "blocked"), help="site location policy (default: standard)")
    parser.add_argument("--canvas-readback", choices=("standard", "blocked"), help="2D canvas pixel readback policy")
    parser.add_argument("--webgl-debug-info", choices=("standard", "blocked"), help="WebGL debug renderer information policy")
    parser.add_argument(
        "--binary", type=Path, default=native.default_binary(),
        help="Moltskin Chromium executable or .app bundle (default: newest supported local build)",
    )
    args = parser.parse_args()
    try:
        binary, _, capabilities = native.probe_capabilities(args.binary)
        overrides = {key: getattr(args, key) for key in POLICIES if getattr(args, key) is not None}
        if args.profile is None:
            switches = permission_switches(dict.fromkeys(POLICIES, "standard") | overrides, capabilities)
            with TemporaryDirectory(prefix="moltskin-preview-") as temporary:
                print("Opening a disposable preview. Its data will be removed when Chromium exits.", flush=True)
                return run_browser(binary, Path(temporary), switches, disposable=True)
        repo = Path(__file__).resolve().parents[1]
        data = Path(os.environ.get("MOLTSKIN_DATA_DIR", repo / "data")).expanduser().resolve()
        return launch(data / "chromium-preview" / args.profile, binary, capabilities, overrides)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Cannot launch Chromium preview: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
