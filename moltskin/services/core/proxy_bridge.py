"""Loopback HTTP proxy that forwards to an authenticated upstream proxy.

Chromium cannot take proxy credentials on its command line without exposing
them. Each profile gets a private listener on 127.0.0.1; Chromium points at
that listener, and this process performs HTTP CONNECT or SOCKS5 authentication
upstream over an asyncio streaming event loop. A failed upstream connection is
returned to the browser as a 502 proxy error, never as a direct connection.
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

BUFFER_SIZE = 64 * 1024
HEADER_LIMIT = 64 * 1024
CONNECT_TIMEOUT = 20.0
HEADER_TIMEOUT = 30.0
IDLE_TIMEOUT = 300.0


class ProxyBridgeError(RuntimeError):
    pass


def _basic_auth(proxy: dict) -> str:
    username = proxy.get("username") or ""
    password = proxy.get("password") or ""
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Proxy-Authorization: Basic {token}\r\n"


def _set_nodelay(writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass


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


async def _close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    try:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        pass


async def _read_http_head(reader: asyncio.StreamReader, timeout: float) -> tuple[str, dict[str, str]]:
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=timeout)
    except asyncio.LimitOverrunError as exc:
        raise ProxyBridgeError("HTTP 头部超过大小限制") from exc
    except asyncio.IncompleteReadError as exc:
        raise ProxyBridgeError("连接在完整 HTTP 头部到达前关闭") from exc
    except asyncio.TimeoutError as exc:
        raise ProxyBridgeError("读取 HTTP 头部超时") from exc

    if len(raw) > HEADER_LIMIT:
        raise ProxyBridgeError("HTTP 头部超过大小限制")

    head = raw[:-4]
    lines = head.split(b"\r\n")
    first_line = lines[0].decode("latin-1")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition(b":")
        if not sep:
            continue
        headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return first_line, headers


async def _connect_upstream(proxy: dict) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host = proxy["host"].strip("[]")
    port = int(proxy["port"])
    ssl_ctx = ssl.create_default_context() if proxy["protocol"] == "https" else None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host,
                port,
                ssl=ssl_ctx,
                server_hostname=host if ssl_ctx else None,
                limit=HEADER_LIMIT,
            ),
            timeout=CONNECT_TIMEOUT,
        )
    except (OSError, asyncio.TimeoutError, ssl.SSLError) as exc:
        raise ProxyBridgeError(f"连接上游代理失败：{exc}") from exc
    _set_nodelay(writer)
    return reader, writer


async def _socks5_connect(
    proxy: dict, host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    upstream_host = proxy["host"].strip("[]")
    upstream_port = int(proxy["port"])
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(upstream_host, upstream_port, limit=HEADER_LIMIT),
            timeout=CONNECT_TIMEOUT,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise ProxyBridgeError(f"连接 SOCKS5 代理失败：{exc}") from exc

    _set_nodelay(writer)
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            username = proxy.get("username") or ""
            password = proxy.get("password") or ""
            user_bytes = username.encode("utf-8")
            pass_bytes = password.encode("utf-8")
            methods = b"\x00\x02" if (username or password) else b"\x00"
            writer.write(b"\x05" + bytes([len(methods)]) + methods)
            await writer.drain()

            reply = await reader.readexactly(2)
            if reply[0] != 5:
                raise ProxyBridgeError("SOCKS5 握手失败")
            if reply[1] == 2:
                if len(user_bytes) > 255 or len(pass_bytes) > 255:
                    raise ProxyBridgeError("SOCKS5 凭据过长")
                writer.write(
                    b"\x01" + bytes([len(user_bytes)]) + user_bytes
                    + bytes([len(pass_bytes)]) + pass_bytes
                )
                await writer.drain()
                auth_reply = await reader.readexactly(2)
                if auth_reply[1] != 0:
                    raise ProxyBridgeError("SOCKS5 认证失败")
            elif reply[1] != 0:
                raise ProxyBridgeError("SOCKS5 代理不支持所选认证方式")

            clean_host = host.strip("[]")
            try:
                address = b"\x01" + socket.inet_pton(socket.AF_INET, clean_host)
            except OSError:
                try:
                    address = b"\x04" + socket.inet_pton(socket.AF_INET6, clean_host)
                except OSError:
                    encoded = clean_host.encode("idna")
                    if len(encoded) > 255:
                        raise ProxyBridgeError("SOCKS5 主机名过长")
                    address = b"\x03" + bytes([len(encoded)]) + encoded

            writer.write(b"\x05\x01\x00" + address + struct.pack("!H", port))
            await writer.drain()

            head = await reader.readexactly(4)
            if head[0] != 5 or head[1] != 0:
                raise ProxyBridgeError("SOCKS5 连接目标失败")
            if head[3] == 1:
                await reader.readexactly(4)
            elif head[3] == 4:
                await reader.readexactly(16)
            elif head[3] == 3:
                length = (await reader.readexactly(1))[0]
                if length:
                    await reader.readexactly(length)
            else:
                raise ProxyBridgeError("SOCKS5 地址类型未知")
            await reader.readexactly(2)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError, UnicodeError) as exc:
        await _close_writer(writer)
        raise ProxyBridgeError(f"SOCKS5 隧道建立失败：{exc}") from exc
    except BaseException:
        await _close_writer(writer)
        raise
    return reader, writer


async def _upstream_tunnel(
    proxy: dict, host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if proxy["protocol"] == "socks5":
        return await _socks5_connect(proxy, host, port)

    reader, writer = await _connect_upstream(proxy)
    try:
        authority = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
        request = (
            f"CONNECT {authority} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            f"Proxy-Connection: keep-alive\r\n"
        )
        if proxy.get("username") or proxy.get("password"):
            request += _basic_auth(proxy)
        writer.write((request + "\r\n").encode("latin-1"))
        await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT)

        status_line, _ = await _read_http_head(reader, CONNECT_TIMEOUT)
        parts = status_line.split()
        status = parts[1] if len(parts) > 1 else ""
        if status != "200":
            raise ProxyBridgeError(f"上游代理拒绝 CONNECT（{status or '无响应'}）")
    except BaseException:
        await _close_writer(writer)
        raise
    return reader, writer


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(BUFFER_SIZE), timeout=IDLE_TIMEOUT)
            if not chunk:
                if writer.can_write_eof():
                    try:
                        writer.write_eof()
                    except OSError:
                        pass
                return
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return


async def _relay_bidirectional(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    c2u = asyncio.create_task(_pipe(client_reader, upstream_writer))
    u2c = asyncio.create_task(_pipe(upstream_reader, client_writer))
    try:
        await asyncio.gather(c2u, u2c)
    finally:
        for task in (c2u, u2c):
            if not task.done():
                task.cancel()
        await asyncio.gather(c2u, u2c, return_exceptions=True)


async def _stream_fixed_body(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, length: int
) -> None:
    remaining = length
    while remaining > 0:
        chunk = await asyncio.wait_for(
            reader.read(min(BUFFER_SIZE, remaining)),
            timeout=IDLE_TIMEOUT,
        )
        if not chunk:
            raise ProxyBridgeError("请求体提前结束")
        writer.write(chunk)
        await writer.drain()
        remaining -= len(chunk)


async def _stream_chunked_body(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    while True:
        try:
            line = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=IDLE_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise ProxyBridgeError("分块请求体不完整") from exc
        try:
            size = int(line[:-2].split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise ProxyBridgeError("请求体分块格式无效") from exc
        writer.write(line)
        if size == 0:
            # Forward trailing headers until CRLF.
            while True:
                trailer = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=IDLE_TIMEOUT)
                writer.write(trailer)
                if trailer == b"\r\n":
                    await writer.drain()
                    return
        remaining = size + 2
        while remaining > 0:
            chunk = await asyncio.wait_for(reader.read(min(BUFFER_SIZE, remaining)), timeout=IDLE_TIMEOUT)
            if not chunk:
                raise ProxyBridgeError("分块请求体提前关闭")
            writer.write(chunk)
            await writer.drain()
            remaining -= len(chunk)


class ProxyBridge:
    def __init__(self, proxy: dict):
        self.proxy = dict(proxy)
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None
        self._thread: threading.Thread | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = threading.Lock()
        self._closed = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "ProxyBridge":
        ready: Future[int] = Future()
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(loop, ready),
            name="moltskin-proxy-bridge",
            daemon=True,
        )
        self._thread.start()
        self.port = ready.result(timeout=5.0)
        return self

    def _run_loop(self, loop: asyncio.AbstractEventLoop, ready: Future[int]) -> None:
        asyncio.set_event_loop(loop)
        try:
            server = loop.run_until_complete(
                asyncio.start_server(
                    self._on_client,
                    host="127.0.0.1",
                    port=0,
                    backlog=512,
                    reuse_address=True,
                    limit=HEADER_LIMIT,
                )
            )
            self._server = server
            sockets = server.sockets or ()
            if not sockets:
                raise ProxyBridgeError("本地代理桥未能绑定端口")
            ready.set_result(sockets[0].getsockname()[1])
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            loop.close()
            return

        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
            if hasattr(self._server, "abort_clients"):
                self._server.abort_clients()
        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._server is not None:
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        asyncio.get_running_loop().stop()

    def _on_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        if self._closed:
            client_writer.close()
            return
        task = asyncio.create_task(self._handle_client(client_reader, client_writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        _set_nodelay(client_writer)
        upstream_writer: asyncio.StreamWriter | None = None
        tunnel_established = False
        try:
            try:
                request_line, headers = await _read_http_head(client_reader, HEADER_TIMEOUT)
            except ProxyBridgeError:
                return
            parts = request_line.split()
            if len(parts) < 3:
                return
            method, target, version = parts[0], parts[1], parts[2]

            if method.upper() == "CONNECT":
                host, port = _split_host_port(target, 443)
                if not host:
                    raise ProxyBridgeError("CONNECT 缺少目标主机")
                upstream_reader, upstream_writer = await _upstream_tunnel(self.proxy, host, port)
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
                tunnel_established = True
                await _relay_bidirectional(
                    client_reader, client_writer, upstream_reader, upstream_writer
                )
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
                host, port = _split_host_port(headers.get("host", ""), 80)
                path = target or "/"

            if not host:
                raise ProxyBridgeError("HTTP 请求缺少目标主机")

            if self.proxy["protocol"] == "socks5":
                upstream_reader, upstream_writer = await _socks5_connect(self.proxy, host, port)
                request_target = path
                extra = ""
            else:
                upstream_reader, upstream_writer = await _connect_upstream(self.proxy)
                request_target = target if absolute else f"http://{host}:{port}{path}"
                extra = (
                    _basic_auth(self.proxy)
                    if (self.proxy.get("username") or self.proxy.get("password"))
                    else ""
                )

            content_length: int | None = None
            if "content-length" in headers:
                try:
                    content_length = int(headers["content-length"])
                    if content_length < 0:
                        raise ValueError
                except ValueError as exc:
                    raise ProxyBridgeError("请求体长度无效") from exc
            is_chunked = "chunked" in headers.get("transfer-encoding", "").lower()

            forward_headers: list[str] = []
            for key, value in headers.items():
                if key in ("proxy-authorization", "proxy-connection", "connection"):
                    continue
                forward_headers.append(f"{key}: {value}\r\n")
            forward_headers.append("Connection: close\r\n")
            outbound_head = (
                f"{method} {request_target} {version}\r\n"
                + "".join(forward_headers)
                + extra
                + "\r\n"
            )
            upstream_writer.write(outbound_head.encode("latin-1"))
            await upstream_writer.drain()

            if is_chunked:
                await _stream_chunked_body(client_reader, upstream_writer)
            elif content_length:
                await _stream_fixed_body(client_reader, upstream_writer, content_length)

            tunnel_established = True
            await _pipe(upstream_reader, client_writer)
        except (OSError, ProxyBridgeError, ValueError, asyncio.TimeoutError, ssl.SSLError):
            if not tunnel_established:
                try:
                    client_writer.write(
                        b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
                    )
                    await client_writer.drain()
                except OSError:
                    pass
        except asyncio.CancelledError:
            raise
        finally:
            await _close_writer(upstream_writer)
            await _close_writer(client_writer)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None

        if loop is not None and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                fut.result(timeout=2.0)
            except Exception:  # noqa: BLE001
                loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)
