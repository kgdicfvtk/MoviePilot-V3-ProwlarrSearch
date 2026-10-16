from __future__ import annotations

from typing import Any

from app.sdk.logging import logger
from app.sdk.network import AsyncRequestUtils, RequestUtils

from .config import ProwlarrConfig
from .mapper import prowlarr_search_type


def _page_number(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("+-").isdigit():
            return max(int(stripped), 0)
    return 0


def result_page_bounds(page: object, page_size: int) -> tuple[int, int]:
    page_number = _page_number(page)
    start = page_number * page_size
    return start, start + page_size


def build_search_params(
    keyword: object, mtype: Any, page: object, page_size: int
) -> dict[str, Any] | None:
    if not isinstance(keyword, str) or not keyword.strip():
        return None
    query = keyword.strip()
    return {
        "query": query,
        "type": prowlarr_search_type(mtype),
        "limit": page_size,
        "offset": 0,
    }


class ProwlarrClient:
    def __init__(self, config: ProwlarrConfig) -> None:
        self.config = config

    def _request_details(
        self, keyword: object, mtype: Any, page: object
    ) -> tuple[str, dict[str, Any]] | None:
        if not self.config.ready or self.config.base_url is None:
            return None
        params = build_search_params(keyword, mtype, page, self.config.candidate_limit)
        if params is None:
            return None
        return f"{self.config.base_url}/api/v1/search", params

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.config.api_key, "Accept": "application/json"}

    @staticmethod
    def _decode_response(response: Any, mode: str) -> list[Any]:
        if response is None:
            logger.warning(f"Prowlarr {mode}搜索失败（无响应）")
            return []
        status_code = getattr(response, "status_code", 0)
        if type(status_code) is not int or not 200 <= status_code < 300:
            safe_status = status_code if type(status_code) is int else 0
            logger.warning(f"Prowlarr {mode}搜索失败（HTTP {safe_status}）")
            return []
        try:
            payload = response.json()
        except Exception as error:
            logger.warning(f"Prowlarr {mode}搜索失败（{type(error).__name__}）")
            return []
        if not isinstance(payload, list):
            logger.warning(f"Prowlarr {mode}搜索失败（响应格式）")
            return []
        return payload

    def search(
        self, keyword: object, mtype: Any = None, page: object = 0
    ) -> list[Any]:
        request = self._request_details(keyword, mtype, page)
        if request is None:
            return []
        url, params = request
        try:
            response = RequestUtils(
                headers=self._headers(), timeout=self.config.timeout
            ).get_res(url, params=params)
        except Exception as error:
            logger.warning(f"Prowlarr 同步搜索失败（{type(error).__name__}）")
            return []
        return self._decode_response(response, "同步")

    async def async_search(
        self, keyword: object, mtype: Any = None, page: object = 0
    ) -> list[Any]:
        request = self._request_details(keyword, mtype, page)
        if request is None:
            return []
        url, params = request
        try:
            response = await AsyncRequestUtils(
                headers=self._headers(), timeout=self.config.timeout
            ).get_res(url, params=params)
        except Exception as error:
            logger.warning(f"Prowlarr 异步搜索失败（{type(error).__name__}）")
            return []
        return self._decode_response(response, "异步")
