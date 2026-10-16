from __future__ import annotations

import asyncio
import inspect
import json
from enum import Enum
from typing import Any

import pytest

from app.plugins import prowlarrsearch as plugin_module
from app.plugins.prowlarrsearch import ProwlarrSearch
from app.plugins.prowlarrsearch.relay import InvalidRelayToken
from app.sdk.media import TorrentInfo


TEST_KEY = "TEST_PROWLARR_KEY_DO_NOT_PACKAGE"


class MediaType(Enum):
    MOVIE = "电影"


def _ready_config(api_key: str = TEST_KEY) -> dict[str, object]:
    return {
        "enabled": True,
        "base_url": "http://prowlarr:9696",
        "api_key": api_key,
        "timeout": 15,
        "page_size": 25,
        "candidate_limit": 300,
    }


def _walk_components(value: object):
    if isinstance(value, dict):
        if "component" in value:
            yield value
        for child in value.values():
            yield from _walk_components(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_components(child)


def test_production_namespace_metadata_and_disabled_default() -> None:
    plugin = ProwlarrSearch()

    assert plugin_module.__name__ == "app.plugins.prowlarrsearch"
    assert plugin.plugin_name == "Prowlarr 搜索"
    assert plugin.plugin_version == "1.1.0"
    assert plugin.plugin_author == "Codex"
    assert plugin.plugin_config_prefix == "prowlarrsearch_"
    assert plugin.auth_level == 1
    assert plugin.get_state() is False


def test_init_replaces_snapshot_and_api_key_rotation_invalidates_old_token() -> None:
    plugin = ProwlarrSearch()
    plugin.init_plugin(_ready_config())
    first_config = plugin._config
    first_client = plugin._client
    first_relay = plugin._relay
    assert first_relay is not None
    old_enclosure = first_relay.make_enclosure(
        "http://prowlarr:9696/1/download?apikey=TEST_PROWLARR_KEY_DO_NOT_PACKAGE&link=x"
    )
    assert old_enclosure is not None
    old_token = old_enclosure.rsplit("/", 1)[1]

    plugin.init_plugin(_ready_config(TEST_KEY[::-1]))

    assert plugin.get_state() is True
    assert plugin._config is not first_config
    assert plugin._client is not first_client
    assert plugin._relay is not first_relay
    assert plugin._config.api_key == TEST_KEY[::-1]
    with pytest.raises(InvalidRelayToken):
        plugin._relay.decode_target(old_token)


def test_services_commands_and_stop_are_side_effect_free() -> None:
    plugin = ProwlarrSearch()
    plugin.init_plugin(_ready_config())

    assert plugin.get_service() == []
    assert plugin.get_command() == []

    plugin.stop_service()

    assert plugin.get_state() is False
    assert plugin._config.ready is False
    assert plugin._client is None
    assert plugin._relay is None


def test_relay_api_descriptor_is_the_only_anonymous_sync_route() -> None:
    plugin = ProwlarrSearch()

    routes = plugin.get_api()

    assert len(routes) == 1
    assert routes[0]["path"] == "/download/{token}"
    assert routes[0]["methods"] == ["GET"]
    assert routes[0]["endpoint"] == plugin.download_relay
    assert routes[0]["allow_anonymous"] is True
    assert inspect.iscoroutinefunction(routes[0]["endpoint"]) is False


def test_download_relay_returns_generic_unavailable_response_before_init() -> None:
    plugin = ProwlarrSearch()

    response = plugin.download_relay("sensitive-token")

    assert response.status_code == 503
    assert b"sensitive-token" not in response.body


def test_module_registry_contains_only_sync_and_async_search_hooks() -> None:
    plugin = ProwlarrSearch()

    modules = plugin.get_module()

    assert set(modules) == {"search_torrents", "async_search_torrents"}
    assert modules["search_torrents"] == plugin.search_torrents
    assert modules["async_search_torrents"] == plugin.async_search_torrents


def test_search_hooks_ignore_site_and_share_one_mapping_contract(monkeypatch) -> None:
    plugin = ProwlarrSearch()
    plugin.init_plugin(_ready_config())
    events: list[tuple[str, object]] = []
    raw = [{"protocol": "torrent", "title": "Raw"}]

    class FakeClient:
        def search(self, keyword: object, mtype: Any = None, page: object = 0):
            events.append(("sync-search", (keyword, mtype, page)))
            return raw

        async def async_search(self, keyword: object, mtype: Any = None, page: object = 0):
            events.append(("async-search", (keyword, mtype, page)))
            return raw

    class FakeRelay:
        def make_enclosure(self, value: object) -> str | None:
            return None

    fake_client = FakeClient()
    fake_relay = FakeRelay()
    plugin._client = fake_client
    plugin._relay = fake_relay
    mapped = [TorrentInfo(title="Mapped", enclosure="magnet:?xt=mapped")]

    def fake_map(releases, mtype, relay_builder):
        events.append(("map", (releases, mtype, relay_builder)))
        return mapped

    monkeypatch.setattr(plugin_module, "map_releases", fake_map)
    site = {"id": 999, "name": "must be ignored"}

    sync_result = plugin.search_torrents(site, " query ", MediaType.MOVIE, 0)
    async_result = asyncio.run(
        plugin.async_search_torrents(site, " query ", MediaType.MOVIE, 0)
    )

    assert sync_result == mapped
    assert async_result == mapped
    assert events[0] == ("sync-search", (" query ", MediaType.MOVIE, 0))
    assert events[1][0] == "map"
    assert events[1][1][:2] == (raw, MediaType.MOVIE)
    assert events[1][1][2] == fake_relay.make_enclosure
    assert events[2] == ("async-search", (" query ", MediaType.MOVIE, 0))
    assert events[3][0] == "map"
    assert all("must be ignored" not in repr(event) for event in events)


def test_sync_and_async_hooks_rank_full_candidate_pool_before_return_limit() -> None:
    plugin = ProwlarrSearch()
    plugin.init_plugin(_ready_config())
    raw = [
        {
            "protocol": "torrent",
            "title": f"Unrelated.Release.{index}",
            "magnetUrl": f"magnet:?xt=urn:btih:{index:040x}",
            "seeders": index % 7,
        }
        for index in range(300)
    ]
    raw[249] = {
        "protocol": "torrent",
        "title": "Parasite.2019.1080p.BluRay",
        "magnetUrl": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "seeders": 1,
    }

    class FakeClient:
        def search(self, keyword: object, mtype: Any = None, page: object = 0):
            return raw

        async def async_search(self, keyword: object, mtype: Any = None, page: object = 0):
            return raw

    plugin._client = FakeClient()

    sync_result = plugin.search_torrents({}, "Parasite 2019", MediaType.MOVIE)
    async_result = asyncio.run(
        plugin.async_search_torrents({}, "Parasite 2019", MediaType.MOVIE)
    )

    assert len(sync_result) == len(async_result) == 25
    assert sync_result[0].title == async_result[0].title == "Parasite.2019.1080p.BluRay"


def test_requested_page_slices_the_ranked_pool_without_skipping_candidates() -> None:
    plugin = ProwlarrSearch()
    plugin.init_plugin(_ready_config())
    raw = [
        {
            "protocol": "torrent",
            "title": "Same Target Title",
            "magnetUrl": f"magnet:?xt=urn:btih:{index:040x}",
            "seeders": index,
        }
        for index in range(60)
    ]

    class FakeClient:
        def search(self, keyword: object, mtype: Any = None, page: object = 0):
            return raw

        async def async_search(self, keyword: object, mtype: Any = None, page: object = 0):
            return raw

    plugin._client = FakeClient()

    second_page = plugin.search_torrents({}, "Same Target Title", page=1)
    async_second_page = asyncio.run(
        plugin.async_search_torrents({}, "Same Target Title", page=1)
    )

    assert len(second_page) == len(async_second_page) == 25
    assert [item.seeders for item in second_page] == list(range(34, 9, -1))
    assert [item.seeders for item in async_second_page] == list(range(34, 9, -1))


def test_disabled_hooks_return_empty_without_client_activity() -> None:
    plugin = ProwlarrSearch()
    plugin.init_plugin({"enabled": False, "api_key": TEST_KEY})

    class ForbiddenClient:
        def search(self, keyword: object, mtype: Any = None, page: object = 0):
            pytest.fail("disabled sync hook called client")

        async def async_search(self, keyword: object, mtype: Any = None, page: object = 0):
            pytest.fail("disabled async hook called client")

    plugin._client = ForbiddenClient()

    assert plugin.search_torrents({}, "query") == []
    assert asyncio.run(plugin.async_search_torrents({}, "query")) == []


def test_form_has_exact_models_safe_defaults_and_password_field() -> None:
    plugin = ProwlarrSearch()

    form, defaults = plugin.get_form()
    components = list(_walk_components(form))
    fields = {
        item.get("props", {}).get("model"): item.get("props", {})
        for item in components
        if item.get("props", {}).get("model")
    }

    assert defaults == {
        "enabled": False,
        "base_url": "http://prowlarr:9696",
        "api_key": "",
        "timeout": 30,
        "page_size": 100,
        "candidate_limit": 500,
    }
    assert set(fields) == {
        "enabled",
        "base_url",
        "api_key",
        "timeout",
        "page_size",
        "candidate_limit",
    }
    assert fields["api_key"]["type"] == "password"
    assert fields["timeout"]["type"] == "number"
    assert fields["page_size"]["type"] == "number"
    assert fields["candidate_limit"]["type"] == "number"


def test_status_page_shows_state_guidance_without_url_or_key() -> None:
    plugin = ProwlarrSearch()
    unique_url = "http://unique-prowlarr-host:9696/private-base"
    plugin.init_plugin(
        {
            "enabled": True,
            "base_url": unique_url,
            "api_key": TEST_KEY,
        }
    )

    enabled_page = json.dumps(plugin.get_page(), ensure_ascii=False)
    assert "已启用" in enabled_page
    assert unique_url not in enabled_page
    assert TEST_KEY not in enabled_page

    plugin.init_plugin({"enabled": False})
    disabled_page = json.dumps(plugin.get_page(), ensure_ascii=False)
    assert "未启用" in disabled_page
    assert "Prowlarr" in disabled_page
