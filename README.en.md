# 📰 Telegram News Scheduler Bot

🌐 **English** · [Русский](README.md)

The bot monitors private Telegram **source** channels, collects new posts into a queue and
publishes them to **target** channels — on a schedule, by time slots, or manually with
moderation. Everything is managed through inline buttons in a private chat with the bot.

> Stack: **Python 3.11+**, [aiogram 3.x](https://docs.aiogram.dev), SQLite (aiosqlite),
> APScheduler, loguru.

---

## ✨ Features

- **Sources & targets** — multiple channels; each target has its own settings
  (delay, tags, prefix/suffix, media filter).
- **Queue browser** — posts are paged one by one with a real media preview
  (photo/video/document/album), text and time editing.
- **Flexible publishing**:
  - by schedule (arrival + delay),
  - by time slots (09:00 / 15:00 / 21:00 — editable),
  - manually "now" to all channels or to selected ones via checkboxes,
  - **manual moderation** — posts wait until a human decides when to publish.
- **Quiet hours**, active weekdays, minimum interval, daily post limit.
- **Time zone** — all scheduling is computed in the configured TZ.
- **Roles**: `superadmin` / `editor` / `viewer`.
- **Statistics** with bars and CSV export, admin action log.
- **Notifications**: send errors, "queue empty for N hours", daily report, restart.
- **Reliability**: duplicate protection (atomic post claim), crash recovery,
  daily DB backup, retries on errors, FloodWait handling.
- **Auto-cleanup** of the bot's service messages in DMs (except the last one).

---

## 🚀 Installation

```bash
git clone <repo-url>
cd TGNEWS

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
cp .env.example .env            # then fill it in (see below)
```

### `.env` configuration

Required fields:

| Variable             | Description                                                       |
|----------------------|-------------------------------------------------------------------|
| `BOT_TOKEN`          | Bot token from [@BotFather](https://t.me/BotFather)               |
| `SOURCE_CHANNEL_IDS` | Source channel IDs, comma-separated (bot must be an admin there)  |
| `ADMIN_IDS`          | Admin IDs, comma-separated. The **first** one becomes `superadmin`|

Other parameters (delay, interval, webhook, etc.) are optional — see comments in
`.env.example`.

### Run

```bash
python -m bot.main
```

In Telegram, open a DM with the bot → `/start` → the menu appears.

---

## 🛠 Usage

1. Add the bot as an **administrator** to the source channel (otherwise it can't see posts).
2. Add the bot as an admin with **Post Messages** permission to the target channels.
3. In the bot: 📡 **Channels** → ➕ Add → paste the target's `@username` or `chat_id`.
4. Publish a post in the source — it lands in the queue.
5. Then, as you prefer:
   - leave it → it goes out on schedule;
   - 📋 **Queue** → ✏️ Time / 🚀 Now → pick a moment or send immediately;
   - enable ⚙️ **Settings → Manual moderation** → posts wait for your decision.

### Commands

- `/start`, `/menu` — main menu (dashboard)
- `/queue` — the queue
- `/pause`, `/resume` — global send pause

---

## 📦 Deployment

### Docker (recommended)
```bash
cp .env.example .env   # fill it in
docker compose up -d --build
```
DB and backups live in `./data` (survive rebuilds), auto-restart is enabled.

### systemd (VPS without Docker)
```bash
sudo cp deploy/tgnews-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tgnews-bot
journalctl -u tgnews-bot -f
```

### Webhook
Set `WEBHOOK_BASE_URL=https://your-domain` in `.env` (public HTTPS required).
Leave empty → polling mode.

---

## 🗂 Structure

```
bot/
├── main.py              entry point: bot + DB + scheduler
├── config.py            reads and validates .env
├── handlers/            listener (sources), admin (panel), keyboards, access
├── scheduler/           APScheduler: queue tick, backup, notifications
├── services/            queue / sender / filter / notify
├── db/                  models and repository (aiosqlite)
└── utils/               media, logger, timeutil, autodelete
```

---

## 📄 License

Distributed under the **[PolyForm Noncommercial 1.0.0](LICENSE)** license —
any **noncommercial** use is allowed (personal, educational, research, by nonprofit
organizations). Commercial use requires separate permission from the rights holder
(**Nonaqie**).

The software is provided "as is", without warranties.
