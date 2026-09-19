from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass(frozen=True)
class Employee:
    vk_id: int
    full_name: str
    personnel_number: str
    bureau_number: str
    active: bool
    approved: bool


@dataclass(frozen=True)
class Manager:
    vk_id: int
    full_name: str
    personnel_number: str
    bureau_number: str
    active: bool
    approved: bool


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._create_schema()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _create_schema(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS employees (
                    vk_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    personnel_number TEXT NOT NULL UNIQUE,
                    bureau_number TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    approved INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attendance (
                    work_date TEXT NOT NULL,
                    vk_id INTEGER NOT NULL REFERENCES employees(vk_id),
                    status TEXT NOT NULL,
                    marked_at TEXT NOT NULL,
                    PRIMARY KEY (work_date, vk_id)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS managers (
                    vk_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    personnel_number TEXT NOT NULL,
                    bureau_number TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    approved INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_manager_per_bureau
                    ON managers(bureau_number) WHERE active = 1 AND approved = 1;
                """
            )
            # Миграция базы предыдущей версии: ранее зарегистрированные
            # сотрудники уже считались допущенными к отметкам.
            columns = {row[1] for row in db.execute("PRAGMA table_info(employees)")}
            if "bureau_number" not in columns:
                db.execute("ALTER TABLE employees ADD COLUMN bureau_number TEXT NOT NULL DEFAULT ''")
            if "approved" not in columns:
                db.execute("ALTER TABLE employees ADD COLUMN approved INTEGER NOT NULL DEFAULT 1")

    @staticmethod
    def _employee(row: sqlite3.Row | None) -> Employee | None:
        return Employee(
            row["vk_id"], row["full_name"], row["personnel_number"],
            row["bureau_number"], bool(row["active"]), bool(row["approved"]),
        ) if row else None

    @staticmethod
    def _manager(row: sqlite3.Row | None) -> Manager | None:
        return Manager(
            row["vk_id"], row["full_name"], row["personnel_number"],
            row["bureau_number"], bool(row["active"]), bool(row["approved"]),
        ) if row else None

    def employee(self, vk_id: int) -> Employee | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM employees WHERE vk_id = ?", (vk_id,)).fetchone()
        return self._employee(row)

    def register(self, vk_id: int, full_name: str, personnel_number: str, bureau_number: str) -> Employee:
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self.connect() as db:
                db.execute(
                    """INSERT INTO employees(vk_id, full_name, personnel_number, bureau_number, active, approved, created_at)
                       VALUES (?, ?, ?, ?, 1, 0, ?)
                       ON CONFLICT(vk_id) DO UPDATE SET
                         full_name=excluded.full_name,
                         personnel_number=excluded.personnel_number,
                         bureau_number=excluded.bureau_number,
                         active=1,
                         approved=0""",
                    (vk_id, full_name, personnel_number, bureau_number, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Этот табельный номер уже зарегистрирован другим сотрудником") from exc
        return self.employee(vk_id)  # type: ignore[return-value]

    def employees(self, bureau_number: str | None = None) -> list[Employee]:
        with self.connect() as db:
            if bureau_number is None:
                rows = db.execute(
                    "SELECT * FROM employees WHERE active = 1 AND approved = 1 ORDER BY created_at, full_name"
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM employees
                       WHERE active = 1 AND approved = 1 AND bureau_number = ?
                       ORDER BY created_at, full_name""",
                    (bureau_number,),
                ).fetchall()
        return [self._employee(row) for row in rows if self._employee(row)]  # type: ignore[misc]

    def pending_employees(self, bureau_number: str | None = None) -> list[Employee]:
        with self.connect() as db:
            if bureau_number is None:
                rows = db.execute(
                    "SELECT * FROM employees WHERE active = 1 AND approved = 0 ORDER BY created_at, full_name"
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM employees
                       WHERE active = 1 AND approved = 0 AND bureau_number = ?
                       ORDER BY created_at, full_name""",
                    (bureau_number,),
                ).fetchall()
        return [self._employee(row) for row in rows if self._employee(row)]  # type: ignore[misc]

    def manager(self, vk_id: int) -> Manager | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM managers WHERE vk_id = ?", (vk_id,)).fetchone()
        return self._manager(row)

    def managers(self) -> list[Manager]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM managers WHERE active = 1 AND approved = 1 ORDER BY bureau_number, full_name"
            ).fetchall()
        return [self._manager(row) for row in rows if self._manager(row)]  # type: ignore[misc]

    def pending_managers(self) -> list[Manager]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM managers WHERE active = 1 AND approved = 0 ORDER BY created_at, full_name"
            ).fetchall()
        return [self._manager(row) for row in rows if self._manager(row)]  # type: ignore[misc]

    def manager_for_bureau(self, bureau_number: str) -> Manager | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT * FROM managers
                   WHERE bureau_number = ? AND active = 1 AND approved = 1""",
                (bureau_number,),
            ).fetchone()
        return self._manager(row)

    def request_manager(self, vk_id: int, full_name: str, personnel_number: str, bureau_number: str) -> Manager:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as db:
            db.execute(
                """INSERT INTO managers(vk_id, full_name, personnel_number, bureau_number, active, approved, created_at)
                   VALUES (?, ?, ?, ?, 1, 0, ?)
                   ON CONFLICT(vk_id) DO UPDATE SET
                     full_name=excluded.full_name,
                     personnel_number=excluded.personnel_number,
                     bureau_number=excluded.bureau_number,
                     active=1,
                     approved=0""",
                (vk_id, full_name, personnel_number, bureau_number, now),
            )
        return self.manager(vk_id)  # type: ignore[return-value]

    def approve_manager(self, vk_id: int) -> Manager | None:
        try:
            with self.connect() as db:
                db.execute("UPDATE managers SET approved = 1, active = 1 WHERE vk_id = ?", (vk_id,))
        except sqlite3.IntegrityError as exc:
            raise ValueError("За этим бюро уже закреплён другой руководитель") from exc
        return self.manager(vk_id)

    def reject_manager(self, vk_id: int) -> Manager | None:
        manager = self.manager(vk_id)
        if manager:
            with self.connect() as db:
                db.execute("UPDATE managers SET approved = 0, active = 0 WHERE vk_id = ?", (vk_id,))
        return manager

    def set_manager_name(self, vk_id: int, full_name: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE managers SET full_name = ? WHERE vk_id = ?", (full_name, vk_id))

    def approve(self, vk_id: int) -> Employee | None:
        with self.connect() as db:
            db.execute("UPDATE employees SET approved = 1, active = 1 WHERE vk_id = ?", (vk_id,))
        return self.employee(vk_id)

    def reject(self, vk_id: int) -> Employee | None:
        employee = self.employee(vk_id)
        if employee:
            with self.connect() as db:
                db.execute("UPDATE employees SET approved = 0, active = 0 WHERE vk_id = ?", (vk_id,))
        return employee

    def mark(self, vk_id: int, work_date: date, status: str, marked_at: datetime) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO attendance(work_date, vk_id, status, marked_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(work_date, vk_id) DO UPDATE SET
                     status=excluded.status, marked_at=excluded.marked_at""",
                (work_date.isoformat(), vk_id, status, marked_at.isoformat(timespec="seconds")),
            )

    def mark_unmarked_as_unknown(self, work_date: date, marked_at: datetime) -> None:
        """Вносит «Причина не выяснена» только тем допущенным, кто ещё не отметился."""
        with self.connect() as db:
            db.execute(
                """INSERT INTO attendance(work_date, vk_id, status, marked_at)
                   SELECT ?, vk_id, 'unknown', ?
                   FROM employees
                   WHERE active = 1 AND approved = 1
                     AND NOT EXISTS (
                         SELECT 1 FROM attendance
                         WHERE attendance.work_date = ? AND attendance.vk_id = employees.vk_id
                     )""",
                (work_date.isoformat(), marked_at.isoformat(timespec="seconds"), work_date.isoformat()),
            )

    def attendance(self, work_date: date, bureau_number: str | None = None) -> dict[int, str]:
        with self.connect() as db:
            if bureau_number is None:
                rows = db.execute(
                    "SELECT vk_id, status FROM attendance WHERE work_date = ?", (work_date.isoformat(),)
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT attendance.vk_id, attendance.status FROM attendance
                       JOIN employees ON employees.vk_id = attendance.vk_id
                       WHERE attendance.work_date = ? AND employees.bureau_number = ?""",
                    (work_date.isoformat(), bureau_number),
                ).fetchall()
        return {r["vk_id"]: r["status"] for r in rows}

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
