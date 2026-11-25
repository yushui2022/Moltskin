"""本地 API token。"""

from __future__ import annotations

import hmac
import secrets

from . import config


def get_token() -> str:
    config.ensure_dirs()
    if config.TOKEN_PATH.exists():
        token = config.TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    config.TOKEN_PATH.write_text(token, encoding="utf-8")
    config.TOKEN_PATH.chmod(0o600)
    return token


def workbench_signature(session_id: str) -> str:
    message = ("moltskin:session-workbench:" + session_id).encode("utf-8")
    return hmac.digest(get_token().encode("utf-8"), message, "sha256").hex()
