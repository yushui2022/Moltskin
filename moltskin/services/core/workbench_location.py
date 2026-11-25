"""Look up a public address in existing Camoufox MMDB files without network access."""

from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path

from platformdirs import user_cache_dir


def public_address(value: str) -> str:
    address = ip_address(value)
    if (not address.is_global or address.is_multicast or address.is_reserved
            or isinstance(address, IPv6Address) and (address.is_site_local or address.scope_id or address.ipv4_mapped)):
        raise ValueError("请输入公网 IPv4 或 IPv6 单播地址。")
    return str(address)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _name(value):
    if isinstance(value, str):
        return value
    names = _mapping(_mapping(value).get("names"))
    return names.get("zh-CN") or names.get("en")


def _location(record) -> dict[str, str]:
    record = _mapping(record)
    location = _mapping(record.get("location")) or record
    divisions = record.get("subdivisions")
    region = divisions[0] if isinstance(divisions, list) and divisions else record.get("state1_name")
    fields = {
        "country": _mapping(record.get("country")).get("iso_code") or record.get("country_code"),
        "region": _name(region),
        "city": _name(record.get("city")) or record.get("city_name"),
        "postal": _mapping(record.get("postal")).get("code") or record.get("postcode"),
        "timezone": location.get("time_zone") or location.get("timezone"),
    }
    result = {key: text for key, text in fields.items() if isinstance(text, str) and text}
    lat, lon = location.get("latitude"), location.get("longitude")
    if type(lat) in (float, int) and type(lon) in (float, int) and -90 <= lat <= 90 and -180 <= lon <= 180:
        result["loc"] = f"{lat},{lon}"
    return result


def lookup(address: str) -> dict[str, str]:
    try:
        import maxminddb
    except ImportError:
        return {}
    directory = Path(user_cache_dir("camoufox")) / "geoip" / "mmdb"
    family = "ipv4" if isinstance(ip_address(address), IPv4Address) else "ipv6"
    # The default AIO DB covers both families. Older GeoLite2 caches store them separately.
    for suffix in ("combined", family):
        for path in sorted(directory.glob(f"*-{suffix}.mmdb")):
            try:
                if path.is_file():
                    with maxminddb.open_database(str(path)) as reader:
                        result = _location(reader.get(address))
                    if result:
                        return result
            except (OSError, ValueError, maxminddb.InvalidDatabaseError):
                continue
    return {}
