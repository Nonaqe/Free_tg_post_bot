"""Точка входа: поднимает бота, БД, планировщик (ТЗ 2)."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import config
from bot.db import repository as repo
from bot.handlers import admin, listener
from bot.scheduler.scheduler import shutdown_scheduler, start_scheduler
from bot.services import notify_service
from bot.utils.autodelete import AutoDeleteMiddleware
from bot.utils.logger import logger


async def main() -> None:
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # автоудаление сообщений меню в ЛС (кроме последнего)
    bot.session.middleware(AutoDeleteMiddleware())

    dp = Dispatcher()
    # listener для каналов, admin для ЛС — порядок важен (admin ловит ЛС-команды)
    dp.include_router(admin.router)
    dp.include_router(listener.router)

    await repo.init_db()
    await start_scheduler(bot)

    me = await bot.get_me()
    logger.info(f"Бот @{me.username} запущен ({'webhook' if config.use_webhook else 'polling'})")
    await notify_service.notify(bot, "notify_on_restart", "🔄 Бот перезапущен и готов к работе")

    if config.use_webhook:
        await _run_webhook(bot, dp)
    else:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            await _shutdown(bot)


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    """Запуск через webhook (aiohttp) для прод-деплоя."""
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    url = config.webhook_base_url + config.webhook_path
    await bot.set_webhook(
        url,
        secret_token=config.webhook_secret or None,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=True,
    )
    logger.info(f"Webhook установлен: {url}")

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=config.webhook_secret or None
    ).register(app, path=config.webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=config.webapp_host, port=config.webapp_port)
    await site.start()
    logger.info(f"HTTP-сервер на {config.webapp_host}:{config.webapp_port}")
    try:
        await asyncio.Event().wait()  # держим процесс
    finally:
        await runner.cleanup()
        await _shutdown(bot)


async def _shutdown(bot: Bot) -> None:
    shutdown_scheduler()
    await repo.close_db()
    await bot.session.close()
    logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Выход по сигналу")
