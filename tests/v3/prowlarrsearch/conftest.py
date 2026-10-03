from __future__ import annotations

import sys
import types
from abc import ABCMeta, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pytest


RUNTIME_ROOT = Path(__file__).parents[3] / "plugins.v3"


class StubLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def debug(self, message: str) -> None:
        self.records.append(("debug", message))

    def info(self, message: str) -> None:
        self.records.append(("info", message))

    def warning(self, message: str) -> None:
        self.records.append(("warning", message))

    def error(self, message: str) -> None:
        self.records.append(("error", message))


class StubPluginBase(metaclass=ABCMeta):
    def __init__(self) -> None:
        pass

    @abstractmethod
    def init_plugin(self, config: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_state(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_api(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_form(self) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_page(self) -> list[dict[str, Any]] | None:
        raise NotImplementedError

    @abstractmethod
    def stop_service(self) -> None:
        raise NotImplementedError

    @staticmethod
    def get_command() -> list[dict[str, Any]] | None:
        return None

    def get_service(self) -> list[dict[str, Any]] | None:
        return None

    def get_module(self) -> dict[str, Any] | None:
        return None


class StubRequestUtils:
    def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None) -> None:
        self.headers = headers
        self.timeout = timeout

    def get_res(self, url: str, params: dict[str, Any] | None = None) -> Any:
        raise AssertionError("unexpected synchronous network request")

    @contextmanager
    def get_stream(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        allow_redirects: bool = True,
        raise_exception: bool = False,
    ):
        raise AssertionError("unexpected streaming network request")
        yield None


class StubAsyncRequestUtils:
    def __init__(self, headers: dict[str, Any] | None = None, timeout: int | None = None) -> None:
        self.headers = headers
        self.timeout = timeout

    async def get_res(self, url: str, params: dict[str, Any] | None = None) -> Any:
        raise AssertionError("unexpected asynchronous network request")


@dataclass
class StubTorrentInfo:
    site: int | None = None
    site_name: str | None = None
    site_cookie: str | None = None
    site_ua: str | None = None
    site_proxy: bool = False
    site_order: int = 0
    site_downloader: str | None = None
    title: str | None = None
    description: str | None = None
    media_source: Any = None
    media_id: str | None = None
    enclosure: str | None = None
    page_url: str | None = None
    size: float = 0.0
    seeders: int = 0
    peers: int = 0
    grabs: int = 0
    pubdate: str | None = None
    date_elapsed: str | None = None
    freedate: str | None = None
    uploadvolumefactor: float | None = None
    downloadvolumefactor: float | None = None
    hit_and_run: bool = False
    labels: list[Any] = field(default_factory=list)
    pri_order: int = 0
    category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


stub_logger = StubLogger()


def _install_module(name: str, **attributes: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


app_module = _install_module("app")
app_module.__path__ = []
plugins_module = _install_module("app.plugins")
plugins_module.__path__ = [str(RUNTIME_ROOT)]
sdk_module = _install_module("app.sdk")
sdk_module.__path__ = []

_install_module("app.sdk.plugin", _PluginBase=StubPluginBase)
_install_module(
    "app.sdk.network",
    RequestUtils=StubRequestUtils,
    AsyncRequestUtils=StubAsyncRequestUtils,
)
_install_module("app.sdk.media", TorrentInfo=StubTorrentInfo)
_install_module("app.sdk.logging", logger=stub_logger)
_install_module("app.sdk.config", settings=types.SimpleNamespace(PORT=3001))


@pytest.fixture(autouse=True)
def reset_stub_logger() -> None:
    stub_logger.records.clear()


@pytest.fixture
def captured_plugin_logs() -> list[tuple[str, str]]:
    return stub_logger.records

