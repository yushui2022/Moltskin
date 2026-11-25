"""Exercise the preview CLI with a local stand-in for the native executable."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None


CLI = Path(__file__).resolve().parents[1] / "chromium" / "launch_preview.py"
SWITCH = "--moltskin-disable-local-font-access"
MEDIA_SWITCH = "--moltskin-block-media-capture"
LOCATION_SWITCH = "--moltskin-block-geolocation"


@unittest.skipUnless(fcntl is not None and sys.platform in {"darwin", "linux"}, "macOS/Linux preview")
class ChromiumPreviewTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve()
        self.binary = self.data / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text(
            f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ["--version", "--moltskin-capabilities"]:
    print("Chromium Preview")
    print(os.environ.get("TEST_CAPABILITY", "moltskin-disable-local-font-access\\nmoltskin-block-media-capture\\nmoltskin-block-geolocation"))
else:
    (Path(os.environ["MOLTSKIN_DATA_DIR"]) / "last-launch").write_text(json.dumps(sys.argv[1:]))
''', encoding="utf-8"
        )
        self.binary.chmod(0o700)

    def run_cli(self, *args, **env):
        return subprocess.run(
            [sys.executable, CLI, *args, "--binary", self.binary.parents[2]],
            env={**os.environ, "MOLTSKIN_DATA_DIR": str(self.data), **env},
            capture_output=True, text=True, timeout=20, check=False,
        )

    def browser_args(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.data / "last-launch").read_text())

    def test_disposable_previews_get_fresh_directories_and_clean_up(self):
        directories = []
        for options in [("--local-fonts", "restricted"), ()]:
            args = self.browser_args(*options)
            self.assertEqual(SWITCH in args, bool(options))
            self.assertEqual("--use-mock-keychain" in args, sys.platform == "darwin")
            data_arg, = (arg for arg in args if arg.startswith("--user-data-dir="))
            user_data = Path(data_arg.split("=", 1)[1])
            directories.append(user_data)
            self.assertFalse(user_data.exists())
        self.assertNotEqual(*directories)
        self.assertFalse((self.data / "chromium-preview").exists())

    def test_per_profile_switch_and_restart(self):
        a = self.browser_args("one", "--local-fonts", "restricted")
        b = self.browser_args("two")
        self.assertIn(SWITCH, a)
        self.assertNotIn(SWITCH, b)
        self.assertNotIn("--use-mock-keychain", a)
        self.assertNotIn("--use-mock-keychain", b)
        base = self.data / "chromium-preview"
        self.assertIn(f"--user-data-dir={base / 'one/browser'}", a)
        self.assertIn(f"--user-data-dir={base / 'two/browser'}", b)
        self.assertEqual(self.browser_args("one"), a)
        self.assertNotIn(SWITCH, self.browser_args("one", "--local-fonts", "standard"))
        self.assertNotIn(SWITCH, self.browser_args("one"))

    def test_rejects_unpatched_build_before_saving_or_launching(self):
        self.browser_args("one")
        last = self.data / "last-launch"
        last.unlink()
        result = self.run_cli("one", "--local-fonts", "restricted", TEST_CAPABILITY="unsupported")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not report", result.stderr)
        self.assertFalse(last.exists())
        self.assertNotIn(SWITCH, self.browser_args("one"))

    def test_media_policy_survives_restart_and_requires_patched_binary(self):
        profile = self.data / "chromium-preview" / "one"
        profile.mkdir(parents=True)
        (profile / "local-fonts").write_text("restricted\n", encoding="utf-8")
        args = self.browser_args("one", "--media-capture", "blocked")
        self.assertIn(SWITCH, args)
        self.assertIn(MEDIA_SWITCH, args)
        self.assertEqual(self.browser_args("one"), args)
        self.assertNotIn(MEDIA_SWITCH, self.browser_args("two"))

        last = self.data / "last-launch"
        last.unlink()
        result = self.run_cli("one", TEST_CAPABILITY=SWITCH.removeprefix("--"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("moltskin-block-media-capture", result.stderr)
        self.assertFalse(last.exists())
        self.assertIn(MEDIA_SWITCH, self.browser_args("one"))
        self.assertNotIn(MEDIA_SWITCH, self.browser_args("one", "--media-capture", "standard"))

    def test_geolocation_with_existing_settings_and_legacy_binary(self):
        self.browser_args("updated", "--media-capture", "blocked")
        policy_file = self.data / "chromium-preview" / "updated" / "permissions.json"
        settings = json.loads(policy_file.read_text())
        settings.pop("geolocation")
        policy_file.write_text(json.dumps(settings))
        args = self.browser_args("updated")
        self.assertNotIn(LOCATION_SWITCH, args)
        self.assertIn(MEDIA_SWITCH, args)
        args = self.browser_args("updated", "--geolocation", "blocked")
        self.assertIn(LOCATION_SWITCH, args)
        self.assertIn(MEDIA_SWITCH, args)
        self.assertEqual(self.browser_args("updated"), args)
        last = self.data / "last-launch"
        last.unlink()
        previous_capabilities = "\n".join(value.removeprefix("--") for value in (SWITCH, MEDIA_SWITCH))
        result = self.run_cli("updated", TEST_CAPABILITY=previous_capabilities)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(LOCATION_SWITCH.removeprefix("--"), result.stderr)
        self.assertFalse(last.exists())
        self.assertNotIn(LOCATION_SWITCH, self.browser_args("updated", "--geolocation", "standard"))

    def test_active_launches_cannot_change_saved_mode(self):
        self.browser_args("one")
        profile = self.data / "chromium-preview" / "one"
        with (profile / "preview.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertIn("already open", self.run_cli("one", "--local-fonts", "restricted").stderr)
        user_data = profile / "browser"
        user_data.mkdir()
        native_lock = user_data / "SingletonLock"
        native_lock.symlink_to(f"{socket.gethostname()}-{os.getpid()}")
        self.assertIn("already open", self.run_cli("one", "--local-fonts", "restricted").stderr)
        self.assertEqual(json.loads((profile / "permissions.json").read_text())["local_fonts"], "standard")
        native_lock.unlink()
        self.assertIn(SWITCH, self.browser_args("one", "--local-fonts", "restricted"))

    def test_rejects_path_as_profile(self):
        result = self.run_cli("../one")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.data / "last-launch").exists())


if __name__ == "__main__":
    unittest.main()
