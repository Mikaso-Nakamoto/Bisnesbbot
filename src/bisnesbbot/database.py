from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class SlotUnavailableError(Exception):
    """Время успели занять или оно больше не доступно."""


@dataclass(frozen=True, slots=True)
class Appointment:
    id: int
    client_name: str
    contact_method: str
    contact_value: str
    service_name: str
    start_ts: int
    end_ts: int
    status: str
    source_channel: str
    external_user_id: str
    external_chat_id: str
    client_username: str | None
    created_at: int
    decided_at: int | None
    decided_by: str | None


class AppointmentRepository:
    """Хранилище записей в одном SQLite-файле на ноутбуке."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS appointments (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_name         TEXT NOT NULL,
                    contact_method      TEXT NOT NULL DEFAULT '',
                    contact_value       TEXT NOT NULL DEFAULT '',
                    service_name        TEXT NOT NULL,
                    start_ts            INTEGER NOT NULL,
                    end_ts              INTEGER NOT NULL,
                    status              TEXT NOT NULL DEFAULT 'pending'
                                        CHECK (status IN ('pending', 'confirmed', 'declined', 'cancelled')),
                    source_channel      TEXT NOT NULL,
                    external_user_id    TEXT NOT NULL,
                    external_chat_id    TEXT NOT NULL,
                    client_username     TEXT,
                    created_at          INTEGER NOT NULL,
                    decided_at          INTEGER,
                    decided_by          TEXT,
                    CHECK (start_ts < end_ts)
                );

                CREATE INDEX IF NOT EXISTS idx_appointments_status_time
                    ON appointments(status, start_ts, end_ts);
                CREATE INDEX IF NOT EXISTS idx_appointments_user
                    ON appointments(source_channel, external_user_id, created_at);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(appointments)")
            }
            if "contact_method" not in columns:
                connection.execute(
                    "ALTER TABLE appointments ADD COLUMN "
                    "contact_method TEXT NOT NULL DEFAULT ''"
                )
            if "contact_value" not in columns:
                connection.execute(
                    "ALTER TABLE appointments ADD COLUMN "
                    "contact_value TEXT NOT NULL DEFAULT ''"
                )
        finally:
            connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> Appointment | None:
        if row is None:
            return None
        return Appointment(
            id=int(row["id"]),
            client_name=str(row["client_name"]),
            contact_method=str(row["contact_method"]),
            contact_value=str(row["contact_value"]),
            service_name=str(row["service_name"]),
            start_ts=int(row["start_ts"]),
            end_ts=int(row["end_ts"]),
            status=str(row["status"]),
            source_channel=str(row["source_channel"]),
            external_user_id=str(row["external_user_id"]),
            external_chat_id=str(row["external_chat_id"]),
            client_username=row["client_username"],
            created_at=int(row["created_at"]),
            decided_at=int(row["decided_at"])
            if row["decided_at"] is not None
            else None,
            decided_by=row["decided_by"],
        )

    def create_pending(
        self,
        *,
        client_name: str,
        contact_method: str,
        contact_value: str,
        service_name: str,
        start_ts: int,
        end_ts: int,
        source_channel: str,
        external_user_id: str,
        external_chat_id: str,
        client_username: str | None,
        buffer_seconds: int = 0,
    ) -> Appointment:
        if start_ts >= end_ts:
            raise ValueError("Время начала записи должно быть раньше времени окончания")
        contact_method = contact_method.strip()
        contact_value = " ".join(contact_value.split())
        if not contact_method or not contact_value:
            raise ValueError("Для предварительной заявки нужно указать контакт")
        if buffer_seconds < 0:
            raise ValueError("buffer_seconds не может быть отрицательным")

        created_at = int(time.time())
        connection = self._connect()
        try:
            # Сериализуем попытки занять слот, чтобы две заявки не прошли одновременно.
            connection.execute("BEGIN IMMEDIATE")
            conflict = connection.execute(
                """
                SELECT 1
                FROM appointments
                WHERE status IN ('pending', 'confirmed')
                  AND start_ts < ?
                  AND end_ts > ?
                LIMIT 1
                """,
                (end_ts + buffer_seconds, start_ts - buffer_seconds),
            ).fetchone()
            if conflict:
                connection.rollback()
                raise SlotUnavailableError(
                    "Этот слот уже занят или ожидает личного согласования"
                )

            cursor = connection.execute(
                """
                INSERT INTO appointments (
                    client_name, contact_method, contact_value, service_name,
                    start_ts, end_ts, status, source_channel, external_user_id,
                    external_chat_id, client_username, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    client_name,
                    contact_method,
                    contact_value,
                    service_name,
                    start_ts,
                    end_ts,
                    source_channel,
                    external_user_id,
                    external_chat_id,
                    client_username,
                    created_at,
                ),
            )
            appointment_id = int(cursor.lastrowid)
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

        appointment = self.get(appointment_id)
        assert appointment is not None
        return appointment

    def get_busy_intervals(
        self,
        *,
        range_start_ts: int,
        range_end_ts: int,
        buffer_seconds: int = 0,
    ) -> list[tuple[int, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT start_ts, end_ts
                FROM appointments
                WHERE status IN ('pending', 'confirmed')
                  AND start_ts < ?
                  AND end_ts > ?
                ORDER BY start_ts
                """,
                (range_end_ts + buffer_seconds, range_start_ts - buffer_seconds),
            ).fetchall()
        finally:
            connection.close()
        return [
            (int(row["start_ts"]) - buffer_seconds, int(row["end_ts"]) + buffer_seconds)
            for row in rows
        ]

    def get(self, appointment_id: int) -> Appointment | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
            ).fetchone()
            return self._from_row(row)
        finally:
            connection.close()

    def get_pending(self, limit: int = 50) -> list[Appointment]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM appointments
                WHERE status = 'pending'
                ORDER BY start_ts, id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                appointment
                for row in rows
                if (appointment := self._from_row(row)) is not None
            ]
        finally:
            connection.close()

    def decide(
        self, appointment_id: int, *, status: str, admin_id: str
    ) -> Appointment | None:
        if status not in {"confirmed", "declined"}:
            raise ValueError("Решение должно быть confirmed или declined")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE appointments
                SET status = ?, decided_at = ?, decided_by = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, int(time.time()), admin_id, appointment_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get(appointment_id)
