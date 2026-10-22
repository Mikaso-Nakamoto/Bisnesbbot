from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bisnesbbot.config import DEMO_WEEKLY_SCHEDULE, Settings
from bisnesbbot.database import AppointmentRepository
from bisnesbbot.notifications import NotificationService
from bisnesbbot.vk_bot import VKBookingBot
from bisnesbbot.vk_ui import admin_keyboard, date_keyboard, parse_payload


def make_settings(
    *,
    telegram_admins: frozenset[int] = frozenset({301}),
    vk_admins: frozenset[int] = frozenset({401}),
    max_admins: frozenset[int] = frozenset(),
) -> Settings:
    return Settings(
        bot_token="test-telegram-token" if telegram_admins else None,
        admin_telegram_ids=telegram_admins,
        vk_group_token="test-vk-token",
        vk_group_id=12345,
        vk_api_version="5.199",
        admin_vk_user_ids=vk_admins,
        db_path=Path("test.sqlite3"),
        timezone=ZoneInfo("Europe/Moscow"),
        service_name="Массаж",
        service_duration_minutes=60,
        buffer_minutes=0,
        booking_days_ahead=14,
        weekly_schedule=DEMO_WEEKLY_SCHEDULE,
        demo_mode=True,
        max_bot_token="test-max-token" if max_admins else None,
        max_admin_user_ids=max_admins,
    )


class FakeTelegramBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})


class FakeVKClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.callback_answers: list[dict[str, object]] = []

    async def send_message(
        self,
        *,
        peer_id: int,
        text: str,
        keyboard: dict[str, object] | None = None,
    ) -> int:
        self.messages.append({"peer_id": peer_id, "text": text, "keyboard": keyboard})
        return len(self.messages)

    async def answer_callback(
        self, *, event_id: str, user_id: int, peer_id: int, text: str
    ) -> None:
        self.callback_answers.append(
            {
                "event_id": event_id,
                "user_id": user_id,
                "peer_id": peer_id,
                "text": text,
            }
        )


class FakeMAXClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, *, user_id: int, text: str) -> None:
        self.messages.append({"user_id": user_id, "text": text})


class VKIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AppointmentRepository(
            Path(self.temp_dir.name) / "appointments.sqlite3"
        )
        self.repository.initialize()
        self.start_ts = int(datetime(2030, 1, 7, 10, 0, tzinfo=UTC).timestamp())
        self.end_ts = int(datetime(2030, 1, 7, 11, 0, tzinfo=UTC).timestamp())
        self.settings = make_settings()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_vk_appointment(self):
        return self.repository.create_pending(
            client_name="Анна",
            contact_method="max",
            contact_value="+7 999 123-45-67",
            service_name="Массаж",
            start_ts=self.start_ts,
            end_ts=self.end_ts,
            source_channel="vk",
            external_user_id="900",
            external_chat_id="900",
            client_username=None,
        )

    async def test_vk_booking_notification_is_copied_to_both_admin_channels(
        self,
    ) -> None:
        appointment = self.create_vk_appointment()
        telegram = FakeTelegramBot()
        vk = FakeVKClient()
        notifications = NotificationService(
            self.settings, telegram_bot=telegram, vk_client=vk
        )

        await notifications.notify_admins(appointment)

        self.assertEqual(len(telegram.messages), 1)
        self.assertEqual(len(vk.messages), 1)
        self.assertEqual(telegram.messages[0]["chat_id"], 301)
        self.assertEqual(vk.messages[0]["peer_id"], 401)
        self.assertIn("Канал клиента: ВКонтакте", str(telegram.messages[0]["text"]))
        self.assertIn("Предварительная заявка №1", str(vk.messages[0]["text"]))
        self.assertIn("Предпочтительный способ связи: MAX", str(vk.messages[0]["text"]))
        self.assertIn("Номер телефона: +7 999 123-45-67", str(vk.messages[0]["text"]))

    async def test_request_notification_is_sent_to_configured_max_admin(self) -> None:
        appointment = self.create_vk_appointment()
        max_client = FakeMAXClient()
        notifications = NotificationService(
            make_settings(max_admins=frozenset({501})), max_client=max_client
        )

        await notifications.notify_admins(appointment)

        self.assertEqual(len(max_client.messages), 1)
        self.assertEqual(max_client.messages[0]["user_id"], 501)
        self.assertIn(
            "Номер телефона: +7 999 123-45-67", str(max_client.messages[0]["text"])
        )

    async def test_decision_is_sent_back_to_the_original_vk_dialog(self) -> None:
        appointment = self.create_vk_appointment()
        telegram = FakeTelegramBot()
        vk = FakeVKClient()
        notifications = NotificationService(
            self.settings, telegram_bot=telegram, vk_client=vk
        )

        await notifications.notify_client_decision(
            self.repository.decide(
                appointment.id, status="confirmed", admin_id="telegram:301"
            )
        )

        self.assertEqual(len(telegram.messages), 0)
        self.assertEqual(vk.messages[-1]["peer_id"], 900)
        self.assertIn("личного согласования", str(vk.messages[-1]["text"]))

    async def test_telegram_source_decision_returns_to_telegram(self) -> None:
        appointment = self.repository.create_pending(
            client_name="Пётр",
            contact_method="phone",
            contact_value="+7 999 123-45-67",
            service_name="Массаж",
            start_ts=self.start_ts,
            end_ts=self.end_ts,
            source_channel="telegram",
            external_user_id="700",
            external_chat_id="700",
            client_username=None,
        )
        telegram = FakeTelegramBot()
        vk = FakeVKClient()
        notifications = NotificationService(
            self.settings, telegram_bot=telegram, vk_client=vk
        )
        decided = self.repository.decide(
            appointment.id, status="confirmed", admin_id="vk:401"
        )

        await notifications.notify_client_decision(decided)

        self.assertEqual(len(telegram.messages), 1)
        self.assertEqual(telegram.messages[0]["chat_id"], 700)
        self.assertEqual(len(vk.messages), 0)

    async def test_client_can_create_one_shared_vk_booking_with_buttons(self) -> None:
        vk = FakeVKClient()
        telegram = FakeTelegramBot()
        notifications = NotificationService(
            self.settings, telegram_bot=telegram, vk_client=vk
        )
        bot = VKBookingBot(
            settings=self.settings,
            repository=self.repository,
            vk_client=vk,
            notifications=notifications,
        )

        async def click(payload: dict[str, object]) -> None:
            await bot.handle_event(
                {
                    "type": "message_event",
                    "object": {
                        "event_id": "client-event",
                        "user_id": 900,
                        "peer_id": 900,
                        "payload": json.dumps(payload),
                    },
                }
            )

        async def send_text(text: str) -> None:
            await bot.handle_event(
                {
                    "type": "message_new",
                    "object": {
                        "message": {
                            "from_id": 900,
                            "peer_id": 900,
                            "out": 0,
                            "text": text,
                        }
                    },
                }
            )

        await click({"action": "book:choose", "offset": 0})
        date_button = vk.messages[-1]["keyboard"]["buttons"][0][0]
        await click(json.loads(date_button["action"]["payload"]))

        slot_button = vk.messages[-1]["keyboard"]["buttons"][0][0]
        await click(json.loads(slot_button["action"]["payload"]))
        self.assertIn("Как с вами связаться?", str(vk.messages[-1]["text"]))
        await send_text("+7 999 123-45-67")

        method_buttons = vk.messages[-1]["keyboard"]["buttons"]
        self.assertEqual(len(method_buttons), 3)
        self.assertIn("ВКонтакте", method_buttons[0][0]["action"]["label"])
        self.assertIn("MAX", method_buttons[1][0]["action"]["label"])
        self.assertIn("Телефонный звонок", method_buttons[2][0]["action"]["label"])
        await click(json.loads(method_buttons[1][0]["action"]["payload"]))
        await send_text("Анна")

        preview = str(vk.messages[-1]["text"])
        self.assertIn("Номер телефона: +7 999 123-45-67", preview)
        self.assertIn("Как связаться: MAX", preview)
        submit_button = vk.messages[-1]["keyboard"]["buttons"][0][0]
        await click(json.loads(submit_button["action"]["payload"]))

        pending = self.repository.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].source_channel, "vk")
        self.assertEqual(pending[0].external_chat_id, "900")
        self.assertEqual(pending[0].contact_method, "max")
        self.assertEqual(pending[0].contact_value, "+7 999 123-45-67")
        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(any(message["peer_id"] == 401 for message in vk.messages))

    async def test_vk_admin_callback_confirms_shared_database_record(self) -> None:
        appointment = self.create_vk_appointment()
        vk = FakeVKClient()
        notifications = NotificationService(self.settings, vk_client=vk)
        bot = VKBookingBot(
            settings=self.settings,
            repository=self.repository,
            vk_client=vk,
            notifications=notifications,
        )
        payload = json.dumps(
            {"action": "admin:confirm", "appointment_id": appointment.id}
        )

        await bot.handle_event(
            {
                "type": "message_event",
                "object": {
                    "event_id": "event-1",
                    "user_id": 401,
                    "peer_id": 401,
                    "payload": payload,
                },
            }
        )

        saved = self.repository.get(appointment.id)
        self.assertEqual(saved.status, "confirmed")
        self.assertEqual(saved.decided_by, "vk:401")
        self.assertEqual(vk.callback_answers[0]["text"], "Заявка №1: согласовали.")
        self.assertEqual(vk.messages[-1]["peer_id"], 900)

    async def test_unlisted_vk_user_cannot_decide_appointment(self) -> None:
        appointment = self.create_vk_appointment()
        vk = FakeVKClient()
        notifications = NotificationService(self.settings, vk_client=vk)
        bot = VKBookingBot(
            settings=self.settings,
            repository=self.repository,
            vk_client=vk,
            notifications=notifications,
        )

        await bot.handle_event(
            {
                "type": "message_event",
                "object": {
                    "event_id": "event-2",
                    "user_id": 777,
                    "peer_id": 777,
                    "payload": json.dumps(
                        {"action": "admin:confirm", "appointment_id": appointment.id}
                    ),
                },
            }
        )

        self.assertEqual(self.repository.get(appointment.id).status, "pending")
        self.assertEqual(
            vk.callback_answers[0]["text"],
            "Эта кнопка доступна только администратору.",
        )

    def test_vk_button_payload_contains_only_action_data(self) -> None:
        keyboard = admin_keyboard(appointment_id=42)
        payload = keyboard["buttons"][0][0]["action"]["payload"]

        self.assertEqual(
            parse_payload(payload), {"action": "admin:confirm", "appointment_id": 42}
        )

    def test_vk_date_keyboard_paginates_horizon(self) -> None:
        days = [(datetime(2030, 1, 1 + offset).date(), []) for offset in range(7)]
        keyboard = date_keyboard(days, offset=0)
        button_count = sum(len(row) for row in keyboard["buttons"])
        payloads = [
            button["action"].get("payload")
            for row in keyboard["buttons"]
            for button in row
        ]

        self.assertLessEqual(button_count, 8)
        self.assertTrue(any('"offset":6' in str(payload) for payload in payloads))


if __name__ == "__main__":
    unittest.main()
