"""Inline-админка: меню и все разделы (ТЗ 5)."""
from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.db import repository as repo
from bot.handlers import keyboards as kb
from bot.handlers.access import IsAdmin, get_role, has_access
from bot.services import queue_service, sender_service
from bot.utils import media as M
from bot.utils import timeutil
from bot.utils.logger import logger

# выбор каналов для публикации: (user_id, post_id) -> set(channel_id)
_publish_sel: dict[tuple[int, int], set[int]] = {}

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 5
WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DIV = "──────────"
MEDIA_ICON = {"none": "📝", "photo": "🖼", "video": "🎬", "document": "📎", "album": "🖼"}


def _crumb(path: str) -> str:
    """Заголовок-хлебная крошка: 🏠 ▸ Раздел."""
    return f"🏠 ▸ {path}"


def _bar(part: int, total: int, cells: int = 10) -> str:
    """ASCII-полоса прогресса для статистики."""
    pct = (part / total) if total else 0
    filled = round(pct * cells)
    return "█" * filled + "░" * (cells - filled) + f" {round(pct * 100)}%"


async def _dashboard_text(user_id: int) -> str:
    role = await get_role(user_id)
    now = await timeutil.now()
    pending = await repo.count_pending()
    due = await repo.count_due(now)
    channels = await repo.list_channels()
    active = sum(1 for c in channels if c["is_active"])
    sent = await repo.sent_count_today()

    if await repo.get_setting_bool("pause_mode"):
        state = "🟡 Пауза"
    elif await repo.get_setting_bool("test_mode"):
        state = "🧪 Тест-режим"
    else:
        state = "🟢 Активен"

    return (
        "🤖 <b>Планировщик новостей</b>\n"
        f"{DIV}\n"
        f"{state} · <i>{role}</i>\n"
        f"📋 В очереди: <b>{pending}</b>  ·  📤 К отправке: <b>{due}</b>\n"
        f"📡 Каналы: <b>{active}/{len(channels)}</b>  ·  📨 Сегодня: <b>{sent}</b>\n"
        f"{DIV}"
    )


class Await(StatesGroup):
    """Одно состояние ожидания текстового ввода; вид — в data['kind']."""
    input = State()


# --------------------------------------------------------------------------- #
#  Хелперы
# --------------------------------------------------------------------------- #
async def _ask(callback: CallbackQuery, state: FSMContext, kind: str, prompt: str, **ctx) -> None:
    await state.set_state(Await.input)
    await state.update_data(kind=kind, **ctx)
    await callback.message.answer(prompt)
    await callback.answer()


def _short(text: str, n: int = 60) -> str:
    text = (text or "").replace("\n", " ")
    return text[:n] + "…" if len(text) > n else text or "(без текста)"


def _is_held(post) -> bool:
    """Пост на ручной модерации — без времени, ждёт решения человека."""
    return not post["scheduled_at"]


def _is_due(post, now: datetime | None = None) -> bool:
    """Пост уже ждёт ближайшего тика планировщика (scheduled_at <= сейчас)."""
    if not post["scheduled_at"]:
        return False  # held — сам не уходит
    now = now or datetime.now()
    try:
        return datetime.fromisoformat(post["scheduled_at"]) <= now
    except ValueError:
        return False


def _when_label(post) -> str | None:
    if not post or not post["scheduled_at"]:
        return None
    try:
        return f"{datetime.fromisoformat(post['scheduled_at']):%d.%m %H:%M}"
    except ValueError:
        return None


def _post_badge(post, now: datetime | None = None) -> str:
    if _is_held(post):
        return "⏸ Ожидает: задай «✏️ Время» или «🚀 Сейчас»"
    if _is_due(post, now):
        return "📤 Отправляется (ближайший тик)"
    when = post["scheduled_at"][:16].replace("T", " ")
    return f"🕐 Запланирован на {when}"


def _parse_dt(raw: str, now: datetime) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if fmt == "%H:%M":
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
                if dt < now:
                    dt += timedelta(days=1)
            return dt
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
#  Команды (Фаза 1: /queue /pause /resume)
# --------------------------------------------------------------------------- #
@router.message(Command("start", "menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer(await _dashboard_text(message.from_user.id), reply_markup=kb.main_menu())


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    if not await has_access(message.from_user.id, "editor"):
        return
    await repo.set_setting("pause_mode", "true")
    await message.answer("⏸ Пауза включена — отправки остановлены.")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if not await has_access(message.from_user.id, "editor"):
        return
    await repo.set_setting("pause_mode", "false")
    await message.answer("▶️ Пауза снята.")


@router.message(Command("queue"))
async def cmd_queue(message: Message, bot: Bot) -> None:
    await _open_queue(bot, message.chat.id, 0)


# --------------------------------------------------------------------------- #
#  Главное меню
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:main")
async def cb_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = await _dashboard_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb.main_menu())
    except Exception:  # noqa: BLE001  — текущее сообщение медиа, редактировать нельзя
        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            pass
        await callback.message.answer(text, reply_markup=kb.main_menu())
    await callback.answer()


# --------------------------------------------------------------------------- #
#  5.1 Очередь — листалка по одному посту
# --------------------------------------------------------------------------- #
async def _queue_caption(p, idx: int, total: int, now: datetime) -> tuple[str, object]:
    """Подпись карточки (≤1024 для медиа) + клавиатура."""
    due = _is_due(p, now)
    files = json.loads(p["media_file_ids"] or "[]")
    icon = MEDIA_ICON.get(p["media_type"], "📄")
    media_info = f"{icon} {p['media_type']}" + (f" ×{len(files)}" if files else "")
    raw = p["content"] or ""
    body = (html.escape(raw[:600]) + ("…" if len(raw) > 600 else "")) if raw else "<i>(без текста)</i>"
    text = (
        f"{_crumb(f'Очередь · {idx + 1}/{total}')}\n"
        f"{DIV}\n"
        f"🆔 <code>#{p['id']}</code> · {media_info}\n"
        f"{_post_badge(p, now)}\n"
        f"{DIV}\n"
        f"<blockquote>{body}</blockquote>"
    )
    return text, kb.queue_item_kb(p["id"], idx, total, due)


async def _open_queue(bot: Bot, chat_id: int, idx: int = 0, post_id: int | None = None,
                      delete_msg: Message | None = None) -> None:
    """Показать пост очереди как медиа (фото/видео/…) или текст. Удаляет старое сообщение."""
    pending = await repo.list_pending(10_000, 0)
    total = len(pending)
    if delete_msg is not None:
        try:
            await delete_msg.delete()
        except Exception:  # noqa: BLE001
            pass

    if total == 0:
        await bot.send_message(
            chat_id,
            f"{_crumb('Очередь')}\n{DIV}\n📭 Очередь пуста.\n"
            "<i>Опубликуй пост в канале-источнике — он появится здесь.</i>",
            reply_markup=kb.queue_empty_kb(),
        )
        return

    if post_id is not None:
        for i, pp in enumerate(pending):
            if pp["id"] == post_id:
                idx = i
                break
    idx = max(0, min(idx, total - 1))
    p = pending[idx]
    now = await timeutil.now()
    caption, markup = await _queue_caption(p, idx, total, now)
    files = json.loads(p["media_file_ids"] or "[]")
    mt = p["media_type"]
    try:
        if mt == M.MEDIA_PHOTO and files:
            await bot.send_photo(chat_id, files[0], caption=caption, reply_markup=markup)
        elif mt == M.MEDIA_VIDEO and files:
            await bot.send_video(chat_id, files[0], caption=caption, reply_markup=markup)
        elif mt == M.MEDIA_DOCUMENT and files:
            await bot.send_document(chat_id, files[0], caption=caption, reply_markup=markup)
        elif mt == M.MEDIA_ALBUM and files:
            await bot.send_photo(
                chat_id, files[0],
                caption=caption + "\n<i>🖼 альбом — «👁 Превью» покажет все</i>",
                reply_markup=markup,
            )
        else:
            await bot.send_message(chat_id, caption, reply_markup=markup)
    except Exception as e:  # noqa: BLE001  — file_id устарел и т.п.
        await bot.send_message(chat_id, caption + f"\n<i>(медиа недоступно: {e})</i>",
                               reply_markup=markup)


async def _swap(callback: CallbackQuery, text: str, markup) -> None:
    """Заменить текущее сообщение (медиа или текст) текстовым экраном."""
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu:queue")
async def cb_queue(callback: CallbackQuery, bot: Bot) -> None:
    await _open_queue(bot, callback.message.chat.id, 0, delete_msg=callback.message)
    await callback.answer()


async def _send_preview(bot: Bot, post, chat_id: int) -> None:
    """Показать пост как он выглядит (реальное медиа + текст, без prefix/suffix канала)."""
    files = json.loads(post["media_file_ids"] or "[]")
    await sender_service._dispatch(bot, chat_id, post["media_type"], post["content"], files)


@router.callback_query(F.data.startswith("q:"))
async def cb_queue_action(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    parts = callback.data.split(":")
    action = parts[1]
    chat = callback.message.chat.id

    if action == "noop":
        return await callback.answer()
    if action == "nav":
        await _open_queue(bot, chat, int(parts[2]), delete_msg=callback.message)
        return await callback.answer()

    if action == "clearall":
        if not await has_access(callback.from_user.id, "editor"):
            return await callback.answer("Нет прав", show_alert=True)
        return await _swap(callback, "Удалить ВСЕ pending-посты?", kb.confirm("clearqueue"))

    if not await has_access(callback.from_user.id, "editor"):
        return await callback.answer("Нужна роль editor+", show_alert=True)

    # --- 5-частные раскладки q:act:<arg>:<post_id>:<idx> ---
    if action == "pchk":  # переключить канал в выборе (картинки нет — это текст-экран)
        cid, post_id, idx = int(parts[2]), int(parts[3]), int(parts[4])
        sel = _publish_sel.setdefault((callback.from_user.id, post_id), set())
        sel.symmetric_difference_update({cid})
        channels = await repo.list_channels(active_only=True)
        when = _when_label(await repo.get_post(post_id))
        await callback.message.edit_reply_markup(
            reply_markup=kb.publish_targets_kb(post_id, idx, channels, sel, when)
        )
        return await callback.answer()
    if action == "tset":
        minutes, post_id = int(parts[2]), int(parts[3])
        await queue_service.reschedule(post_id, await timeutil.now() + timedelta(minutes=minutes))
        await _open_queue(bot, chat, post_id=post_id, delete_msg=callback.message)
        return await callback.answer("Время изменено")
    if action == "tslot":
        code, post_id = parts[2], int(parts[3])
        t = timeutil.parse_hhmm(f"{code[:2]}:{code[2:]}")
        base = await timeutil.now()
        cand = base.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if cand <= base:
            cand += timedelta(days=1)
        await queue_service.reschedule(post_id, cand)
        await _open_queue(bot, chat, post_id=post_id, delete_msg=callback.message)
        return await callback.answer(f"→ {cand:%d.%m %H:%M}")

    # --- 4-частные раскладки q:act:<post_id>:<idx> ---
    if len(parts) < 4:
        return await callback.answer()
    post_id, idx = int(parts[2]), int(parts[3])

    if action == "skip":
        await queue_service.skip_post(post_id)
        await _log(callback, "skip_post", str(post_id))
        await _open_queue(bot, chat, idx, delete_msg=callback.message)
        await callback.answer("Пропущен")
    elif action == "del":
        await repo.delete_post(post_id)
        await _log(callback, "delete_post", str(post_id))
        await _open_queue(bot, chat, idx, delete_msg=callback.message)
        await callback.answer("Удалён")
    elif action == "dup":
        new_id = await queue_service.duplicate_to_end(post_id)
        await _open_queue(bot, chat, post_id=post_id, delete_msg=callback.message)
        await callback.answer(f"Дубль #{new_id}")
    elif action == "unpub":
        post = await repo.get_post(post_id)
        if not post or post["status"] != "pending":
            return await callback.answer("Пост уже отправлен — снять нельзя.", show_alert=True)
        delay = await repo.get_setting_int("default_delay_minutes", 30)
        dt = await timeutil.now() + timedelta(minutes=delay if delay > 0 else 30)
        await queue_service.reschedule(post_id, dt)
        await repo.set_published_by(post_id, 0)  # сброс отметки публикатора
        await _log(callback, "unpublish", str(post_id))
        await _open_queue(bot, chat, post_id=post_id, delete_msg=callback.message)
        await callback.answer("Снято с публикации")
    elif action in ("up", "down"):
        await queue_service.move_post(post_id, -1 if action == "up" else 1)
        await _open_queue(bot, chat, post_id=post_id, delete_msg=callback.message)
        await callback.answer("Перемещено")
    elif action == "prev":
        post = await repo.get_post(post_id)
        if post:
            await _send_preview(bot, post, callback.from_user.id)
        await callback.answer("Превью отправлено")
    elif action == "edit":
        return await _ask(callback, state, "post_text",
                          "Введи новый текст поста (или «-» чтобы очистить):",
                          post_id=post_id, idx=idx)
    elif action == "pub":
        channels = await repo.list_channels(active_only=True)
        if not channels:
            return await callback.answer("Нет активных каналов", show_alert=True)
        _publish_sel[(callback.from_user.id, post_id)] = set()
        post = await repo.get_post(post_id)
        when = _when_label(post)
        note = (f"⏰ Сейчас запланировано на <b>{when}</b> — уйдёт само.\n"
                "Кнопки ниже — отправить <b>немедленно</b>." if when else
                "Выбери, куда отправить немедленно.")
        await _swap(
            callback,
            f"Публикация поста #{post_id}\n{DIV}\n{note}",
            kb.publish_targets_kb(post_id, idx, channels, set(), when),
        )
        await callback.answer()
    elif action == "keep":
        post = await repo.get_post(post_id)
        when = _when_label(post)
        await _open_queue(bot, chat, post_id=post_id, delete_msg=callback.message)
        await callback.answer(f"Оставлено по расписанию: {when or '—'}", show_alert=True)
    elif action == "pall":
        await queue_service.send_now(post_id)
        await repo.set_published_by(post_id, callback.from_user.id)
        await _log(callback, "publish_all", str(post_id))
        _publish_sel.pop((callback.from_user.id, post_id), None)
        await _open_queue(bot, chat, idx, delete_msg=callback.message)
        await callback.answer("📢 Уйдёт во все активные (~60 сек)", show_alert=True)
    elif action == "pgo":
        await _publish_selected(callback, bot, post_id, idx)
    elif action == "time":
        slots = await queue_service.get_slots()
        await _swap(callback, f"Когда отправить пост #{post_id}?",
                    kb.time_picker_kb(post_id, idx, slots))
        await callback.answer()
    elif action == "tnext":
        nxt = await queue_service.next_slot()
        if not nxt:
            return await callback.answer("Слоты не заданы", show_alert=True)
        await queue_service.reschedule(post_id, nxt)
        await _open_queue(bot, chat, post_id=post_id, delete_msg=callback.message)
        await callback.answer(f"→ {nxt:%d.%m %H:%M}")
    elif action == "tman":
        return await _ask(callback, state, "post_time",
                          "Введи время: `ГГГГ-ММ-ДД ЧЧ:ММ`, `ДД.ММ.ГГГГ ЧЧ:ММ` или `ЧЧ:ММ`",
                          post_id=post_id, idx=idx)
    else:
        await callback.answer()


async def _publish_selected(callback: CallbackQuery, bot: Bot, post_id: int, idx: int) -> None:
    """Опубликовать пост в выбранные чекбоксами каналы (с захватом — без дублей)."""
    sel = _publish_sel.get((callback.from_user.id, post_id), set())
    if not sel:
        return await callback.answer("Не выбран ни один канал", show_alert=True)
    post = await repo.get_post(post_id)
    if not post:
        return await callback.answer("Пост не найден", show_alert=True)
    if not await repo.claim_post(post_id):
        return await callback.answer("Пост уже отправляется другим админом.", show_alert=True)

    ok, fail = 0, 0
    for cid in list(sel):
        channel = await repo.get_channel(cid)
        if not channel:
            continue
        try:
            await sender_service.send(bot, post, channel)
            await repo.add_log(post_id, cid, "sent")
            ok += 1
        except Exception as e:  # noqa: BLE001
            await repo.add_log(post_id, cid, "failed", str(e))
            fail += 1

    if ok:
        await repo.set_post_status(post_id, "sent", sent=True)
        await repo.set_published_by(post_id, callback.from_user.id)
    else:
        await repo.release_post(post_id)  # ничего не ушло — вернуть в очередь
    await _log(callback, "publish_selected", f"{post_id} ok={ok} fail={fail}")
    _publish_sel.pop((callback.from_user.id, post_id), None)
    await _open_queue(bot, callback.message.chat.id, idx, delete_msg=callback.message)
    await callback.answer(f"Опубликовано: {ok}, ошибок: {fail}", show_alert=True)


# --------------------------------------------------------------------------- #
#  На публикации (главное меню)
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:onpub")
async def cb_onpub(callback: CallbackQuery) -> None:
    now = await timeutil.now()
    posts = await repo.on_publication_posts()
    if not posts:
        text = (
            f"{_crumb('На публикации')}\n{DIV}\n"
            "✅ Очередь на публикацию пуста.\n"
            "<i>Сюда попадают все посты, ожидающие отправки (по расписанию или вручную).</i>"
        )
        return await _safe_edit(callback, text, kb.on_publication_kb(posts))

    lines = [f"{_crumb('На публикации')} · {len(posts)}", DIV]
    for p in posts:
        when = (p["scheduled_at"] or "")[:16].replace("T", " ") or "—"
        who = await _who(p["published_by"])
        if p["status"] == "sending":
            state = "⏳ отправляется"
        elif _is_held(p):
            state = "⏸ ожидает решения"
        elif _is_due(p, now):
            state = "📤 ближайший тик"
        else:
            state = "🕐 запланирован"
        icon = MEDIA_ICON.get(p["media_type"], "📄")
        raw = (p["content"] or "").replace("\n", " ")
        preview = html.escape(raw[:50]) + ("…" if len(raw) > 50 else "") if raw else "<i>(без текста)</i>"
        lines.append(
            f"{state} · 🆔<code>#{p['id']}</code> {icon}\n"
            f"  🕐 {when} · 👤 {who}\n"
            f"  «{preview}»"
        )
    await _safe_edit(callback, "\n".join(lines), kb.on_publication_kb(posts))


async def _who(telegram_id) -> str:
    if not telegram_id:
        return "по расписанию"
    a = await repo.get_admin(telegram_id)
    if a and a["username"]:
        return "@" + a["username"]
    return f"id {telegram_id}"


async def _safe_edit(callback: CallbackQuery, text: str, markup) -> None:
    """edit_text, а если сообщение медиа — удалить и отправить заново."""
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        await _swap(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith("op:cancel:"))
async def cb_onpub_cancel(callback: CallbackQuery) -> None:
    if not await has_access(callback.from_user.id, "editor"):
        return await callback.answer("Нужна роль editor+", show_alert=True)
    post_id = int(callback.data.split(":")[2])
    post = await repo.get_post(post_id)
    if not post or post["status"] != "pending":
        return await callback.answer("Пост уже отправляется/отправлен.", show_alert=True)
    await queue_service.skip_post(post_id)  # отмена публикации — пост не уйдёт
    await _log(callback, "cancel_publication", str(post_id))
    await callback.answer(f"Публикация #{post_id} отменена", show_alert=True)
    await cb_onpub(callback)


# --------------------------------------------------------------------------- #
#  5.2 Каналы
# --------------------------------------------------------------------------- #
async def _backfill_channel_titles(bot: Bot) -> None:
    for ch in await repo.list_channels():
        if ch["title"]:
            continue
        try:
            chat = await bot.get_chat(ch["chat_id"])
            if chat.title:
                await repo.update_channel(ch["id"], title=chat.title)
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data == "menu:channels")
async def cb_channels(callback: CallbackQuery, bot: Bot) -> None:
    await _backfill_channel_titles(bot)
    channels = await repo.list_channels()
    active = sum(1 for c in channels if c["is_active"])
    text = (
        f"{_crumb('Каналы-получатели')}\n{DIV}\n"
        f"Всего: <b>{len(channels)}</b> · активных: <b>{active}</b>\n"
        "🟢 вкл · 🔴 выкл"
    )
    await callback.message.edit_text(text, reply_markup=kb.channels_list_kb(channels))
    await callback.answer()


async def _render_channel(callback: CallbackQuery, channel_id: int) -> None:
    ch = await repo.get_channel(channel_id)
    if not ch:
        return await callback.answer("Не найдено", show_alert=True)
    tags = json.loads(ch["tags_filter"] or "[]")
    title = html.escape(ch["title"] or str(ch["chat_id"]))
    text = (
        f"{_crumb('Каналы')} ▸ <b>{title}</b>\n"
        f"{DIV}\n"
        f"{'🟢 включён' if ch['is_active'] else '🔴 выключен'}\n"
        f"🆔 <code>{ch['chat_id']}</code>\n"
        f"⏱ Задержка: {ch['delay_minutes']} мин\n"
        f"🏷 Теги: {', '.join(tags) or '—'}\n"
        f"🎛 Медиа: {ch['media_filter']}\n"
        f"🔼 Prefix: {html.escape(ch['prefix']) or '—'}\n"
        f"🔽 Suffix: {html.escape(ch['suffix']) or '—'}"
    )
    await callback.message.edit_text(text, reply_markup=kb.channel_view_kb(ch))
    await callback.answer()


@router.callback_query(F.data.startswith("ch:view:"))
async def cb_channel_view(callback: CallbackQuery) -> None:
    await _render_channel(callback, int(callback.data.split(":")[2]))


@router.callback_query(F.data == "ch:add")
async def cb_channel_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    await _ask(callback, state, "channel_add",
               "Введи @username или chat_id канала-получателя:")


@router.callback_query(F.data.startswith("ch:toggle:"))
async def cb_channel_toggle(callback: CallbackQuery) -> None:
    cid = int(callback.data.split(":")[2])
    await repo.toggle_channel(cid)
    await _render_channel(callback, cid)


@router.callback_query(F.data.startswith("ch:media:"))
async def cb_channel_media(callback: CallbackQuery) -> None:
    cid = int(callback.data.split(":")[2])
    await callback.message.edit_text(
        "Выбери медиа-фильтр:", reply_markup=kb.media_filter_kb(cid)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ch:mediaset:"))
async def cb_channel_media_set(callback: CallbackQuery) -> None:
    _, _, cid, value = callback.data.split(":")
    await repo.update_channel(int(cid), media_filter=value)
    await _render_channel(callback, int(cid))


@router.callback_query(F.data.startswith("ch:set:"))
async def cb_channel_set(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, cid, field = callback.data.split(":")
    prompts = {
        "delay": "Задержка канала в минутах:",
        "tags": "Теги через запятую (пусто = слать все):",
        "prefix": "Текст шапки (prefix):",
        "suffix": "Текст подвала (suffix):",
    }
    await _ask(callback, state, f"channel_{field}", prompts[field], channel_id=int(cid))


@router.callback_query(F.data.startswith("ch:test:"))
async def cb_channel_test(callback: CallbackQuery, bot: Bot) -> None:
    ch = await repo.get_channel(int(callback.data.split(":")[2]))
    post = await repo.latest_post()
    if not ch or not post:
        return await callback.answer("Нет канала или постов", show_alert=True)
    try:
        await sender_service.send(bot, post, ch)
        await callback.answer("✅ Отправлено")
    except Exception as e:  # noqa: BLE001
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("ch:del:"))
async def cb_channel_del(callback: CallbackQuery) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    cid = callback.data.split(":")[2]
    await callback.message.edit_text(
        "Удалить канал?", reply_markup=kb.confirm("delchannel", cid)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  5.4 Источники
# --------------------------------------------------------------------------- #
async def _backfill_source_titles(bot: Bot) -> None:
    for s in await repo.list_sources():
        if s["title"]:
            continue
        try:
            chat = await bot.get_chat(s["chat_id"])
            if chat.title:
                await repo.update_source(s["id"], title=chat.title)
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data == "menu:sources")
async def cb_sources(callback: CallbackQuery, bot: Bot) -> None:
    await _backfill_source_titles(bot)
    sources = await repo.list_sources()
    text = (
        f"{_crumb('Источники')}\n{DIV}\n"
        "Бот должен быть админом канала.\n🟢 активен · 🟡 на паузе"
    )
    await callback.message.edit_text(text, reply_markup=kb.sources_list_kb(sources))
    await callback.answer()


async def _render_source(callback: CallbackQuery, source_id: int) -> None:
    s = await repo.get_source(source_id)
    if not s:
        return await callback.answer("Не найдено", show_alert=True)
    stitle = html.escape(s["title"] or str(s["chat_id"]))
    text = (
        f"{_crumb('Источники')} ▸ <b>{stitle}</b>\n"
        f"{DIV}\n"
        f"{'🟢 активен' if s['is_active'] else '🟡 на паузе'}\n"
        f"🆔 <code>{s['chat_id']}</code>\n"
        f"⏱ Задержка: {s['default_delay_minutes'] if s['default_delay_minutes'] is not None else 'глобальная'}\n"
        f"📏 Мин. длина: {s['min_post_length'] if s['min_post_length'] is not None else 'глобальная'}\n"
        f"↪️ Игнор пересланных: {'да' if s['ignore_forwarded'] else 'нет'}"
    )
    await callback.message.edit_text(text, reply_markup=kb.source_view_kb(s))
    await callback.answer()


@router.callback_query(F.data.startswith("src:view:"))
async def cb_source_view(callback: CallbackQuery) -> None:
    await _render_source(callback, int(callback.data.split(":")[2]))


@router.callback_query(F.data == "src:add")
async def cb_source_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    await _ask(callback, state, "source_add", "Введи chat_id источника:")


@router.callback_query(F.data.startswith("src:toggle:"))
async def cb_source_toggle(callback: CallbackQuery) -> None:
    sid = int(callback.data.split(":")[2])
    await repo.toggle_source(sid)
    await _render_source(callback, sid)


@router.callback_query(F.data.startswith("src:fwd:"))
async def cb_source_fwd(callback: CallbackQuery) -> None:
    sid = int(callback.data.split(":")[2])
    s = await repo.get_source(sid)
    await repo.update_source(sid, ignore_forwarded=0 if s["ignore_forwarded"] else 1)
    await _render_source(callback, sid)


@router.callback_query(F.data.startswith("src:set:"))
async def cb_source_set(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, sid, field = callback.data.split(":")
    prompt = "Задержка в минутах:" if field == "default_delay_minutes" else "Мин. длина поста:"
    await _ask(callback, state, f"source_{field}", prompt, source_id=int(sid))


@router.callback_query(F.data.startswith("src:del:"))
async def cb_source_del(callback: CallbackQuery) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    sid = callback.data.split(":")[2]
    await callback.message.edit_text(
        "Удалить источник?", reply_markup=kb.confirm("delsource", sid)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  5.3 Расписание
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:schedule")
async def cb_schedule(callback: CallbackQuery) -> None:
    s = await repo.all_settings()
    enabled = await repo.get_setting_bool("quiet_hours_enabled", True)
    quiet_line = (
        f"{s.get('quiet_hours_start')}–{s.get('quiet_hours_end')}"
        if enabled else "выключены"
    )
    text = (
        f"{_crumb('Расписание')}\n{DIV}\n"
        f"🌍 Пояс: {s.get('timezone')}\n"
        f"🌙 Тихие часы: {quiet_line}\n"
        f"⚙️ Поведение: {s.get('quiet_hours_behavior')}\n"
        f"⏲ Мин. интервал: {s.get('min_interval_minutes')} мин\n"
        f"🎯 Слоты: {s.get('time_slots')}\n"
        f"📅 Дни: {_weekdays_str(s.get('active_weekdays', ''))}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=kb.schedule_kb(s.get("quiet_hours_behavior", "accumulate"), enabled),
    )
    await callback.answer()


@router.callback_query(F.data == "sch:quiet_toggle")
async def cb_schedule_quiet_toggle(callback: CallbackQuery) -> None:
    cur = await repo.get_setting_bool("quiet_hours_enabled", True)
    await repo.set_setting("quiet_hours_enabled", "false" if cur else "true")
    await cb_schedule(callback)


def _weekdays_str(raw: str) -> str:
    try:
        days = sorted(int(x) for x in raw.split(",") if x.strip() != "")
    except ValueError:
        return "—"
    return ", ".join(WEEKDAY_NAMES[d] for d in days if 0 <= d < 7) or "нет"


@router.callback_query(F.data.startswith("sch:set:"))
async def cb_schedule_set(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":")[2]
    if key == "timezone":
        return await _ask(callback, state, "timezone",
                          "Введи часовой пояс (IANA), напр. <code>Europe/Moscow</code>, "
                          "<code>Asia/Yekaterinburg</code>, <code>UTC</code>:")
    prompt = {
        "quiet_hours_start": "Начало тихих часов (ЧЧ:ММ):",
        "quiet_hours_end": "Конец тихих часов (ЧЧ:ММ):",
        "min_interval_minutes": "Мин. интервал между постами (мин):",
    }[key]
    await _ask(callback, state, "setting", prompt, key=key)


@router.callback_query(F.data == "sch:slots")
async def cb_schedule_slots(callback: CallbackQuery) -> None:
    slots = await queue_service.get_slots()
    await callback.message.edit_text(
        "🎯 <b>Слоты публикации</b>\nИспользуются кнопкой «Ближайший слот» при выборе времени.",
        reply_markup=kb.slots_kb(slots),
    )
    await callback.answer()


@router.callback_query(F.data == "sch:slotadd")
async def cb_schedule_slot_add(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask(callback, state, "add_slot", "Введи время слота в формате ЧЧ:ММ (напр. 12:30):")


@router.callback_query(F.data.startswith("sch:slotdel:"))
async def cb_schedule_slot_del(callback: CallbackQuery) -> None:
    code = callback.data.split(":")[2]
    target = f"{code[:2]}:{code[2:]}"
    slots = await queue_service.get_slots()
    kept = [f"{t.hour:02d}:{t.minute:02d}" for t in slots if f"{t.hour:02d}:{t.minute:02d}" != target]
    await repo.set_setting("time_slots", ",".join(kept))
    await cb_schedule_slots(callback)


@router.callback_query(F.data == "sch:behavior")
async def cb_schedule_behavior(callback: CallbackQuery) -> None:
    cur = await repo.get_setting("quiet_hours_behavior", "accumulate")
    await repo.set_setting("quiet_hours_behavior", "skip" if cur == "accumulate" else "accumulate")
    await cb_schedule(callback)


@router.callback_query(F.data == "sch:weekdays")
async def cb_schedule_weekdays(callback: CallbackQuery) -> None:
    active = _active_set(await repo.get_setting("active_weekdays", ""))
    await callback.message.edit_text("Активные дни недели:", reply_markup=kb.weekdays_kb(active))
    await callback.answer()


def _active_set(raw: str) -> set[int]:
    try:
        return {int(x) for x in raw.split(",") if x.strip() != ""}
    except ValueError:
        return set()


@router.callback_query(F.data.startswith("sch:wd:"))
async def cb_schedule_wd_toggle(callback: CallbackQuery) -> None:
    day = int(callback.data.split(":")[2])
    active = _active_set(await repo.get_setting("active_weekdays", ""))
    active.symmetric_difference_update({day})
    await repo.set_setting("active_weekdays", ",".join(str(d) for d in sorted(active)))
    await cb_schedule_weekdays(callback)


# --------------------------------------------------------------------------- #
#  5.5 Статистика
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:stats")
@router.callback_query(F.data.startswith("stats:period:"))
async def cb_stats(callback: CallbackQuery) -> None:
    period = callback.data.split(":")[2] if callback.data.startswith("stats:period:") else "1"
    days = int(period)
    s = await repo.stats_since(days)
    by_ch = await repo.stats_by_channel(days)
    errors = await repo.recent_errors(5)

    period_label = "сегодня" if period == "1" else f"{period} дн."
    base = s["sent"] + s["failed"] + s["skipped"]
    lines = [
        f"{_crumb(f'Статистика · {period_label}')}",
        DIV,
        f"✅ Отправлено  <code>{_bar(s['sent'], base)}</code>",
        f"⏭ Пропущено   <code>{_bar(s['skipped'], base)}</code>",
        f"❌ Ошибки      <code>{_bar(s['failed'], base)}</code>",
        DIV,
        f"📤 Всего: <b>{s['sent']}</b> · ⏭ <b>{s['skipped']}</b> · "
        f"❌ <b>{s['failed']}</b> · 📋 в очереди <b>{s['in_queue']}</b>",
    ]
    if by_ch:
        lines.append(f"\n<b>По каналам:</b>")
        for r in by_ch:
            title = html.escape(r["title"] or str(r["chat_id"]))
            lines.append(f"  🟢 {title}: ✅{r['sent']} ❌{r['failed']}")
    if errors:
        lines.append("\n<b>Последние ошибки:</b>")
        for e in errors:
            lines.append(f"  ❌ #{e['post_id']}: {html.escape(_short(e['error_msg'] or '', 45))}")
    await callback.message.edit_text("\n".join(lines), reply_markup=kb.stats_period_kb(period))
    await callback.answer()


@router.callback_query(F.data == "stats:export")
async def cb_stats_export(callback: CallbackQuery) -> None:
    logs = await repo.all_logs()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "post_id", "channel", "status", "error", "sent_at"])
    for r in logs:
        writer.writerow([
            r["id"], r["post_id"], r["channel_title"] or r["channel_id"],
            r["status"], r["error_msg"] or "", r["sent_at"],
        ])
    data = buf.getvalue().encode("utf-8-sig")
    await callback.message.answer_document(
        BufferedInputFile(data, filename="post_logs.csv"),
        caption=f"📤 Экспорт: {len(logs)} записей",
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  5.6 Администраторы
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:admins")
async def cb_admins(callback: CallbackQuery) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    admins = await repo.list_admins()
    text = (
        f"{_crumb('Администраторы')}\n{DIV}\n"
        f"Всего: <b>{len(admins)}</b>\n👑 superadmin · ✏️ editor · 👁 viewer"
    )
    await callback.message.edit_text(text, reply_markup=kb.admins_list_kb(admins))
    await callback.answer()


async def _render_admin(callback: CallbackQuery, tid: int) -> None:
    a = await repo.get_admin(tid)
    if not a:
        return await callback.answer("Не найдено", show_alert=True)
    await callback.message.edit_text(
        f"👤 <code>{tid}</code> {a['username'] or ''}\nРоль: <b>{a['role']}</b>",
        reply_markup=kb.admin_view_kb(tid),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:view:"))
async def cb_admin_view(callback: CallbackQuery) -> None:
    await _render_admin(callback, int(callback.data.split(":")[2]))


@router.callback_query(F.data == "adm:add")
async def cb_admin_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    await _ask(callback, state, "admin_add",
               "Введи Telegram ID нового админа (или перешли его сообщение):")


@router.callback_query(F.data.startswith("adm:role:"))
async def cb_admin_role(callback: CallbackQuery) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    _, _, tid, role = callback.data.split(":")
    await repo.set_admin_role(int(tid), role)
    await _log(callback, "set_role", f"{tid}->{role}")
    await _render_admin(callback, int(tid))


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_admin_del(callback: CallbackQuery) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    tid = int(callback.data.split(":")[2])
    if tid == callback.from_user.id:
        return await callback.answer("Нельзя удалить себя", show_alert=True)
    await callback.message.edit_text(
        "Удалить админа?", reply_markup=kb.confirm("deladmin", str(tid))
    )
    await callback.answer()


@router.callback_query(F.data == "adm:log")
async def cb_admin_log(callback: CallbackQuery) -> None:
    actions = await repo.recent_admin_actions(15)
    lines = ["📜 <b>Лог действий</b> (30 дней)\n"]
    for a in actions:
        when = a["created_at"][:16].replace("T", " ")
        lines.append(f"{when} | {a['telegram_id']} | {a['action']} {a['details'] or ''}")
    await callback.message.edit_text(
        "\n".join(lines) if actions else "Лог пуст.",
        reply_markup=kb.back_button("menu:admins"),
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  5.7 Уведомления
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:notify")
async def cb_notify(callback: CallbackQuery) -> None:
    flags = {
        k: await repo.get_setting_bool(k)
        for k in (
            "notify_on_sent", "notify_on_error", "notify_on_empty_queue",
            "notify_on_restart", "notify_daily_report",
        )
    }
    chat = await repo.get_setting("notify_chat_id", "") or "не задан"
    rtime = await repo.get_setting("daily_report_time", "09:00")
    text = (
        f"{_crumb('Уведомления')}\n{DIV}\n"
        f"💬 Чат: <code>{chat}</code>\n🕘 Время отчёта: {rtime}\n"
        "✅ включено · ❌ выключено"
    )
    await callback.message.edit_text(text, reply_markup=kb.notify_kb(flags))
    await callback.answer()


@router.callback_query(F.data.startswith("ntf:toggle:"))
async def cb_notify_toggle(callback: CallbackQuery) -> None:
    key = callback.data.split(":")[2]
    cur = await repo.get_setting_bool(key)
    await repo.set_setting(key, "false" if cur else "true")
    await cb_notify(callback)


@router.callback_query(F.data == "ntf:setchat")
async def cb_notify_setchat(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask(callback, state, "setting", "Введи chat_id для уведомлений:",
               key="notify_chat_id")


@router.callback_query(F.data == "ntf:reporttime")
async def cb_notify_reporttime(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask(callback, state, "report_time",
               "Время ежедневного отчёта (ЧЧ:ММ). Применится после перезапуска бота:")


# --------------------------------------------------------------------------- #
#  5.8 Настройки
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:settings")
async def cb_settings(callback: CallbackQuery) -> None:
    if not await has_access(callback.from_user.id, "superadmin"):
        return await callback.answer("Только superadmin", show_alert=True)
    pause = await repo.get_setting_bool("pause_mode")
    test = await repo.get_setting_bool("test_mode")
    autodel = await repo.get_setting_int("autodelete_seconds", 90) > 0
    moder = await repo.get_setting_bool("moderation_mode")
    s = await repo.all_settings()
    moder_line = ("📝 Модерация: <b>ВКЛ</b> — новые посты ждут решения"
                  if moder else
                  f"📝 Модерация: выкл — авто-отправка через {s.get('default_delay_minutes')} мин")
    text = (
        f"{_crumb('Настройки')}\n{DIV}\n"
        f"{moder_line}\n"
        f"{'🟡' if pause else '🟢'} Пауза: {'да' if pause else 'нет'} · "
        f"{'🧪' if test else '▫️'} Тест: {'да' if test else 'нет'}\n"
        f"🧹 Автоочистка: {'вкл (' + s.get('autodelete_seconds', '90') + ' с)' if autodel else 'выкл'}\n"
        f"⏱ Задержка: {s.get('default_delay_minutes')} мин\n"
        f"📈 Макс/сутки: {s.get('max_posts_per_day')} (0=без лимита)\n"
        f"🔁 Повторов: {s.get('retry_attempts')}"
    )
    await callback.message.edit_text(text, reply_markup=kb.settings_kb(pause, test, autodel, moder))
    await callback.answer()


@router.callback_query(F.data == "set:pause")
async def cb_set_pause(callback: CallbackQuery) -> None:
    cur = await repo.get_setting_bool("pause_mode")
    await repo.set_setting("pause_mode", "false" if cur else "true")
    await cb_settings(callback)


@router.callback_query(F.data == "set:test")
async def cb_set_test(callback: CallbackQuery) -> None:
    cur = await repo.get_setting_bool("test_mode")
    await repo.set_setting("test_mode", "false" if cur else "true")
    await cb_settings(callback)


@router.callback_query(F.data == "set:autodel")
async def cb_set_autodel(callback: CallbackQuery) -> None:
    cur = await repo.get_setting_int("autodelete_seconds", 90)
    await repo.set_setting("autodelete_seconds", "0" if cur > 0 else "90")
    await cb_settings(callback)


@router.callback_query(F.data == "set:moder")
async def cb_set_moder(callback: CallbackQuery) -> None:
    cur = await repo.get_setting_bool("moderation_mode")
    await repo.set_setting("moderation_mode", "false" if cur else "true")
    await cb_settings(callback)


@router.callback_query(F.data.startswith("set:edit:"))
async def cb_set_edit(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":")[2]
    await _ask(callback, state, "setting", f"Новое значение для {key}:", key=key)


@router.callback_query(F.data == "set:reset")
async def cb_set_reset(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Сбросить все настройки к значениям по умолчанию?",
        reply_markup=kb.confirm("resetsettings"),
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Подтверждения
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(callback: CallbackQuery) -> None:
    _, action, payload = callback.data.split(":", 2)

    if action == "cancel":
        await callback.message.edit_text("Отменено.", reply_markup=kb.main_menu())
        return await callback.answer()

    if action == "clearqueue":
        n = await repo.clear_pending()
        await _log(callback, "clear_queue", str(n))
        await callback.message.edit_text(f"🧹 Удалено {n} постов.", reply_markup=kb.back_button())
    elif action == "delchannel":
        await repo.delete_channel(int(payload))
        await _log(callback, "delete_channel", payload)
        await callback.message.edit_text("Канал удалён.", reply_markup=kb.back_button("menu:channels"))
    elif action == "delsource":
        await repo.delete_source(int(payload))
        await callback.message.edit_text("Источник удалён.", reply_markup=kb.back_button("menu:sources"))
    elif action == "deladmin":
        await repo.delete_admin(int(payload))
        await _log(callback, "delete_admin", payload)
        await callback.message.edit_text("Админ удалён.", reply_markup=kb.back_button("menu:admins"))
    elif action == "resetsettings":
        from bot.db.models import DEFAULT_SETTINGS
        for k, v in DEFAULT_SETTINGS.items():
            await repo.set_setting(k, v)
        await callback.message.edit_text("♻️ Настройки сброшены.", reply_markup=kb.back_button())
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Обработка текстового ввода (FSM)
# --------------------------------------------------------------------------- #
@router.message(Await.input)
async def on_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    kind = data.get("kind")
    raw = (message.text or "").strip()
    await state.clear()

    try:
        await _handle_input(message, kind, raw, data, bot)
    except Exception as e:  # noqa: BLE001
        logger.exception("Ошибка ввода")
        await message.answer(f"Ошибка: {e}")


async def _handle_input(message: Message, kind: str, raw: str, data: dict, bot: Bot) -> None:
    if kind == "post_time":
        dt = _parse_dt(raw, await timeutil.now())
        if not dt:
            return await message.answer("Не понял формат. Пример: 2026-06-25 14:30")
        await queue_service.reschedule(data["post_id"], dt)
        await message.answer(f"✏️ Время поста #{data['post_id']} → {dt:%Y-%m-%d %H:%M}",
                             reply_markup=kb.main_menu())

    elif kind == "post_text":
        new_text = "" if raw == "-" else raw
        await repo.update_post_content(data["post_id"], new_text)
        await message.answer(f"✏️ Текст поста #{data['post_id']} обновлён.",
                             reply_markup=kb.main_menu())

    elif kind == "add_slot":
        t = timeutil.parse_hhmm(raw)
        if not t:
            return await message.answer("Формат ЧЧ:ММ. Пример: 12:30")
        slots = await queue_service.get_slots()
        labels = {f"{x.hour:02d}:{x.minute:02d}" for x in slots}
        labels.add(f"{t.hour:02d}:{t.minute:02d}")
        await repo.set_setting("time_slots", ",".join(sorted(labels)))
        await message.answer(f"🎯 Слот {t.hour:02d}:{t.minute:02d} добавлен.",
                             reply_markup=kb.main_menu())

    elif kind == "timezone":
        if not timeutil.is_valid_tz(raw):
            return await message.answer("Неизвестный пояс. Пример: Europe/Moscow")
        await repo.set_setting("timezone", raw)
        await message.answer(
            f"🌍 Часовой пояс: <b>{raw}</b>.\n"
            "Тихие часы/расписание применятся сразу; cron-задачи (отчёт, бэкап) — после перезапуска.",
            reply_markup=kb.main_menu(),
        )

    elif kind == "report_time":
        t = timeutil.parse_hhmm(raw)
        if not t:
            return await message.answer("Формат ЧЧ:ММ. Пример: 09:00")
        await repo.set_setting("daily_report_time", f"{t.hour:02d}:{t.minute:02d}")
        await message.answer("🕘 Время отчёта сохранено (применится после перезапуска).",
                             reply_markup=kb.main_menu())

    elif kind == "channel_add":
        chat_id = await _resolve_chat(raw, message, bot)
        if chat_id is None:
            return
        title = ""
        try:
            chat = await bot.get_chat(chat_id)
            title = chat.title or ""
        except Exception:  # noqa: BLE001
            pass
        cid = await repo.add_channel(chat_id, title)
        await message.answer(
            f"📡 Канал добавлен: <b>{title or chat_id}</b>", reply_markup=kb.main_menu()
        )

    elif kind == "channel_delay":
        await repo.update_channel(data["channel_id"], delay_minutes=int(raw))
        await message.answer("Задержка обновлена.", reply_markup=kb.main_menu())
    elif kind == "channel_tags":
        tags = [t.strip().lstrip("#") for t in raw.split(",") if t.strip()]
        await repo.update_channel(data["channel_id"], tags_filter=json.dumps(tags))
        await message.answer(f"Теги: {tags or 'все'}", reply_markup=kb.main_menu())
    elif kind == "channel_prefix":
        await repo.update_channel(data["channel_id"], prefix=raw)
        await message.answer("Prefix обновлён.", reply_markup=kb.main_menu())
    elif kind == "channel_suffix":
        await repo.update_channel(data["channel_id"], suffix=raw)
        await message.answer("Suffix обновлён.", reply_markup=kb.main_menu())

    elif kind == "source_add":
        chat_id = int(raw)
        title = ""
        try:
            chat = await bot.get_chat(chat_id)
            title = chat.title or ""
        except Exception:  # noqa: BLE001
            pass
        await repo.add_source(chat_id, title)
        await message.answer(
            f"🔧 Источник добавлен: <b>{title or chat_id}</b>", reply_markup=kb.main_menu()
        )
    elif kind == "source_default_delay_minutes":
        await repo.update_source(data["source_id"], default_delay_minutes=int(raw))
        await message.answer("Задержка источника обновлена.", reply_markup=kb.main_menu())
    elif kind == "source_min_post_length":
        await repo.update_source(data["source_id"], min_post_length=int(raw))
        await message.answer("Мин. длина обновлена.", reply_markup=kb.main_menu())

    elif kind == "admin_add":
        tid = await _resolve_admin_id(raw, message)
        if tid is None:
            return await message.answer("Не понял ID.")
        await repo.add_admin(tid, role="viewer",
                             username=message.forward_from.username if message.forward_from else None)
        await message.answer(f"👤 Админ {tid} добавлен (viewer).", reply_markup=kb.main_menu())

    elif kind == "setting":
        key = data["key"]
        await repo.set_setting(key, raw)
        await message.answer(f"{key} = {raw}", reply_markup=kb.main_menu())


async def _resolve_chat(raw: str, message: Message, bot: Bot) -> int | None:
    try:
        if raw.startswith("@") or not raw.lstrip("-").isdigit():
            chat = await bot.get_chat(raw)
            return chat.id
        return int(raw)
    except Exception as e:  # noqa: BLE001
        await message.answer(f"Не смог найти чат: {e}")
        return None


async def _resolve_admin_id(raw: str, message: Message) -> int | None:
    if message.forward_from:
        return message.forward_from.id
    if raw.lstrip("-").isdigit():
        return int(raw)
    return None


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #
async def _log(callback: CallbackQuery, action: str, details: str = "") -> None:
    await repo.log_admin_action(callback.from_user.id, action, details)
