"""Контроль доступа по ролям (ТЗ 5.6 / 9)."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from bot.db import repository as repo

ROLE_RANK = {"viewer": 1, "editor": 2, "superadmin": 3}


async def get_role(telegram_id: int) -> str | None:
    admin = await repo.get_admin(telegram_id)
    return admin["role"] if admin else None


async def has_access(telegram_id: int, min_role: str = "viewer") -> bool:
    role = await get_role(telegram_id)
    if role is None:
        return False
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[min_role]


class IsAdmin(BaseFilter):
    """Пропускает только пользователей из таблицы admins."""

    min_role: str = "viewer"

    def __init__(self, min_role: str = "viewer") -> None:
        self.min_role = min_role

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        if user is None:
            return False
        return await has_access(user.id, self.min_role)
