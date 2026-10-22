from __future__ import annotations

from typing import Any

import aiohttp


class MAXAPIError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"MAX API returned HTTP {status}: {message}")


class MAXClient:
    """Минимальный клиент MAX Bot API для личных уведомлений администратору."""

    API_URL = "https://platform-api2.max.ru"

    def __init__(self, *, access_token: str) -> None:
        self._access_token = access_token
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25, connect=10),
                trust_env=True,
            )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        session = await self._get_session()
        async with session.request(
            method,
            f"{self.API_URL}{path}",
            params=params,
            json=json_body,
            headers={"Authorization": self._access_token},
        ) as response:
            body = await response.json(content_type=None)
            if response.status >= 400:
                if isinstance(body, dict):
                    message = body.get("message", body.get("error", "ошибка запроса"))
                else:
                    message = "ошибка запроса"
                raise MAXAPIError(response.status, str(message))
        if isinstance(body, dict) and body.get("success") is False:
            raise MAXAPIError(200, str(body.get("message", "запрос не выполнен")))
        return body

    async def send_message(self, *, user_id: int, text: str) -> None:
        await self._request(
            "POST",
            "/messages",
            params={"user_id": user_id},
            json_body={"text": text[:4000]},
        )

    async def get_updates(
        self, *, timeout: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            "/updates",
            params={
                "timeout": timeout,
                "limit": limit,
                "types": "bot_started,message_created",
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("updates"), list):
            raise TypeError("MAX API вернул неожиданный список событий")
        return [item for item in result["updates"] if isinstance(item, dict)]

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
