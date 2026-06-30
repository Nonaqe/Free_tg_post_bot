"""Алерты и уведомления в рабочий чат (ТЗ 5.7 / 6)."""
from __future__ import annotations

from aiogram import Bot

from bot.db import repository as repo
from bot.utils.logger import logger


async def _target_chat() -> int | None:
    raw = await repo.get_setting("notify_chat_id", "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _send(bot: Bot, text: str) -> None:
    chat = await _target_chat()
    if chat is None:
        return
    try:
        await bot.send_message(chat, text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Не смог отправить уведомление: {e}")


async def alert(bot: Bot, text: str) -> None:
    """Безусловный алерт (ошибки инфраструктуры)."""
    await _send(bot, f"⚠️ {text}")


async def notify(bot: Bot, flag_key: str, text: str) -> None:
    """Уведомление, если соответствующий флаг включён в настройках."""
    if await repo.get_setting_bool(flag_key, False):
        await _send(bot, text)
