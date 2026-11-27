"""Map an exit country to the product's supported browser language."""

from __future__ import annotations

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
