from __future__ import annotations

import base64
import hashlib
import hmac
import json

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


@pytest.mark.parametrize(
    ("base_url", "target", "expected"),
    [
        (
            "http://prowlarr:9696",
            "http://prowlarr:9696/123/download?apikey=secret&link=abc&file=name.torrent",
            "http://prowlarr:9696/123/download?link=abc&file=name.torrent",
        ),
        (
            "http://host:9696/prowlarr",
            "http://HOST:9696/prowlarr/7/download?link=a&APIKEY=one&file=b&ApiKey=two&x=3",
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
        f"?apikey={TEST_KEY}&link=opaque&file=name.torrent"
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
            "u": "http://prowlarr:9696/1/download?apikey=leak&link=x",
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
    rotated = ProwlarrRelay(_config(api_key="rotated-key"), port=3001, clock=lambda: NOW)
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
