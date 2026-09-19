from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "да", "on"}


@dataclass(frozen=True)
class Settings:
    vk_group_token: str
    superadmin_vk_id: int
    timezone: ZoneInfo
    workdays: frozenset[int]
    report_time: time
    send_after_each_mark: bool
    vk_group_id: int | None
    database_path: Path
    template_path: Path
    reports_dir: Path


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    token = os.getenv("VK_GROUP_TOKEN", "").strip()
    superadmin_raw = os.getenv("SUPERADMIN_VK_ID", "").strip()
    if not token or token == "vk1.a.example":
        raise ValueError("Заполните VK_GROUP_TOKEN в файле .env")
    if not superadmin_raw.isdigit():
        raise ValueError("Заполните SUPERADMIN_VK_ID числовым VK ID главного администратора")

    hour, minute = map(int, os.getenv("REPORT_TIME", "08:29").split(":"))
    workdays = frozenset(int(x.strip()) for x in os.getenv("WORKDAYS", "1,2,3,4,5").split(","))
    group_raw = os.getenv("VK_GROUP_ID", "").strip()
    templates = sorted(ROOT.glob("*.xlsx"))
    if not templates:
        raise ValueError("В папке проекта не найден шаблон .xlsx")

    data_dir = Path(os.getenv("DATA_DIR", str(ROOT)))
    data_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        vk_group_token=token,
        superadmin_vk_id=int(superadmin_raw),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow")),
        workdays=workdays,
        report_time=time(hour, minute),
        send_after_each_mark=_bool("SEND_AFTER_EACH_MARK", True),
        vk_group_id=int(group_raw) if group_raw.isdigit() else None,
        database_path=data_dir / "attendance.sqlite3",
        template_path=templates[0],
        reports_dir=data_dir / "generated_reports",
    )
