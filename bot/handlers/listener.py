"""Слушает каналы-источники, копит посты в очередь (ТЗ 4.1)."""
from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.db import repository as repo
from bot.services import queue_service
from bot.utils.logger import logger
from bot.utils.media import (
    MEDIA_ALBUM,
    detect_media_type,
    extract_file_id,
    extract_text,
)

router = Router(name="listener")

ALBUM_DELAY = 1.5  # сек на сбор медиагруппы (ТЗ 4.1 / 6)

# media_group_id -> {"messages": [...], "task": Task}
_album_buffer: dict[str, dict] = {}


@router.channel_post()
async def on_channel_post(message: Message, bot: Bot) -> None:
    source_chat_id = message.chat.id

    if not await repo.is_known_source(source_chat_id):
        return  # не наш источник или выключен

    # игнор пересланных, если включено для источника
    src = await repo.get_source_by_chat(source_chat_id)
    ignore_fwd = (src and src["ignore_forwarded"]) or await repo.get_setting_bool(
        "ignore_forwarded", False
    )
    if ignore_fwd and (message.forward_origin or message.forward_from_chat):
        return

    if message.media_group_id:
        await _buffer_album(message)
        return

    await _save_single(message, source_chat_id)


async def _save_single(message: Message, source_chat_id: int) -> None:
    content = extract_text(message)
    media_type = detect_media_type(message)
    file_id = extract_file_id(message)
    file_ids = [file_id] if file_id else []

    if not await _passes_min_length(content, media_type, source_chat_id):
        logger.info(f"Пост {message.message_id} короче минимума — пропущен")
        return

    scheduled = await _schedule_for(source_chat_id)
    post_id = await repo.add_post(
        source_chat_id=source_chat_id,
        source_msg_id=message.message_id,
        content=content,
        media_type=media_type,
        media_file_ids=file_ids,
        scheduled_at=scheduled,
    )
    if post_id is None:
        logger.debug(f"Дубль поста {message.message_id} проигнорирован")
    elif scheduled:
        logger.info(f"Пост #{post_id} в очереди, отправка {scheduled:%Y-%m-%d %H:%M}")
    else:
        logger.info(f"Пост #{post_id} ждёт решения (модерация)")


async def _buffer_album(message: Message) -> None:
    gid = message.media_group_id
    entry = _album_buffer.get(gid)
    if entry is None:
        entry = {"messages": [], "task": None}
        _album_buffer[gid] = entry
    entry["messages"].append(message)

    if entry["task"]:
        entry["task"].cancel()
    entry["task"] = asyncio.create_task(_flush_album(gid))


async def _flush_album(gid: str) -> None:
    try:
        await asyncio.sleep(ALBUM_DELAY)
    except asyncio.CancelledError:
        return
    entry = _album_buffer.pop(gid, None)
    if not entry or not entry["messages"]:
        return

    messages = sorted(entry["messages"], key=lambda m: m.message_id)
    first = messages[0]
    source_chat_id = first.chat.id

    content = ""
    file_ids: list[str] = []
    for m in messages:
        if not content:
            content = extract_text(m)
        fid = extract_file_id(m)
        if fid:
            file_ids.append(fid)

    if not await _passes_min_length(content, MEDIA_ALBUM, source_chat_id):
        return

    scheduled = await _schedule_for(source_chat_id)
    post_id = await repo.add_post(
        source_chat_id=source_chat_id,
        source_msg_id=first.message_id,
        content=content,
        media_type=MEDIA_ALBUM,
        media_file_ids=file_ids,
        scheduled_at=scheduled,
    )
    if post_id:
        logger.info(f"Альбом #{post_id} ({len(file_ids)} медиа) "
                    f"{'в очереди' if scheduled else 'ждёт решения'}")


async def _schedule_for(source_chat_id: int):
    """Время отправки нового поста; None — ручная модерация (ждёт решения)."""
    if await repo.get_setting_bool("moderation_mode", False):
        return None
    return await queue_service.compute_scheduled_at(source_chat_id)


async def _passes_min_length(content: str, media_type: str, source_chat_id: int) -> bool:
    src = await repo.get_source_by_chat(source_chat_id)
    if src and src["min_post_length"] is not None:
        min_len = src["min_post_length"]
    else:
        min_len = await repo.get_setting_int("min_post_length", 0)
    if min_len <= 0:
        return True
    # медиа-посты с коротким текстом всё равно пропускаем
    if media_type != "none":
        return True
    return len(content) >= min_len
