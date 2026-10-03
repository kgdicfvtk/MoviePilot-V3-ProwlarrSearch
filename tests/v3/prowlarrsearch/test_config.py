from __future__ import annotations

import pytest

from app.plugins.prowlarrsearch.config import (
    ProwlarrConfig,
    normalize_base_url,
    normalize_config,
)


TEST_KEY = "TEST_PROWLARR_KEY_DO_NOT_PACKAGE"


def test_missing_config_uses_exact_safe_defaults() -> None:
    assert normalize_config(None) == ProwlarrConfig(
        enabled=False,
        base_url="http://prowlarr:9696",
        api_key="",
        timeout=30,
        page_size=100,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        (1, True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        (0, False),
        ("yes", False),
        (2, False),
        (None, False),
    ],
)
def test_enabled_parsing_does_not_truthify_arbitrary_values(value: object, expected: bool) -> None:
    assert normalize_config({"enabled": value}).enabled is expected


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("timeout", "5", 5),
        ("timeout", 4, 5),
        ("timeout", 121, 120),
        ("timeout", "bad", 30),
        ("timeout", True, 30),
        ("timeout", 10.5, 30),
        ("page_size", "1", 1),
        ("page_size", 0, 1),
        ("page_size", 1001, 1000),
        ("page_size", "bad", 100),
        ("page_size", False, 100),
        ("page_size", 10.5, 100),
    ],
)
def test_numeric_fields_convert_or_clamp_without_accepting_booleans(
    field: str, value: object, expected: int
) -> None:
    assert getattr(normalize_config({field: value}), field) == expected


def test_ready_requires_enabled_valid_url_and_nonempty_key() -> None:
    assert normalize_config({"enabled": True, "api_key": f" {TEST_KEY} "}).ready is True
    assert normalize_config({"enabled": False, "api_key": TEST_KEY}).ready is False
    assert normalize_config({"enabled": True, "base_url": "", "api_key": TEST_KEY}).ready is False
    assert normalize_config({"enabled": True, "api_key": "   "}).ready is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" http://prowlarr:9696/ ", "http://prowlarr:9696"),
        ("http://host:9696/api/v1", "http://host:9696"),
        ("http://host:9696/api/v1/search/", "http://host:9696"),
        (
            "http://host:9696/prowlarr/api/v1/search/",
            "http://host:9696/prowlarr",
        ),
        ("https://PROWLARR.example/prowlarr/", "https://prowlarr.example/prowlarr"),
    ],
)
def test_base_url_normalizes_only_unambiguous_inputs(value: object, expected: str) -> None:
    assert normalize_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        9696,
        "",
        "ftp://host:9696",
        "http://user:pass@host:9696",
        "http:///prowlarr",
        "http://host:9696/prowlarr?x=1",
        "http://host:9696/prowlarr#fragment",
        "http://host:9696/prowlarr/%2e%2e/secret",
        "http://host:9696/prowlarr\\secret",
        "http://host:9696/prowlarr//api/v1/search",
    ],
)
def test_base_url_rejects_ambiguous_or_hostile_inputs(value: object) -> None:
    assert normalize_base_url(value) is None


def test_explicit_invalid_base_url_is_not_replaced_by_default() -> None:
    config = normalize_config({"base_url": "https://host/path?secret=value"})

    assert config.base_url is None
