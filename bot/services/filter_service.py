"""Решает, нужно ли слать пост в конкретный канал (ТЗ 4.4)."""
from __future__ import annotations

import json
import re
from datetime import datetime, time

from bot.db import repository as repo
from bot.utils import timeutil
from bot.utils.media import MEDIA_NONE

_TAG_RE = re.compile(r"#(\w+)", re.UNICODE)


def extract_tags(text: str) -> set[str]:
    return {t.lower() for t in _TAG_RE.findall(text or "")}


def _parse_hhmm(raw: str) -> time | None:
    try:
        h, m = raw.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def passes_tags(post_content: str, tags_filter_json: str) -> bool:
    """Пустой фильтр — пропускать все. Иначе нужен хотя бы один тег."""
    try:
        allowed = [t.lower().lstrip("#") for t in json.loads(tags_filter_json or "[]")]
    except json.JSONDecodeError:
        allowed = []
    if not allowed:
        return True
    post_tags = extract_tags(post_content)
    return any(tag in post_tags for tag in allowed)


def passes_media(media_type: str, media_filter: str) -> bool:
    """media_filter: all / text_only / media_only."""
    if media_filter == "text_only":
        return media_type == MEDIA_NONE
    if media_filter == "media_only":
        return media_type != MEDIA_NONE
    return True  # all


def should_send_to_channel(post, channel) -> bool:
    return passes_tags(post["content"], channel["tags_filter"]) and passes_media(
        post["media_type"], channel["media_filter"]
    )


async def in_quiet_hours(now: datetime | None = None) -> bool:
    if not await repo.get_setting_bool("quiet_hours_enabled", True):
        return False
    now = now or await timeutil.now()
    start = _parse_hhmm(await repo.get_setting("quiet_hours_start", "23:00"))
    end = _parse_hhmm(await repo.get_setting("quiet_hours_end", "08:00"))
    if not start or not end:
        return False
    cur = now.time()
    if start <= end:
        return start <= cur < end
    # диапазон через полночь (23:00 -> 08:00)
    return cur >= start or cur < end


async def is_active_weekday(now: datetime | None = None) -> bool:
    now = now or await timeutil.now()
    raw = await repo.get_setting("active_weekdays", "0,1,2,3,4,5,6")
    try:
        days = {int(x) for x in raw.split(",") if x.strip() != ""}
    except ValueError:
        return True
    return now.weekday() in days
