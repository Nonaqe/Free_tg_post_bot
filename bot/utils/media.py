"""Определение типа медиа в сообщении и извлечение file_id."""
from __future__ import annotations

from aiogram.types import Message

# Типы медиа из ТЗ: none / photo / video / document / album
MEDIA_NONE = "none"
MEDIA_PHOTO = "photo"
MEDIA_VIDEO = "video"
MEDIA_DOCUMENT = "document"
MEDIA_ALBUM = "album"


def detect_media_type(message: Message) -> str:
    """Одиночное сообщение -> тип медиа (без album, его собирает listener)."""
    if message.photo:
        return MEDIA_PHOTO
    if message.video:
        return MEDIA_VIDEO
    if message.document:
        return MEDIA_DOCUMENT
    return MEDIA_NONE


def extract_file_id(message: Message) -> str | None:
    """Вернуть file_id основного медиа сообщения, если есть."""
    if message.photo:
        return message.photo[-1].file_id  # самое крупное превью
    if message.video:
        return message.video.file_id
    if message.document:
        return message.document.file_id
    return None


def extract_text(message: Message) -> str:
    """Текст или подпись сообщения."""
    return message.text or message.caption or ""


def has_media(media_type: str) -> bool:
    return media_type != MEDIA_NONE
