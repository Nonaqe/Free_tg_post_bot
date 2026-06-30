"""Слой доступа к данным. Одна aiosqlite-связь на процесс, row_factory=Row."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import aiosqlite

from bot.config import config
from bot.db.models import DEFAULT_SETTINGS, SCHEMA
from bot.utils.logger import logger

_db: aiosqlite.Connection | None = None

# Единый формат хранения дат — как у SQLite datetime('now'): "YYYY-MM-DD HH:MM:SS".
_DT_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _utc_since(days: int) -> str:
    """Граница периода в UTC — совпадает с datetime('now') в SQLite."""
    return _fmt(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days))


# --------------------------------------------------------------------------- #
#  Подключение / инициализация
# --------------------------------------------------------------------------- #
async def init_db() -> None:
    global _db
    _db = await aiosqlite.connect(config.db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA foreign_keys = ON")
    await _db.execute("PRAGMA journal_mode = WAL")
    for ddl in SCHEMA:
        await _db.execute(ddl)
    # миграция для существующих БД: добавить колонку, если её нет
    try:
        await _db.execute("ALTER TABLE posts ADD COLUMN published_by INTEGER")
    except Exception:  # noqa: BLE001  — колонка уже есть
        pass
    await _db.commit()
    await _seed_settings()
    await _seed_admins()
    await _seed_sources()
    recovered = await recover_sending()
    if recovered:
        logger.warning(f"Восстановлено зависших постов: {recovered}")
    logger.info(f"БД готова: {config.db_path}")


async def backup_db(keep: int = 7) -> str | None:
    """Консистентная копия БД через VACUUM INTO. Хранит последние `keep` копий."""
    from pathlib import Path

    backups = Path(config.db_path).resolve().parent / "backups"
    backups.mkdir(exist_ok=True)
    dest = backups / f"bot_{_fmt(datetime.now()).replace(':', '-').replace(' ', '_')}.db"
    try:
        await _conn().execute("VACUUM INTO ?", (str(dest),))
        await _conn().commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Бэкап не удался: {e}")
        return None

    # чистка старых
    files = sorted(backups.glob("bot_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    logger.info(f"Бэкап БД: {dest.name}")
    return str(dest)


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("init_db() не вызван")
    return _db


async def _seed_settings() -> None:
    for key, value in DEFAULT_SETTINGS.items():
        await _conn().execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    # перенос значений из .env при первом старте
    if config.notify_chat_id:
        await _conn().execute(
            "UPDATE settings SET value=? WHERE key='notify_chat_id' AND value=''",
            (config.notify_chat_id,),
        )
    await _conn().commit()


async def _seed_admins() -> None:
    """Первый ID из ADMIN_IDS становится superadmin, остальные — editor."""
    for i, tg_id in enumerate(config.admin_ids):
        role = "superadmin" if i == 0 else "editor"
        await _conn().execute(
            "INSERT OR IGNORE INTO admins (telegram_id, role) VALUES (?, ?)",
            (tg_id, role),
        )
    await _conn().commit()


async def _seed_sources() -> None:
    for chat_id in config.source_channel_ids:
        await _conn().execute(
            "INSERT OR IGNORE INTO sources (chat_id) VALUES (?)", (chat_id,)
        )
    await _conn().commit()


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #
async def get_setting(key: str, default: str | None = None) -> str | None:
    cur = await _conn().execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    await _conn().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    await _conn().commit()


async def get_setting_int(key: str, default: int = 0) -> int:
    raw = await get_setting(key)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


async def get_setting_bool(key: str, default: bool = False) -> bool:
    raw = await get_setting(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def all_settings() -> dict[str, str]:
    cur = await _conn().execute("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in await cur.fetchall()}


# --------------------------------------------------------------------------- #
#  Posts
# --------------------------------------------------------------------------- #
async def add_post(
    source_chat_id: int,
    source_msg_id: int,
    content: str,
    media_type: str,
    media_file_ids: Iterable[str],
    scheduled_at: datetime | None,
) -> int | None:
    """Создать пост. scheduled_at=None — пост ждёт решения (ручная модерация).

    Вернёт id или None если дубль (уникальный индекс).
    """
    try:
        cur = await _conn().execute(
            """INSERT INTO posts
               (source_chat_id, source_msg_id, content, media_type,
                media_file_ids, status, scheduled_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (
                source_chat_id,
                source_msg_id,
                content,
                media_type,
                json.dumps(list(media_file_ids)),
                _fmt(scheduled_at) if scheduled_at else None,
            ),
        )
        await _conn().commit()
        return cur.lastrowid
    except aiosqlite.IntegrityError:
        return None  # дубль


async def get_post(post_id: int) -> aiosqlite.Row | None:
    cur = await _conn().execute("SELECT * FROM posts WHERE id=?", (post_id,))
    return await cur.fetchone()


async def due_pending_posts(now: datetime) -> list[aiosqlite.Row]:
    cur = await _conn().execute(
        """SELECT * FROM posts
           WHERE status='pending' AND scheduled_at <= ?
           ORDER BY scheduled_at ASC, id ASC""",
        (_fmt(now),),
    )
    return list(await cur.fetchall())


async def list_pending(limit: int, offset: int) -> list[aiosqlite.Row]:
    cur = await _conn().execute(
        """SELECT * FROM posts WHERE status='pending'
           ORDER BY scheduled_at ASC, id ASC LIMIT ? OFFSET ?""",
        (limit, offset),
    )
    return list(await cur.fetchall())


async def count_pending() -> int:
    cur = await _conn().execute("SELECT COUNT(*) c FROM posts WHERE status='pending'")
    return (await cur.fetchone())["c"]


async def count_due(now: datetime) -> int:
    cur = await _conn().execute(
        "SELECT COUNT(*) c FROM posts WHERE status='pending' AND scheduled_at <= ?",
        (_fmt(now),),
    )
    return (await cur.fetchone())["c"]


async def claim_post(post_id: int) -> bool:
    """Атомарно захватить пост на отправку: pending -> sending.

    Возвращает True только одному вызывающему. Защита от двойной отправки,
    когда несколько админов (или админ и планировщик) берут один пост сразу.
    """
    cur = await _conn().execute(
        "UPDATE posts SET status='sending' WHERE id=? AND status='pending'", (post_id,)
    )
    await _conn().commit()
    return cur.rowcount == 1


async def release_post(post_id: int) -> None:
    """Вернуть пост в очередь, если отправка не удалась: sending -> pending."""
    await _conn().execute(
        "UPDATE posts SET status='pending' WHERE id=? AND status='sending'", (post_id,)
    )
    await _conn().commit()


async def recover_sending() -> int:
    """При старте вернуть зависшие в 'sending' посты в очередь (после краша)."""
    cur = await _conn().execute("UPDATE posts SET status='pending' WHERE status='sending'")
    await _conn().commit()
    return cur.rowcount


async def set_post_status(post_id: int, status: str, sent: bool = False) -> None:
    if sent:
        await _conn().execute(
            "UPDATE posts SET status=?, sent_at=datetime('now') WHERE id=?",
            (status, post_id),
        )
    else:
        await _conn().execute(
            "UPDATE posts SET status=? WHERE id=?", (status, post_id)
        )
    await _conn().commit()


async def set_post_schedule(post_id: int, scheduled_at: datetime) -> None:
    await _conn().execute(
        "UPDATE posts SET scheduled_at=? WHERE id=?",
        (_fmt(scheduled_at), post_id),
    )
    await _conn().commit()


async def set_published_by(post_id: int, telegram_id: int) -> None:
    await _conn().execute(
        "UPDATE posts SET published_by=? WHERE id=?", (telegram_id, post_id)
    )
    await _conn().commit()


async def on_publication_posts() -> list[aiosqlite.Row]:
    """Все посты, ожидающие отправки: pending (по расписанию) + sending."""
    cur = await _conn().execute(
        """SELECT * FROM posts
           WHERE status IN ('pending', 'sending')
           ORDER BY scheduled_at ASC, id ASC"""
    )
    return list(await cur.fetchall())


async def update_post_content(post_id: int, content: str) -> None:
    await _conn().execute("UPDATE posts SET content=? WHERE id=?", (content, post_id))
    await _conn().commit()


async def delete_post(post_id: int) -> None:
    await _conn().execute("DELETE FROM posts WHERE id=?", (post_id,))
    await _conn().commit()


async def clear_pending() -> int:
    cur = await _conn().execute("DELETE FROM posts WHERE status='pending'")
    await _conn().commit()
    return cur.rowcount


async def duplicate_post(post_id: int, scheduled_at: datetime) -> int | None:
    src = await get_post(post_id)
    if not src:
        return None
    cur = await _conn().execute(
        """INSERT INTO posts
           (source_chat_id, source_msg_id, content, media_type,
            media_file_ids, status, scheduled_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
        (
            src["source_chat_id"],
            # сдвиг msg_id чтобы не словить уникальный индекс
            -abs(src["source_msg_id"]) * 1000 - (post_id % 1000),
            src["content"],
            src["media_type"],
            src["media_file_ids"],
            _fmt(scheduled_at),
        ),
    )
    await _conn().commit()
    return cur.lastrowid


async def latest_post() -> aiosqlite.Row | None:
    cur = await _conn().execute("SELECT * FROM posts ORDER BY id DESC LIMIT 1")
    return await cur.fetchone()


# --------------------------------------------------------------------------- #
#  Channels
# --------------------------------------------------------------------------- #
async def add_channel(chat_id: int, title: str = "") -> int:
    cur = await _conn().execute(
        "INSERT INTO channels (chat_id, title) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title "
        "RETURNING id",
        (chat_id, title),
    )
    row = await cur.fetchone()
    await _conn().commit()
    return row["id"]


async def list_channels(active_only: bool = False) -> list[aiosqlite.Row]:
    q = "SELECT * FROM channels"
    if active_only:
        q += " WHERE is_active=1"
    q += " ORDER BY id ASC"
    cur = await _conn().execute(q)
    return list(await cur.fetchall())


async def get_channel(channel_id: int) -> aiosqlite.Row | None:
    cur = await _conn().execute("SELECT * FROM channels WHERE id=?", (channel_id,))
    return await cur.fetchone()


async def update_channel(channel_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    await _conn().execute(
        f"UPDATE channels SET {cols} WHERE id=?",
        (*fields.values(), channel_id),
    )
    await _conn().commit()


async def toggle_channel(channel_id: int) -> None:
    await _conn().execute(
        "UPDATE channels SET is_active = 1 - is_active WHERE id=?", (channel_id,)
    )
    await _conn().commit()


async def delete_channel(channel_id: int) -> None:
    await _conn().execute("DELETE FROM channels WHERE id=?", (channel_id,))
    await _conn().commit()


# --------------------------------------------------------------------------- #
#  Sources
# --------------------------------------------------------------------------- #
async def add_source(chat_id: int, title: str = "") -> int:
    cur = await _conn().execute(
        "INSERT INTO sources (chat_id, title) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title "
        "RETURNING id",
        (chat_id, title),
    )
    row = await cur.fetchone()
    await _conn().commit()
    return row["id"]


async def list_sources(active_only: bool = False) -> list[aiosqlite.Row]:
    q = "SELECT * FROM sources"
    if active_only:
        q += " WHERE is_active=1"
    q += " ORDER BY id ASC"
    cur = await _conn().execute(q)
    return list(await cur.fetchall())


async def get_source_by_chat(chat_id: int) -> aiosqlite.Row | None:
    cur = await _conn().execute("SELECT * FROM sources WHERE chat_id=?", (chat_id,))
    return await cur.fetchone()


async def get_source(source_id: int) -> aiosqlite.Row | None:
    cur = await _conn().execute("SELECT * FROM sources WHERE id=?", (source_id,))
    return await cur.fetchone()


async def update_source(source_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    await _conn().execute(
        f"UPDATE sources SET {cols} WHERE id=?", (*fields.values(), source_id)
    )
    await _conn().commit()


async def toggle_source(source_id: int) -> None:
    await _conn().execute(
        "UPDATE sources SET is_active = 1 - is_active WHERE id=?", (source_id,)
    )
    await _conn().commit()


async def delete_source(source_id: int) -> None:
    await _conn().execute("DELETE FROM sources WHERE id=?", (source_id,))
    await _conn().commit()


async def is_known_source(chat_id: int) -> bool:
    cur = await _conn().execute(
        "SELECT 1 FROM sources WHERE chat_id=? AND is_active=1", (chat_id,)
    )
    return await cur.fetchone() is not None


# --------------------------------------------------------------------------- #
#  Post logs
# --------------------------------------------------------------------------- #
async def add_log(post_id: int, channel_id: int, status: str, error_msg: str | None = None) -> None:
    await _conn().execute(
        "INSERT INTO post_logs (post_id, channel_id, status, error_msg) VALUES (?, ?, ?, ?)",
        (post_id, channel_id, status, error_msg),
    )
    await _conn().commit()


async def stats_since(days: int) -> dict[str, int]:
    since = _utc_since(days)
    cur = await _conn().execute(
        """SELECT status, COUNT(*) c FROM post_logs
           WHERE sent_at >= ? GROUP BY status""",
        (since,),
    )
    rows = await cur.fetchall()
    out = {"sent": 0, "failed": 0}
    for r in rows:
        out[r["status"]] = r["c"]
    out["in_queue"] = await count_pending()
    cur2 = await _conn().execute(
        "SELECT COUNT(*) c FROM posts WHERE status='skipped' AND created_at >= ?",
        (since,),
    )
    out["skipped"] = (await cur2.fetchone())["c"]
    return out


async def stats_by_channel(days: int) -> list[aiosqlite.Row]:
    cur = await _conn().execute(
        """SELECT c.title, c.chat_id,
                  SUM(CASE WHEN l.status='sent' THEN 1 ELSE 0 END) sent,
                  SUM(CASE WHEN l.status='failed' THEN 1 ELSE 0 END) failed
           FROM post_logs l JOIN channels c ON c.id = l.channel_id
           WHERE l.sent_at >= ?
           GROUP BY l.channel_id ORDER BY sent DESC""",
        (_utc_since(days),),
    )
    return list(await cur.fetchall())


async def recent_errors(limit: int = 10) -> list[aiosqlite.Row]:
    cur = await _conn().execute(
        """SELECT l.*, c.title FROM post_logs l
           LEFT JOIN channels c ON c.id = l.channel_id
           WHERE l.status='failed' ORDER BY l.sent_at DESC LIMIT ?""",
        (limit,),
    )
    return list(await cur.fetchall())


async def all_logs() -> list[aiosqlite.Row]:
    cur = await _conn().execute(
        """SELECT l.id, l.post_id, l.channel_id, c.title channel_title,
                  l.status, l.error_msg, l.sent_at
           FROM post_logs l LEFT JOIN channels c ON c.id=l.channel_id
           ORDER BY l.sent_at DESC"""
    )
    return list(await cur.fetchall())


async def sent_count_today() -> int:
    cur = await _conn().execute(
        "SELECT COUNT(*) c FROM post_logs WHERE status='sent' AND date(sent_at)=date('now')"
    )
    return (await cur.fetchone())["c"]


async def prune_logs(days: int = 90) -> int:
    """ТЗ 9: хранить post_logs минимум 90 дней."""
    cutoff = _utc_since(days)
    cur = await _conn().execute("DELETE FROM post_logs WHERE sent_at < ?", (cutoff,))
    await _conn().commit()
    return cur.rowcount


# --------------------------------------------------------------------------- #
#  Admins
# --------------------------------------------------------------------------- #
async def list_admins() -> list[aiosqlite.Row]:
    cur = await _conn().execute("SELECT * FROM admins ORDER BY id ASC")
    return list(await cur.fetchall())


async def get_admin(telegram_id: int) -> aiosqlite.Row | None:
    cur = await _conn().execute(
        "SELECT * FROM admins WHERE telegram_id=?", (telegram_id,)
    )
    return await cur.fetchone()


async def add_admin(telegram_id: int, role: str = "viewer", username: str | None = None) -> None:
    await _conn().execute(
        "INSERT INTO admins (telegram_id, role, username) VALUES (?, ?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET role=excluded.role",
        (telegram_id, role, username),
    )
    await _conn().commit()


async def set_admin_role(telegram_id: int, role: str) -> None:
    await _conn().execute(
        "UPDATE admins SET role=? WHERE telegram_id=?", (role, telegram_id)
    )
    await _conn().commit()


async def delete_admin(telegram_id: int) -> None:
    await _conn().execute("DELETE FROM admins WHERE telegram_id=?", (telegram_id,))
    await _conn().commit()


async def count_admins() -> int:
    cur = await _conn().execute("SELECT COUNT(*) c FROM admins")
    return (await cur.fetchone())["c"]


async def log_admin_action(telegram_id: int, action: str, details: str = "") -> None:
    await _conn().execute(
        "INSERT INTO admin_actions (telegram_id, action, details) VALUES (?, ?, ?)",
        (telegram_id, action, details),
    )
    # хранить 30 дней
    cutoff = _utc_since(30)
    await _conn().execute("DELETE FROM admin_actions WHERE created_at < ?", (cutoff,))
    await _conn().commit()


async def recent_admin_actions(limit: int = 15) -> list[aiosqlite.Row]:
    cur = await _conn().execute(
        "SELECT * FROM admin_actions ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return list(await cur.fetchall())
