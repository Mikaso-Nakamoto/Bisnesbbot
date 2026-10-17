from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Settings
from .database import Appointment, AppointmentRepository, SlotUnavailableError
from .schedule import Slot, available_slots_for_day, format_day, format_slot

logger = logging.getLogger(__name__)


class BookingFlow(StatesGroup):
    waiting_for_name = State()


def _admin_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"admin:confirm:{appointment_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"admin:decline:{appointment_id}",
                ),
            ]
        ]
    )


def _main_keyboard(*, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📅 Записаться", callback_data="book:choose")]]
    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📥 Заявки на подтверждение", callback_data="admin:pending"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_appointment(appointment: Appointment, settings: Settings) -> str:
    starts_at = datetime.fromtimestamp(appointment.start_ts, settings.timezone)
    ends_at = datetime.fromtimestamp(appointment.end_ts, settings.timezone)
    username = (
        f"\nTelegram: @{appointment.client_username}"
        if appointment.client_username
        else ""
    )
    return (
        f"Заявка №{appointment.id}\n"
        f"Услуга: {appointment.service_name}\n"
        f"Клиент: {appointment.client_name}{username}\n"
        f"Время: {format_day(starts_at.date())}, {starts_at:%H:%M}–{ends_at:%H:%M}\n"
        f"Статус: ожидает подтверждения"
    )


def _slots_for_day(
    day: date,
    *,
    settings: Settings,
    repository: AppointmentRepository,
    now: datetime | None = None,
) -> list[Slot]:
    start_of_day = datetime.combine(day, time.min, tzinfo=settings.timezone)
    end_of_day = start_of_day + timedelta(days=1)
    buffer_seconds = settings.buffer_minutes * 60
    busy = repository.get_busy_intervals(
        range_start_ts=int(start_of_day.timestamp()),
        range_end_ts=int(end_of_day.timestamp()),
        buffer_seconds=buffer_seconds,
    )
    return available_slots_for_day(
        day,
        weekly_schedule=settings.weekly_schedule,
        timezone=settings.timezone,
        duration_minutes=settings.service_duration_minutes,
        buffer_minutes=settings.buffer_minutes,
        busy_intervals=busy,
        now=now,
    )


def _available_days(
    *,
    settings: Settings,
    repository: AppointmentRepository,
    now: datetime | None = None,
) -> list[tuple[date, list[Slot]]]:
    local_now = (now or datetime.now(settings.timezone)).astimezone(settings.timezone)
    today = local_now.date()
    results: list[tuple[date, list[Slot]]] = []
    for offset in range(settings.booking_days_ahead + 1):
        day = today + timedelta(days=offset)
        slots = _slots_for_day(
            day, settings=settings, repository=repository, now=local_now
        )
        if slots:
            results.append((day, slots))
    return results


def _date_keyboard(days: list[tuple[date, list[Slot]]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(days), 2):
        row = [
            InlineKeyboardButton(
                text=format_day(day),
                callback_data=f"book:date:{day.isoformat()}",
            )
            for day, _ in days[index : index + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="↩️ В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _slot_keyboard(day: date, slots: list[Slot]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(slots), 2):
        rows.append(
            [
                InlineKeyboardButton(
                    text=format_slot(slot),
                    callback_data=f"book:slot:{slot.start_ts}",
                )
                for slot in slots[index : index + 2]
            ]
        )
    rows.append([InlineKeyboardButton(text="↩️ К датам", callback_data="book:choose")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _schedule_notice(settings: Settings) -> str:
    if not settings.demo_mode:
        return ""
    return (
        "\n\n⚠️ Сейчас показано условное тестовое расписание. "
        "Заявка не считается записью, пока тётя её не подтвердит."
    )


async def _edit_or_send(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    if callback.message is None:
        await callback.answer("Откройте бота в личном чате.", show_alert=True)
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            pass
        else:
            await callback.message.answer(text, reply_markup=reply_markup)
    await callback.answer()


def create_dispatcher(
    settings: Settings, repository: AppointmentRepository
) -> Dispatcher:
    router = Router(name="booking")
    dispatcher = Dispatcher(storage=MemoryStorage())

    def is_admin(user_id: int | None) -> bool:
        return user_id is not None and user_id in settings.admin_telegram_ids

    def date_is_in_booking_horizon(day: date) -> bool:
        today = datetime.now(settings.timezone).date()
        return today <= day <= today + timedelta(days=settings.booking_days_ahead)

    async def show_dates(callback: CallbackQuery) -> None:
        days = _available_days(settings=settings, repository=repository)
        if not days:
            await callback.answer("Пока нет свободного времени.", show_alert=True)
            if callback.message is not None:
                await callback.message.answer(
                    "Свободных слотов пока нет. Попробуйте проверить расписание позже."
                )
            return
        await _edit_or_send(
            callback,
            "Выберите день для заявки на запись:" + _schedule_notice(settings),
            _date_keyboard(days),
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        user_id = message.from_user.id if message.from_user else None
        text = (
            "Здравствуйте! Здесь можно оставить заявку на массаж.\n"
            "Выберите свободный день и время — тётя подтвердит или отклонит заявку."
            + _schedule_notice(settings)
        )
        await message.answer(
            text, reply_markup=_main_keyboard(is_admin=is_admin(user_id))
        )

    @router.message(Command("myid"))
    async def my_id(message: Message) -> None:
        if message.from_user is None:
            return
        await message.answer(
            f"Ваш Telegram ID: {message.from_user.id}\n"
            "Для настройки администратора добавьте этот номер в ADMIN_TELEGRAM_IDS в файле .env."
        )

    @router.message(Command("cancel"))
    async def cancel_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Заявка отменена. Чтобы начать заново, нажмите /start.")

    @router.message(Command("pending"))
    async def pending_command(message: Message) -> None:
        if message.from_user is None or not is_admin(message.from_user.id):
            await message.answer("Эта команда доступна только администратору.")
            return
        pending = repository.get_pending()
        if not pending:
            await message.answer("Заявок, ожидающих подтверждения, нет.")
            return
        for appointment in pending:
            await message.answer(
                _format_appointment(appointment, settings),
                reply_markup=_admin_keyboard(appointment.id),
            )

    @router.callback_query(F.data == "book:choose")
    async def choose_date(callback: CallbackQuery) -> None:
        await show_dates(callback)

    @router.callback_query(F.data.startswith("book:date:"))
    async def choose_time(callback: CallbackQuery) -> None:
        if callback.data is None:
            return
        try:
            day = date.fromisoformat(callback.data.removeprefix("book:date:"))
        except ValueError:
            await callback.answer(
                "Не удалось прочитать дату. Попробуйте ещё раз.", show_alert=True
            )
            return

        if not date_is_in_booking_horizon(day):
            await callback.answer(
                "Эта дата уже недоступна. Выберите день из списка.", show_alert=True
            )
            return

        slots = _slots_for_day(day, settings=settings, repository=repository)
        if not slots:
            await show_dates(callback)
            return

        await _edit_or_send(
            callback,
            f"Свободное время на {format_day(day)}. Выберите подходящий слот:",
            _slot_keyboard(day, slots),
        )

    @router.callback_query(F.data.startswith("book:slot:"))
    async def select_slot(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.data is None:
            return
        try:
            selected_start_ts = int(callback.data.removeprefix("book:slot:"))
            selected_start = datetime.fromtimestamp(
                selected_start_ts, settings.timezone
            )
        except (ValueError, OverflowError, OSError):
            await callback.answer(
                "Не удалось прочитать время. Выберите слот ещё раз.", show_alert=True
            )
            return

        if not date_is_in_booking_horizon(selected_start.date()):
            await callback.answer(
                "Эта дата уже недоступна. Выберите день из списка.", show_alert=True
            )
            return

        slots = _slots_for_day(
            selected_start.date(), settings=settings, repository=repository
        )
        selected = next(
            (slot for slot in slots if slot.start_ts == selected_start_ts), None
        )
        if selected is None:
            await show_dates(callback)
            return

        await state.update_data(start_ts=selected.start_ts, end_ts=selected.end_ts)
        await state.set_state(BookingFlow.waiting_for_name)
        if callback.message is not None:
            await callback.message.answer(
                f"Вы выбрали {format_day(selected.starts_at.date())}, {format_slot(selected)}.\n"
                "Напишите, как к вам обращаться. После этого тётя получит заявку на подтверждение.\n"
                "Если передумали, отправьте /cancel."
            )
        await callback.answer()

    @router.message(BookingFlow.waiting_for_name)
    async def receive_name(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or message.chat.type != "private":
            return
        client_name = (message.text or "").strip()
        if not 2 <= len(client_name) <= 80:
            await message.answer(
                "Напишите имя длиной от 2 до 80 символов или отправьте /cancel."
            )
            return

        selected = await state.get_data()
        start_ts = int(selected["start_ts"])
        end_ts = int(selected["end_ts"])
        start_local = datetime.fromtimestamp(start_ts, settings.timezone)
        current_slots = _slots_for_day(
            start_local.date(), settings=settings, repository=repository
        )
        if not any(
            slot.start_ts == start_ts and slot.end_ts == end_ts
            for slot in current_slots
        ):
            await state.clear()
            await message.answer(
                "Пока вы заполняли заявку, это время уже заняли. Нажмите /start и выберите другое."
            )
            return

        user = message.from_user
        try:
            appointment = repository.create_pending(
                client_name=client_name,
                service_name=settings.service_name,
                start_ts=start_ts,
                end_ts=end_ts,
                source_channel="telegram",
                external_user_id=str(user.id),
                external_chat_id=str(message.chat.id),
                client_username=user.username,
                buffer_seconds=settings.buffer_minutes * 60,
            )
        except SlotUnavailableError:
            await state.clear()
            await message.answer(
                "Это время только что заняли. Нажмите /start, чтобы выбрать другой слот."
            )
            return

        await state.clear()
        await message.answer(
            f"Заявка №{appointment.id} отправлена тёте.\n"
            "Пока это не подтверждённая запись. Я напишу сюда, когда тётя ответит."
        )

        if not settings.admin_telegram_ids:
            logger.error(
                "Заявка #%s сохранена, но ADMIN_TELEGRAM_IDS не настроен; "
                "администратор сможет увидеть её командой /pending после настройки.",
                appointment.id,
            )
            return

        for admin_id in settings.admin_telegram_ids:
            try:
                await bot.send_message(
                    admin_id,
                    _format_appointment(appointment, settings),
                    reply_markup=_admin_keyboard(appointment.id),
                )
            except TelegramAPIError:
                logger.exception(
                    "Не удалось отправить заявку #%s администратору Telegram ID %s",
                    appointment.id,
                    admin_id,
                )

    @router.callback_query(F.data == "admin:pending")
    async def pending_button(callback: CallbackQuery) -> None:
        if callback.from_user is None or not is_admin(callback.from_user.id):
            await callback.answer(
                "Эта кнопка доступна только администратору.", show_alert=True
            )
            return
        pending = repository.get_pending()
        await callback.answer()
        if callback.message is None:
            return
        if not pending:
            await callback.message.answer("Заявок, ожидающих подтверждения, нет.")
            return
        for appointment in pending:
            await callback.message.answer(
                _format_appointment(appointment, settings),
                reply_markup=_admin_keyboard(appointment.id),
            )

    @router.callback_query(F.data.startswith("admin:confirm:"))
    async def confirm_appointment(callback: CallbackQuery, bot: Bot) -> None:
        await decide_appointment(callback, bot, decision="confirmed")

    @router.callback_query(F.data.startswith("admin:decline:"))
    async def decline_appointment(callback: CallbackQuery, bot: Bot) -> None:
        await decide_appointment(callback, bot, decision="declined")

    async def decide_appointment(
        callback: CallbackQuery, bot: Bot, *, decision: str
    ) -> None:
        if callback.from_user is None or not is_admin(callback.from_user.id):
            await callback.answer(
                "Эта кнопка доступна только администратору.", show_alert=True
            )
            return
        if callback.data is None:
            return
        try:
            appointment_id = int(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Некорректный номер заявки.", show_alert=True)
            return

        appointment = repository.decide(
            appointment_id,
            status=decision,
            admin_id=str(callback.from_user.id),
        )
        if appointment is None:
            await callback.answer(
                "Заявка уже обработана или не найдена.", show_alert=True
            )
            return

        await callback.answer("Решение сохранено.")
        if callback.message is not None:
            label = "✅ Подтверждена" if decision == "confirmed" else "❌ Отклонена"
            try:
                await callback.message.edit_text(
                    _format_appointment(appointment, settings).replace(
                        "Статус: ожидает подтверждения", f"Статус: {label}"
                    ),
                    reply_markup=None,
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"Заявка №{appointment.id}: {label.lower()}."
                )

        starts_at = datetime.fromtimestamp(appointment.start_ts, settings.timezone)
        ends_at = datetime.fromtimestamp(appointment.end_ts, settings.timezone)
        if decision == "confirmed":
            client_text = (
                f"✅ Тётя подтвердила заявку №{appointment.id}.\n"
                f"Запись: {format_day(starts_at.date())}, {starts_at:%H:%M}–{ends_at:%H:%M}."
            )
        else:
            client_text = (
                f"К сожалению, тётя не подтвердила заявку №{appointment.id}.\n"
                "Это время снова доступно для записи. Нажмите /start, чтобы выбрать другой слот."
            )
        try:
            await bot.send_message(int(appointment.external_chat_id), client_text)
        except (TelegramAPIError, ValueError):
            logger.exception(
                "Не удалось сообщить клиенту результат заявки #%s", appointment.id
            )

    dispatcher.include_router(router)
    return dispatcher
