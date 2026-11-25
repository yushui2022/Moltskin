"""Start or reuse the local service and open the desktop environment manager."""
from __future__ import annotations

import argparse
import errno
import fcntl
import hmac
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from . import native
else:
    import native

ROOT = Path(__file__).resolve().parents[1]
ACTIVATE_MANAGER = """
ObjC.import('AppKit')
ObjC.import('CoreGraphics')
function isManagerWindow(window, pid) {
    // Chromium also owns layer-zero menu strips and small accessory surfaces.
    const bounds = window.kCGWindowBounds
    return window.kCGWindowOwnerPID === pid && window.kCGWindowLayer === 0 &&
        bounds && bounds.Width >= 200 && bounds.Height >= 120
}
function run(args) {
    const pid = Number(args[0])
    const until = Date.now() + Number(args[1] || 0)
    do {
        const windows = $.CGWindowListCopyWindowInfo($.kCGWindowListExcludeDesktopElements, 0)
        const rows = ObjC.deepUnwrap(ObjC.castRefToObject(windows))
        if (rows.some(w => isManagerWindow(w, pid))) {
            const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(pid)
            return app && app.activateWithOptions(3) ? 'yes' : 'no'
        }
        if (Date.now() >= until) return 'no'
        $.NSThread.sleepForTimeInterval(0.1)
    } while (true)
}
"""


def service_matches(port: int, data: Path) -> bool:
    challenge = secrets.token_hex(16)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        token = (data / "api.token").read_text(encoding="utf-8").strip()
        if not token:
            return False
        conn.request("GET", f"/api/v1/health?panel_challenge={challenge}")
        response = conn.getresponse()
        if response.status != 200:
            return False
        health = json.loads(response.read(4096))
        expected = hmac.digest(token.encode(), b"moltskin-panel-v1\0" + challenge.encode(), "sha256").hex()
        if not isinstance(health, dict):
            return False
        proof = health.get("panel_proof")
        return isinstance(proof, str) and hmac.compare_digest(proof.encode(), expected.encode())
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def ensure_service(port: int, data: Path, log) -> None:
    child = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen()
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
        else:
            env = dict(os.environ, MOLTSKIN_PORT=str(port), MOLTSKIN_HOST="127.0.0.1",
                       MOLTSKIN_DATA_DIR=str(data), MOLTSKIN_MANAGER_URL=f"http://127.0.0.1:{port}")
            fd = listener.fileno()
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--serve-fd", str(fd)],
                cwd=ROOT, env=env, pass_fds=(fd,), start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
    deadline = time.monotonic() + (25 if child else 3)
    while time.monotonic() < deadline:
        if service_matches(port, data):
            return
        if child and child.poll() is not None:
            break
        time.sleep(0.15)
    if child is None:
        raise RuntimeError(f"Port {port} is occupied by a different service or data directory.")
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    raise RuntimeError(f"The local Moltskin service did not start. See {log.name}.")


def manager_pid(profile: Path, port: int, saved_port: Path) -> str | None:
    if not native.chromium_owns_directory(profile):
        return None
    if not saved_port.is_file() or saved_port.read_text().strip() != str(port):
        raise RuntimeError("Close the existing Moltskin manager window before changing its port.")
    host, _, pid = os.readlink(profile / "SingletonLock").rpartition("-")
    if host != socket.gethostname() or not pid.isascii() or not pid.isdigit():
        raise RuntimeError("The manager profile is in use by another browser.")
    return pid


def open_manager(port: int, data: Path) -> None:
    binary, _ = native.probe(native.default_binary(), capability="moltskin-panel")
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (data / "panel.lock").open("a") as lock, (data / "panel.log").open("a") as log:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ensure_service(port, data, log)
        saved_port = data / "manager-port"
        app_switch = f"--app=http://127.0.0.1:{port}/#/windows"
        profile = data / "manager-desktop"
        previous = data / "manager-browser"
        if not profile.exists() and previous.is_dir():
            # Reuse the prior manager's display preferences on a cold upgrade.
            # A still-open tabbed manager keeps its process and all its tabs.
            pid = manager_pid(previous, port, saved_port)
            if pid is None:
                profile = previous
            else:
                command = subprocess.run(["ps", "-ww", "-p", pid, "-o", "command="],
                                         capture_output=True, text=True, timeout=5, check=False)
                if command.returncode == 0 and app_switch in command.stdout.split():
                    profile = previous
        profile.mkdir(mode=0o700, exist_ok=True)
        pid = manager_pid(profile, port, saved_port)
        if pid and sys.platform == "darwin":
            # A double click can arrive after the Chromium lock but before its first window.
            result = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", "-e", ACTIVATE_MANAGER, pid, "3000"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "yes":
                return
        saved_port.write_text(str(port), encoding="utf-8")
        args = [str(binary), f"--user-data-dir={profile}", f"--moltskin-panel-port={port}",
                "--no-first-run", "--no-default-browser-check", app_switch]
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        # Release the launch lock once Chromium owns its user-data directory.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if native.chromium_owns_directory(profile):
                return
            if child.poll() is not None:
                raise RuntimeError(f"The Chromium manager exited during startup. See {log.name}.")
            time.sleep(0.1)
        raise RuntimeError(f"Chromium has not opened the manager yet. See {log.name}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--show-errors", action="store_true", help="show a macOS alert if launch fails")
    args = parser.parse_args()
    try:
        port = int(os.environ.get("MOLTSKIN_PORT", "8765"))
        if not 1 <= port <= 65535:
            raise ValueError("MOLTSKIN_PORT must be between 1 and 65535")
        if args.serve_fd is not None:
            sys.path.insert(0, str(ROOT))
            import uvicorn
            server = uvicorn.Server(uvicorn.Config("moltskin.services.core.main:app", host="127.0.0.1",
                                                   port=port, access_log=False))
            with socket.socket(fileno=args.serve_fd) as listener:
                server.run(sockets=[listener])
            return 0
        data = Path(os.environ.get("MOLTSKIN_DATA_DIR", ROOT / "data")).expanduser().resolve()
        open_manager(port, data)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        message = str(exc)
        print(message, file=sys.stderr)
        if args.show_errors and sys.platform == "darwin":
            alert = 'on run argv\n display alert "Moltskin" message (item 1 of argv) as critical\nend run'
            subprocess.run(["/usr/bin/osascript", "-e", alert, message], check=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
