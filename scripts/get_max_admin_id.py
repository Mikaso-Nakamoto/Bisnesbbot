from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from bisnesbbot.max import MAXClient


def _event_user_id(update: dict[str, object]) -> int | None:
    candidates: list[object] = [update.get("user")]
    message = update.get("message")
    if isinstance(message, dict):
        candidates.append(message.get("sender"))

    for candidate in candidates:
        if isinstance(candidate, dict):
            value = candidate.get("user_id")
            if isinstance(value, int) and value > 0:
                return value
    return None


async def main() -> int:
    load_dotenv()
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if not token:
        print("Сначала укажи MAX_BOT_TOKEN в локальном .env.", file=sys.stderr)
        return 1

    client = MAXClient(access_token=token)
    try:
        updates = await client.get_updates(timeout=0)
    finally:
        await client.close()

    user_ids = sorted(
        {user_id for update in updates if (user_id := _event_user_id(update))}
    )
    if not user_ids:
        print(
            "MAX не вернул событие с ID пользователя. Открой диалог с ботом, "
            "отправь ему сообщение и запусти скрипт ещё раз.",
            file=sys.stderr,
        )
        return 1

    print("MAX user ID:")
    for user_id in user_ids:
        print(user_id)
    print("Добавь нужный ID в MAX_ADMIN_USER_IDS в .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
