"""Run one disposable, SSH-accessible Chromium desktop per Linux login."""
import argparse
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import string
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NAME = "moltskin-remote-viewer"
UNIT = NAME + ".service"
CHROME = Path("/opt/moltskin-preview/chrome")
WEB = Path("/usr/share/novnc")
WORKBENCH = "Ubuntu 云端预览"
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def runtime_dir():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    info = runtime.lstat()
    if not runtime.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Use a private Linux user runtime directory.")
    return runtime


def state():
    return subprocess.check_output(["systemctl", "--user", "show", "-p", "ActiveState",
                                    "--value", UNIT], text=True).strip()


def wait_for(label, check, *, children=(), timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for process in children:
            if process.poll() is not None:
                raise RuntimeError(f"{Path(process.args[0]).name} exited ({process.returncode}) while waiting for {label}")
        value = check()
        if value:
            return value
        time.sleep(.2)
    raise RuntimeError(f"Timed out waiting for {label}")


def read_connection(path):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or set(data) != {"port", "password"}:
        raise RuntimeError("Invalid viewer connection state")
    if type(data["port"]) is not int or not 0 < data["port"] <= 65535:
        raise RuntimeError("Invalid viewer port")
    if not isinstance(data["password"], str) or not re.fullmatch(r"[a-zA-Z0-9]{8}", data["password"]):
        raise RuntimeError("Invalid viewer credential")
    return data


def control(action):
    import fcntl

    runtime = runtime_dir()
    ready = runtime / NAME / "connection.json"
    # This lock is outside the transient unit's RuntimeDirectory so stop/start
    # cannot unlink a lock still held by a concurrent SSH command.
    with (runtime / (NAME + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status = state()
        if action == "stop":
            if status in {"active", "activating", "reloading", "deactivating"}:
                subprocess.run(["systemctl", "--user", "stop", UNIT], check=True)
            return
        if action == "connection":
            details = read_connection(ready) if status == "active" else None
            if not details:
                raise RuntimeError("The remote browser is not running. Start it first.")
            print(json.dumps(details), flush=True)
            return
        if status == "deactivating":
            wait_for("previous session to stop", lambda: state() in ("inactive", "failed"))
            status = "inactive"
        if status not in {"active", "activating", "reloading"}:
            for tool in ("xvfb-run", "Xvfb", "xauth", "openbox", "xprop", "xdotool", "x11vnc",
                         "websockify", "tigervncpasswd", "dbus-run-session"):
                if not shutil.which(tool):
                    raise RuntimeError(f"Missing Ubuntu viewer dependency: {tool}")
            if not os.access(CHROME, os.X_OK) or not (WEB / "vnc.html").is_file():
                raise RuntimeError("Install the packaged Chromium and Ubuntu noVNC before starting.")
            subprocess.run([
                "systemd-run", "--user", "--quiet", "--collect", "--service-type=exec", "--unit", UNIT,
                "-p", "RuntimeDirectory=" + NAME, "-p", "RuntimeDirectoryMode=0700",
                "-p", "UMask=0077", "-p", "KillMode=control-group", "-p", "TimeoutStopSec=12",
                "--", "xvfb-run", "-a", "-s", "-screen 0 1440x900x24 -nolisten tcp",
                "dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "_serve",
            ], check=True)

        def ready_or_failed():
            if state() != "active":
                raise RuntimeError(f"Viewer startup failed. Inspect journalctl --user -u {UNIT}.")
            return read_connection(ready)

        wait_for("the Chromium desktop", ready_or_failed, timeout=100)


@contextmanager
def loopback_listener():
    # Reserve the port until the server inherits the listening descriptor.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        yield listener


def vnc_command(display, authority, flag, auth_file):
    # x11vnc and LibVNCServer have independent IPv6 listeners. An explicit
    # IPv4 address alone still allows a wildcard socket on the library's port.
    return ["x11vnc", "-norc", "-display", display, "-auth", authority,
            "-listen", "127.0.0.1", "-no6", "-rfbportv6", "0", "-autoport", "5900",
            "-flag", str(flag), "-rfbauth", str(auth_file), "-forever", "-shared",
            "-nolookup", "-nosel", "-noxdamage"]


def rfb_port(path):
    """Confirm that the loopback listener requires VNC password authentication."""
    if not path.exists():
        return None
    match = re.fullmatch(r"PORT=(\d+)", path.read_text().strip())
    if not match:
        return None
    port = int(match[1])
    if not 0 < port <= 65535:
        raise RuntimeError("x11vnc reported an invalid port")
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock, sock.makefile("rb") as reader:
        if reader.read(12) != b"RFB 003.008\n":
            raise RuntimeError("Expected an RFB 3.8 desktop")
        sock.sendall(b"RFB 003.008\n")
        count = reader.read(1)
        if not count or reader.read(count[0]) != bytes([2]):
            raise RuntimeError("Expected password-authenticated VNC")
    return port


def openbox_config(folder):
    tree = ET.parse("/etc/xdg/openbox/rc.xml")
    ns = "{http://openbox.org/3.4/rc}"
    apps = tree.getroot().find(ns + "applications")
    if apps is None:
        apps = ET.SubElement(tree.getroot(), ns + "applications")
    ET.register_namespace("", ns[1:-1])
    rule = ET.SubElement(apps, ns + "application", {"class": "*", "type": "normal"})
    ET.SubElement(rule, ns + "maximized").text = "yes"
    filename = folder / "openbox.xml"
    tree.write(filename, encoding="utf-8", xml_declaration=True)
    return str(filename)


def xquery(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else ""


def shows_page(url, marker):
    try:
        with LOCAL_HTTP.open(url, timeout=2) as response:
            return response.status == 200 and marker in response.read(65536)
    except OSError:
        return False


def stop_child(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def serve():
    folder = runtime_dir() / NAME
    if os.environ.get("RUNTIME_DIRECTORY") != str(folder) or not os.environ.get("XAUTHORITY"):
        raise RuntimeError("Start the private display with the start command.")
    data = folder / "environment"
    password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    auth = subprocess.check_output(["tigervncpasswd", "-f"], input=(password + "\n").encode())
    if len(auth) != 8:
        raise RuntimeError("Unable to create the VNC password file")
    auth_file = folder / "vnc.auth"
    auth_file.write_bytes(auth)

    with ExitStack() as stack:
        children = []
        def start(args, *, stdin=subprocess.DEVNULL, **kwargs):
            proc = subprocess.Popen(args, cwd=ROOT, stdin=stdin, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, **kwargs)
            children.append(proc)
            stack.callback(stop_child, proc)

        start(["openbox", "--config-file", openbox_config(folder)])
        wait_for("Openbox", lambda: "window id # 0x" in xquery("xprop", "-root", "_NET_SUPPORTING_WM_CHECK"), children=children)
        with loopback_listener() as api_socket:
            port = api_socket.getsockname()[1]
            endpoint = f"http://127.0.0.1:{port}/api/v1"
            env = dict(os.environ, MOLTSKIN_HOST="127.0.0.1", MOLTSKIN_PORT=str(port),
                       MOLTSKIN_DATA_DIR=str(data), MOLTSKIN_HEADLESS="0", MOLTSKIN_OPEN_MANAGER="1",
                       MOLTSKIN_CHROMIUM_BINARY=str(CHROME), XDG_SESSION_TYPE="x11")
            env.pop("WAYLAND_DISPLAY", None)
            fd = api_socket.fileno()
            start([sys.executable, "-m", "uvicorn", "moltskin.services.core.main:app",
                   "--fd", str(fd), "--no-access-log"], env=env, pass_fds=(fd,))
        wait_for("Moltskin API", lambda: shows_page(endpoint + "/health", b'"status":"ok"'), children=children)
        token = (data / "api.token").read_text().strip()

        def post(path, payload):
            req = urllib.request.Request(endpoint + path, headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json"},
                data=json.dumps(payload).encode())
            with LOCAL_HTTP.open(req, timeout=40) as response:
                return json.load(response)["data"]

        profile = post("/profiles", {"name": WORKBENCH, "engine": "chromium", "headless": False, "startup_page": "default"})
        if post(f"/profiles/{profile['id']}/launch", {})["state"] != "running":
            raise RuntimeError("Chromium did not start")
        windows = wait_for("visible Chromium workbench", lambda: xquery("xdotool", "search", "--onlyvisible", "--name",
                                                                       WORKBENCH + ".*Moltskin"), children=children)
        window = windows.splitlines()[0]

        flag = folder / "rfb.port"
        start(vnc_command(os.environ["DISPLAY"], os.environ["XAUTHORITY"], flag, auth_file))
        rfb = wait_for("authenticated framebuffer", lambda: rfb_port(flag), children=children)
        with loopback_listener() as web_socket:
            web_port = web_socket.getsockname()[1]
            start(["websockify", "--inetd", "--web", str(WEB), f"127.0.0.1:{rfb}"], stdin=web_socket)
        wait_for("noVNC", lambda: shows_page(f"http://127.0.0.1:{web_port}/vnc.html", b"noVNC"), children=children)
        tmp = folder / "connection.tmp"
        tmp.write_text(json.dumps({"port": web_port, "password": password}))
        tmp.replace(folder / "connection.json")
        while xquery("xdotool", "getwindowgeometry", window):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A remote desktop process exited")
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "connection", "stop", "_serve"))
    args = parser.parse_args()
    if sys.platform != "linux" or os.getuid() == 0:
        parser.error("Run as an ordinary user on the Linux desktop host.")
    os.umask(0o077)
    if args.command == "_serve":
        serve()
    else:
        control(args.command)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))
