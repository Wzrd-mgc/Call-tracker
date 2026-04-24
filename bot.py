"""
Telegram Call Tracker Bot
Автоматически считает звонки диспетчера и отправляет месячные отчёты.
"""

import os
import re
import sqlite3
import logging
from datetime import datetime, date
from collections import defaultdict

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ─── Настройки ─────────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8639482529:AAHLEXpIudvpHU5Ae5bgzEtkUaPDWEvDupk")

# ID чата, куда бот будет отправлять месячный отчёт (ваш личный чат или отдельный чат)
# Получите свой ID: напишите @userinfobot в Telegram
REPORT_CHAT_ID = os.environ.get("REPORT_CHAT_ID", "5010509877"
")

DB_FILE = "calls.db"

# ─── Логирование ────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── База данных ────────────────────────────────────────────────────────────────

def init_db():
    """Создаёт таблицу для хранения звонков."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calls (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            phone     TEXT,
            category  TEXT,
            full_text TEXT,
            chat_id   TEXT,
            date      TEXT,          -- YYYY-MM-DD
            month     TEXT,          -- YYYY-MM
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

def save_call(phone, category, full_text, chat_id):
    today = date.today()
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT INTO calls (phone, category, full_text, chat_id, date, month) VALUES (?,?,?,?,?,?)",
        (phone, category, full_text, str(chat_id), str(today), today.strftime("%Y-%m"))
    )
    conn.commit()
    conn.close()

def get_calls_by_month(month: str):
    """month = 'YYYY-MM'"""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT phone, category, full_text, date FROM calls WHERE month=? ORDER BY date",
        (month,)
    ).fetchall()
    conn.close()
    return rows

def get_all_months():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT DISTINCT month FROM calls ORDER BY month DESC"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]

# ─── Парсер звонков ─────────────────────────────────────────────────────────────

# Паттерны телефонов: (562) 230-0023 / 562-230-0023 / 5622300023
PHONE_RE = re.compile(
    r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"
)

# Категория — слово или фраза в скобках в конце: (small appliance)
CATEGORY_RE = re.compile(r"\(([^)]+)\)\s*$")

def parse_call(text: str):
    """
    Возвращает (phone, category) или None если не звонок.
    Пример: "(562) 230-0023 dispute (small appliance)" → ("(562) 230-0023", "small appliance")
    """
    phone_match = PHONE_RE.search(text)
    if not phone_match:
        return None

    phone = phone_match.group().strip()

    # Ищем категорию в скобках
    cat_match = CATEGORY_RE.search(text)
    if cat_match:
        category = cat_match.group(1).strip()
    else:
        # Берём первое слово после номера как категорию
        after = text[phone_match.end():].strip()
        parts = after.split()
        category = parts[0].rstrip(".,;") if parts else "other"

    return phone, category

# ─── Форматирование отчёта ──────────────────────────────────────────────────────

MONTHS_RU = {
    "01": "Январь", "02": "Февраль", "03": "Март",
    "04": "Апрель", "05": "Май",     "06": "Июнь",
    "07": "Июль",   "08": "Август",  "09": "Сентябрь",
    "10": "Октябрь","11": "Ноябрь",  "12": "Декабрь",
}

def build_report(month: str) -> str:
    """Строит текстовый отчёт за месяц YYYY-MM."""
    rows = get_calls_by_month(month)
    year, m = month.split("-")
    month_name = MONTHS_RU.get(m, m)

    if not rows:
        return f"📭 За {month_name} {year} звонков не найдено."

    total = len(rows)

    # Подсчёт по категориям
    cats = defaultdict(int)
    for _, cat, _, _ in rows:
        cats[cat.lower()] += 1

    # Подсчёт по дням
    days = defaultdict(int)
    for _, _, _, d in rows:
        days[d] += 1

    # Топ категории
    top_cats = sorted(cats.items(), key=lambda x: -x[1])

    lines = [
        f"📊 *Отчёт за {month_name} {year}*",
        f"",
        f"📞 Всего звонков: *{total}*",
        f"📅 Активных дней: {len(days)}",
        f"",
        f"*По категориям:*",
    ]
    for cat, cnt in top_cats:
        bar = "█" * min(cnt, 15)
        pct = round(cnt / total * 100)
        lines.append(f"  `{cat:<22}` {cnt:>3} ({pct}%) {bar}")

    # Топ 3 дня
    if days:
        lines.append("")
        lines.append("*Самые загруженные дни:*")
        for d, cnt in sorted(days.items(), key=lambda x: -x[1])[:3]:
            dt = datetime.strptime(d, "%Y-%m-%d")
            lines.append(f"  {dt.strftime('%d.%m')} — {cnt} звонков")

    return "\n".join(lines)

# ─── Обработчики ────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Слушает все сообщения в группе и сохраняет звонки."""
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()
    result = parse_call(text)

    if result:
        phone, category = result
        save_call(phone, category, text, msg.chat_id)
        logger.info(f"✅ Звонок сохранён: {phone} | {category} | чат {msg.chat_id}")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/report или /report 2024-03 — отчёт за месяц."""
    if context.args:
        month = context.args[0]  # Пользователь указал YYYY-MM
    else:
        month = date.today().strftime("%Y-%m")  # Текущий месяц

    report = build_report(month)
    await update.message.reply_text(report, parse_mode="Markdown")


async def cmd_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/months — список всех месяцев с данными."""
    months = get_all_months()
    if not months:
        await update.message.reply_text("Данных пока нет.")
        return
    lines = ["📅 *Доступные месяцы:*", ""]
    for m in months:
        rows = get_calls_by_month(m)
        year, mn = m.split("-")
        lines.append(f"  /report\\_month {m} — {MONTHS_RU.get(mn,mn)} {year}: {len(rows)} звонков")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/today — звонки за сегодня."""
    today = str(date.today())
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT phone, category, full_text FROM calls WHERE date=? ORDER BY created_at",
        (today,)
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Сегодня звонков нет.")
        return

    lines = [f"📞 *Звонки за сегодня ({len(rows)}):*", ""]
    for phone, cat, full in rows:
        lines.append(f"• `{phone}` — {cat}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def send_monthly_report(context: ContextTypes.DEFAULT_TYPE):
    """Автоматически отправляет отчёт 1-го числа каждого месяца."""
    # Отчёт за прошлый месяц
    today = date.today()
    if today.month == 1:
        prev_month = f"{today.year - 1}-12"
    else:
        prev_month = f"{today.year}-{str(today.month - 1).zfill(2)}"

    report = build_report(prev_month)
    await context.bot.send_message(
        chat_id=REPORT_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )
    logger.info(f"📨 Автоотчёт за {prev_month} отправлен.")


# ─── Запуск ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    logger.info("🤖 Бот запускается...")

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("months", cmd_months))

    # Слушаем все текстовые сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Автоотчёт каждый 1-й день месяца в 09:00
    app.job_queue.run_monthly(
        send_monthly_report,
        when=datetime.strptime("09:00", "%H:%M").time(),
        day=1,
    )

    logger.info("✅ Бот работает. Ожидаю звонки...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
