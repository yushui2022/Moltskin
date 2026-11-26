"""Open the server's Moltskin Chromium window in a local browser over SSH."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import html
import http.client
import ipaddress
import json
from pathlib import Path
import re
import select
import shlex
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlencode
import webbrowser


SSH = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
PREFERRED_PORT = 18820


def ssh_host(value: str) -> str:
    user, separator, host = value.rpartition("@")
    if separator and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
        raise argparse.ArgumentTypeError("invalid SSH user")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host):
        try:
            ipaddress.IPv6Address(host.removeprefix("[").removesuffix("]"))
        except ValueError:
            raise argparse.ArgumentTypeError("use an SSH host alias, hostname or IP address") from None
        if "%" in host or host.startswith("[") != host.endswith("]"):
            raise argparse.ArgumentTypeError("use an SSH alias for scoped IPv6 addresses")
    return value


def remote_path(path: str) -> str:
    """Quote one shell word and expand only the remote user's home directory."""
    if not path or any(not c.isprintable() for c in path):
        raise ValueError("remote paths must be nonempty printable text")
    if path.startswith("/"):
        return shlex.quote(path)
    if path.startswith("~") and path != "~" and not path.startswith("~/"):
        raise ValueError("specify an absolute path or one relative to your SSH user's home")
    return '"$HOME"/' + shlex.quote("" if path == "~" else path.removeprefix("~/"))


def command(host: str, repo: str, python: str, action: str) -> list[str]:
    script = repo.rstrip("/") + "/chromium/remote/session.py"
    return [*SSH, "-o", "ClearAllForwardings=yes", "--", host,
            f"exec {remote_path(python)} {remote_path(script)} {action}"]


def connection(output: bytes) -> tuple[int, str]:
    try:
        data = json.loads(output)
        port, password = data["port"], data["password"]
        if (type(port) is int and 1 <= port <= 65535 and isinstance(password, str)
                and re.fullmatch(r"[A-Za-z0-9]{8}", password)):
            return port, password
    except (TypeError, KeyError, ValueError):
        pass
    raise ValueError("invalid viewer connection information returned by the Linux session")


def available_port() -> int:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", PREFERRED_PORT))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def forward(host: str, destination: int):
    port = available_port()
    # A remote command can only acknowledge once this SSH connection has bound
    # its own local listener. Never hand credentials to an unrelated listener.
    args = [*SSH, "-o", "ControlPath=none", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-L", f"127.0.0.1:{port}:127.0.0.1:{destination}", "--", host,
            "echo moltskin-forward-ready && exec cat >/dev/null"]
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
        try:
            if (not select.select([process.stdout], [], [], 20)[0]
                    or process.stdout.readline(128) != b"moltskin-forward-ready\n"):
                raise RuntimeError("SSH could not establish the local viewer tunnel")
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
            try:
                client.request("GET", "/vnc.html")
                response = client.getresponse()
                if response.status != 200 or b"novnc" not in response.read(16000).lower():
                    raise RuntimeError("the connected Linux session did not serve the noVNC viewer")
            finally:
                client.close()
            yield port, process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


def open_viewer(port: int, password: str, directory: Path) -> None:
    # noVNC reads credentials from the fragment. Open a private launcher file
    # so the browser command line contains only a filename, never the password.
    options = urlencode({"autoconnect": "1", "resize": "scale", "password": password})
    url = f"http://127.0.0.1:{port}/vnc.html#{options}"
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".html", mode="w",
                                     encoding="utf-8", delete=False) as page:
        page.write('<!doctype html><html lang="en"><meta charset="utf-8">'
                   '<meta name="referrer" content="no-referrer"><title>Moltskin</title>'
                   f'<meta http-equiv="refresh" content="0;url={html.escape(url, quote=True)}">')
    if not webbrowser.open(Path(page.name).as_uri(), new=2):
        raise RuntimeError("could not open your default browser; run the viewer from a desktop session")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", type=ssh_host, help="SSH alias or [user@]hostname")
    parser.add_argument("--repo", default="~/Moltskin", help="Moltskin checkout on Linux (default: ~/Moltskin)")
    parser.add_argument("--python", help="Linux Python with server dependencies (default: REPO/.venv/bin/python)")
    parser.add_argument("--stop", action="store_true", help="close the remote preview session")
    args = parser.parse_args()
    python = args.python if args.python is not None else args.repo.rstrip("/") + "/.venv/bin/python"
    try:
        if not args.repo:
            raise ValueError("empty remote repository path")
        subprocess.run(command(args.host, args.repo, python, "stop" if args.stop else "start"), check=True)
        if args.stop:
            return 0
        port, password = connection(subprocess.check_output(command(args.host, args.repo, python, "connection")))
        with forward(args.host, port) as (local, tunnel), tempfile.TemporaryDirectory() as temp:
            open_viewer(local, password, Path(temp))
            print(f"Viewing {args.host} through 127.0.0.1:{local}. Press Ctrl+C to disconnect.", flush=True)
            if tunnel.wait():
                raise RuntimeError("SSH viewer connection closed unexpectedly")
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        parser.exit(1, f"Remote session command failed (SSH exit {exc.returncode}).\n")
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        parser.exit(1, f"Viewer failed: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
