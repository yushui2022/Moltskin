"""The loopback bridge performs upstream proxy authentication."""

import base64
import socket
import threading
import time

from moltskin.services.core.proxy_bridge import ProxyBridge


class FakeProxy:
    def __init__(self, *, response=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"):
        self.response = response
        self.requests = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                return
            data += chunk
        self.requests.append(data)
        line = data.split(b"\r\n", 1)[0]
        if line.startswith(b"CONNECT"):
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                client.sendall(b"echo:" + chunk)
        else:
            client.sendall(self.response)
        client.close()

    def close(self):
        self.sock.close()


def test_connect_forwards_upstream_basic_auth():
    upstream = FakeProxy()
    bridge = ProxyBridge({
        "protocol": "http", "host": "127.0.0.1", "port": upstream.port,
        "username": "user", "password": "p@ss",
    }).start()
    try:
        client = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
        client.sendall(b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n")
        assert client.recv(4096).startswith(b"HTTP/1.1 200")
        client.sendall(b"ping")
        assert client.recv(4096) == b"echo:ping"
        client.close()
    finally:
        bridge.close()
        upstream.close()
    request = upstream.requests[0].decode("latin-1")
    assert "CONNECT example.test:443" in request
    token = base64.b64encode(b"user:p@ss").decode()
    assert f"Proxy-Authorization: Basic {token}" in request


def test_plain_http_forwarding_keeps_absolute_target():
    upstream = FakeProxy()
    bridge = ProxyBridge({
        "protocol": "http", "host": "127.0.0.1", "port": upstream.port,
        "username": "user", "password": "secret",
    }).start()
    try:
        client = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
        client.sendall(
            b"GET http://example.test/a?b=1 HTTP/1.1\r\nHost: example.test\r\n\r\n")
        response = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
        client.close()
    finally:
        bridge.close()
        upstream.close()
    assert response.endswith(b"ok")
    request = upstream.requests[0].decode("latin-1")
    assert request.startswith("GET http://example.test/a?b=1 HTTP/1.1")
    assert "Proxy-Authorization: Basic" in request


def test_upstream_failure_returns_proxy_error():
    upstream = socket.socket()
    upstream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    port = upstream.getsockname()[1]

    def accept_and_close():
        client, _ = upstream.accept()
        client.close()
        upstream.close()

    threading.Thread(target=accept_and_close, daemon=True).start()
    bridge = ProxyBridge({"protocol": "http", "host": "127.0.0.1", "port": port,
                          "username": "user", "password": "secret"}).start()
    try:
        client = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
        client.sendall(b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n")
        assert client.recv(4096).startswith(b"HTTP/1.1 502")
        client.close()
    finally:
        bridge.close()
