from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable
from urllib.parse import urlparse

import httpx


class JellyfinError(RuntimeError):
    pass


class JellyfinConfigurationError(JellyfinError):
    pass


class JellyfinConnectionError(JellyfinError):
    pass


@dataclass(slots=True)
class JellyfinImage:
    content: bytes
    content_type: str


class JellyfinClient:
    ITEM_PAGE_SIZE = 500
    REQUEST_ATTEMPTS = 3

    def __init__(self, base_url: str, api_key: str, *, timeout_seconds: float = 30.0) -> None:
        self.base_url = self._validate_base_url(base_url)
        self.api_key = api_key.strip()
        if not self.api_key:
            raise JellyfinConfigurationError("A Jellyfin API key is required")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _validate_base_url(value: str) -> str:
        candidate = value.strip().rstrip("/")
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise JellyfinConfigurationError("Jellyfin URL must be an absolute http(s) URL")
        return candidate

    def _request(self, path: str, *, params: dict | None = None) -> httpx.Response:
        for attempt in range(self.REQUEST_ATTEMPTS):
            try:
                response = httpx.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={"X-Emby-Token": self.api_key, "Accept": "application/json"},
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 >= self.REQUEST_ATTEMPTS:
                    raise JellyfinConnectionError(f"Could not connect to Jellyfin: {exc}") from exc
                sleep(0.5 * (attempt + 1))
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                detail = "authentication failed" if status in {401, 403} else f"HTTP {status}"
                raise JellyfinConnectionError(f"Jellyfin request failed: {detail}") from exc
        raise JellyfinConnectionError("Could not connect to Jellyfin")

    def get_system_info(self) -> dict:
        return self._request("/System/Info").json()

    def get_users(self) -> list[dict]:
        payload = self._request("/Users").json()
        return payload if isinstance(payload, list) else []

    def get_virtual_folders(self) -> list[dict]:
        payload = self._request("/Library/VirtualFolders").json()
        return payload if isinstance(payload, list) else []

    def get_items(
        self,
        *,
        user_id: str | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> list[dict]:
        items: list[dict] = []
        start_index = 0
        while True:
            params: dict[str, str | bool | int] = {
                "Recursive": True,
                "IncludeItemTypes": "Movie,Episode,Audio,AudioBook",
                "Fields": (
                    "Path,DateCreated,OriginalTitle,Overview,ProviderIds,PremiereDate,"
                    "ProductionYear,SeriesInfo,MediaSources"
                ),
                "ImageTypeLimit": 1,
                "EnableImageTypes": "Primary,Backdrop,Thumb",
                "EnableTotalRecordCount": True,
                "StartIndex": start_index,
                "Limit": self.ITEM_PAGE_SIZE,
            }
            if user_id:
                params["UserId"] = user_id
            payload = self._request("/Items", params=params).json()
            if not isinstance(payload, dict):
                break
            page = payload.get("Items")
            if not isinstance(page, list) or not page:
                break
            items.extend(item for item in page if isinstance(item, dict))
            next_index = start_index + len(page)
            total = payload.get("TotalRecordCount")
            if progress_callback is not None:
                progress_callback(next_index, total if isinstance(total, int) else None)
            if isinstance(total, int) and next_index >= total:
                break
            if len(page) < self.ITEM_PAGE_SIZE or next_index <= start_index:
                break
            start_index = next_index
        return items

    def get_image(self, item_id: str, image_type: str, *, tag: str | None = None) -> JellyfinImage:
        params = {"tag": tag} if tag else None
        response = self._request(f"/Items/{item_id}/Images/{image_type}", params=params)
        return JellyfinImage(
            content=response.content,
            content_type=response.headers.get("content-type", "image/jpeg").split(";", 1)[0],
        )
