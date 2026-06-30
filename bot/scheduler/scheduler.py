"""Инициализация APScheduler (ТЗ 4.2)."""
from __future__ import annotations

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import config
from bot.db import repository as repo
from bot.scheduler.tasks import (
    check_empty_queue,
    daily_backup,
    daily_prune_logs,
    daily_report,
    process_queue,
)
from bot.utils import timeutil
from bot.utils.logger import logger

_scheduler: AsyncIOScheduler | None = None


async def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = await timeutil.tz_name()
    _scheduler = AsyncIOScheduler(timezone=tz)
    interval = config.scheduler_interval_seconds

    _scheduler.add_job(
        process_queue, "interval", seconds=interval, args=[bot],
        id="process_queue", max_instances=1, coalesce=True, replace_existing=True,
    )
    # ежедневная чистка логов (ТЗ 9: хранить 90 дней)
    _scheduler.add_job(daily_prune_logs, "cron", hour=4, minute=0,
                       id="prune_logs", replace_existing=True)
    # ежедневный бэкап БД
    _scheduler.add_job(daily_backup, "cron", hour=4, minute=10,
                       id="backup_db", replace_existing=True)
    # проверка «очередь пуста N часов»
    _scheduler.add_job(check_empty_queue, "interval", minutes=15, args=[bot],
                       id="empty_queue", max_instances=1, replace_existing=True)
    # ежедневный отчёт
    report_t = timeutil.parse_hhmm(await repo.get_setting("daily_report_time", "09:00"))
    rh, rm = (report_t.hour, report_t.minute) if report_t else (9, 0)
    _scheduler.add_job(daily_report, "cron", hour=rh, minute=rm, args=[bot],
                       id="daily_report", replace_existing=True)

    _scheduler.start()
    logger.info(f"Планировщик запущен (TZ {tz}, интервал {interval}с)")
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
