"""Loopback HTTP proxy that forwards to an authenticated upstream proxy.

Chromium cannot take proxy credentials on its command line without exposing
them. Each profile gets a private listener on 127.0.0.1; Chromium points at
that listener, and this process performs HTTP CONNECT or SOCKS5 authentication
upstream. A failed upstream connection is returned to the browser as a proxy
error, never as a direct connection.
"""

from __future__ import annotations

import base64
import select
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

BUFFER_SIZE = 64 * 1024
HEADER_LIMIT = 64 * 1024
CONNECT_TIMEOUT = 20


class ProxyBridgeError(RuntimeError):
    pass


def _basic_auth(proxy: dict) -> str:
    username = proxy.get("username") or ""
    password = proxy.get("password") or ""
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Proxy-Authorization: Basic {token}\r\n"


def _read_headers(sock: socket.socket) -> tuple[bytes, str, dict[str, str]]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(BUFFER_SIZE)
        if not chunk:
            raise ProxyBridgeError("上游代理提前关闭连接")
        data += chunk
        if len(data) > HEADER_LIMIT:
            raise ProxyBridgeError("代理响应头过大")
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status_parts = lines[0].decode("latin-1").split()
    status = status_parts[1] if len(status_parts) > 1 else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, _, value = line.partition(b":")
        headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return rest, status, headers


def _read_chunked(sock: socket.socket, initial: bytes) -> bytes:
    data = bytearray(initial)
    body = bytearray()
    while True:
        while b"\r\n" not in data:
            chunk = sock.recv(BUFFER_SIZE)
            if not chunk:
                raise ProxyBridgeError("请求体不完整")
            data.extend(chunk)
        line, _, data = bytes(data).partition(b"\r\n")
        try:
            size = int(line.split(b";", 1)[0], 16)
        except ValueError as exc:
            raise ProxyBridgeError("请求体分块格式无效") from exc
        if size == 0:
            return bytes(body)
        while len(data) < size + 2:
            chunk = sock.recv(BUFFER_SIZE)
            if not chunk:
                raise ProxyBridgeError("请求体不完整")
            data += chunk
        body.extend(data[:size])
        data = bytearray(data[size + 2:])


def _read_request_body(client: socket.socket, headers: dict[str, str], initial: bytes) -> bytes:
    length = headers.get("content-length")
    if length:
        try:
            remaining = int(length)
        except ValueError as exc:
            raise ProxyBridgeError("请求体长度无效") from exc
        body = bytearray(initial)
        while len(body) < remaining:
            chunk = client.recv(BUFFER_SIZE)
            if not chunk:
                break
            body.extend(chunk)
        return bytes(body[:remaining])
    if headers.get("transfer-encoding", "").lower() == "chunked":
        return _read_chunked(client, initial)
    return b""


def _connect_upstream(proxy: dict) -> socket.socket:
    host = proxy["host"].strip("[]")
    port = int(proxy["port"])
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    if proxy["protocol"] == "https":
        context = ssl.create_default_context()
        sock = context.wrap_socket(sock, server_hostname=host)
    return sock


def _socks5_connect(proxy: dict, host: str, port: int) -> socket.socket:
    sock = socket.create_connection((proxy["host"].strip("[]"), int(proxy["port"])),
                                    timeout=CONNECT_TIMEOUT)
    username = proxy.get("username") or ""
    password = proxy.get("password") or ""
    methods = b"\x00\x02" if username else b"\x00"
    sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
    reply = sock.recv(2)
    if len(reply) != 2 or reply[0] != 5:
        raise ProxyBridgeError("SOCKS5 握手失败")
    if reply[1] == 2:
        if len(username.encode()) > 255 or len(password.encode()) > 255:
            raise ProxyBridgeError("SOCKS5 凭据过长")
        sock.sendall(b"\x01" + bytes([len(username)]) + username.encode() +
                     bytes([len(password)]) + password.encode())
        auth_reply = sock.recv(2)
        if len(auth_reply) != 2 or auth_reply[1] != 0:
            raise ProxyBridgeError("SOCKS5 认证失败")
    elif reply[1] != 0:
        raise ProxyBridgeError("SOCKS5 代理不支持所选认证方式")

    try:
        ip = socket.inet_pton(socket.AF_INET, host)
        address = b"\x01" + ip
    except OSError:
        try:
            ip = socket.inet_pton(socket.AF_INET6, host)
            address = b"\x04" + ip
        except OSError:
            encoded = host.encode("idna")
            if len(encoded) > 255:
                raise ProxyBridgeError("SOCKS5 主机名过长")
            address = b"\x03" + bytes([len(encoded)]) + encoded
    sock.sendall(b"\x05\x01\x00" + address + struct.pack("!H", port))
    head = sock.recv(4)
    if len(head) != 4 or head[0] != 5 or head[1] != 0:
        raise ProxyBridgeError("SOCKS5 连接目标失败")
    if head[3] == 1:
        sock.recv(4)
    elif head[3] == 4:
        sock.recv(16)
    elif head[3] == 3:
        length = sock.recv(1)
        if length:
            sock.recv(length[0])
    sock.recv(2)
    return sock


def _upstream_tunnel(proxy: dict, host: str, port: int) -> socket.socket:
    if proxy["protocol"] == "socks5":
        return _socks5_connect(proxy, host, port)
    sock = _connect_upstream(proxy)
    request = (f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Proxy-Connection: keep-alive\r\n")
    if proxy.get("username") or proxy.get("password"):
        request += _basic_auth(proxy)
    sock.sendall((request + "\r\n").encode("latin-1"))
    _, status, _ = _read_headers(sock)
    if status != "200":
        sock.close()
        raise ProxyBridgeError(f"上游代理拒绝 CONNECT（{status or '无响应'}）")
    return sock


def _split_host_port(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else default_port
        return host, port
    host, sep, port_text = target.rpartition(":")
    if sep and host:
        try:
            return host, int(port_text)
        except ValueError:
            pass
    return target, default_port


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    sockets = [client, upstream]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 60)
            if not readable:
                continue
            for source in readable:
                target = upstream if source is client else client
                data = source.recv(BUFFER_SIZE)
                if not data:
                    return
                target.sendall(data)
    finally:
        try:
            upstream.close()
        except OSError:
            pass


class ProxyBridge:
    def __init__(self, proxy: dict):
        self.proxy = dict(proxy)
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "ProxyBridge":
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(64)
        server.settimeout(1.0)
        self._server = server
        self.port = server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="moltskin-proxy-bridge",
                                        daemon=True)
        self._thread.start()
        return self

    def _serve(self) -> None:
        assert self._server is not None
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        upstream = None
        try:
            client.settimeout(30)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = client.recv(BUFFER_SIZE)
                if not chunk:
                    return
                data += chunk
                if len(data) > HEADER_LIMIT:
                    return
            head, _, rest = data.partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            request_line = lines[0].decode("latin-1").split()
            if len(request_line) < 3:
                return
            method, target, version = request_line
            headers = {}
            for line in lines[1:]:
                key, _, value = line.partition(b":")
                headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()

            if method.upper() == "CONNECT":
                host, port = _split_host_port(target, 443)
                upstream = _upstream_tunnel(self.proxy, host, port)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
                _relay(client, upstream)
                return

            absolute = target.startswith("http://") or target.startswith("https://")
            if absolute:
                parsed = urlsplit(target)
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
            else:
                host = headers.get("host", "")
                host, port = _split_host_port(host, 80)
                path = target

            if self.proxy["protocol"] == "socks5":
                upstream = _socks5_connect(self.proxy, host, port)
                request_target = path
                extra = ""
            else:
                upstream = _connect_upstream(self.proxy)
                request_target = target if absolute else f"http://{host}:{port}{path}"
                extra = _basic_auth(self.proxy) if (
                    self.proxy.get("username") or self.proxy.get("password")) else ""

            body = _read_request_body(client, headers, rest)
            forward_headers = []
            for key, value in headers.items():
                if key in ("proxy-authorization", "proxy-connection", "connection",
                           "content-length", "transfer-encoding"):
                    continue
                forward_headers.append(f"{key}: {value}\r\n")
            if body:
                forward_headers.append(f"Content-Length: {len(body)}\r\n")
            forward_headers.append("Connection: close\r\n")
            request = (f"{method} {request_target} {version}\r\n" +
                       "".join(forward_headers) + extra + "\r\n")
            upstream.sendall(request.encode("latin-1") + body)
            while True:
                chunk = upstream.recv(BUFFER_SIZE)
                if not chunk:
                    break
                client.sendall(chunk)
        except (OSError, ProxyBridgeError, ValueError):
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
        finally:
            try:
                client.close()
            except OSError:
                pass
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass

    def close(self) -> None:
        self._closed.set()
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
