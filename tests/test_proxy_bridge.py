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


def test_socks5_authentication_and_target_forwarding():
    seen = {}
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def recv_exact(client, size):
        data = b""
        while len(data) < size:
            chunk = client.recv(size - len(data))
            if not chunk:
                raise AssertionError("short read")
            data += chunk
        return data

    def serve():
        client, _ = server.accept()
        greeting = recv_exact(client, 4)
        assert greeting[:2] == b"\x05\x02"
        client.sendall(b"\x05\x02")
        auth = recv_exact(client, 2)
        seen["username"] = recv_exact(client, auth[1]).decode()
        plen = recv_exact(client, 1)[0]
        seen["password"] = recv_exact(client, plen).decode()
        client.sendall(b"\x01\x00")
        request = recv_exact(client, 4)
        assert request[:3] == b"\x05\x01\x00"
        if request[3] == 3:
            length = recv_exact(client, 1)[0]
            seen["host"] = recv_exact(client, length).decode()
        elif request[3] == 1:
            seen["host"] = socket.inet_ntoa(recv_exact(client, 4))
        else:
            seen["host"] = socket.inet_ntop(socket.AF_INET6, recv_exact(client, 16))
        seen["target_port"] = int.from_bytes(recv_exact(client, 2), "big")
        client.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
        while True:
            data = client.recv(4096)
            if not data:
                break
            client.sendall(b"echo:" + data)
        client.close()
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    bridge = ProxyBridge({
        "protocol": "socks5", "host": "127.0.0.1", "port": port,
        "username": "user", "password": "secret",
    }).start()
    try:
        client = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
        client.sendall(b"CONNECT example.test:8443 HTTP/1.1\r\nHost: example.test:8443\r\n\r\n")
        assert client.recv(4096).startswith(b"HTTP/1.1 200")
        client.sendall(b"ping")
        assert client.recv(4096) == b"echo:ping"
        client.close()
    finally:
        bridge.close()
    assert seen == {"username": "user", "password": "secret",
                    "host": "example.test", "target_port": 8443}
