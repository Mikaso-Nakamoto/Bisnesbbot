from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bisnesbbot.availability import available_days
from bisnesbbot.bot import create_dispatcher
from bisnesbbot.config import DEMO_WEEKLY_SCHEDULE, Settings
from bisnesbbot.contacts import PHONE_PROMPT
from bisnesbbot.database import AppointmentRepository
from bisnesbbot.notifications import NotificationService


class TelegramBookingFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_phone_then_three_contact_buttons_then_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                bot_token="123456:TEST_TOKEN",
                admin_telegram_ids=frozenset({301}),
                vk_group_token=None,
                vk_group_id=None,
                vk_api_version="5.199",
                admin_vk_user_ids=frozenset(),
                db_path=Path(temp_dir) / "appointments.sqlite3",
                timezone=ZoneInfo("Europe/Moscow"),
                service_name="Массаж",
                service_duration_minutes=60,
                buffer_minutes=0,
                booking_days_ahead=14,
                weekly_schedule=DEMO_WEEKLY_SCHEDULE,
                demo_mode=True,
            )
            repository = AppointmentRepository(settings.db_path)
            repository.initialize()
            dispatcher = create_dispatcher(
                settings, repository, NotificationService(settings)
            )
            bot = Bot(token=settings.bot_token)
            user = User(id=900, is_bot=False, first_name="Анна")
            chat = Chat(id=900, type="private")
            update_id = 0
            api_calls = AsyncMock(return_value=None)

            async def send_text(text: str) -> None:
                nonlocal update_id
                update_id += 1
                message = Message(
                    message_id=update_id,
                    date=datetime.now(),
                    chat=chat,
                    from_user=user,
                    text=text,
                )
                await dispatcher.feed_update(
                    bot, Update(update_id=update_id, message=message)
                )

            async def click(data: str) -> None:
                nonlocal update_id
                update_id += 1
                message = Message(
                    message_id=update_id,
                    date=datetime.now(),
                    chat=chat,
                    from_user=user,
                    text="Кнопка",
                )
                callback = CallbackQuery(
                    id=f"callback-{update_id}",
                    from_user=user,
                    chat_instance="private-chat",
                    message=message,
                    data=data,
                )
                await dispatcher.feed_update(
                    bot, Update(update_id=update_id, callback_query=callback)
                )

            try:
                days = available_days(settings=settings, repository=repository)
                day, slots = next((day, slots) for day, slots in days if slots)
                with patch.object(Bot, "__call__", new=api_calls):
                    await click("book:choose")
                    await click(f"book:date:{day.isoformat()}")
                    await click(f"book:slot:{slots[0].start_ts}")

                    await send_text("+7 999 123-45-67")
                    await click("contact:max")
                    await send_text("Анна")
                    await click("booking:submit")

                sent_methods = [
                    call.args[0]
                    for call in api_calls.await_args_list
                    if call.args and type(call.args[0]).__name__ == "SendMessage"
                ]
                phone_prompt_index = next(
                    index
                    for index, method in enumerate(sent_methods)
                    if PHONE_PROMPT in method.text
                )
                choice_message = next(
                    method
                    for method in sent_methods[phone_prompt_index + 1 :]
                    if method.reply_markup is not None
                    and method.text == "Выберите, как тёте связаться с вами:"
                )
                buttons = choice_message.reply_markup.inline_keyboard
                name_prompt_index = next(
                    index
                    for index, method in enumerate(sent_methods)
                    if method.text == "Как к вам обращаться? Напишите имя."
                )
                summary_index = next(
                    index
                    for index, method in enumerate(sent_methods)
                    if method.text.startswith("Проверьте данные перед отправкой")
                )
                self.assertLess(phone_prompt_index, sent_methods.index(choice_message))
                self.assertLess(sent_methods.index(choice_message), name_prompt_index)
                self.assertLess(name_prompt_index, summary_index)
                self.assertEqual(len(buttons), 3)
                self.assertEqual(buttons[0][0].text, "ВКонтакте")
                self.assertEqual(buttons[1][0].text, "MAX")
                self.assertEqual(buttons[2][0].text, "📞 Телефонный звонок")

                pending = repository.get_pending()
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0].contact_method, "max")
                self.assertEqual(pending[0].contact_value, "+7 999 123-45-67")
                self.assertEqual(pending[0].client_name, "Анна")
            finally:
                await bot.session.close()


if __name__ == "__main__":
    unittest.main()
