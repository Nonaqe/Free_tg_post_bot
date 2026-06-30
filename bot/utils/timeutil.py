"""Работа с часовым поясом. Всё расписание считается в настроенном TZ.

`now()` возвращает наивное локальное время в выбранном поясе — в том же виде
хранится `scheduled_at`, поэтому сравнения консистентны независимо от TZ сервера.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.db import repository as repo

DEFAULT_TZ = "Europe/Moscow"


async def tz_name() -> str:
    return (await repo.get_setting("timezone", DEFAULT_TZ)) or DEFAULT_TZ


async def get_tz() -> ZoneInfo:
    try:
        return ZoneInfo(await tz_name())
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


async def now() -> datetime:
    """Наивное локальное время в настроенном поясе."""
    return datetime.now(await get_tz()).replace(tzinfo=None)


def is_valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def parse_hhmm(raw: str) -> time | None:
    try:
        h, m = raw.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None
