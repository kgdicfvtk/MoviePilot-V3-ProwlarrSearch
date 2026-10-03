from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any
from urllib.parse import urlsplit

from app.sdk.logging import logger
from app.sdk.media import TorrentInfo


_SEARCH_TYPES = {
    "movie": "movie",
    "电影": "movie",
    "tv": "tvsearch",
    "tvshow": "tvsearch",
    "tvsearch": "tvsearch",
    "电视剧": "tvsearch",
    "电视": "tvsearch",
    "music": "music",
    "音乐": "music",
}


def _media_type_key(mtype: Any) -> str | None:
    if mtype is None:
        return None
    candidates: list[object] = []
    try:
        candidates.append(getattr(mtype, "name", None))
    except Exception:
        pass
    try:
        candidates.append(getattr(mtype, "value", None))
    except Exception:
        pass
    if isinstance(mtype, str):
        candidates.append(mtype)
    for candidate in candidates:
        if isinstance(candidate, str):
            normalized = candidate.strip().lower()
            if normalized in _SEARCH_TYPES:
                return normalized
    return None


def prowlarr_search_type(mtype: Any) -> str:
    key = _media_type_key(mtype)
    return _SEARCH_TYPES.get(key, "search")


def moviepilot_category(mtype: Any) -> str | None:
    if _media_type_key(mtype) is None:
        return None
    try:
        value = getattr(mtype, "value", None)
    except Exception:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(mtype, str) and mtype.strip():
        return mtype.strip()
    return None


def normalize_pubdate(value: object, local_tz: tzinfo | None = None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    encoded = value.strip()
    if encoded.endswith("Z"):
        encoded = f"{encoded[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(encoded)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        resolved_tz = local_tz
        if resolved_tz is None:
            resolved_tz = datetime.now().astimezone().tzinfo
        if resolved_tz is None:
            return None
        return parsed.astimezone(resolved_tz).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return None


def _nonempty_string(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, float) and not value.is_integer():
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return parsed if parsed >= 0 else 0


def _labels(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for category in value:
        if not isinstance(category, dict):
            continue
        name = _nonempty_string(category.get("name"))
        if name is not None and name not in seen:
            result.append(name)
            seen.add(name)
    return result


def _http_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return urlsplit(value.strip()).scheme.lower() in {"http", "https"}
    except ValueError:
        return False


def _magnet_url(value: object) -> str | None:
    candidate = _nonempty_string(value)
    if candidate is not None and candidate.lower().startswith("magnet:"):
        return candidate
    return None


def _enclosure(
    release: dict[str, Any], relay_builder: Callable[[object], str | None]
) -> str | None:
    for candidate in (release.get("downloadUrl"), release.get("magnetUrl")):
        direct_magnet = _magnet_url(candidate)
        if direct_magnet is not None:
            return direct_magnet
        if _http_url(candidate):
            relayed = relay_builder(candidate)
            if isinstance(relayed, str) and relayed:
                return relayed
    return None


def map_release(
    release: object,
    mtype: Any,
    relay_builder: Callable[[object], str | None],
    local_tz: tzinfo | None = None,
) -> TorrentInfo | None:
    if not isinstance(release, dict):
        return None
    protocol = release.get("protocol")
    if not isinstance(protocol, str) or protocol.strip().lower() != "torrent":
        return None
    title = _nonempty_string(release.get("title"))
    if title is None:
        return None
    enclosure = _enclosure(release, relay_builder)
    if enclosure is None:
        return None

    indexer = _nonempty_string(release.get("indexer")) or "Prowlarr"
    labels = _labels(release.get("categories"))
    description = f"Prowlarr | {indexer}"
    if labels:
        description = f"{description} | {', '.join(labels)}"
    page_url = _nonempty_string(release.get("infoUrl")) or _nonempty_string(
        release.get("commentUrl")
    )

    return TorrentInfo(
        site=None,
        site_name=indexer,
        title=title,
        description=description,
        enclosure=enclosure,
        page_url=page_url,
        size=_non_negative_int(release.get("size")),
        seeders=_non_negative_int(release.get("seeders")),
        peers=_non_negative_int(release.get("leechers")),
        grabs=_non_negative_int(release.get("grabs")),
        pubdate=normalize_pubdate(release.get("publishDate"), local_tz),
        labels=labels,
        category=moviepilot_category(mtype),
    )


def map_releases(
    releases: object,
    mtype: Any,
    relay_builder: Callable[[object], str | None],
    local_tz: tzinfo | None = None,
) -> list[TorrentInfo]:
    if not isinstance(releases, list):
        return []
    results: list[TorrentInfo] = []
    for index, release in enumerate(releases):
        try:
            mapped = map_release(release, mtype, relay_builder, local_tz)
        except Exception:
            logger.warning(f"Prowlarr 结果映射失败（位置 {index}）")
            continue
        if mapped is not None:
            results.append(mapped)
    return results
