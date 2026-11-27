"""Encrypt proxy passwords at rest.

The master key lives beside the database with mode 0600. A platform keychain
adapter can replace this file later without changing the database format.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from . import config

PREFIX = "enc:v1:"
KEY_NAME = "proxy.key"


def key_path():
    return config.DATA_DIR / KEY_NAME


def _master_key() -> bytes:
    config.ensure_dirs()
    path = key_path()
    try:
        key = path.read_bytes().strip()
    except FileNotFoundError:
        key = Fernet.generate_key()
        path.write_bytes(key)
        path.chmod(0o600)
        return key
    if not key:
        raise ValueError("代理密钥文件为空")
    return key


def _fernet() -> Fernet:
    try:
        return Fernet(_master_key())
    except (ValueError, TypeError) as exc:
        raise ValueError("代理密钥无效") from exc


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: str | None) -> str | None:
    if not value:
        return value
    if is_encrypted(value):
        return value
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(value: str | None) -> str | None:
    if not value:
        return value
    if not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("代理密码无法解密，请重新填写") from exc


def ensure_key_file_mode() -> None:
    path = key_path()
    if path.exists():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
