"""Автоудаление сообщений бота в ЛС, чтобы не засорять чат.

Каждое отправленное в приватный чат сообщение через TTL удаляется — КРОМЕ
последнего на текущий момент. Посты в каналах (chat.type != private) не трогаются.
"""
from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.types import Message

from bot.db import repository as repo
from bot.utils.logger import logger


class AutoDeleteMiddleware:
    """Outer-middleware сессии бота: ловит исходящие сообщения."""

    def __init__(self) -> None:
        # chat_id -> message_id последнего сообщения бота в этом чате
        self._last: dict[int, int] = {}

    async def __call__(self, make_request, bot: Bot, method):
        result = await make_request(bot, method)
        try:
            await self._track(bot, result)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"autodelete: пропуск ({e})")
        return result

    async def _track(self, bot: Bot, result) -> None:
        if isinstance(result, Message):
            messages = [result]
        elif isinstance(result, list) and result and isinstance(result[0], Message):
            messages = result
        else:
            return

        ttl = await repo.get_setting_int("autodelete_seconds", 90)
        for m in messages:
            if m.chat.type != "private":
                continue  # каналы/группы не чистим
            self._last[m.chat.id] = m.message_id
            if ttl > 0:
                asyncio.create_task(self._delete_later(bot, m.chat.id, m.message_id, ttl))

    async def _delete_later(self, bot: Bot, chat_id: int, message_id: int, ttl: int) -> None:
        await asyncio.sleep(ttl)
        if self._last.get(chat_id) == message_id:
            return  # это последнее сообщение в чате — оставляем
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception:  # noqa: BLE001
            pass  # уже удалено / слишком старое / нет прав
