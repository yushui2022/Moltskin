"""代理检测 —— 直接复用 Camoufox 自己的组件。

- 出口 IP / 时区：`camoufox.ip.proxy_exit_geo`
- 国家 / 语言 / 经纬度：`camoufox.geolocation.get_geolocation`
- 代理字符串规范化：`camoufox.ip.Proxy`

不再自己写 HTTP 探测逻辑。
"""

from __future__ import annotations

import time

from camoufox.exceptions import InvalidIP
from camoufox.geolocation import get_geolocation
from camoufox.ip import Proxy, proxy_exit_geo

from . import db


def check_proxy(
    protocol: str,
    host: str,
    port: int,
    username: str | None = None,
    password: str | None = None,
) -> dict:
    proxy = Proxy(
        server=f"{protocol}://{host}:{port}",
        username=username or None,
        password=password or None,
    )
    proxy_str = proxy.as_string()

    started = time.perf_counter()
    try:
        exit_ip, proxy_timezone = proxy_exit_geo(proxy_str)
    except InvalidIP as exc:
        return {
            "status": "failed",
            "exit_ip": None,
            "country": None,
            "timezone": None,
            "locale": None,
            "latitude": None,
            "longitude": None,
            "latency_ms": None,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "exit_ip": None,
            "country": None,
            "timezone": None,
            "locale": None,
            "latitude": None,
            "longitude": None,
            "latency_ms": None,
            "error": str(exc),
        }

    latency_ms = int((time.perf_counter() - started) * 1000)

    country = None
    locale = None
    latitude = None
    longitude = None
    timezone = proxy_timezone
    try:
        geo = get_geolocation(exit_ip)
        country = geo.locale.region
        locale = geo.locale.as_string
        latitude = geo.latitude
        longitude = geo.longitude
        timezone = geo.timezone or proxy_timezone
    except Exception as exc:  # noqa: BLE001 - 本地 GeoIP 缺失时仍然返回 IP
        geo_error = str(exc)
    else:
        geo_error = None

    return {
        "status": "slow" if latency_ms > 3000 else "ok",
        "exit_ip": exit_ip,
        "country": country,
        "timezone": timezone,
        "locale": locale,
        "latitude": latitude,
        "longitude": longitude,
        "latency_ms": latency_ms,
        "error": geo_error,
    }


def check_and_save(proxy: dict) -> dict:
    result = check_proxy(
        proxy["protocol"],
        proxy["host"],
        proxy["port"],
        proxy.get("username"),
        proxy.get("password"),
    )
    with db.session() as conn:
        conn.execute(
            "UPDATE proxies SET status=?, exit_ip=?, country=?, timezone=?, latency_ms=?, "
            "last_checked_at=?, updated_at=? WHERE id=?",
            (
                result["status"],
                result["exit_ip"],
                result["country"],
                result["timezone"],
                result["latency_ms"],
                db.now(),
                db.now(),
                proxy["id"],
            ),
        )
    result["id"] = proxy["id"]
    return result
