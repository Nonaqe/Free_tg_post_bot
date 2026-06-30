"""DDL схемы БД и значения настроек по умолчанию (ТЗ раздел 3)."""
from __future__ import annotations

# Порядок важен: таблицы с FK создаются после родительских.
SCHEMA: list[str] = [
    # 3.1 posts — очередь постов
    """
    CREATE TABLE IF NOT EXISTS posts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_chat_id  INTEGER NOT NULL,
        source_msg_id   INTEGER NOT NULL,
        content         TEXT    NOT NULL DEFAULT '',
        media_type      TEXT    NOT NULL DEFAULT 'none',
        media_file_ids  TEXT    NOT NULL DEFAULT '[]',
        status          TEXT    NOT NULL DEFAULT 'pending',
        scheduled_at    TEXT,
        sent_at         TEXT,
        published_by    INTEGER,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # защита от дублей: один пост из источника = одна запись
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_source
        ON posts (source_chat_id, source_msg_id)
    """,
    "CREATE INDEX IF NOT EXISTS ix_posts_status_sched ON posts (status, scheduled_at)",

    # 3.2 channels — каналы-получатели
    """
    CREATE TABLE IF NOT EXISTS channels (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id         INTEGER NOT NULL UNIQUE,
        title           TEXT    NOT NULL DEFAULT '',
        delay_minutes   INTEGER NOT NULL DEFAULT 0,
        is_active       INTEGER NOT NULL DEFAULT 1,
        tags_filter     TEXT    NOT NULL DEFAULT '[]',
        prefix          TEXT    NOT NULL DEFAULT '',
        suffix          TEXT    NOT NULL DEFAULT '',
        media_filter    TEXT    NOT NULL DEFAULT 'all',
        created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # доп. таблица: источники (раздел 5.4 — управление источниками)
    """
    CREATE TABLE IF NOT EXISTS sources (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id             INTEGER NOT NULL UNIQUE,
        title               TEXT    NOT NULL DEFAULT '',
        is_active           INTEGER NOT NULL DEFAULT 1,
        default_delay_minutes INTEGER,
        min_post_length     INTEGER,
        ignore_forwarded    INTEGER NOT NULL DEFAULT 0,
        created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # 3.3 post_logs — история отправок
    """
    CREATE TABLE IF NOT EXISTS post_logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id     INTEGER REFERENCES posts(id) ON DELETE SET NULL,
        channel_id  INTEGER REFERENCES channels(id) ON DELETE SET NULL,
        status      TEXT    NOT NULL,
        error_msg   TEXT,
        sent_at     TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_post_logs_sent_at ON post_logs (sent_at)",

    # 3.4 admins — администраторы
    """
    CREATE TABLE IF NOT EXISTS admins (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id  INTEGER NOT NULL UNIQUE,
        username     TEXT,
        role         TEXT    NOT NULL DEFAULT 'viewer',
        added_at     TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # лог действий администраторов (раздел 5.6, хранить 30 дней)
    """
    CREATE TABLE IF NOT EXISTS admin_actions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id  INTEGER NOT NULL,
        action       TEXT    NOT NULL,
        details      TEXT,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # 3.5 settings — глобальные настройки (key-value)
    """
    CREATE TABLE IF NOT EXISTS settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    )
    """,
]

# Значения по умолчанию (ТЗ 3.5). Хранятся строками.
DEFAULT_SETTINGS: dict[str, str] = {
    "default_delay_minutes": "30",
    "quiet_hours_enabled": "true",  # глобальный вкл/выкл тихих часов
    "quiet_hours_start": "23:00",
    "quiet_hours_end": "08:00",
    "quiet_hours_behavior": "accumulate",  # accumulate / skip
    "min_post_length": "0",
    "max_posts_per_day": "0",
    "scheduler_interval_sec": "60",
    "retry_attempts": "3",
    "pause_mode": "false",
    "test_mode": "false",
    "notify_chat_id": "",
    "active_weekdays": "0,1,2,3,4,5,6",  # 0=Пн .. 6=Вс
    "min_interval_minutes": "0",
    "ignore_forwarded": "false",
    # уведомления (раздел 5.7)
    "notify_on_sent": "false",
    "notify_on_error": "true",
    "notify_on_empty_queue": "false",
    "notify_empty_hours": "6",
    "notify_on_restart": "true",
    "notify_daily_report": "false",
    "daily_report_time": "09:00",  # время ежедневного отчёта
    # автоудаление сообщений меню в ЛС (сек); 0 = выключено. Последнее не удаляется.
    "autodelete_seconds": "90",
    # ручная модерация: новые посты ждут решения, не уходят сами
    "moderation_mode": "false",
    # часовой пояс для расписания и отображения времени
    "timezone": "Europe/Moscow",
    # слоты публикации по времени (через запятую, формат ЧЧ:ММ)
    "time_slots": "09:00,15:00,21:00",
    # служебное: когда очередь стала пустой (для алерта) и был ли алерт послан
    "queue_empty_since": "",
    "queue_empty_alerted": "false",
}
