from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from .bot import create_dispatcher
from .config import Settings, load_settings
from .database import AppointmentRepository
from .max import MAXClient
from .notifications import NotificationService
from .vk import VKClient
from .vk_bot import VKBookingBot

logger = logging.getLogger(__name__)


async def _run_channels(
    *,
    settings: Settings,
    repository: AppointmentRepository,
    telegram_bot: Bot | None,
    vk_client: VKClient | None,
    notifications: NotificationService,
) -> None:
    tasks: list[asyncio.Task[None]] = []
    if telegram_bot is not None:
        dispatcher = create_dispatcher(settings, repository, notifications)
        tasks.append(
            asyncio.create_task(
                dispatcher.start_polling(
                    telegram_bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                    close_bot_session=False,
                ),
                name="telegram-polling",
            )
        )
    if vk_client is not None:
        vk_bot = VKBookingBot(
            settings=settings,
            repository=repository,
            vk_client=vk_client,
            notifications=notifications,
        )
        tasks.append(asyncio.create_task(vk_bot.run(), name="vk-long-poll"))

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


async def run() -> None:
    settings = load_settings()
    repository = AppointmentRepository(settings.db_path)
    repository.initialize()

    if (
        not settings.admin_telegram_ids
        and not settings.admin_vk_user_ids
        and not settings.max_admin_user_ids
    ):
        logger.warning(
            "ID администратора пока не настроены. Заявки будут сохраняться со статусом "
            "pending, но для личного согласования нужно задать VK_ADMIN_USER_IDS, "
            "MAX_ADMIN_USER_IDS и/или ADMIN_TELEGRAM_IDS."
        )
    if settings.max_admin_user_ids and not (
        settings.admin_vk_user_ids or settings.admin_telegram_ids
    ):
        logger.warning(
            "MAX получает копии заявок без кнопок решения. Для отметки результата "
            "подключите админ-диалог VK или Telegram."
        )
    if settings.demo_mode:
        logger.warning(
            "DEMO_MODE включён: бот показывает условное расписание. "
            "Перед реальным использованием задайте график и часовой пояс."
        )

    telegram_bot = Bot(token=settings.bot_token) if settings.bot_token else None
    vk_client = (
        VKClient(
            access_token=settings.vk_group_token,
            group_id=settings.vk_group_id,
            api_version=settings.vk_api_version,
        )
        if settings.vk_group_token is not None and settings.vk_group_id is not None
        else None
    )
    max_client = (
        MAXClient(access_token=settings.max_bot_token)
        if settings.max_bot_token is not None
        else None
    )
    notifications = NotificationService(
        settings,
        telegram_bot=telegram_bot,
        vk_client=vk_client,
        max_client=max_client,
    )

    try:
        await _run_channels(
            settings=settings,
            repository=repository,
            telegram_bot=telegram_bot,
            vk_client=vk_client,
            notifications=notifications,
        )
    finally:
        if telegram_bot is not None:
            await telegram_bot.session.close()
        if vk_client is not None:
            await vk_client.close()
        if max_client is not None:
            await max_client.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
