"""Логика очереди: расчёт времени отправки, перемещение, действия (ТЗ 4.1 / 5.1)."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from bot.db import repository as repo
from bot.services import filter_service
from bot.utils import timeutil


async def get_slots() -> list[time]:
    """Слоты публикации из настроек (отсортированы)."""
    raw = await repo.get_setting("time_slots", "09:00,15:00,21:00")
    slots = []
    for part in (raw or "").split(","):
        t = timeutil.parse_hhmm(part)
        if t:
            slots.append(t)
    return sorted(set(slots))


async def next_slot(base: datetime | None = None) -> datetime | None:
    """Ближайший слот публикации >= base. None если слотов нет."""
    base = base or await timeutil.now()
    slots = await get_slots()
    if not slots:
        return None
    for t in slots:
        cand = base.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if cand > base:
            return cand
    # все слоты сегодня прошли — первый слот завтра
    first = slots[0]
    return (base + timedelta(days=1)).replace(
        hour=first.hour, minute=first.minute, second=0, microsecond=0
    )


async def _shift_out_of_quiet(dt: datetime) -> datetime:
    """Если попадает в тихие часы и поведение=skip — сдвинуть на конец тихих часов."""
    behavior = await repo.get_setting("quiet_hours_behavior", "accumulate")
    if behavior != "skip":
        return dt
    if not await filter_service.in_quiet_hours(dt):
        return dt
    end_raw = await repo.get_setting("quiet_hours_end", "08:00")
    try:
        h, m = map(int, end_raw.split(":"))
    except ValueError:
        return dt
    end_t = time(h, m)
    candidate = dt.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)
    if candidate <= dt:
        candidate += timedelta(days=1)
    return candidate


async def compute_scheduled_at(source_chat_id: int, base: datetime | None = None) -> datetime:
    """now + задержка (источника или глобальная) с учётом min_interval и тихих часов."""
    base = base or await timeutil.now()

    src = await repo.get_source_by_chat(source_chat_id)
    delay = None
    if src and src["default_delay_minutes"] is not None:
        delay = src["default_delay_minutes"]
    if delay is None:
        delay = await repo.get_setting_int("default_delay_minutes", 30)

    scheduled = base + timedelta(minutes=delay)

    # минимальный интервал между постами в очереди
    min_interval = await repo.get_setting_int("min_interval_minutes", 0)
    if min_interval > 0:
        last = await _last_scheduled()
        if last and (scheduled - last) < timedelta(minutes=min_interval):
            scheduled = last + timedelta(minutes=min_interval)

    return await _shift_out_of_quiet(scheduled)


async def _last_scheduled() -> datetime | None:
    posts = await repo.list_pending(limit=1, offset=0)
    # list_pending сортирует по возрастанию; нужен максимум
    all_pending = await repo.list_pending(limit=10_000, offset=0)
    times = []
    for p in all_pending:
        if p["scheduled_at"]:
            try:
                times.append(datetime.fromisoformat(p["scheduled_at"]))
            except ValueError:
                pass
    return max(times) if times else None


async def move_post(post_id: int, direction: int) -> None:
    """direction: -1 вверх (раньше), +1 вниз (позже). Меняет местами scheduled_at."""
    pending = await repo.list_pending(limit=10_000, offset=0)
    ids = [p["id"] for p in pending]
    if post_id not in ids:
        return
    idx = ids.index(post_id)
    swap = idx + direction
    if swap < 0 or swap >= len(pending):
        return
    a, b = pending[idx], pending[swap]
    # посты без времени (ручная модерация) двигать нельзя — иначе получат время
    if not a["scheduled_at"] or not b["scheduled_at"]:
        return
    ta = datetime.fromisoformat(a["scheduled_at"])
    tb = datetime.fromisoformat(b["scheduled_at"])
    await repo.set_post_schedule(a["id"], tb)
    await repo.set_post_schedule(b["id"], ta)


async def send_now(post_id: int) -> None:
    """Обнулить расписание — уйдёт в ближайшем тике планировщика."""
    await repo.set_post_schedule(post_id, await timeutil.now() - timedelta(seconds=1))


async def reschedule(post_id: int, new_dt: datetime) -> None:
    await repo.set_post_schedule(post_id, new_dt)


async def skip_post(post_id: int) -> None:
    await repo.set_post_status(post_id, "skipped")


async def duplicate_to_end(post_id: int) -> int | None:
    last = await _last_scheduled()
    base = (last or await timeutil.now()) + timedelta(minutes=1)
    return await repo.duplicate_post(post_id, base)
