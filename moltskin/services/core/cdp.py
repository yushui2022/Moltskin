"""Stealth loopback CDP client for running Moltskin Chromium sessions.

Connects strictly to 127.0.0.1 via the profile's DevToolsActivePort file.
Never enables Runtime.enable, Debugger.enable, or Console.enable, avoiding
all console/getter/executionContextCreated automation detection vectors.
DOM inspection runs inside a dedicated isolated world via Page.createIsolatedWorld,
and user actions dispatch trusted browser-level Input events (event.isTrusted === true).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time
from typing import Any

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class CDPError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def read_devtools_endpoint(profile_dir: str | Path, timeout: float = 5.0) -> tuple[int, str]:
    """Read (port, ws_path) from <profile_dir>/DevToolsActivePort."""
    port_file = Path(profile_dir) / "DevToolsActivePort"
    deadline = time.monotonic() + max(0.05, timeout)
    while True:
        if port_file.is_file() and not port_file.is_symlink():
            try:
                lines = port_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            if len(lines) >= 2 and lines[0].strip().isdigit():
                port = int(lines[0].strip())
                ws_path = lines[1].strip()
                if 1 <= port <= 65535 and ws_path.startswith("/"):
                    return port, ws_path
        if time.monotonic() >= deadline:
            raise CDPError(
                "CDP_UNAVAILABLE",
                "当前环境未开启或尚未就绪本地调试通道，请确认此 Chromium 环境正在运行",
                409,
            )
        time.sleep(0.05)


class _LoopbackWebSocket:
    """Minimal RFC 6455 WebSocket client bound strictly to 127.0.0.1."""

    def __init__(self, port: int, path: str, timeout: float = 30.0):
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        self._handshake(port, path)

    def _handshake(self, port: int, path: str) -> None:
        raw_key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {raw_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(req.encode("latin-1"))
        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(16384)
            if not chunk:
                self.close()
                raise CDPError("CDP_HANDSHAKE_FAILED", "本地调试端口握手中断", 502)
            self._buf.extend(chunk)
            if len(self._buf) > 65536:
                self.close()
                raise CDPError("CDP_HANDSHAKE_FAILED", "本地调试端口响应异常", 502)
        idx = self._buf.index(b"\r\n\r\n")
        head = bytes(self._buf[:idx]).decode("latin-1")
        del self._buf[: idx + 4]
        lines = head.split("\r\n")
        if not lines or " 101 " not in lines[0]:
            self.close()
            raise CDPError("CDP_HANDSHAKE_FAILED", f"本地调试端口拒绝连接：{lines[0]}", 502)
        expected_accept = base64.b64encode(
            hashlib.sha1((raw_key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        if headers.get("sec-websocket-accept") != expected_accept:
            self.close()
            raise CDPError("CDP_HANDSHAKE_FAILED", "WebSocket 握手校验失败", 502)

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise CDPError("CDP_CLOSED", "浏览器调试连接已关闭", 409)
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask_key = os.urandom(4)
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask_key)
        masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def send_json(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_frame(0x1, raw)

    def recv_json(self) -> dict[str, Any]:
        fragments = bytearray()
        while True:
            b0, b1 = self._read_exact(2)
            fin = bool(b0 & 0x80)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask_key = self._read_exact(4) if masked else b""
            data = self._read_exact(length) if length else b""
            if masked:
                data = bytes(b ^ mask_key[i & 3] for i, b in enumerate(data))

            if opcode == 0x8:  # Close
                raise CDPError("CDP_CLOSED", "浏览器调试连接已关闭", 409)
            if opcode == 0x9:  # Ping -> Pong
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:  # Pong
                continue
            if opcode in (0x0, 0x1, 0x2):
                fragments.extend(data)
                if fin:
                    return json.loads(fragments.decode("utf-8"))

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class CDPBrowserSession:
    """Short-lived synchronous CDP session against a local Chromium profile."""

    def __init__(self, profile_dir: str | Path, timeout: float = 30.0):
        self.profile_dir = Path(profile_dir)
        self.timeout = timeout
        port, ws_path = read_devtools_endpoint(self.profile_dir, timeout=min(timeout, 5.0))
        try:
            self._ws = _LoopbackWebSocket(port, ws_path, timeout=timeout)
        except OSError as exc:
            raise CDPError("CDP_CONNECT_FAILED", f"无法连接本地浏览器调试端口：{exc}", 409) from exc
        self._next_id = 0

    def __enter__(self) -> "CDPBrowserSession":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._ws.close()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        # Hard guard: never allow callers to enable observable Runtime/Debugger domains.
        if method in ("Runtime.enable", "Debugger.enable", "Console.enable"):
            raise CDPError("CDP_FORBIDDEN_METHOD", f"反检测安全策略禁止调用 {method}", 422)
        self._next_id += 1
        call_id = self._next_id
        msg: dict[str, Any] = {"id": call_id, "method": method}
        if params:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        self._ws.send_json(msg)
        while True:
            incoming = self._ws.recv_json()
            if incoming.get("id") != call_id:
                continue
            if "error" in incoming:
                err = incoming["error"] or {}
                raise CDPError(
                    "CDP_COMMAND_ERROR",
                    f"{method} 调用失败：{err.get('message', err)}",
                    422,
                )
            return incoming.get("result") or {}

    def page_targets(self) -> list[dict[str, Any]]:
        infos = self.call("Target.getTargets").get("targetInfos") or []
        pages = []
        for info in infos:
            if info.get("type") != "page":
                continue
            url = str(info.get("url") or "")
            if url.startswith("devtools://") or url.startswith("chrome-extension://"):
                continue
            pages.append(
                {
                    "target_id": info["targetId"],
                    "title": info.get("title") or "",
                    "url": url,
                    "attached": bool(info.get("attached")),
                }
            )
        return pages

    def resolve_page_target(self, target_id: str = "", *, create_if_empty: bool = True) -> dict[str, Any]:
        pages = self.page_targets()
        if target_id:
            for p in pages:
                if p["target_id"] == target_id:
                    return p
            raise CDPError("TARGET_NOT_FOUND", f"未找到标签页 {target_id}", 404)
        if pages:
            # Prefer a non-workbench user page if available; otherwise return the first tab.
            for p in pages:
                if "/workbench/" not in p["url"] and p["url"] != "about:blank":
                    return p
            return pages[0]
        if not create_if_empty:
            raise CDPError("NO_PAGE_TARGET", "当前浏览器窗口没有打开的网页标签页", 404)
        created = self.call("Target.createTarget", {"url": "about:blank"})
        new_id = created["targetId"]
        return {"target_id": new_id, "title": "", "url": "about:blank", "attached": False}

    def attach_page(self, target_id: str = "") -> tuple[str, dict[str, Any]]:
        target = self.resolve_page_target(target_id)
        attached = self.call(
            "Target.attachToTarget",
            {"targetId": target["target_id"], "flatten": True},
        )
        return attached["sessionId"], target

    def eval_isolated(
        self,
        session_id: str,
        expression: str,
        *,
        await_promise: bool = True,
    ) -> Any:
        tree = self.call("Page.getFrameTree", session_id=session_id)
        frame_id = tree["frameTree"]["frame"]["id"]
        world = self.call(
            "Page.createIsolatedWorld",
            {
                "frameId": frame_id,
                "worldName": "moltskin_isolated",
                "grantUniveralAccess": True,
            },
            session_id=session_id,
        )
        ctx_id = world["executionContextId"]
        return self._evaluate_in_context(
            session_id, expression, context_id=ctx_id, await_promise=await_promise
        )

    def eval_main(
        self,
        session_id: str,
        expression: str,
        *,
        await_promise: bool = True,
    ) -> Any:
        # Runtime.evaluate without contextId uses Blink's ensureDefaultContextInGroup
        # directly WITHOUT requiring Runtime.enable!
        return self._evaluate_in_context(
            session_id, expression, context_id=None, await_promise=await_promise
        )

    def _evaluate_in_context(
        self,
        session_id: str,
        expression: str,
        *,
        context_id: int | None,
        await_promise: bool,
    ) -> Any:
        params: dict[str, Any] = {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        }
        if context_id is not None:
            params["contextId"] = context_id
        res = self.call("Runtime.evaluate", params, session_id=session_id)
        if "exceptionDetails" in res:
            details = res["exceptionDetails"] or {}
            exc_obj = details.get("exception") or {}
            desc = exc_obj.get("description") or details.get("text") or "JS 执行异常"
            raise CDPError("EVALUATE_ERROR", str(desc), 422)
        remote = res.get("result") or {}
        if remote.get("subtype") == "null":
            return None
        if "value" in remote:
            return remote["value"]
        if remote.get("type") == "undefined":
            return None
        return remote.get("description")


_SNAPSHOT_JS = """
(() => {
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
      return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  const cssPath = (el) => {
    if (el.id && /^[A-Za-z_][A-Za-z0-9_\\-:.]*$/.test(el.id)) {
      const byId = '#' + CSS.escape(el.id);
      if (document.querySelectorAll(byId).length === 1) return byId;
    }
    for (const attr of ['data-testid', 'name', 'aria-label', 'placeholder']) {
      const v = el.getAttribute(attr);
      if (v && v.length <= 64) {
        const candidate = `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(v)}"]`;
        try {
          if (document.querySelectorAll(candidate).length === 1) return candidate;
        } catch (_) {}
      }
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      let tag = cur.tagName.toLowerCase();
      if (cur.id && /^[A-Za-z_][A-Za-z0-9_\\-:.]*$/.test(cur.id)) {
        parts.unshift('#' + CSS.escape(cur.id));
        break;
      }
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (siblings.length > 1) {
          tag += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
        }
      }
      parts.unshift(tag);
      cur = parent;
    }
    return parts.join(' > ');
  };
  const query = [
    'a[href]', 'button', 'input:not([type="hidden"])', 'textarea', 'select',
    '[role="button"]', '[role="link"]', '[role="textbox"]', '[role="checkbox"]',
    '[role="radio"]', '[role="combobox"]', '[role="tab"]', '[contenteditable="true"]'
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(query));
  const elements = [];
  for (const el of nodes) {
    if (elements.length >= __MAX_ELEMENTS__) break;
    if (!isVisible(el)) continue;
    const r = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const label = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.textContent ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      ''
    ).replace(/\\s+/g, ' ').trim().slice(0, 120);
    const item = {
      selector: cssPath(el),
      tag,
      role: el.getAttribute('role') || '',
      label,
      rect: {
        x: Math.round(r.x),
        y: Math.round(r.y),
        width: Math.round(r.width),
        height: Math.round(r.height),
      },
    };
    if (type) item.type = type;
    if ('disabled' in el && el.disabled) item.disabled = true;
    if (tag === 'a') item.href = el.getAttribute('href') || '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      item.value = type === 'password' ? '***' : String(el.value || '').slice(0, 120);
      if (type === 'checkbox' || type === 'radio') item.checked = Boolean(el.checked);
    }
    elements.push(item);
  }
  const bodyText = __INCLUDE_TEXT__
    ? ((document.body && document.body.innerText) || '').replace(/\\n{3,}/g, '\\n\\n').trim().slice(0, 4000)
    : '';
  return {
    url: location.href,
    title: document.title || '',
    ready_state: document.readyState,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      scroll_x: Math.round(window.scrollX),
      scroll_y: Math.round(window.scrollY),
    },
    text: bodyText,
    elements,
  };
})()
"""


def _locate_selector_js(selector: str) -> str:
    sel_json = json.dumps(selector)
    return f"""
(() => {{
  const raw = {sel_json};
  let el = null;
  if (raw.startsWith('text=')) {{
    const needle = raw.slice(5).trim().toLowerCase();
    const candidates = Array.from(document.querySelectorAll('a,button,label,[role="button"],[role="link"],[role="tab"],span,div'));
    el = candidates.find((n) => (n.innerText || n.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase().includes(needle)) || null;
  }} else {{
    el = document.querySelector(raw);
  }}
  if (!el) return null;
  el.scrollIntoView({{ block: 'center', inline: 'center', behavior: 'instant' }});
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return {{ hidden: true }};
  return {{
    x: r.left + r.width / 2,
    y: r.top + r.height / 2,
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
  }};
}})()
"""


def list_pages(profile_dir: str | Path) -> list[dict[str, Any]]:
    with CDPBrowserSession(profile_dir) as cdp:
        return cdp.page_targets()


def new_page(profile_dir: str | Path, url: str = "about:blank") -> dict[str, Any]:
    with CDPBrowserSession(profile_dir) as cdp:
        created = cdp.call("Target.createTarget", {"url": url or "about:blank"})
        return {"target_id": created["targetId"], "url": url or "about:blank"}


def close_page(profile_dir: str | Path, target_id: str) -> dict[str, Any]:
    with CDPBrowserSession(profile_dir) as cdp:
        res = cdp.call("Target.closeTarget", {"targetId": target_id})
        return {"target_id": target_id, "closed": bool(res.get("success", True))}


def navigate(
    profile_dir: str | Path,
    url: str,
    *,
    target_id: str = "",
    wait_until: str = "load",
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not isinstance(url, str) or not url.strip():
        raise CDPError("INVALID_URL", "导航地址不能为空", 422)
    if wait_until not in ("none", "domcontentloaded", "load"):
        raise CDPError("INVALID_WAIT_UNTIL", "wait_until 须为 none、domcontentloaded 或 load", 422)
    with CDPBrowserSession(profile_dir, timeout=timeout) as cdp:
        sid, target = cdp.attach_page(target_id)
        nav = cdp.call("Page.navigate", {"url": url.strip()}, session_id=sid)
        if nav.get("errorText"):
            raise CDPError("NAVIGATION_FAILED", f"页面加载失败：{nav['errorText']}", 502)
        if wait_until != "none":
            deadline = time.monotonic() + timeout
            desired = ("interactive", "complete") if wait_until == "domcontentloaded" else ("complete",)
            while time.monotonic() < deadline:
                try:
                    info = cdp.eval_isolated(
                        sid,
                        "({url: location.href, title: document.title || '', state: document.readyState})",
                    )
                    if isinstance(info, dict) and info.get("state") in desired and info.get("url") != "about:blank" or url.strip() == "about:blank":
                        if isinstance(info, dict) and info.get("state") in desired:
                            return {
                                "target_id": target["target_id"],
                                "url": info["url"],
                                "title": info["title"],
                                "ready_state": info["state"],
                            }
                except CDPError:
                    pass
                time.sleep(0.08)
        info = cdp.eval_isolated(
            sid,
            "({url: location.href, title: document.title || '', state: document.readyState})",
        ) or {}
        return {
            "target_id": target["target_id"],
            "url": info.get("url", url),
            "title": info.get("title", ""),
            "ready_state": info.get("state", ""),
        }


def snapshot(
    profile_dir: str | Path,
    *,
    target_id: str = "",
    max_elements: int = 120,
    include_text: bool = True,
) -> dict[str, Any]:
    cap = max(1, min(int(max_elements), 500))
    script = _SNAPSHOT_JS.replace("__MAX_ELEMENTS__", str(cap)).replace(
        "__INCLUDE_TEXT__", "true" if include_text else "false"
    )
    with CDPBrowserSession(profile_dir) as cdp:
        sid, target = cdp.attach_page(target_id)
        data = cdp.eval_isolated(sid, script)
        return {"target_id": target["target_id"], **(data or {})}


def _dispatch_click(cdp: CDPBrowserSession, sid: str, x: float, y: float, button: str = "left", click_count: int = 1) -> None:
    btn = button if button in ("left", "right", "middle") else "left"
    cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
        session_id=sid,
    )
    cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": btn, "clickCount": click_count},
        session_id=sid,
    )
    cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x, "y": y, "button": btn, "clickCount": click_count},
        session_id=sid,
    )


def click(
    profile_dir: str | Path,
    selector: str | None = None,
    *,
    x: float | None = None,
    y: float | None = None,
    target_id: str = "",
    button: str = "left",
    click_count: int = 1,
) -> dict[str, Any]:
    with CDPBrowserSession(profile_dir) as cdp:
        sid, target = cdp.attach_page(target_id)
        if selector:
            pos = cdp.eval_isolated(sid, _locate_selector_js(selector))
            if pos is None:
                raise CDPError("ELEMENT_NOT_FOUND", f"未找到匹配元素：{selector}", 404)
            if pos.get("hidden"):
                raise CDPError("ELEMENT_HIDDEN", f"目标元素不可见：{selector}", 422)
            cx, cy = float(pos["x"]), float(pos["y"])
        elif x is not None and y is not None:
            cx, cy = float(x), float(y)
        else:
            raise CDPError("MISSING_TARGET", "click 需要提供 selector 或 (x, y) 坐标", 422)
        _dispatch_click(cdp, sid, cx, cy, button=button, click_count=max(1, int(click_count)))
        return {"target_id": target["target_id"], "clicked": True, "x": round(cx, 1), "y": round(cy, 1)}


_KEY_DEFS: dict[str, tuple[int, str, str]] = {
    "Enter": (13, "Enter", "Enter"),
    "Tab": (9, "Tab", "Tab"),
    "Escape": (27, "Escape", "Escape"),
    "Backspace": (8, "Backspace", "Backspace"),
    "Delete": (46, "Delete", "Delete"),
    "ArrowUp": (38, "ArrowUp", "ArrowUp"),
    "ArrowDown": (40, "ArrowDown", "ArrowDown"),
    "ArrowLeft": (37, "ArrowLeft", "ArrowLeft"),
    "ArrowRight": (39, "ArrowRight", "ArrowRight"),
    "Space": (32, " ", "Space"),
}


def _dispatch_key(cdp: CDPBrowserSession, sid: str, key: str) -> None:
    if key in _KEY_DEFS:
        vk, k_val, code = _KEY_DEFS[key]
        down_params: dict[str, Any] = {
            "type": "rawKeyDown",
            "windowsVirtualKeyCode": vk,
            "nativeVirtualKeyCode": vk,
            "key": k_val,
            "code": code,
        }
        if key == "Enter":
            down_params["text"] = "\r"
            down_params["unmodifiedText"] = "\r"
        elif key == "Space":
            down_params["text"] = " "
            down_params["unmodifiedText"] = " "
        cdp.call("Input.dispatchKeyEvent", down_params, session_id=sid)
        cdp.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk,
                "key": k_val,
                "code": code,
            },
            session_id=sid,
        )
    elif len(key) == 1:
        vk = ord(key.upper())
        cdp.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "text": key, "unmodifiedText": key, "key": key, "windowsVirtualKeyCode": vk},
            session_id=sid,
        )
        cdp.call(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": key, "windowsVirtualKeyCode": vk},
            session_id=sid,
        )
    else:
        raise CDPError("UNSUPPORTED_KEY", f"不支持的按键名称：{key}", 422)


def fill(
    profile_dir: str | Path,
    selector: str,
    value: str,
    *,
    target_id: str = "",
    clear: bool = True,
    submit: bool = False,
) -> dict[str, Any]:
    if not selector:
        raise CDPError("MISSING_SELECTOR", "fill 需要提供 selector", 422)
    with CDPBrowserSession(profile_dir) as cdp:
        sid, target = cdp.attach_page(target_id)
        pos = cdp.eval_isolated(sid, _locate_selector_js(selector))
        if pos is None:
            raise CDPError("ELEMENT_NOT_FOUND", f"未找到输入框：{selector}", 404)
        if pos.get("hidden"):
            raise CDPError("ELEMENT_HIDDEN", f"输入框不可见：{selector}", 422)

        if pos.get("tag") == "select":
            sel_js = f"""
            (() => {{
              const el = document.querySelector({json.dumps(selector)});
              if (!el) return false;
              el.value = {json.dumps(value)};
              el.dispatchEvent(new Event('input', {{ bubbles: true }}));
              el.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return true;
            }})()
            """
            cdp.eval_isolated(sid, sel_js)
        else:
            _dispatch_click(cdp, sid, float(pos["x"]), float(pos["y"]))
            if clear:
                clear_js = f"""
                (() => {{
                  const el = document.querySelector({json.dumps(selector)});
                  if (!el) return;
                  el.focus();
                  if ('value' in el) {{
                    const proto = el.tagName === 'TEXTAREA'
                      ? window.HTMLTextAreaElement.prototype
                      : window.HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && desc.set) desc.set.call(el, '');
                    else el.value = '';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  }} else if (el.isContentEditable) {{
                    el.textContent = '';
                  }}
                }})()
                """
                cdp.eval_isolated(sid, clear_js)
            if value:
                cdp.call("Input.insertText", {"text": str(value)}, session_id=sid)
            cdp.eval_isolated(
                sid,
                f"""
                (() => {{
                  const el = document.querySelector({json.dumps(selector)});
                  if (el) el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }})()
                """,
            )
        if submit:
            _dispatch_key(cdp, sid, "Enter")
        return {"target_id": target["target_id"], "filled": True, "selector": selector}


def press_key(profile_dir: str | Path, key: str, *, target_id: str = "") -> dict[str, Any]:
    with CDPBrowserSession(profile_dir) as cdp:
        sid, target = cdp.attach_page(target_id)
        _dispatch_key(cdp, sid, key)
        return {"target_id": target["target_id"], "pressed": key}


def evaluate(
    profile_dir: str | Path,
    expression: str,
    *,
    target_id: str = "",
    isolated: bool = False,
    await_promise: bool = True,
) -> dict[str, Any]:
    if not isinstance(expression, str) or not expression.strip():
        raise CDPError("EMPTY_EXPRESSION", "JS 表达式不能为空", 422)
    with CDPBrowserSession(profile_dir) as cdp:
        sid, target = cdp.attach_page(target_id)
        result = (
            cdp.eval_isolated(sid, expression, await_promise=await_promise)
            if isolated
            else cdp.eval_main(sid, expression, await_promise=await_promise)
        )
        return {"target_id": target["target_id"], "result": result}


def screenshot(
    profile_dir: str | Path,
    *,
    target_id: str = "",
    full_page: bool = False,
    image_format: str = "png",
) -> dict[str, Any]:
    fmt = "jpeg" if image_format == "jpeg" else "png"
    with CDPBrowserSession(profile_dir) as cdp:
        sid, target = cdp.attach_page(target_id)
        params: dict[str, Any] = {"format": fmt}
        if full_page:
            metrics = cdp.call("Page.getLayoutMetrics", session_id=sid)
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            width = max(1, min(int(size.get("width") or 1280), 8192))
            height = max(1, min(int(size.get("height") or 800), 16384))
            params["clip"] = {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}
            params["captureBeyondViewport"] = True
        captured = cdp.call("Page.captureScreenshot", params, session_id=sid)
        b64 = captured["data"]
        return {
            "target_id": target["target_id"],
            "format": fmt,
            "mime_type": f"image/{fmt}",
            "data_base64": b64,
        }


def export_live_cookies(profile_dir: str | Path) -> list[dict[str, Any]]:
    """Read all decrypted cookies from the running Chromium instance via Storage.getCookies."""
    with CDPBrowserSession(profile_dir, timeout=5.0) as cdp:
        raw_cookies = cdp.call("Storage.getCookies").get("cookies") or []
    result: list[dict[str, Any]] = []
    for c in raw_cookies:
        if c.get("partitionKey"):
            continue
        same_site = str(c.get("sameSite") or "unspecified").lower()
        same_site = {"none": "no_restriction", "lax": "lax", "strict": "strict"}.get(
            same_site, "unspecified"
        )
        expires = float(c.get("expires") or -1)
        if c.get("session") or expires <= 0:
            expires = -1
        result.append(
            {
                "name": c["name"],
                "value": c.get("value", ""),
                "domain": c["domain"],
                "path": c.get("path") or "/",
                "expires": expires,
                "secure": bool(c.get("secure")),
                "httpOnly": bool(c.get("httpOnly")),
                "sameSite": same_site,
            }
        )
    return result
