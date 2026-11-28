"""The loopback bridge performs upstream proxy authentication over asyncio."""

import base64
from concurrent.futures import ThreadPoolExecutor
import socket
import threading

from moltskin.services.core.proxy_bridge import ProxyBridge


class FakeProxy:
    def __init__(self, *, response=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"):
        self.response = response
        self.requests = []
        self.lock = threading.Lock()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
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
                client.close()
                return
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        line = head.split(b"\r\n", 1)[0]
        if line.startswith(b"CONNECT"):
            with self.lock:
                self.requests.append(head + b"\r\n\r\n")
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                client.sendall(b"echo:" + rest)
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                client.sendall(b"echo:" + chunk)
        else:
            headers = {}
            for raw_line in head.split(b"\r\n")[1:]:
                k, _, v = raw_line.partition(b":")
                headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
            body = bytearray(rest)
            if "content-length" in headers:
                total = int(headers["content-length"])
                while len(body) < total:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    body.extend(chunk)
            elif "chunked" in headers.get("transfer-encoding", "").lower():
                while not body.endswith(b"0\r\n\r\n"):
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    body.extend(chunk)
            with self.lock:
                self.requests.append(head + b"\r\n\r\n" + bytes(body))
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


def test_high_concurrency_parallel_tunnels_use_single_event_loop_thread():
    upstream = FakeProxy()
    bridge = ProxyBridge({
        "protocol": "http", "host": "127.0.0.1", "port": upstream.port,
        "username": "u", "password": "p",
    }).start()

    def worker(idx: int) -> bytes:
        with socket.create_connection(("127.0.0.1", bridge.port), timeout=10) as client:
            # Send pipelined payload right after CONNECT headers to verify zero byte loss.
            payload = f"hello-{idx}".encode("ascii")
            client.sendall(
                b"CONNECT stream.test:443 HTTP/1.1\r\nHost: stream.test:443\r\n\r\n" + payload
            )
            received = b""
            expected_suffix = b"echo:" + payload
            while expected_suffix not in received:
                chunk = client.recv(4096)
                if not chunk:
                    break
                received += chunk
            return received

    try:
        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(worker, range(64)))
        bridge_threads = [t for t in threading.enumerate() if t.name == "moltskin-proxy-bridge"]
        assert len(bridge_threads) == 1
    finally:
        bridge.close()
        upstream.close()

    assert len(results) == 64
    for idx, res in enumerate(results):
        assert res.startswith(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        assert res.endswith(f"echo:hello-{idx}".encode("ascii"))


def test_streaming_chunked_and_large_body_http_forwarding():
    large_resp = b"X" * (256 * 1024)
    upstream = FakeProxy(
        response=(
            f"HTTP/1.1 200 OK\r\nContent-Length: {len(large_resp)}\r\n\r\n".encode("latin-1")
            + large_resp
        )
    )
    bridge = ProxyBridge({
        "protocol": "http", "host": "127.0.0.1", "port": upstream.port,
        "username": "user", "password": "secret",
    }).start()
    try:
        with socket.create_connection(("127.0.0.1", bridge.port), timeout=10) as client:
            client.sendall(
                b"POST http://upload.test/chunked HTTP/1.1\r\n"
                b"Host: upload.test\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
                b"5\r\nhello\r\n6\r\n-world\r\n0\r\n\r\n"
            )
            data = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                data.extend(chunk)
    finally:
        bridge.close()
        upstream.close()

    _, _, body = bytes(data).partition(b"\r\n\r\n")
    assert len(body) == len(large_resp)
    assert body == large_resp
    forwarded = upstream.requests[0]
    assert b"5\r\nhello\r\n6\r\n-world\r\n0\r\n\r\n" in forwarded
