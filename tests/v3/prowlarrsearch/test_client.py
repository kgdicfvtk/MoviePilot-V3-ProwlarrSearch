from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any

import pytest

from app.plugins.prowlarrsearch import client as client_module
from app.plugins.prowlarrsearch.client import ProwlarrClient, build_search_params
from app.plugins.prowlarrsearch.config import ProwlarrConfig


class MediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"
    MUSIC = "音乐"


def _config(
    *,
    enabled: bool = True,
    base_url: str | None = "http://prowlarr:9696/prowlarr",
    api_key: str = "TEST_PROWLARR_KEY_DO_NOT_PACKAGE",
    timeout: int = 17,
    page_size: int = 50,
) -> ProwlarrConfig:
    return ProwlarrConfig(enabled, base_url, api_key, timeout, page_size)


@pytest.mark.parametrize(
    ("keyword", "mtype", "page", "expected"),
    [
        (
            "  The Matrix  ",
            MediaType.MOVIE,
            2,
            {"query": "The Matrix", "type": "movie", "limit": 50, "offset": 100},
        ),
        (
            "Show",
            MediaType.TV,
            "3",
            {"query": "Show", "type": "tvsearch", "limit": 50, "offset": 150},
        ),
        (
            "Song",
            MediaType.MUSIC,
            -4,
            {"query": "Song", "type": "music", "limit": 50, "offset": 0},
        ),
        (
            "Anything",
            None,
            "bad",
            {"query": "Anything", "type": "search", "limit": 50, "offset": 0},
        ),
        (
            "Anything",
            None,
            True,
            {"query": "Anything", "type": "search", "limit": 50, "offset": 0},
        ),
        ("", None, 0, None),
        ("   ", None, 0, None),
        (None, None, 0, None),
        (123, None, 0, None),
    ],
)
def test_search_params_trim_and_bound_inputs(
    keyword: object,
    mtype: object,
    page: object,
    expected: dict[str, Any] | None,
) -> None:
    assert build_search_params(keyword, mtype, page, 50) == expected


@pytest.mark.parametrize(
    ("config", "keyword"),
    [
        (_config(enabled=False), "query"),
        (_config(base_url=None), "query"),
        (_config(api_key=""), "query"),
        (_config(), ""),
        (_config(), "   "),
        (_config(), None),
    ],
)
def test_not_ready_or_blank_search_never_constructs_transport(
    monkeypatch, config: ProwlarrConfig, keyword: object
) -> None:
    class ForbiddenSync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pytest.fail("sync transport must not be constructed")

    class ForbiddenAsync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pytest.fail("async transport must not be constructed")

    monkeypatch.setattr(client_module, "RequestUtils", ForbiddenSync)
    monkeypatch.setattr(client_module, "AsyncRequestUtils", ForbiddenAsync)
    client = ProwlarrClient(config)

    assert client.search(keyword) == []
    assert asyncio.run(client.async_search(keyword)) == []


def test_sync_and_async_transports_receive_identical_contract(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    releases = [{"protocol": "torrent", "title": "Result"}]

    class Response:
        status_code = 200

        def json(self) -> object:
            return releases

    class FakeSync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            events.append(("sync-init", (headers, timeout)))

        def get_res(self, url: str, params: dict[str, Any] | None = None) -> Response:
            events.append(("sync-get", (url, params)))
            return Response()

    class FakeAsync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            events.append(("async-init", (headers, timeout)))

        async def get_res(self, url: str, params: dict[str, Any] | None = None) -> Response:
            events.append(("async-get", (url, params)))
            return Response()

    monkeypatch.setattr(client_module, "RequestUtils", FakeSync)
    monkeypatch.setattr(client_module, "AsyncRequestUtils", FakeAsync)
    client = ProwlarrClient(_config())

    sync_result = client.search(" Show ", MediaType.TV, "2")
    async_result = asyncio.run(client.async_search(" Show ", MediaType.TV, "2"))

    expected_headers = {
        "X-Api-Key": "TEST_PROWLARR_KEY_DO_NOT_PACKAGE",
        "Accept": "application/json",
    }
    expected_params = {
        "query": "Show",
        "type": "tvsearch",
        "limit": 50,
        "offset": 100,
    }
    expected_url = "http://prowlarr:9696/prowlarr/api/v1/search"
    assert sync_result == releases
    assert async_result == releases
    assert events == [
        ("sync-init", (expected_headers, 17)),
        ("sync-get", (expected_url, expected_params)),
        ("async-init", (expected_headers, 17)),
        ("async-get", (expected_url, expected_params)),
    ]


def test_sync_network_exception_logs_only_safe_class(
    monkeypatch, captured_plugin_logs: list[tuple[str, str]]
) -> None:
    secret_url = "http://prowlarr:9696/1/download?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE"

    class SecretTimeout(TimeoutError):
        pass

    class RaisingSync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pass

        def get_res(self, url: str, params: dict[str, Any] | None = None):
            raise SecretTimeout(f"key=TEST_PROWLARR_KEY_DO_NOT_PACKAGE url={secret_url}")

    monkeypatch.setattr(client_module, "RequestUtils", RaisingSync)

    assert ProwlarrClient(_config()).search("query") == []
    log_text = repr(captured_plugin_logs)
    assert "SecretTimeout" in log_text
    assert "TEST_PROWLARR_KEY_DO_NOT_PACKAGE" not in log_text
    assert secret_url not in log_text


def test_async_network_exception_logs_only_safe_class(
    monkeypatch, captured_plugin_logs: list[tuple[str, str]]
) -> None:
    secret_url = "https://example/redirect?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE"

    class SecretTlsError(ConnectionError):
        pass

    class RaisingAsync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pass

        async def get_res(self, url: str, params: dict[str, Any] | None = None):
            raise SecretTlsError(f"TEST_PROWLARR_KEY_DO_NOT_PACKAGE {secret_url}")

    monkeypatch.setattr(client_module, "AsyncRequestUtils", RaisingAsync)

    assert asyncio.run(ProwlarrClient(_config()).async_search("query")) == []
    log_text = repr(captured_plugin_logs)
    assert "SecretTlsError" in log_text
    assert "TEST_PROWLARR_KEY_DO_NOT_PACKAGE" not in log_text
    assert secret_url not in log_text


@pytest.mark.parametrize("status_code", [401, 500])
def test_http_errors_log_only_numeric_status(
    monkeypatch,
    captured_plugin_logs: list[tuple[str, str]],
    status_code: int,
) -> None:
    class ErrorResponse:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self):
            pytest.fail("error response body must not be decoded")

    class ErrorSync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pass

        def get_res(self, url: str, params: dict[str, Any] | None = None) -> ErrorResponse:
            return ErrorResponse()

    monkeypatch.setattr(client_module, "RequestUtils", ErrorSync)

    assert ProwlarrClient(_config()).search("query") == []
    assert captured_plugin_logs == [
        ("warning", f"Prowlarr 同步搜索失败（HTTP {status_code}）")
    ]


def test_invalid_json_does_not_log_exception_message(
    monkeypatch, captured_plugin_logs: list[tuple[str, str]]
) -> None:
    class InvalidJsonResponse:
        status_code = 200

        def json(self):
            raise ValueError("TEST_PROWLARR_KEY_DO_NOT_PACKAGE response body")

    class InvalidJsonSync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pass

        def get_res(self, url: str, params: dict[str, Any] | None = None) -> InvalidJsonResponse:
            return InvalidJsonResponse()

    monkeypatch.setattr(client_module, "RequestUtils", InvalidJsonSync)

    assert ProwlarrClient(_config()).search("query") == []
    assert captured_plugin_logs == [("warning", "Prowlarr 同步搜索失败（ValueError）")]


@pytest.mark.parametrize("payload", [{"results": []}, "text", 3, None])
def test_non_list_json_is_rejected_without_logging_payload(
    monkeypatch,
    captured_plugin_logs: list[tuple[str, str]],
    payload: object,
) -> None:
    class ObjectResponse:
        status_code = 200

        def json(self) -> object:
            return payload

    class ObjectSync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pass

        def get_res(self, url: str, params: dict[str, Any] | None = None) -> ObjectResponse:
            return ObjectResponse()

    monkeypatch.setattr(client_module, "RequestUtils", ObjectSync)

    assert ProwlarrClient(_config()).search("query") == []
    assert captured_plugin_logs == [("warning", "Prowlarr 同步搜索失败（响应格式）")]


def test_none_response_is_safe_for_both_transports(
    monkeypatch, captured_plugin_logs: list[tuple[str, str]]
) -> None:
    class NoneSync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pass

        def get_res(self, url: str, params: dict[str, Any] | None = None):
            return None

    class NoneAsync:
        def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None):
            pass

        async def get_res(self, url: str, params: dict[str, Any] | None = None):
            return None

    monkeypatch.setattr(client_module, "RequestUtils", NoneSync)
    monkeypatch.setattr(client_module, "AsyncRequestUtils", NoneAsync)
    client = ProwlarrClient(_config())

    assert client.search("query") == []
    assert asyncio.run(client.async_search("query")) == []
    assert captured_plugin_logs == [
        ("warning", "Prowlarr 同步搜索失败（无响应）"),
        ("warning", "Prowlarr 异步搜索失败（无响应）"),
    ]
