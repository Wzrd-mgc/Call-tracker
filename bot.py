import os, re, sqlite3, logging
from datetime import datetime, date
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

BOT_TOKEN = os.environ["BOT_TOKEN"]
REPORT_CHAT_ID = os.environ["REPORT_CHAT_ID"]
DB_FILE = "calls.db"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, category TEXT, full_text TEXT, chat_id TEXT, date TEXT, month TEXT, created_at TEXT DEFAULT (datetime('now')))")
    conn.commit()
    conn.close()

def save_call(phone, category, full_text, chat_id):
    today = date.today()
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT INTO calls (phone, category, full_text, chat_id, date, month) VALUES (?,?,?,?,?,?)", (phone, category, full_text, str(chat_id), str(today), today.strftime("%Y-%m")))
    conn.commit()
    conn.close()

def get_calls_by_month(month):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT phone, category, full_text, date FROM calls WHERE month=? ORDER BY date", (month,)).fetchall()
    conn.close()
    return rows

def get_all_months():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT DISTINCT month FROM calls ORDER BY month DESC").fetchall()
    conn.close()
    return [r[0] for r in rows]

PHONE_RE = re.compile(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}")
CATEGORY_RE = re.compile(r"\(([^)]+)\)\s*$")

def parse_call(text):
    phone_match = PHONE_RE.search(text)
    if not phone_match:
        return None
    phone = phone_match.group().strip()
    cat_match = CATEGORY_RE.search(text)
    if cat_match:
        category = cat_match.group(1).strip()
    else:
        after = text[phone_match.end():].strip()
        parts = after.split()
        category = parts[0].rstrip(".,;") if parts else "other"
    return phone, category

MONTHS_RU = {"01":"Январь","02":"Февраль","03":"Март","04":"Апрель","05":"Май","06":"Июнь","07":"Июль","08":"Август","09":"Сентябрь","10":"Октябрь","11":"Ноябрь","12":"Декабрь"}

def build_report(month):
    rows = get_calls_by_month(month)
    year, m = month.split("-")
    month_name = MONTHS_RU.get(m, m)
    if not rows:
        return f"За {month_name} {year} звонков не найдено."
    total = len(rows)
    cats = defaultdict(int)
    days = defaultdict(int)
    for _, cat, _, d in rows:
        cats[cat.lower()] += 1
        days[d] += 1
    lines = [f"Отчёт за {month_name} {year}", f"Всего звонков: {total}", f"Активных дней: {len(days)}", "", "По категориям:"]
    for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
        lines.append(f"  {cat}: {cnt} ({round(cnt/total*100)}%)")
    lines.append("")
    lines.append("Самые загруженные дни:")
    for d, cnt in sorted(days.items(), key=lambda x: -x[1])[:3]:
        dt = datetime.strptime(d, "%Y-%m-%d")
        lines.append(f"  {dt.strftime('%d.%m')} — {cnt} звонков")
    return "\n".join(lines)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    result = parse_call(msg.text.strip())
    if result:
        phone, category = result
        save_call(phone, category, msg.text, msg.chat_id)
        logger.info(f"Звонок: {phone} | {category}")

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = context.args[0] if context.args else date.today().strftime("%Y-%m")
    await update.message.reply_text(build_report(month))

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = str(date.today())
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT phone, category FROM calls WHERE date=? ORDER BY created_at", (today,)).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Сегодня звонков нет.")
        return
    lines = [f"Звонки за сегодня ({len(rows)}):"]
    for phone, cat in rows:
        lines.append(f"• {phone} — {cat}")
    await update.message.reply_text("\n".join(lines))

async def cmd_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    months = get_all_months()
    if not months:
        await update.message.reply_text("Данных пока нет.")
        return
    lines = ["Доступные месяцы:"]
    for m in months:
        rows = get_calls_by_month(m)
        year, mn = m.split("-")
        lines.append(f"  {MONTHS_RU.get(mn,mn)} {year}: {len(rows)} звонков — /report {m}")
    await update.message.reply_text("\n".join(lines))

async def send_monthly_report(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    prev = f"{today.year - 1}-12" if today.month == 1 else f"{today.year}-{str(today.month-1).zfill(2)}"
    await context.bot.send_message(chat_id=REPORT_CHAT_ID, text=build_report(prev))

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("months", cmd_months))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_monthly(send_monthly_report, when=datetime.strptime("09:00", "%H:%M").time(), day=1)
    logger.info("Бот работает!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
