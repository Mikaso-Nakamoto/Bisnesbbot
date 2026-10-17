from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from .bot import create_dispatcher
from .config import load_settings
from .database import AppointmentRepository

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = load_settings()
    repository = AppointmentRepository(settings.db_path)
    repository.initialize()

    if not settings.admin_telegram_ids:
        logger.warning(
            "ADMIN_TELEGRAM_IDS пока не задан. Заявки будут сохраняться со статусом pending, "
            "но для подтверждения нужно настроить ID тёти. Узнать ID можно командой /myid."
        )
    if settings.demo_mode:
        logger.warning(
            "DEMO_MODE включён: бот показывает условное расписание. "
            "Перед реальным использованием задайте график и часовой пояс."
        )

    bot = Bot(token=settings.bot_token)
    dispatcher = create_dispatcher(settings, repository)
    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
