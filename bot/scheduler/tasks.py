"""Задача планировщика: взять готовые посты и разослать по каналам (ТЗ 4.2)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from aiogram import Bot

from bot.config import config
from bot.db import repository as repo
from bot.services import filter_service, notify_service, sender_service
from bot.utils import timeutil
from bot.utils.logger import logger

RETRY_PAUSE = 5  # сек между повторами (ТЗ 4.2)


async def process_queue(bot: Bot) -> None:
    """Один тик планировщика."""
    if await repo.get_setting_bool("pause_mode", False):
        return
    if not await filter_service.is_active_weekday():
        return

    # лимит постов в сутки
    max_per_day = await repo.get_setting_int("max_posts_per_day", 0)
    if max_per_day > 0 and await repo.sent_count_today() >= max_per_day:
        return

    now = await timeutil.now()
    posts = await repo.due_pending_posts(now)
    if not posts:
        return

    test_mode = await repo.get_setting_bool("test_mode", False)
    channels = await repo.list_channels(active_only=True)
    if not channels:
        logger.warning("Нет активных каналов-получателей")
        return

    for post in posts:
        await _process_one(bot, post, channels, test_mode)


async def _process_one(bot: Bot, post, channels, test_mode: bool) -> None:
    post_id = post["id"]

    # тихие часы (accumulate) — отложить, не меняя статус (останется pending)
    if await filter_service.in_quiet_hours():
        behavior = await repo.get_setting("quiet_hours_behavior", "accumulate")
        if behavior == "accumulate":
            logger.info(f"Пост {post_id}: тихие часы, откладываю")
            return

    # атомарный захват — защита от двойной отправки (другой тик/админ)
    if not await repo.claim_post(post_id):
        logger.debug(f"Пост {post_id} уже захвачен другим процессом — пропуск")
        return

    any_failed = False
    for channel in channels:
        if not filter_service.should_send_to_channel(post, channel):
            continue
        ok = await _send_with_retry(bot, post, channel, test_mode)
        any_failed = any_failed or not ok

    status = "failed" if any_failed else "sent"
    await repo.set_post_status(post_id, status, sent=True)

    if status == "sent":
        await notify_service.notify(
            bot, "notify_on_sent", f"✅ Пост #{post_id} отправлен во все каналы"
        )
    else:
        await notify_service.notify(
            bot, "notify_on_error", f"⚠️ Пост #{post_id} отправлен с ошибками"
        )


async def _send_with_retry(bot: Bot, post, channel, test_mode: bool) -> bool:
    attempts = await repo.get_setting_int("retry_attempts", config.retry_attempts)
    attempts = max(1, attempts)

    if test_mode:
        await repo.add_log(post["id"], channel["id"], "sent", "TEST_MODE")
        logger.info(f"[TEST] пост {post['id']} -> канал {channel['chat_id']}")
        return True

    last_err = ""
    for attempt in range(1, attempts + 1):
        try:
            await sender_service.send(bot, post, channel)
            await repo.add_log(post["id"], channel["id"], "sent")
            return True
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            logger.warning(
                f"Отправка поста {post['id']} -> {channel['chat_id']} "
                f"попытка {attempt}/{attempts}: {last_err}"
            )
            if attempt < attempts:
                await asyncio.sleep(RETRY_PAUSE)

    await repo.add_log(post["id"], channel["id"], "failed", last_err)
    await notify_service.notify(
        bot,
        "notify_on_error",
        f"❌ Ошибка отправки поста #{post['id']} в «{channel['title'] or channel['chat_id']}»:\n{last_err}",
    )
    return False


async def daily_prune_logs() -> None:
    deleted = await repo.prune_logs(days=90)
    if deleted:
        logger.info(f"Очистка логов: удалено {deleted} записей старше 90 дней")


async def daily_backup() -> None:
    await repo.backup_db(keep=7)


async def check_empty_queue(bot: Bot) -> None:
    """Алерт «очередь пуста более N часов» (ТЗ 5.7)."""
    if not await repo.get_setting_bool("notify_on_empty_queue", False):
        return

    if await repo.count_pending() > 0:
        await repo.set_setting("queue_empty_since", "")
        await repo.set_setting("queue_empty_alerted", "false")
        return

    since_raw = await repo.get_setting("queue_empty_since", "")
    now = await timeutil.now()
    if not since_raw:
        await repo.set_setting("queue_empty_since", now.strftime("%Y-%m-%d %H:%M:%S"))
        return

    if await repo.get_setting_bool("queue_empty_alerted", False):
        return

    try:
        since = datetime.fromisoformat(since_raw)
    except ValueError:
        return
    hours = await repo.get_setting_int("notify_empty_hours", 6)
    if now - since >= timedelta(hours=hours):
        await notify_service.notify(
            bot, "notify_on_empty_queue",
            f"📭 Очередь пуста уже более {hours} ч. Источники не публикуют новое?",
        )
        await repo.set_setting("queue_empty_alerted", "true")


async def daily_report(bot: Bot) -> None:
    """Ежедневный отчёт (ТЗ 5.7)."""
    if not await repo.get_setting_bool("notify_daily_report", False):
        return
    s = await repo.stats_since(1)
    by_ch = await repo.stats_by_channel(1)
    lines = [
        "📊 <b>Ежедневный отчёт</b> (за 24 ч)",
        f"Отправлено: {s['sent']} | Ошибок: {s['failed']} | В очереди: {s['in_queue']}",
    ]
    if by_ch:
        lines.append("\nПо каналам:")
        for r in by_ch:
            lines.append(f"  {r['title'] or r['chat_id']}: ✅{r['sent']} ❌{r['failed']}")
    await notify_service.notify(bot, "notify_daily_report", "\n".join(lines))
