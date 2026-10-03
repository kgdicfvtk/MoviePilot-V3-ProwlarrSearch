from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.sdk.network import RequestUtils

from .config import ProwlarrConfig, normalize_base_url


_DECIMAL_INDEXER_PATH = re.compile(r"/[0-9]+/download\Z")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_TOKEN_VERSION = 1
_TOKEN_LIFETIME = 86_400
_HMAC_DOMAIN = b"MoviePilot-ProwlarrSearch/relay/v1\x00"


class InvalidRelayToken(ValueError):
    pass


def _parsed_http_url(value: str):
    if "\\" in value or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return parsed, port


def _canonical_origin(parsed, port: int | None) -> tuple[str, str, int]:
    scheme = parsed.scheme.lower()
    effective_port = port if port is not None else (80 if scheme == "http" else 443)
    return scheme, parsed.hostname.lower(), effective_port


def _canonical_netloc(parsed, port: int | None) -> str:
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return host if port is None else f"{host}:{port}"


def sanitize_download_url(download_url: object, base_url: str) -> str | None:
    if not isinstance(download_url, str) or not download_url:
        return None

    normalized_base = normalize_base_url(base_url)
    if normalized_base is None:
        return None

    target_parts = _parsed_http_url(download_url)
    base_parts = _parsed_http_url(normalized_base)
    if target_parts is None or base_parts is None:
        return None
    target, target_port = target_parts
    base, base_port = base_parts

    if target.fragment or _canonical_origin(target, target_port) != _canonical_origin(base, base_port):
        return None

    target_path = target.path
    base_path = base.path.rstrip("/")
    if (
        "%" in target_path
        or "%" in base_path
        or "//" in target_path
        or "//" in base_path
        or "\\" in target_path
    ):
        return None

    relative_path = target_path[len(base_path) :] if target_path.startswith(base_path) else ""
    if not relative_path or not _DECIMAL_INDEXER_PATH.fullmatch(relative_path):
        return None
    if base_path and not target_path.startswith(f"{base_path}/"):
        return None

    try:
        query_items = parse_qsl(target.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return None
    clean_query = urlencode(
        [(key, value) for key, value in query_items if key.lower() != "apikey"]
    )
    return urlunsplit(
        (
            target.scheme.lower(),
            _canonical_netloc(target, target_port),
            target_path,
            clean_query,
            "",
        )
    )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or "=" in value or not _BASE64URL.fullmatch(value):
        raise InvalidRelayToken("invalid relay token")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as error:
        raise InvalidRelayToken("invalid relay token") from error
    if _base64url_encode(decoded) != value:
        raise InvalidRelayToken("invalid relay token")
    return decoded


def _canonical_payload_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class ProwlarrRelay:
    def __init__(
        self,
        config: ProwlarrConfig,
        port: int,
        clock: Callable[[], float] = time.time,
        request_factory: Callable[..., Any] = RequestUtils,
    ) -> None:
        self.config = config
        self.port = port
        self.clock = clock
        self.request_factory = request_factory

    def _hmac_key(self) -> bytes:
        return hashlib.sha256(_HMAC_DOMAIN + self.config.api_key.encode("utf-8")).digest()

    def _sign(self, payload_bytes: bytes) -> bytes:
        return hmac.new(self._hmac_key(), payload_bytes, hashlib.sha256).digest()

    def make_enclosure(self, download_url: object) -> str | None:
        if not self.config.ready or self.config.base_url is None:
            return None
        target = sanitize_download_url(download_url, self.config.base_url)
        if target is None:
            return None

        issued_at = int(self.clock())
        payload: dict[str, object] = {
            "v": _TOKEN_VERSION,
            "u": target,
            "iat": issued_at,
            "exp": issued_at + _TOKEN_LIFETIME,
        }
        payload_bytes = _canonical_payload_bytes(payload)
        token = f"{_base64url_encode(payload_bytes)}.{_base64url_encode(self._sign(payload_bytes))}"
        return (
            f"http://127.0.0.1:{self.port}"
            f"/api/v1/plugin/ProwlarrSearch/download/{token}"
        )

    def decode_target(self, token: str) -> str:
        if not self.config.ready or self.config.base_url is None or not isinstance(token, str):
            raise InvalidRelayToken("invalid relay token")
        if token.count(".") != 1:
            raise InvalidRelayToken("invalid relay token")

        payload_part, signature_part = token.split(".", 1)
        payload_bytes = _base64url_decode(payload_part)
        signature = _base64url_decode(signature_part)
        if len(signature) != hashlib.sha256().digest_size:
            raise InvalidRelayToken("invalid relay token")
        if not hmac.compare_digest(signature, self._sign(payload_bytes)):
            raise InvalidRelayToken("invalid relay token")

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidRelayToken("invalid relay token") from error
        if not isinstance(payload, dict) or set(payload) != {"v", "u", "iat", "exp"}:
            raise InvalidRelayToken("invalid relay token")
        if _canonical_payload_bytes(payload) != payload_bytes:
            raise InvalidRelayToken("invalid relay token")

        version = payload["v"]
        target = payload["u"]
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        if (
            type(version) is not int
            or version != _TOKEN_VERSION
            or not isinstance(target, str)
            or type(issued_at) is not int
            or type(expires_at) is not int
        ):
            raise InvalidRelayToken("invalid relay token")

        now = int(self.clock())
        lifetime = expires_at - issued_at
        if issued_at > now or expires_at <= now or lifetime <= 0 or lifetime > _TOKEN_LIFETIME:
            raise InvalidRelayToken("invalid relay token")

        sanitized = sanitize_download_url(target, self.config.base_url)
        if sanitized is None or sanitized != target:
            raise InvalidRelayToken("invalid relay token")
        return target
