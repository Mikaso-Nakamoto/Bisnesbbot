from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def backup_database(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if source == destination:
        raise ValueError("Путь резервной копии должен отличаться от пути основной базы")
    if not source.is_file():
        raise FileNotFoundError(f"Не найдена SQLite-база: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    destination_connection = sqlite3.connect(destination, timeout=30)
    try:
        # SQLite backup API корректно копирует работающую базу в режиме WAL.
        source_connection.backup(destination_connection)
        result = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise sqlite3.DatabaseError(f"Проверка резервной копии не прошла: {result}")
    finally:
        destination_connection.close()
        source_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Безопасная резервная копия базы Bisnesbbot"
    )
    parser.add_argument("source", type=Path, help="Путь к основной SQLite-базе")
    parser.add_argument("destination", type=Path, help="Куда записать резервную копию")
    args = parser.parse_args()
    try:
        backup_database(args.source, args.destination)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Ошибка резервного копирования: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Резервная копия создана и проверена: {args.destination}")


if __name__ == "__main__":
    main()
