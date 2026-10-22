from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Settings
from .contacts import LEGACY_CONTACT_METHOD_LABELS, contact_method_label
from .database import Appointment
from .max import MAXClient
from .schedule import format_day
from .vk import VKClient
from .vk_ui import admin_keyboard as vk_admin_keyboard

logger = logging.getLogger(__name__)


CHANNEL_LABELS = {"telegram": "Telegram", "vk": "ВКонтакте"}


def format_appointment(appointment: Appointment, settings: Settings) -> str:
    starts_at = datetime.fromtimestamp(appointment.start_ts, settings.timezone)
    ends_at = datetime.fromtimestamp(appointment.end_ts, settings.timezone)
    channel = CHANNEL_LABELS.get(appointment.source_channel, appointment.source_channel)
    if appointment.contact_method and appointment.contact_value:
        if appointment.contact_method in LEGACY_CONTACT_METHOD_LABELS:
            contact = (
                f"Способ связи: {contact_method_label(appointment.contact_method)}\n"
                f"Контакт: {appointment.contact_value}"
            )
        else:
            contact = (
                f"Предпочтительный способ связи: "
                f"{contact_method_label(appointment.contact_method)}\n"
                f"Номер телефона: {appointment.contact_value}"
            )
    else:
        contact = "Контакт для связи: не указан (старая заявка)"

    status_labels = {
        "pending": "предварительная — ждёт личного согласования",
        "confirmed": "согласована после личного общения",
        "declined": "не согласована",
        "cancelled": "отменена",
    }
    status = status_labels.get(appointment.status, appointment.status)
    text = (
        f"Предварительная заявка №{appointment.id}\n"
        f"Услуга: {appointment.service_name}\n"
        f"Клиент: {appointment.client_name}\n"
        f"Канал клиента: {channel}\n"
        f"Время для обсуждения: {format_day(starts_at.date())}, "
        f"{starts_at:%H:%M}–{ends_at:%H:%M}\n"
        f"{contact}\n"
        f"Статус: {status}"
    )
    if appointment.status == "pending":
        text += (
            "\n\nСвяжись с клиентом выбранным способом и лично согласуй время. "
            "Нажимай кнопку решения только после разговора."
        )
    return text


def telegram_admin_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Договорились",
                    callback_data=f"admin:confirm:{appointment_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Не договорились",
                    callback_data=f"admin:decline:{appointment_id}",
                ),
            ]
        ]
    )


class NotificationService:
    """Рассылает копии уведомлений, не создавая копий заявок."""

    def __init__(
        self,
        settings: Settings,
        *,
        telegram_bot: Bot | None = None,
        vk_client: VKClient | None = None,
        max_client: MAXClient | None = None,
    ) -> None:
        self.settings = settings
        self.telegram_bot = telegram_bot
        self.vk_client = vk_client
        self.max_client = max_client

    async def notify_admins(
        self,
        appointment: Appointment,
        *,
        telegram_bot: Bot | None = None,
    ) -> None:
        text = format_appointment(appointment, self.settings)
        sent_to_admin = False
        tg_bot = telegram_bot or self.telegram_bot

        if tg_bot is not None:
            for admin_id in self.settings.admin_telegram_ids:
                try:
                    await tg_bot.send_message(
                        admin_id,
                        text,
                        reply_markup=telegram_admin_keyboard(appointment.id),
                    )
                    sent_to_admin = True
                except Exception:
                    logger.exception(
                        "Не удалось отправить заявку #%s администратору Telegram ID %s",
                        appointment.id,
                        admin_id,
                    )

        if self.vk_client is not None:
            for admin_id in self.settings.admin_vk_user_ids:
                try:
                    await self.vk_client.send_message(
                        peer_id=admin_id,
                        text=text,
                        keyboard=vk_admin_keyboard(appointment.id),
                    )
                    sent_to_admin = True
                except Exception:
                    logger.exception(
                        "Не удалось отправить заявку #%s администратору VK ID %s",
                        appointment.id,
                        admin_id,
                    )

        if self.max_client is not None:
            for admin_id in self.settings.max_admin_user_ids:
                try:
                    await self.max_client.send_message(user_id=admin_id, text=text)
                    sent_to_admin = True
                except Exception:
                    logger.exception(
                        "Не удалось отправить заявку #%s администратору MAX ID %s",
                        appointment.id,
                        admin_id,
                    )

        if not sent_to_admin:
            logger.error(
                "Заявка #%s сохранена, но уведомление администратору не отправлено. "
                "Проверьте настройки ID и доступность личных сообщений.",
                appointment.id,
            )

    async def send_client_message(
        self,
        appointment: Appointment,
        text: str,
        *,
        telegram_bot: Bot | None = None,
    ) -> bool:
        try:
            if appointment.source_channel == "telegram":
                tg_bot = telegram_bot or self.telegram_bot
                if tg_bot is None:
                    logger.error(
                        "Нет Telegram-бота для ответа клиенту по заявке #%s",
                        appointment.id,
                    )
                    return False
                await tg_bot.send_message(int(appointment.external_chat_id), text)
                return True

            if appointment.source_channel == "vk":
                if self.vk_client is None:
                    logger.error(
                        "Нет VK-бота для ответа клиенту по заявке #%s", appointment.id
                    )
                    return False
                await self.vk_client.send_message(
                    peer_id=int(appointment.external_chat_id), text=text
                )
                return True

            logger.error(
                "Неизвестный канал %r у заявки #%s",
                appointment.source_channel,
                appointment.id,
            )
        except Exception:
            logger.exception(
                "Не удалось отправить клиенту ответ по заявке #%s", appointment.id
            )
        return False

    async def notify_client_decision(
        self,
        appointment: Appointment,
        *,
        telegram_bot: Bot | None = None,
    ) -> bool:
        starts_at = datetime.fromtimestamp(appointment.start_ts, self.settings.timezone)
        ends_at = datetime.fromtimestamp(appointment.end_ts, self.settings.timezone)
        if appointment.status == "confirmed":
            text = (
                f"🤝 После личного согласования заявка №{appointment.id} отмечена как "
                "согласованная.\n"
                f"Согласованное время: {format_day(starts_at.date())}, "
                f"{starts_at:%H:%M}–{ends_at:%H:%M}."
            )
        elif appointment.status == "declined":
            start_command = (
                "/start" if appointment.source_channel == "telegram" else "«начать»"
            )
            text = (
                f"Заявка №{appointment.id} не была согласована.\n"
                "Выбранное время снова доступно. Чтобы выбрать другой слот, "
                f"отправьте {start_command}."
            )
        else:
            raise ValueError("Клиенту можно сообщить только об итоговом решении")
        return await self.send_client_message(
            appointment, text, telegram_bot=telegram_bot
        )
