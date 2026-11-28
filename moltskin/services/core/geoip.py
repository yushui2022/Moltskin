"""Map exit IP / country to browser language and deterministic spoofed coordinates."""

from __future__ import annotations

import hashlib
import math

from . import config

_COUNTRY_LANGUAGES = {
    "US": "en-US", "GB": "en-GB", "AU": "en-GB", "CA": "en-US", "NZ": "en-GB",
    "IE": "en-GB", "SG": "en-GB", "IN": "en-GB",
    "CN": "zh-CN", "HK": "zh-CN", "TW": "zh-CN", "MO": "zh-CN",
    "JP": "ja-JP", "DE": "de-DE", "AT": "de-DE", "CH": "de-DE",
    "FR": "fr-FR", "BE": "fr-FR", "LU": "fr-FR", "MC": "fr-FR",
}

_LANGUAGE_FALLBACKS = {
    "en": ("en-US", "en-GB"),
    "zh": ("zh-CN",),
    "ja": ("ja-JP",),
    "de": ("de-DE",),
    "fr": ("fr-FR",),
}


def locale_for_country(country: str | None, locale: str | None = None) -> str:
    if locale in config.CHROMIUM_BROWSER_LANGUAGES:
        return locale
    if country:
        mapped = _COUNTRY_LANGUAGES.get(country.upper())
        if mapped:
            return mapped
    if locale:
        language = locale.replace("_", "-").split("-", 1)[0].lower()
        for candidate in _LANGUAGE_FALLBACKS.get(language, ()):
            if candidate in config.CHROMIUM_BROWSER_LANGUAGES:
                return candidate
    return ""


def coordinates_for_ip(ip: str | None) -> tuple[float, float] | None:
    """Resolve approximate (latitude, longitude) for an exit IP using offline MaxMind DB."""
    if not ip or not isinstance(ip, str):
        return None
    try:
        from camoufox.geolocation import get_geolocation

        geo = get_geolocation(ip.strip())
        lat = float(geo.latitude)
        lon = float(geo.longitude)
        if math.isfinite(lat) and -90.0 <= lat <= 90.0 and math.isfinite(lon) and -180.0 <= lon <= 180.0:
            return lat, lon
    except Exception:  # noqa: BLE001
        return None
    return None


def jitter_coordinates(
    latitude: float,
    longitude: float,
    profile_id: str,
) -> tuple[float, float, float]:
    """Apply stable per-profile micro-jitter (~150m-550m) so multiple profiles
    sharing the same city/subnet proxy do not report identical 6-decimal GPS coordinates.
    """
    digest = hashlib.sha256(f"moltskin-geo-jitter:{profile_id}:{latitude:.4f},{longitude:.4f}".encode()).digest()
    u1 = int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF
    u2 = int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF
    u3 = int.from_bytes(digest[8:12], "big") / 0xFFFFFFFF

    # Polar offset: radius 0.0015 deg (~165m) to 0.0048 deg (~530m), uniform angle.
    radius_deg = 0.0015 + u1 * 0.0033
    angle = u2 * 2.0 * math.pi
    dlat = radius_deg * math.sin(angle)
    cos_lat = max(0.2, math.cos(math.radians(latitude)))
    dlon = (radius_deg * math.cos(angle)) / cos_lat

    lat = max(-89.9999, min(89.9999, round(latitude + dlat, 6)))
    lon = ((longitude + dlon + 180.0) % 360.0) - 180.0
    lon = round(lon, 6)
    accuracy = round(18.0 + u3 * 24.0, 1)
    return lat, lon, accuracy
