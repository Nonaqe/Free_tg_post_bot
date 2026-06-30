"""Отправка поста в канал с учётом типа медиа и настроек (ТЗ 4.3)."""
from __future__ import annotations

import asyncio
import json

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

from bot.utils.logger import logger
from bot.utils.media import (
    MEDIA_ALBUM,
    MEDIA_DOCUMENT,
    MEDIA_NONE,
    MEDIA_PHOTO,
    MEDIA_VIDEO,
)

RATE_LIMIT_PAUSE = 0.5  # сек между отправками


def _build_text(post, channel) -> str:
    parts = []
    if channel["prefix"]:
        parts.append(channel["prefix"])
    if post["content"]:
        parts.append(post["content"])
    if channel["suffix"]:
        parts.append(channel["suffix"])
    return "\n\n".join(parts)


def _file_ids(post) -> list[str]:
    try:
        return json.loads(post["media_file_ids"] or "[]")
    except json.JSONDecodeError:
        return []


async def send(bot: Bot, post, channel) -> None:
    """Отправить пост в канал. Бросает исключение при ошибке (ловит вызывающий)."""
    chat_id = channel["chat_id"]
    text = _build_text(post, channel)
    media_type = post["media_type"]
    files = _file_ids(post)

    try:
        await _dispatch(bot, chat_id, media_type, text, files)
    except TelegramRetryAfter as e:
        # FloodWait — ждать и повторить один раз (ТЗ 4.3 / 6)
        logger.warning(f"FloodWait {e.retry_after}s для chat={chat_id}, жду")
        await asyncio.sleep(e.retry_after + 1)
        await _dispatch(bot, chat_id, media_type, text, files)

    await asyncio.sleep(RATE_LIMIT_PAUSE)


async def _dispatch(bot: Bot, chat_id: int, media_type: str, text: str, files: list[str]) -> None:
    if media_type == MEDIA_NONE or not files:
        await bot.send_message(chat_id, text or "(пустой пост)")
        return
    if media_type == MEDIA_PHOTO:
        await bot.send_photo(chat_id, files[0], caption=text or None)
        return
    if media_type == MEDIA_VIDEO:
        await bot.send_video(chat_id, files[0], caption=text or None)
        return
    if media_type == MEDIA_DOCUMENT:
        await bot.send_document(chat_id, files[0], caption=text or None)
        return
    if media_type == MEDIA_ALBUM:
        await bot.send_media_group(chat_id, _build_album(files, text))
        return
    # неизвестный тип — fallback на текст
    await bot.send_message(chat_id, text or "(пустой пост)")


def _build_album(files: list[str], caption: str) -> list:
    """Альбом: подпись на первом элементе. По умолчанию фото."""
    media = []
    for i, fid in enumerate(files[:10]):  # Telegram лимит 10
        cap = caption if i == 0 and caption else None
        media.append(InputMediaPhoto(media=fid, caption=cap))
    return media
