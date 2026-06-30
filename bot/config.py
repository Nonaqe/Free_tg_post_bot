"""Конфигурация бота. Читает .env, валидирует обязательные поля."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise ValueError(f"Неверный ID в списке: {part!r}")
    return out


def _db_path_from_url(url: str) -> str:
    # sqlite:///bot.db  ->  bot.db ;  sqlite:////abs/path -> /abs/path
    prefix = "sqlite:///"
    if url.startswith(prefix):
        rel = url[len(prefix):]
        p = Path(rel)
        if not p.is_absolute():
            p = BASE_DIR / rel
        return str(p)
    return url


@dataclass(frozen=True)
class Config:
    bot_token: str
    source_channel_ids: list[int]
    admin_ids: list[int]
    database_url: str
    db_path: str
    default_delay_minutes: int
    scheduler_interval_seconds: int
    retry_attempts: int
    notify_chat_id: str = ""
    # webhook (если webhook_base_url пуст — работает polling)
    webhook_base_url: str = ""
    webhook_path: str = "/webhook"
    webhook_secret: str = ""
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_base_url)

    @classmethod
    def load(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token or token.startswith("123456:ABC"):
            raise RuntimeError("BOT_TOKEN не задан. Заполни .env (см. .env.example)")

        db_url = os.getenv("DATABASE_URL", "sqlite:///bot.db").strip()
        return cls(
            bot_token=token,
            source_channel_ids=_parse_int_list(os.getenv("SOURCE_CHANNEL_IDS")),
            admin_ids=_parse_int_list(os.getenv("ADMIN_IDS")),
            database_url=db_url,
            db_path=_db_path_from_url(db_url),
            default_delay_minutes=int(os.getenv("DEFAULT_DELAY_MINUTES", "30")),
            scheduler_interval_seconds=int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60")),
            retry_attempts=int(os.getenv("RETRY_ATTEMPTS", "3")),
            notify_chat_id=os.getenv("NOTIFY_CHAT_ID", "").strip(),
            webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/"),
            webhook_path=os.getenv("WEBHOOK_PATH", "/webhook").strip(),
            webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
            webapp_host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip(),
            webapp_port=int(os.getenv("WEBAPP_PORT", "8080")),
        )


config = Config.load()
