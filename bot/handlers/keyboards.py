"""Inline-клавиатуры админки (ТЗ 5)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Очередь", callback_data="menu:queue")
    kb.button(text="📤 На публикации", callback_data="menu:onpub")
    kb.button(text="📡 Каналы", callback_data="menu:channels")
    kb.button(text="🔧 Источники", callback_data="menu:sources")
    kb.button(text="⏰ Расписание", callback_data="menu:schedule")
    kb.button(text="📊 Статистика", callback_data="menu:stats")
    kb.button(text="👥 Администраторы", callback_data="menu:admins")
    kb.button(text="🔔 Уведомления", callback_data="menu:notify")
    kb.button(text="⚙️ Настройки", callback_data="menu:settings")
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def back_button(target: str = "menu:main") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=target)
    return kb.as_markup()


def menu_button() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Главное меню", callback_data="menu:main")
    return kb.as_markup()


def confirm(action: str, payload: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=f"confirm:{action}:{payload}")
    kb.button(text="❌ Нет", callback_data="confirm:cancel:")
    kb.adjust(2)
    return kb.as_markup()


# --- Очередь (листалка по одному посту) ---
def queue_item_kb(post_id: int, idx: int, total: int, is_due: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    # навигация
    kb.button(text="◀️", callback_data=f"q:nav:{max(0, idx - 1)}")
    kb.button(text=f"{idx + 1}/{total}", callback_data="q:noop")
    kb.button(text="▶️", callback_data=f"q:nav:{min(total - 1, idx + 1)}")
    # действия
    kb.button(text="✏️ Время", callback_data=f"q:time:{post_id}:{idx}")
    kb.button(text="✏️ Текст", callback_data=f"q:edit:{post_id}:{idx}")
    kb.button(text="👁 Превью", callback_data=f"q:prev:{post_id}:{idx}")
    kb.button(text="⏭ Пропустить", callback_data=f"q:skip:{post_id}:{idx}")
    kb.button(text="🗑 Удалить", callback_data=f"q:del:{post_id}:{idx}")
    kb.button(text="⬆️", callback_data=f"q:up:{post_id}:{idx}")
    if is_due:
        kb.button(text="⏸ Снять", callback_data=f"q:unpub:{post_id}:{idx}")
    else:
        kb.button(text="🚀 Сейчас", callback_data=f"q:pub:{post_id}:{idx}")
    kb.button(text="⬇️", callback_data=f"q:down:{post_id}:{idx}")
    kb.button(text="📑 Дублировать", callback_data=f"q:dup:{post_id}:{idx}")
    kb.button(text="🧹 Очистить всё", callback_data="q:clearall")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(3, 3, 2, 3, 2, 1)
    return kb.as_markup()


def queue_empty_kb() -> InlineKeyboardMarkup:
    return menu_button()


def on_publication_kb(posts) -> InlineKeyboardMarkup:
    """Список постов на публикации с кнопками отмены (для pending)."""
    kb = InlineKeyboardBuilder()
    for p in posts:
        if p["status"] == "pending":
            kb.button(text=f"❌ Отменить #{p['id']}", callback_data=f"op:cancel:{p['id']}")
    kb.button(text="🔄 Обновить", callback_data="menu:onpub")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def _slot_code(t) -> str:
    return f"{t.hour:02d}{t.minute:02d}"


def time_picker_kb(post_id: int, idx: int, slots) -> InlineKeyboardMarkup:
    """Выбор времени отправки: относительные сдвиги + слоты публикации."""
    kb = InlineKeyboardBuilder()
    for label, mins in (("+15 мин", 15), ("+30 мин", 30), ("+1 ч", 60)):
        kb.button(text=label, callback_data=f"q:tset:{mins}:{post_id}:{idx}")
    for label, mins in (("+3 ч", 180), ("+6 ч", 360), ("+12 ч", 720)):
        kb.button(text=label, callback_data=f"q:tset:{mins}:{post_id}:{idx}")
    kb.button(text="🎯 Ближайший слот", callback_data=f"q:tnext:{post_id}:{idx}")
    for t in slots:
        kb.button(text=f"🎯 {t.hour:02d}:{t.minute:02d}",
                  callback_data=f"q:tslot:{_slot_code(t)}:{post_id}:{idx}")
    kb.button(text="✏️ Вручную", callback_data=f"q:tman:{post_id}:{idx}")
    kb.button(text="⬅️ Очередь", callback_data="menu:queue")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(3, 3, 1, 3, 1, 2)
    return kb.as_markup()


def publish_targets_kb(post_id: int, idx: int, channels, selected: set,
                       when_label: str | None = None) -> InlineKeyboardMarkup:
    """Чекбоксы каналов + публикация сейчас / оставить по расписанию."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Во все — сейчас", callback_data=f"q:pall:{post_id}:{idx}")
    if when_label:
        kb.button(text=f"⏰ По расписанию: {when_label}", callback_data=f"q:keep:{post_id}:{idx}")
    for ch in channels:
        mark = "✅" if ch["id"] in selected else "▫️"
        title = ch["title"] or str(ch["chat_id"])
        kb.button(text=f"{mark} {title}", callback_data=f"q:pchk:{ch['id']}:{post_id}:{idx}")
    if selected:
        kb.button(text=f"🚀 В выбранные сейчас ({len(selected)})",
                  callback_data=f"q:pgo:{post_id}:{idx}")
    kb.button(text="⬅️ Очередь", callback_data="menu:queue")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


# --- Каналы ---
def channels_list_kb(channels) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in channels:
        mark = "🟢" if ch["is_active"] else "🔴"
        title = ch["title"] or str(ch["chat_id"])
        kb.button(text=f"{mark} {title}", callback_data=f"ch:view:{ch['id']}")
    kb.button(text="➕ Добавить", callback_data="ch:add")
    kb.button(text="⬅️ Меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def channel_view_kb(ch) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    toggle = "❌ Выключить" if ch["is_active"] else "✅ Включить"
    kb.button(text=toggle, callback_data=f"ch:toggle:{ch['id']}")
    kb.button(text="⏱ Задержка", callback_data=f"ch:set:{ch['id']}:delay")
    kb.button(text="🏷 Теги", callback_data=f"ch:set:{ch['id']}:tags")
    kb.button(text="🔼 Prefix", callback_data=f"ch:set:{ch['id']}:prefix")
    kb.button(text="🔽 Suffix", callback_data=f"ch:set:{ch['id']}:suffix")
    kb.button(text="🎛 Медиа-фильтр", callback_data=f"ch:media:{ch['id']}")
    kb.button(text="🧪 Тест-отправка", callback_data=f"ch:test:{ch['id']}")
    kb.button(text="🗑 Удалить", callback_data=f"ch:del:{ch['id']}")
    kb.button(text="⬅️ К списку", callback_data="menu:channels")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(1, 2, 2, 2, 1, 2)
    return kb.as_markup()


def media_filter_kb(channel_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Всё", callback_data=f"ch:mediaset:{channel_id}:all")
    kb.button(text="Только текст", callback_data=f"ch:mediaset:{channel_id}:text_only")
    kb.button(text="Только медиа", callback_data=f"ch:mediaset:{channel_id}:media_only")
    kb.button(text="⬅️ Назад", callback_data=f"ch:view:{channel_id}")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(3, 2)
    return kb.as_markup()


# --- Источники ---
def sources_list_kb(sources) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in sources:
        mark = "🟢" if s["is_active"] else "🟡"
        title = s["title"] or str(s["chat_id"])
        kb.button(text=f"{mark} {title}", callback_data=f"src:view:{s['id']}")
    kb.button(text="➕ Добавить", callback_data="src:add")
    kb.button(text="⬅️ Меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def source_view_kb(s) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    toggle = "⏸ Пауза" if s["is_active"] else "▶️ Включить"
    kb.button(text=toggle, callback_data=f"src:toggle:{s['id']}")
    kb.button(text="⏱ Задержка", callback_data=f"src:set:{s['id']}:default_delay_minutes")
    kb.button(text="📏 Мин. длина", callback_data=f"src:set:{s['id']}:min_post_length")
    fwd = "🚫 Пересланные: вкл" if s["ignore_forwarded"] else "✅ Пересланные: выкл"
    kb.button(text=fwd, callback_data=f"src:fwd:{s['id']}")
    kb.button(text="🗑 Удалить", callback_data=f"src:del:{s['id']}")
    kb.button(text="⬅️ К списку", callback_data="menu:sources")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(1, 2, 1, 1, 2)
    return kb.as_markup()


# --- Статистика ---
def stats_period_kb(period: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, val in (("Сегодня", "1"), ("7 дней", "7"), ("30 дней", "30")):
        mark = "• " if val == period else ""
        kb.button(text=f"{mark}{label}", callback_data=f"stats:period:{val}")
    kb.button(text="📤 Экспорт CSV", callback_data="stats:export")
    kb.button(text="⬅️ Меню", callback_data="menu:main")
    kb.adjust(3, 1, 1)
    return kb.as_markup()


# --- Администраторы ---
def admins_list_kb(admins) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for a in admins:
        name = a["username"] or str(a["telegram_id"])
        kb.button(text=f"{a['role']}: {name}", callback_data=f"adm:view:{a['telegram_id']}")
    kb.button(text="➕ Добавить", callback_data="adm:add")
    kb.button(text="📜 Лог действий", callback_data="adm:log")
    kb.button(text="⬅️ Меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def admin_view_kb(telegram_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="superadmin", callback_data=f"adm:role:{telegram_id}:superadmin")
    kb.button(text="editor", callback_data=f"adm:role:{telegram_id}:editor")
    kb.button(text="viewer", callback_data=f"adm:role:{telegram_id}:viewer")
    kb.button(text="🗑 Удалить", callback_data=f"adm:del:{telegram_id}")
    kb.button(text="⬅️ К списку", callback_data="menu:admins")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(3, 1, 2)
    return kb.as_markup()


# --- Расписание ---
def schedule_kb(behavior: str, quiet_enabled: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    toggle = "🔕 Тихие часы: ВКЛ" if quiet_enabled else "🔔 Тихие часы: ВЫКЛ"
    kb.button(text=toggle, callback_data="sch:quiet_toggle")
    kb.button(text="🌙 Начало", callback_data="sch:set:quiet_hours_start")
    kb.button(text="🌅 Конец", callback_data="sch:set:quiet_hours_end")
    beh = "Накапливать" if behavior == "accumulate" else "Пропускать"
    kb.button(text=f"В тихие часы: {beh}", callback_data="sch:behavior")
    kb.button(text="📅 Дни недели", callback_data="sch:weekdays")
    kb.button(text="⏲ Мин. интервал", callback_data="sch:set:min_interval_minutes")
    kb.button(text="🎯 Слоты публикации", callback_data="sch:slots")
    kb.button(text="🌍 Часовой пояс", callback_data="sch:set:timezone")
    kb.button(text="⬅️ Меню", callback_data="menu:main")
    kb.adjust(1, 2, 1, 1, 1, 1, 1, 1)
    return kb.as_markup()


def slots_kb(slots) -> InlineKeyboardMarkup:
    """Редактор слотов публикации: удалить существующие, добавить новый."""
    kb = InlineKeyboardBuilder()
    for t in slots:
        kb.button(text=f"🗑 {t.hour:02d}:{t.minute:02d}",
                  callback_data=f"sch:slotdel:{t.hour:02d}{t.minute:02d}")
    kb.button(text="➕ Добавить слот", callback_data="sch:slotadd")
    kb.button(text="⬅️ Назад", callback_data="menu:schedule")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(2, 2, 2, 2, 1, 2)
    return kb.as_markup()


def weekdays_kb(active: set[int]) -> InlineKeyboardMarkup:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    kb = InlineKeyboardBuilder()
    for i, n in enumerate(names):
        mark = "✅" if i in active else "▫️"
        kb.button(text=f"{mark}{n}", callback_data=f"sch:wd:{i}")
    kb.button(text="⬅️ Назад", callback_data="menu:schedule")
    kb.button(text="🏠 Меню", callback_data="menu:main")
    kb.adjust(4, 3, 2)
    return kb.as_markup()


# --- Уведомления ---
def notify_kb(flags: dict[str, bool]) -> InlineKeyboardMarkup:
    items = [
        ("notify_on_sent", "Пост отправлен"),
        ("notify_on_error", "Ошибка отправки"),
        ("notify_on_empty_queue", "Очередь пуста"),
        ("notify_on_restart", "Бот перезапущен"),
        ("notify_daily_report", "Ежедневный отчёт"),
    ]
    kb = InlineKeyboardBuilder()
    for key, label in items:
        mark = "✅" if flags.get(key) else "❌"
        kb.button(text=f"{mark} {label}", callback_data=f"ntf:toggle:{key}")
    kb.button(text="💬 Задать чат", callback_data="ntf:setchat")
    kb.button(text="🕘 Время отчёта", callback_data="ntf:reporttime")
    kb.button(text="⬅️ Меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


# --- Настройки ---
def settings_kb(pause: bool, test: bool, autodel: bool, moder: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    p = "▶️ Снять паузу" if pause else "⏸ Пауза"
    t = "🧪 Тест-режим: вкл" if test else "🧪 Тест-режим: выкл"
    a = "🧹 Автоочистка чата: вкл" if autodel else "🧹 Автоочистка чата: выкл"
    m = "📝 Ручная модерация: ВКЛ" if moder else "📝 Ручная модерация: выкл"
    kb.button(text=m, callback_data="set:moder")
    kb.button(text=p, callback_data="set:pause")
    kb.button(text=t, callback_data="set:test")
    kb.button(text=a, callback_data="set:autodel")
    kb.button(text="⏱ Задержка по умолч.", callback_data="set:edit:default_delay_minutes")
    kb.button(text="📈 Макс/сутки", callback_data="set:edit:max_posts_per_day")
    kb.button(text="🔁 Повторов при ошибке", callback_data="set:edit:retry_attempts")
    kb.button(text="♻️ Сбросить настройки", callback_data="set:reset")
    kb.button(text="⬅️ Меню", callback_data="menu:main")
    kb.adjust(1, 2, 1, 1, 1, 1, 1)
    return kb.as_markup()
