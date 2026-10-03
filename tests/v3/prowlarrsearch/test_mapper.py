from __future__ import annotations

import json
from datetime import datetime as real_datetime
from datetime import timedelta, timezone
from enum import Enum

import pytest

from app.plugins.prowlarrsearch import mapper as mapper_module
from app.plugins.prowlarrsearch.config import ProwlarrConfig
from app.plugins.prowlarrsearch.mapper import (
    map_release,
    map_releases,
    moviepilot_category,
    normalize_pubdate,
    prowlarr_search_type,
)
from app.plugins.prowlarrsearch.relay import ProwlarrRelay


class MediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"
    MUSIC = "音乐"
    UNKNOWN = "未知"


LOCAL_TZ = timezone(timedelta(hours=8))


def test_complete_torrent_release_maps_every_contract_field() -> None:
    calls: list[object] = []

    def relay_builder(value: object) -> str | None:
        calls.append(value)
        return "http://127.0.0.1:3001/api/v1/plugin/ProwlarrSearch/download/signed"

    release = {
        "protocol": "ToRrEnT",
        "title": " Example.Release ",
        "indexer": " Example Indexer ",
        "downloadUrl": "http://prowlarr:9696/7/download?apikey=hidden&link=x",
        "magnetUrl": "magnet:?xt=urn:btih:fallback",
        "infoUrl": "https://indexer.example/details/7",
        "commentUrl": "https://indexer.example/comments/7",
        "size": "123456",
        "seeders": "42",
        "leechers": 7,
        "grabs": "9",
        "publishDate": "2024-01-01T00:30:00Z",
        "categories": [
            {"name": "Movies"},
            {"name": " Movies "},
            {"name": "4K"},
            {"name": ""},
        ],
    }

    result = map_release(release, MediaType.MOVIE, relay_builder, LOCAL_TZ)

    assert result is not None
    assert result.site is None
    assert result.site_name == "Example Indexer"
    assert result.title == "Example.Release"
    assert result.enclosure == (
        "http://127.0.0.1:3001/api/v1/plugin/ProwlarrSearch/download/signed"
    )
    assert result.page_url == "https://indexer.example/details/7"
    assert result.size == 123456
    assert result.seeders == 42
    assert result.peers == 7
    assert result.grabs == 9
    assert result.pubdate == "2024-01-01 08:30:00"
    assert result.labels == ["Movies", "4K"]
    assert result.category == "电影"
    assert result.description == "Prowlarr | Example Indexer | Movies, 4K"
    assert calls == [release["downloadUrl"]]


def test_direct_magnet_bypasses_relay_and_indexer_name_falls_back() -> None:
    result = map_release(
        {
            "protocol": "torrent",
            "title": "Magnet Release",
            "magnetUrl": "magnet:?xt=urn:btih:abc",
            "commentUrl": "https://example/comments",
        },
        MediaType.TV,
        lambda value: pytest.fail(f"relay should not receive {value!r}"),
    )

    assert result is not None
    assert result.enclosure == "magnet:?xt=urn:btih:abc"
    assert result.site_name == "Prowlarr"
    assert result.page_url == "https://example/comments"
    assert result.category == "电视剧"


def test_invalid_http_download_falls_back_to_valid_magnet() -> None:
    result = map_release(
        {
            "protocol": "torrent",
            "title": "Fallback",
            "downloadUrl": "https://foreign.example/1/download",
            "magnetUrl": "magnet:?xt=urn:btih:fallback",
        },
        None,
        lambda value: None,
    )

    assert result is not None
    assert result.enclosure == "magnet:?xt=urn:btih:fallback"


@pytest.mark.parametrize(
    "release",
    [
        None,
        "not-a-release",
        {"protocol": "usenet", "title": "NZB", "downloadUrl": "https://example/nzb"},
        {"protocol": "torrent", "title": "", "magnetUrl": "magnet:?xt=x"},
        {"protocol": "torrent", "title": "No enclosure"},
        {"protocol": "torrent", "title": "Bad magnet", "magnetUrl": "https://example"},
    ],
)
def test_unusable_release_is_skipped(release: object) -> None:
    assert map_release(release, None, lambda value: None) is None


def test_batch_skips_unusable_entries_without_losing_valid_torrent() -> None:
    releases = [
        None,
        {"protocol": "usenet", "title": "NZB"},
        {"protocol": "torrent", "title": "Good", "magnetUrl": "magnet:?xt=good"},
    ]

    results = map_releases(releases, None, lambda value: None)

    assert [item.title for item in results] == ["Good"]


@pytest.mark.parametrize(
    ("mtype", "search_type", "category"),
    [
        (MediaType.MOVIE, "movie", "电影"),
        (MediaType.TV, "tvsearch", "电视剧"),
        (MediaType.MUSIC, "music", "音乐"),
        (MediaType.UNKNOWN, "search", None),
        (None, "search", None),
    ],
)
def test_media_type_mapping_is_bounded(
    mtype: object, search_type: str, category: str | None
) -> None:
    assert prowlarr_search_type(mtype) == search_type
    assert moviepilot_category(mtype) == category


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-01-01T00:00:00Z", "2024-01-01 08:00:00"),
        ("2024-01-01T12:30:00+05:30", "2024-01-01 15:00:00"),
        ("2024-01-01T00:30:00-04:00", "2024-01-01 12:30:00"),
        (None, None),
        ("", None),
        ("not-a-date", None),
        ("2024-01-01T00:00:00", None),
        (123, None),
    ],
)
def test_publish_date_normalizes_aware_values_and_rejects_bad_or_naive_values(
    value: object, expected: str | None
) -> None:
    assert normalize_pubdate(value, LOCAL_TZ) == expected


def test_publish_date_default_uses_process_local_timezone(monkeypatch) -> None:
    process_tz = timezone(timedelta(hours=3))
    calls: list[str] = []

    class LocalProbe:
        tzinfo = process_tz

        def astimezone(self):
            calls.append("astimezone")
            return self

    class FakeDateTime:
        @classmethod
        def now(cls):
            calls.append("now")
            return LocalProbe()

        @classmethod
        def fromisoformat(cls, value: str):
            return real_datetime.fromisoformat(value)

    monkeypatch.setattr(mapper_module, "datetime", FakeDateTime)

    assert normalize_pubdate("2024-01-01T00:00:00Z") == "2024-01-01 03:00:00"
    assert calls == ["now", "astimezone"]


def test_dirty_optional_fields_fall_back_without_aborting_release() -> None:
    result = map_release(
        {
            "protocol": "torrent",
            "title": "Dirty fields",
            "indexer": 123,
            "magnetUrl": "magnet:?xt=urn:btih:dirty",
            "infoUrl": 42,
            "commentUrl": ["bad"],
            "size": "not-a-number",
            "seeders": -1,
            "leechers": None,
            "grabs": True,
            "publishDate": "2024-01-01T00:00:00",
            "categories": [None, "Movies", {"name": 4}, {}, {"name": " Valid "}],
        },
        MediaType.UNKNOWN,
        lambda value: None,
        LOCAL_TZ,
    )

    assert result is not None
    assert result.site_name == "Prowlarr"
    assert result.page_url is None
    assert result.size == 0
    assert result.seeders == 0
    assert result.peers == 0
    assert result.grabs == 0
    assert result.pubdate is None
    assert result.labels == ["Valid"]
    assert result.category is None


def test_batch_isolates_one_raising_release_and_logs_only_its_position(
    captured_plugin_logs: list[tuple[str, str]],
) -> None:
    secret_url = "http://prowlarr:9696/1/download?apikey=secret&link=x"
    releases = [
        {"protocol": "torrent", "title": "Raises", "downloadUrl": secret_url},
        {"protocol": "torrent", "title": "Good", "magnetUrl": "magnet:?xt=good"},
    ]

    def relay_builder(value: object) -> str | None:
        if value == secret_url:
            raise RuntimeError(f"must not log {value}")
        return None

    results = map_releases(releases, None, relay_builder)

    assert [item.title for item in results] == ["Good"]
    assert captured_plugin_logs == [("warning", "Prowlarr 结果映射失败（位置 0）")]
    assert "secret" not in repr(captured_plugin_logs)


@pytest.mark.parametrize("releases", [None, {}, "not-a-list", 3])
def test_batch_rejects_non_list_top_level(releases: object) -> None:
    assert map_releases(releases, None, lambda value: None) == []


def test_serialized_torrent_never_contains_key_or_literal_upstream_url() -> None:
    key = "TEST_PROWLARR_KEY_DO_NOT_PACKAGE"
    upstream = f"http://prowlarr:9696/5/download?apikey={key}&link=opaque"
    relay = ProwlarrRelay(
        ProwlarrConfig(True, "http://prowlarr:9696", key, 30, 100),
        port=3001,
        clock=lambda: 1_700_000_000,
    )

    result = map_release(
        {
            "protocol": "torrent",
            "title": "Secret-safe",
            "downloadUrl": upstream,
        },
        MediaType.MOVIE,
        relay.make_enclosure,
        LOCAL_TZ,
    )

    assert result is not None
    serialized = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True)
    assert result.enclosure.startswith("http://127.0.0.1:3001/")
    assert key not in serialized
    assert upstream not in serialized
