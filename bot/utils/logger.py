"""Настройка loguru: консоль + ротация файла."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from bot.config import BASE_DIR

_CONFIGURED = False


def setup_logger() -> "logger":
    global _CONFIGURED
    if _CONFIGURED:
        return logger

    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
        ),
    )
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "bot_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="DEBUG",
        encoding="utf-8",
        enqueue=True,
    )
    _CONFIGURED = True
    return logger


setup_logger()
