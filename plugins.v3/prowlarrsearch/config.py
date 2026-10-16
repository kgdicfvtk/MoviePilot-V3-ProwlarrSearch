from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit


DEFAULT_BASE_URL = "http://prowlarr:9696"
DEFAULT_TIMEOUT = 30
DEFAULT_PAGE_SIZE = 100
DEFAULT_CANDIDATE_LIMIT = 500


@dataclass(frozen=True, slots=True)
class ProwlarrConfig:
    enabled: bool
    base_url: str | None
    api_key: str
    timeout: int
    page_size: int
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.base_url) and bool(self.api_key)


def normalize_base_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate or "\\" in candidate or any(ord(char) < 32 for char in candidate):
        return None

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None

    raw_path = parsed.path
    decoded_path = unquote(raw_path)
    if raw_path and not raw_path.startswith("/"):
        return None
    if "//" in raw_path or "//" in decoded_path or "\\" in decoded_path:
        return None
    if raw_path.count("/") != decoded_path.count("/"):
        return None
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        return None

    path = raw_path.rstrip("/")
    for suffix in ("/api/v1/search", "/api/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break

    normalized_host = host.lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit((scheme, netloc, path, "", ""))


def _normalize_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return False


def _normalize_bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.lstrip("+-").isdigit():
            return default
        parsed = int(stripped)
    else:
        return default
    return min(max(parsed, minimum), maximum)


def normalize_config(raw: Mapping[str, Any] | None) -> ProwlarrConfig:
    values: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    base_value = values["base_url"] if "base_url" in values else DEFAULT_BASE_URL
    api_key_value = values.get("api_key", "")
    api_key = api_key_value.strip() if isinstance(api_key_value, str) else ""
    page_size = _normalize_bounded_int(
        values.get("page_size", DEFAULT_PAGE_SIZE), DEFAULT_PAGE_SIZE, 1, 1000
    )
    candidate_limit = max(
        page_size,
        _normalize_bounded_int(
            values.get("candidate_limit", DEFAULT_CANDIDATE_LIMIT),
            DEFAULT_CANDIDATE_LIMIT,
            1,
            1000,
        ),
    )
    return ProwlarrConfig(
        enabled=_normalize_enabled(values.get("enabled", False)),
        base_url=normalize_base_url(base_value),
        api_key=api_key,
        timeout=_normalize_bounded_int(
            values.get("timeout", DEFAULT_TIMEOUT), DEFAULT_TIMEOUT, 5, 120
        ),
        page_size=page_size,
        candidate_limit=candidate_limit,
    )
