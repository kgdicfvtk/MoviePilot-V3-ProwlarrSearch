from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest

from app.plugins.prowlarrsearch.config import ProwlarrConfig
from app.plugins.prowlarrsearch import relay as relay_module
from app.plugins.prowlarrsearch.relay import (
    InvalidRelayToken,
    ProwlarrRelay,
    sanitize_download_url,
)


TEST_KEY = "TEST_PROWLARR_KEY_DO_NOT_PACKAGE"
NOW = 1_700_000_000
DOMAIN = b"MoviePilot-ProwlarrSearch/relay/v1\x00"
STREAM_LIMIT = 20 * 1024 * 1024


def _config(
    *, api_key: str = TEST_KEY, base_url: str = "http://prowlarr:9696"
) -> ProwlarrConfig:
    return ProwlarrConfig(
        enabled=True,
        base_url=base_url,
        api_key=api_key,
        timeout=30,
        page_size=100,
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signed_token(payload: dict[str, object], key: str = TEST_KEY) -> str:
    encoded_payload = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    derived_key = hashlib.sha256(DOMAIN + key.encode("utf-8")).digest()
    signature = hmac.new(derived_key, encoded_payload, hashlib.sha256).digest()
    return f"{_b64encode(encoded_payload)}.{_b64encode(signature)}"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        *,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.chunks = chunks or []
        self.iterated = False
        self.yielded_chunks = 0
        self.chunk_size: int | None = None
        self.closed = False

    @property
    def content(self):
        raise AssertionError("relay must never read response.content")

    def iter_content(self, chunk_size: int):
        self.iterated = True
        self.chunk_size = chunk_size
        for chunk in self.chunks:
            self.yielded_chunks += 1
            yield chunk


class FakeStreamContext:
    def __init__(self, step: FakeResponse | Exception) -> None:
        self.step = step

    def __enter__(self) -> FakeResponse:
        if isinstance(self.step, Exception):
            raise self.step
        return self.step

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if isinstance(self.step, FakeResponse):
            self.step.closed = True
        return False


def _scripted_factory(
    steps: list[FakeResponse | Exception], calls: list[dict[str, Any]]
):
    remaining = list(steps)

    def factory(
        headers: dict[str, Any] | None = None, timeout: int | None = None
    ):
        constructor_call = {
            "headers": dict(headers or {}),
            "timeout": timeout,
            "url": None,
            "params": None,
            "allow_redirects": None,
            "raise_exception": None,
        }
        calls.append(constructor_call)

        class FakeRequest:
            def get_stream(
                self,
                url: str,
                params: dict[str, Any] | None = None,
                allow_redirects: bool = True,
                raise_exception: bool = False,
            ) -> FakeStreamContext:
                constructor_call.update(
                    {
                        "url": url,
                        "params": params,
                        "allow_redirects": allow_redirects,
                        "raise_exception": raise_exception,
                    }
                )
                if not remaining:
                    raise AssertionError("unexpected relay request")
                return FakeStreamContext(remaining.pop(0))

        return FakeRequest()

    return factory


def _relay_and_token(
    factory,
) -> tuple[ProwlarrRelay, str, str]:
    upstream = "http://prowlarr:9696/1/download?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE&link=first"
    relay = ProwlarrRelay(
        _config(), port=3001, clock=lambda: NOW, request_factory=factory
    )
    enclosure = relay.make_enclosure(upstream)
    assert enclosure is not None
    return relay, enclosure.rsplit("/", 1)[1], upstream


@pytest.mark.parametrize(
    ("base_url", "target", "expected"),
    [
        (
            "http://prowlarr:9696",
            "http://prowlarr:9696/123/download?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE&link=abc&file=name.torrent",
            "http://prowlarr:9696/123/download?link=abc&file=name.torrent",
        ),
        (
            "http://host:9696/prowlarr",
            "http://HOST:9696/prowlarr/7/download?link=a&APIKEY=TEST_PROWLARR_KEY_DO_NOT_PACKAGE&file=b&ApiKey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE&x=3",
            "http://host:9696/prowlarr/7/download?link=a&file=b&x=3",
        ),
        (
            "http://host",
            "http://HOST:80/1/download?link=a",
            "http://host:80/1/download?link=a",
        ),
        (
            "https://host:443/prowlarr",
            "https://host/prowlarr/999/download?file=",
            "https://host/prowlarr/999/download?file=",
        ),
    ],
)
def test_target_accepts_only_generated_shape_and_removes_all_api_keys(
    base_url: str, target: str, expected: str
) -> None:
    assert sanitize_download_url(target, base_url) == expected


@pytest.mark.parametrize(
    "target",
    [
        "https://prowlarr:9696/1/download?link=x",
        "ftp://prowlarr:9696/1/download?link=x",
        "http://user:pass@prowlarr:9696/1/download?link=x",
        "http://prowlarr:9696/1/download?link=x#fragment",
        "http://prowlarr:9696/%2e%2e/1/download?link=x",
        "http://prowlarr:9696/1/%64ownload?link=x",
        "http://prowlarr:9696/1\\download?link=x",
        "http://prowlarr:9696//1/download?link=x",
        "http://prowlarr:9696/prowlarr/1/download?link=x",
        "http://prowlarr:9696/api/v1/indexer/1/download?link=x",
        "http://prowlarr:9696/abc/download?link=x",
        "http://prowlarr:9696/+1/download?link=x",
        "http://prowlarr:9696/１２/download?link=x",
        "http://prowlarr:9696/1/download/extra?link=x",
        "http://prowlarr:9696.evil/1/download?link=x",
        "http://evil:9696/1/download?link=x",
    ],
)
def test_target_rejects_foreign_ambiguous_or_non_generated_urls(target: str) -> None:
    assert sanitize_download_url(target, "http://prowlarr:9696") is None


@pytest.mark.parametrize("target", [None, 123, "", "not a url"])
def test_target_rejects_non_url_values(target: object) -> None:
    assert sanitize_download_url(target, "http://prowlarr:9696") is None


def test_target_rejects_url_base_boundary_lookalike() -> None:
    target = "http://host:9696/prowlarrx/1/download?link=x"

    assert sanitize_download_url(target, "http://host:9696/prowlarr") is None


def test_token_payload_is_canonical_signed_and_has_exact_lifetime() -> None:
    upstream = (
        "http://prowlarr:9696/123/download"
        "?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE&link=opaque&file=name.torrent"
    )
    relay = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)

    enclosure = relay.make_enclosure(upstream)

    assert enclosure is not None
    assert enclosure.startswith(
        "http://127.0.0.1:3001/api/v1/plugin/ProwlarrSearch/download/"
    )
    assert TEST_KEY not in enclosure
    assert upstream not in enclosure
    token = enclosure.rsplit("/", 1)[1]
    payload_part, signature_part = token.split(".")
    assert "=" not in payload_part
    assert "=" not in signature_part
    payload_bytes = _b64decode(payload_part)
    assert payload_bytes == (
        b'{"exp":1700086400,"iat":1700000000,'
        b'"u":"http://prowlarr:9696/123/download?link=opaque&file=name.torrent","v":1}'
    )
    assert json.loads(payload_bytes) == {
        "v": 1,
        "u": "http://prowlarr:9696/123/download?link=opaque&file=name.torrent",
        "iat": NOW,
        "exp": NOW + 86_400,
    }
    assert token == _signed_token(json.loads(payload_bytes))
    assert relay.decode_target(token) == json.loads(payload_bytes)["u"]


def test_token_verification_uses_constant_time_signature_comparison(monkeypatch) -> None:
    relay = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)
    token = relay.make_enclosure("http://prowlarr:9696/1/download?link=x").rsplit("/", 1)[1]
    calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def recording_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(relay_module.hmac, "compare_digest", recording_compare)

    assert relay.decode_target(token) == "http://prowlarr:9696/1/download?link=x"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "token",
    [
        "",
        "single-part",
        "too.many.parts",
        "!!!!.!!!!",
        f"{_b64encode(b'not json')}.{_b64encode(b'x' * 32)}",
        f"{_b64encode(b'{}')}.{_b64encode(b'x' * 31)}",
    ],
)
def test_token_rejects_malformed_values(token: str) -> None:
    relay = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)

    with pytest.raises(InvalidRelayToken):
        relay.decode_target(token)


def test_token_rejects_altered_payload_and_signature() -> None:
    relay = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)
    token = relay.make_enclosure("http://prowlarr:9696/1/download?link=x").rsplit("/", 1)[1]
    payload_part, signature_part = token.split(".")
    altered_payload = ("A" if payload_part[0] != "A" else "B") + payload_part[1:]
    altered_signature = ("A" if signature_part[0] != "A" else "B") + signature_part[1:]

    with pytest.raises(InvalidRelayToken):
        relay.decode_target(f"{altered_payload}.{signature_part}")
    with pytest.raises(InvalidRelayToken):
        relay.decode_target(f"{payload_part}.{altered_signature}")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "v": 2,
            "u": "http://prowlarr:9696/1/download?link=x",
            "iat": NOW,
            "exp": NOW + 86_400,
        },
        {
            "v": 1,
            "u": "http://prowlarr:9696/1/download?link=x",
            "iat": NOW + 1,
            "exp": NOW + 86_400,
        },
        {
            "v": 1,
            "u": "http://prowlarr:9696/1/download?link=x",
            "iat": NOW - 100,
            "exp": NOW,
        },
        {
            "v": 1,
            "u": "http://prowlarr:9696/1/download?link=x",
            "iat": NOW,
            "exp": NOW + 86_401,
        },
        {
            "v": 1,
            "u": "http://evil:9696/1/download?link=x",
            "iat": NOW,
            "exp": NOW + 60,
        },
        {
            "v": 1,
            "u": "http://prowlarr:9696/api/v1/indexer/1/download?link=x",
            "iat": NOW,
            "exp": NOW + 60,
        },
        {
            "v": 1,
            "u": "http://prowlarr:9696/1/download?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE&link=x",
            "iat": NOW,
            "exp": NOW + 60,
        },
    ],
)
def test_token_rejects_invalid_claims_or_current_target(payload: dict[str, object]) -> None:
    relay = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)

    with pytest.raises(InvalidRelayToken):
        relay.decode_target(_signed_token(payload))


def test_token_rejects_api_key_rotation_and_url_base_change() -> None:
    original = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)
    token = original.make_enclosure(
        "http://prowlarr:9696/1/download?link=x"
    ).rsplit("/", 1)[1]
    rotated = ProwlarrRelay(_config(api_key=TEST_KEY[::-1]), port=3001, clock=lambda: NOW)
    moved = ProwlarrRelay(
        _config(base_url="http://prowlarr:9696/prowlarr"),
        port=3001,
        clock=lambda: NOW,
    )

    with pytest.raises(InvalidRelayToken):
        rotated.decode_target(token)
    with pytest.raises(InvalidRelayToken):
        moved.decode_target(token)


def test_token_rejects_noncanonical_json_and_base64_padding() -> None:
    relay = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)
    payload = {
        "v": 1,
        "u": "http://prowlarr:9696/1/download?link=x",
        "iat": NOW,
        "exp": NOW + 60,
    }
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    derived_key = hashlib.sha256(DOMAIN + TEST_KEY.encode("utf-8")).digest()
    pretty_signature = hmac.new(derived_key, pretty, hashlib.sha256).digest()

    with pytest.raises(InvalidRelayToken):
        relay.decode_target(f"{_b64encode(pretty)}.{_b64encode(pretty_signature)}")
    with pytest.raises(InvalidRelayToken):
        relay.decode_target(_signed_token(payload) + "=")


def test_enclosure_rejects_invalid_or_unready_targets() -> None:
    relay = ProwlarrRelay(_config(), port=3001, clock=lambda: NOW)
    disabled = ProwlarrRelay(
        ProwlarrConfig(False, "http://prowlarr:9696", TEST_KEY, 30, 100),
        port=3001,
        clock=lambda: NOW,
    )

    assert relay.make_enclosure("https://evil/1/download?link=x") is None
    assert disabled.make_enclosure("http://prowlarr:9696/1/download?link=x") is None


@pytest.mark.parametrize("ready", [True, False])
def test_relay_invalid_or_misconfigured_token_performs_no_io_and_is_generic(
    ready: bool, captured_plugin_logs: list[tuple[str, str]]
) -> None:
    def forbidden_factory(
        headers: dict[str, Any] | None = None, timeout: int | None = None
    ):
        pytest.fail("invalid relay token must be rejected before I/O")

    config = _config() if ready else ProwlarrConfig(
        False, "http://prowlarr:9696", TEST_KEY, 30, 100
    )
    relay = ProwlarrRelay(
        config, port=3001, clock=lambda: NOW, request_factory=forbidden_factory
    )

    response = relay.fetch("sensitive-invalid-token")

    assert response.status_code == 403
    assert b"sensitive-invalid-token" not in response.body
    assert TEST_KEY.encode() not in response.body
    assert captured_plugin_logs == [("warning", "Prowlarr 中继失败（令牌）")]


def test_relay_redirect_resolves_relative_location_and_keeps_same_origin_key() -> None:
    calls: list[dict[str, Any]] = []
    first = FakeResponse(302, headers={"Location": "../2/download?link=second"})
    final = FakeResponse(
        200,
        headers={"Content-Type": "application/x-bittorrent"},
        chunks=[b"torrent-data"],
    )
    relay, token, _ = _relay_and_token(_scripted_factory([first, final], calls))

    response = relay.fetch(token)

    assert response.status_code == 200
    assert response.body == b"torrent-data"
    assert [call["url"] for call in calls] == [
        "http://prowlarr:9696/1/download?link=first",
        "http://prowlarr:9696/2/download?link=second",
    ]
    assert all(call["headers"]["X-Api-Key"] == TEST_KEY for call in calls)
    assert all(call["allow_redirects"] is False for call in calls)
    assert all(call["raise_exception"] is True for call in calls)
    assert first.closed is True
    assert final.closed is True


def test_relay_redirect_preserves_query_but_strips_header_permanently() -> None:
    calls: list[dict[str, Any]] = []
    downstream_key = TEST_KEY[::-1]
    first = FakeResponse(
        302,
        headers={
            "Location": f"https://cdn.example/file.torrent?apikey={downstream_key}&part=1"
        },
    )
    second = FakeResponse(
        302,
        headers={
            "Location": f"http://prowlarr:9696/9/download?APIKEY={downstream_key}&link=back"
        },
    )
    final = FakeResponse(200, chunks=[b"torrent"])
    relay, token, _ = _relay_and_token(
        _scripted_factory([first, second, final], calls)
    )

    response = relay.fetch(token)

    assert response.status_code == 200
    assert calls[0]["headers"]["X-Api-Key"] == TEST_KEY
    assert "X-Api-Key" not in calls[1]["headers"]
    assert "X-Api-Key" not in calls[2]["headers"]
    assert calls[1]["url"] == (
        f"https://cdn.example/file.torrent?apikey={downstream_key}&part=1"
    )
    assert calls[2]["url"] == (
        f"http://prowlarr:9696/9/download?APIKEY={downstream_key}&link=back"
    )


def test_relay_redirect_loop_is_rejected() -> None:
    calls: list[dict[str, Any]] = []
    looping = FakeResponse(
        302, headers={"Location": "/1/download?link=first"}
    )
    relay, token, _ = _relay_and_token(_scripted_factory([looping], calls))

    response = relay.fetch(token)

    assert response.status_code == 502
    assert len(calls) == 1
    assert looping.closed is True


def test_relay_redirect_sixth_hop_is_rejected() -> None:
    calls: list[dict[str, Any]] = []
    redirects = [
        FakeResponse(302, headers={"Location": f"/{number}/download?link=x"})
        for number in range(2, 8)
    ]
    relay, token, _ = _relay_and_token(_scripted_factory(redirects, calls))

    response = relay.fetch(token)

    assert response.status_code == 502
    assert len(calls) == 6
    assert all(item.closed for item in redirects)


@pytest.mark.parametrize(
    "location",
    [None, "", "file:///tmp/file.torrent", "javascript:alert(1)", "http://user@evil/file"],
)
def test_relay_redirect_rejects_missing_or_unsafe_location(location: str | None) -> None:
    calls: list[dict[str, Any]] = []
    headers = {} if location is None else {"Location": location}
    redirect = FakeResponse(302, headers=headers)
    relay, token, _ = _relay_and_token(_scripted_factory([redirect], calls))

    response = relay.fetch(token)

    assert response.status_code == 502
    assert len(calls) == 1
    assert redirect.closed is True


def test_relay_stream_rejects_declared_oversize_before_iteration() -> None:
    calls: list[dict[str, Any]] = []
    upstream = FakeResponse(
        200,
        headers={"Content-Length": str(STREAM_LIMIT + 1)},
        chunks=[b"must-not-read"],
    )
    relay, token, _ = _relay_and_token(_scripted_factory([upstream], calls))

    response = relay.fetch(token)

    assert response.status_code == 413
    assert upstream.iterated is False
    assert upstream.closed is True


@pytest.mark.parametrize(
    "content_type",
    [None, "application/octet-stream", "application/x-bittorrent; charset=binary"],
)
def test_relay_stream_accepts_supported_or_absent_content_type(
    content_type: str | None,
) -> None:
    calls: list[dict[str, Any]] = []
    headers = {} if content_type is None else {"Content-Type": content_type}
    upstream = FakeResponse(200, headers=headers, chunks=[b"abc", b"", b"def"])
    relay, token, _ = _relay_and_token(_scripted_factory([upstream], calls))

    response = relay.fetch(token)

    assert response.status_code == 200
    assert response.body == b"abcdef"
    assert response.headers["content-type"] == "application/x-bittorrent"
    assert response.headers["cache-control"] == "no-store"
    assert upstream.iterated is True
    assert upstream.chunk_size == 64 * 1024
    assert upstream.closed is True


def test_relay_stream_allows_exact_limit() -> None:
    calls: list[dict[str, Any]] = []
    one_mebibyte = b"x" * (1024 * 1024)
    upstream = FakeResponse(
        200,
        headers={"Content-Length": str(STREAM_LIMIT)},
        chunks=[one_mebibyte] * 20,
    )
    relay, token, _ = _relay_and_token(_scripted_factory([upstream], calls))

    response = relay.fetch(token)

    assert response.status_code == 200
    assert len(response.body) == STREAM_LIMIT
    assert upstream.yielded_chunks == 20
    assert upstream.closed is True


def test_relay_stream_stops_on_first_byte_over_dishonest_length() -> None:
    calls: list[dict[str, Any]] = []
    one_mebibyte = b"x" * (1024 * 1024)
    upstream = FakeResponse(
        200,
        headers={"Content-Length": "10"},
        chunks=[one_mebibyte] * 20 + [b"y", b"must-not-read"],
    )
    relay, token, _ = _relay_and_token(_scripted_factory([upstream], calls))

    response = relay.fetch(token)

    assert response.status_code == 413
    assert upstream.yielded_chunks == 21
    assert upstream.closed is True


@pytest.mark.parametrize("content_type", ["text/html", "application/json"])
def test_relay_stream_rejects_obvious_non_torrent_without_reading(
    content_type: str,
) -> None:
    calls: list[dict[str, Any]] = []
    upstream = FakeResponse(
        200,
        headers={"Content-Type": content_type},
        chunks=[b"TEST_PROWLARR_KEY_DO_NOT_PACKAGE secret body"],
    )
    relay, token, _ = _relay_and_token(_scripted_factory([upstream], calls))

    response = relay.fetch(token)

    assert response.status_code == 502
    assert upstream.iterated is False
    assert upstream.closed is True


def test_relay_magnet_redirect_returns_no_store_302() -> None:
    calls: list[dict[str, Any]] = []
    magnet = "magnet:?xt=urn:btih:abcdef&dn=Example"
    upstream = FakeResponse(302, headers={"Location": magnet})
    relay, token, _ = _relay_and_token(_scripted_factory([upstream], calls))

    response = relay.fetch(token)

    assert response.status_code == 302
    assert response.headers["location"] == magnet
    assert response.headers["cache-control"] == "no-store"
    assert upstream.closed is True


def test_relay_network_and_http_errors_redact_all_sensitive_values(
    captured_plugin_logs: list[tuple[str, str]],
) -> None:
    network_calls: list[dict[str, Any]] = []
    secret_url = "https://downstream.example/file?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE"
    network_error = RuntimeError(
        "token=" + TEST_KEY + f" key={TEST_KEY} upstream=http://prowlarr:9696 {secret_url}"
    )
    network_relay, network_token, _ = _relay_and_token(
        _scripted_factory([network_error], network_calls)
    )

    network_response = network_relay.fetch(network_token)
    network_log = repr(captured_plugin_logs)

    assert network_response.status_code == 502
    assert captured_plugin_logs == [("warning", "Prowlarr 中继失败（网络）")]
    assert network_token not in network_log
    assert TEST_KEY not in network_log
    assert "prowlarr:9696" not in network_log
    assert "downstream.example" not in network_log

    captured_plugin_logs.clear()
    http_calls: list[dict[str, Any]] = []
    http_error = FakeResponse(
        500,
        headers={"Location": secret_url},
        chunks=[f"secret body {TEST_KEY}".encode()],
    )
    http_relay, http_token, _ = _relay_and_token(
        _scripted_factory([http_error], http_calls)
    )

    http_response = http_relay.fetch(http_token)
    http_log = repr(captured_plugin_logs)

    assert http_response.status_code == 502
    assert http_error.iterated is False
    assert captured_plugin_logs == [("warning", "Prowlarr 中继失败（HTTP 500）")]
    assert http_token not in http_log
    assert TEST_KEY not in http_log
    assert secret_url not in http_log
