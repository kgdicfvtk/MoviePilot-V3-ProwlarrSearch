from __future__ import annotations

from typing import Any

from app.sdk.config import settings
from app.sdk.media import TorrentInfo
from app.sdk.plugin import _PluginBase

from .client import ProwlarrClient
from .config import DEFAULT_BASE_URL, DEFAULT_PAGE_SIZE, DEFAULT_TIMEOUT, ProwlarrConfig, normalize_config
from .mapper import map_releases
from .relay import ProwlarrRelay


class ProwlarrSearch(_PluginBase):
    plugin_name = "Prowlarr 搜索"
    plugin_desc = "在 MoviePilot 搜索页中使用 Prowlarr 的 torrent 索引结果。"
    plugin_version = "1.0.0"
    plugin_label = "搜索"
    plugin_author = "Codex"
    plugin_config_prefix = "prowlarrsearch_"
    plugin_order = 30
    auth_level = 1

    def __init__(self) -> None:
        super().__init__()
        self._config: ProwlarrConfig = normalize_config(None)
        self._client: ProwlarrClient | None = None
        self._relay: ProwlarrRelay | None = None

    def init_plugin(self, config: dict[str, Any] | None = None) -> None:
        normalized = normalize_config(config)
        client = ProwlarrClient(normalized)
        relay = ProwlarrRelay(normalized, port=int(settings.PORT))
        self._config = normalized
        self._client = client
        self._relay = relay

    def get_state(self) -> bool:
        return self._config.ready

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        return []

    def get_service(self) -> list[dict[str, Any]]:
        return []

    def get_api(self) -> list[dict[str, Any]]:
        return []

    def get_module(self) -> dict[str, Any]:
        return {
            "search_torrents": self.search_torrents,
            "async_search_torrents": self.async_search_torrents,
        }

    def search_torrents(
        self,
        site: dict[str, Any],
        keyword: str,
        mtype: Any = None,
        page: object = 0,
    ) -> list[TorrentInfo]:
        if not self._config.ready or self._client is None or self._relay is None:
            return []
        releases = self._client.search(keyword, mtype, page)
        return map_releases(releases, mtype, self._relay.make_enclosure)

    async def async_search_torrents(
        self,
        site: dict[str, Any],
        keyword: str,
        mtype: Any = None,
        page: object = 0,
    ) -> list[TorrentInfo]:
        if not self._config.ready or self._client is None or self._relay is None:
            return []
        releases = await self._client.async_search(keyword, mtype, page)
        return map_releases(releases, mtype, self._relay.make_enclosure)

    def get_form(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用 Prowlarr 搜索",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "base_url",
                                            "label": "Prowlarr 地址",
                                            "placeholder": DEFAULT_BASE_URL,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "api_key",
                                            "label": "Prowlarr API Key",
                                            "type": "password",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "timeout",
                                            "label": "请求超时（秒）",
                                            "type": "number",
                                            "min": 5,
                                            "max": 120,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "page_size",
                                            "label": "每页结果数",
                                            "type": "number",
                                            "min": 1,
                                            "max": 1000,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": (
                                "插件只负责把 Prowlarr torrent 结果加入 MoviePilot 搜索；"
                                "不会修改下载器、整理目录、硬链接或 Jellyfin 配置。"
                            ),
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "base_url": DEFAULT_BASE_URL,
            "api_key": "",
            "timeout": DEFAULT_TIMEOUT,
            "page_size": DEFAULT_PAGE_SIZE,
        }

    def get_page(self) -> list[dict[str, Any]]:
        active = self.get_state()
        status = "已启用" if active else "未启用"
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "success" if active else "info",
                    "variant": "tonal",
                    "text": f"Prowlarr 搜索{status}。",
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "text",
                    "text": (
                        "搜索结果直接来自 Prowlarr；插件不依赖 MoviePilot 官方用户认证或 IYUU，"
                        "也不会改变现有下载与媒体整理流程。"
                    ),
                },
            },
        ]

    def stop_service(self) -> None:
        self._config = normalize_config(None)
        self._client = None
        self._relay = None
