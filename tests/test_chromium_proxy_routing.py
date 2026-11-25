"""Verify fixed proxy routing and failure through two real desktop profiles."""

import os
import socket
import time
from contextlib import ExitStack, contextmanager, suppress
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Error, sync_playwright

from moltskin.services.core import chromium, config, main
from moltskin.services.core.security import get_token


class Handler(BaseHTTPRequestHandler):
    def reply(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def serve(handler):
    connections = []

    class Server(ThreadingHTTPServer):
        def get_request(self):
            connection, peer = super().get_request()
            connections.append(connection)
            return connection, peer

    with Server(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()
            # Chromium preconnects. Stopping accept() alone leaves those sockets serving requests.
            for connection in connections:
                with suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
                connection.close()


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium")
def test_two_profiles_keep_their_proxy_and_fail_closed(data_dir, monkeypatch):
    class Origin(Handler):
        def do_GET(self):
            if self.path.partition("?")[0] != "/route":
                self.send_error(404)
                return
            self.reply(self.headers.get("X-Test-Route", "direct").encode())

    with ExitStack() as resources:
        origin = resources.enter_context(serve(Origin))
        url = f"http://route.test:{origin}/route"

        def proxy(marker):
            class Proxy(Handler):
                def do_GET(self):
                    if self.path.partition("?")[0] != url:
                        self.send_error(403)
                        return
                    conn = HTTPConnection("127.0.0.1", origin, timeout=5)
                    try:
                        conn.request("GET", "/route", headers={"X-Test-Route": marker})
                        self.reply(conn.getresponse().read())
                    finally:
                        conn.close()

            return serve(Proxy)

        proxy_a = resources.enter_context(ExitStack())
        ports = {"A": proxy_a.enter_context(proxy("A")), "B": resources.enter_context(proxy("B"))}
        api = resources.enter_context(TestClient(main.app, headers={"Authorization": "Bearer " + get_token()}))
        profiles = {}
        for name in ("direct", "A", "B"):
            settings = {"name": name, "engine": "chromium", "headless": True, "startup_page": "blank"}
            if name in ports:
                record = api.post("/api/v1/proxies", json={
                    "name": name, "host": "127.0.0.1", "port": ports[name], "protocol": "http",
                }).raise_for_status().json()["data"]
                settings["proxy_id"] = record["id"]
            profile = api.post("/api/v1/profiles", json=settings).raise_for_status().json()["data"]
            profiles[name] = profile["id"]

        prepare_launch = chromium.prepare_launch

        def local_test_launch(profile_dir, *args, **kwargs):
            argv, version = prepare_launch(profile_dir, *args, **kwargs)
            # Keep proxy selection intact. Only the control process ignores host proxy settings.
            flags = ["--remote-debugging-port=0", "--disable-background-networking",
                     "--host-resolver-rules=MAP route.test 127.0.0.1, MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]
            if profile_dir.name == profiles["direct"]:
                flags.append("--no-proxy-server")
            argv[-1:-1] = flags
            return argv, version

        monkeypatch.setattr(chromium, "prepare_launch", local_test_launch)
        driver = resources.enter_context(sync_playwright())

        @contextmanager
        def page_for(name):
            ident = profiles[name]
            path = "/api/v1/profiles/" + ident
            port_file = config.profile_directory(ident) / "DevToolsActivePort"
            port_file.unlink(missing_ok=True)
            api.post(path + "/launch").raise_for_status()
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    lines = port_file.read_text().splitlines() if port_file.exists() else []
                    if len(lines) >= 2 and lines[0].isdigit():
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail(f"{name} did not publish its test debugging port")
                browser = driver.chromium.connect_over_cdp(f"http://127.0.0.1:{lines[0]}")
                yield browser.contexts[0].new_page()
            finally:
                api.post(path + "/stop").raise_for_status()

        probes = count()

        def route(page):
            return page.goto(f"{url}?probe={next(probes)}", timeout=10000).text()

        with page_for("direct") as control, page_for("B") as b:
            assert route(control) == "direct"
            with page_for("A") as a:
                assert route(a) == "A"
                assert route(b) == "B"
            with page_for("A") as restarted:
                assert route(restarted) == "A"
                proxy_a.close()
                with pytest.raises(Error, match="ERR_PROXY_CONNECTION_FAILED"):
                    pytest.fail("received body after the proxy stopped: " + repr(route(restarted)))
                assert route(control) == "direct"
                assert route(b) == "B"
