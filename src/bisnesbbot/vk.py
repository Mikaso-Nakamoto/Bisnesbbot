from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator, Mapping
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class VKAPIError(RuntimeError):
    def __init__(self, method: str, code: int, message: str) -> None:
        self.method = method
        self.code = code
        self.message = message
        super().__init__(f"VK API {method} returned error {code}: {message}")


class VKClient:
    """Минимальный клиент VK API для сообщества и Bots Long Poll API."""

    API_URL = "https://api.vk.ru/method/{}"
    LONG_POLL_WAIT_SECONDS = 25

    def __init__(
        self,
        *,
        access_token: str,
        group_id: int,
        api_version: str = "5.199",
    ) -> None:
        self._access_token = access_token
        self.group_id = group_id
        self.api_version = api_version
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=40,
                connect=10,
                sock_read=35,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                trust_env=True,
            )
        return self._session

    async def _api(self, method: str, **params: Any) -> Any:
        session = await self._get_session()
        payload = {
            "access_token": self._access_token,
            "v": self.api_version,
            **params,
        }
        async with session.post(self.API_URL.format(method), data=payload) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)

        if not isinstance(body, dict):
            raise TypeError(f"Неожиданный ответ VK API для {method}")
        error = body.get("error")
        if isinstance(error, dict):
            raise VKAPIError(
                method,
                int(error.get("error_code", 0)),
                str(error.get("error_msg", "неизвестная ошибка")),
            )
        if "response" not in body:
            raise RuntimeError(f"В ответе VK API для {method} нет поля response")
        return body["response"]

    async def send_message(
        self,
        *,
        peer_id: int,
        text: str,
        keyboard: Mapping[str, Any] | None = None,
    ) -> int:
        params: dict[str, Any] = {
            "peer_id": peer_id,
            "message": text,
            "random_id": secrets.randbelow(2_147_483_646) + 1,
            "group_id": self.group_id,
        }
        if keyboard is not None:
            params["keyboard"] = json.dumps(
                keyboard, ensure_ascii=False, separators=(",", ":")
            )
        result = await self._api("messages.send", **params)
        return int(result)

    async def answer_callback(
        self,
        *,
        event_id: str,
        user_id: int,
        peer_id: int,
        text: str,
    ) -> None:
        await self._api(
            "messages.sendMessageEventAnswer",
            event_id=event_id,
            user_id=user_id,
            peer_id=peer_id,
            event_data=json.dumps(
                {"type": "show_snackbar", "text": text[:90]},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    async def _get_long_poll_server(self) -> dict[str, str]:
        result = await self._api("groups.getLongPollServer", group_id=self.group_id)
        if not isinstance(result, dict):
            raise TypeError("VK не вернул настройки Bots Long Poll API")
        try:
            return {
                "server": str(result["server"]),
                "key": str(result["key"]),
                "ts": str(result["ts"]),
            }
        except KeyError as exc:
            raise RuntimeError(
                "В ответе VK отсутствует server, key или ts для Long Poll"
            ) from exc

    async def _long_poll_request(self, server: Mapping[str, str]) -> dict[str, Any]:
        session = await self._get_session()
        params = {
            "act": "a_check",
            "key": server["key"],
            "ts": server["ts"],
            "wait": self.LONG_POLL_WAIT_SECONDS,
        }
        async with session.get(server["server"], params=params) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)
        if not isinstance(body, dict):
            raise TypeError("Неожиданный ответ Bots Long Poll API")
        return body

    async def _new_long_poll_server(self) -> dict[str, str]:
        while True:
            try:
                return await self._get_long_poll_server()
            except (aiohttp.ClientError, TimeoutError) as exc:
                logger.warning(
                    "Не удалось подключиться к VK Bots Long Poll (%s); повтор через 5 с",
                    type(exc).__name__,
                )
                await asyncio.sleep(5)

    async def updates(self) -> AsyncIterator[dict[str, Any]]:
        """Бесконечно получает события сообщества; временные сбои переподключаются."""
        server = await self._new_long_poll_server()
        logger.info("Подключён VK Bots Long Poll для сообщества %s", self.group_id)

        while True:
            try:
                body = await self._long_poll_request(server)
            except (aiohttp.ClientError, TimeoutError) as exc:
                logger.warning(
                    "Ошибка VK Long Poll (%s); переподключение через 2 с",
                    type(exc).__name__,
                )
                await asyncio.sleep(2)
                server = await self._new_long_poll_server()
                continue

            failed = body.get("failed")
            if failed is not None:
                if failed == 1 and "ts" in body:
                    server["ts"] = str(body["ts"])
                    continue
                logger.warning(
                    "VK Long Poll завершил сессию (код %s); обновляю ключ", failed
                )
                server = await self._new_long_poll_server()
                continue

            if "ts" in body:
                server["ts"] = str(body["ts"])
            updates = body.get("updates", [])
            if not isinstance(updates, list):
                logger.warning("VK Long Poll прислал события в неверном формате")
                continue
            for event in updates:
                if isinstance(event, dict):
                    yield event

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
