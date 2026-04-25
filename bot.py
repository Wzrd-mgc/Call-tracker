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
    conn.execute("""CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT,
        status TEXT,
        category TEXT,
        customer_name TEXT,
        customer_address TEXT,
        appliance TEXT,
        time_slot TEXT,
        full_text TEXT,
        chat_id TEXT,
        date TEXT,
        month TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    conn.close()

def save_call(data, chat_id):
    today = date.today()
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""INSERT INTO calls 
        (phone, status, category, customer_name, customer_address, appliance, time_slot, full_text, chat_id, date, month)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (data.get("phone"), data.get("status"), data.get("category"),
         data.get("customer_name"), data.get("customer_address"),
         data.get("appliance"), data.get("time_slot"),
         data.get("full_text"), str(chat_id), str(today), today.strftime("%Y-%m")))
    conn.commit()
    conn.close()

def get_calls_by_month(month):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT phone, status, category, appliance, customer_name, date FROM calls WHERE month=? ORDER BY date",
        (month,)).fetchall()
    conn.close()
    return rows

def get_all_months():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT DISTINCT month FROM calls ORDER BY month DESC").fetchall()
    conn.close()
    return [r[0] for r in rows]

# ── Парсер ──────────────────────────────────────────────────────────────────

PHONE_RE = re.compile(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
TIME_RE  = re.compile(r"\b\d{1,2}[-–]\d{1,2}\s*(?:am|pm)?\b", re.I)

STATUS_MAP = [
    ("booked",         re.compile(r"\bbooked\b", re.I)),
    ("not booked",     re.compile(r"\bnot\s*booked\b", re.I)),
    ("dispute",        re.compile(r"\bdispute\b", re.I)),
    ("no answer",      re.compile(r"\bno\s*answer\b|\bleft\s*voicemail\b", re.I)),
    ("wrong number",   re.compile(r"\bwrong\s*number\b", re.I)),
    ("will call back", re.compile(r"\bwill\s*call\s*back\b|\bcall\s*back\b", re.I)),
    ("cheap customer", re.compile(r"\bcheap\b", re.I)),
    ("no voice",       re.compile(r"\bno\s*voice\b", re.I)),
]

CATEGORY_RE = re.compile(r"\(([a-zA-Z][^)]{2,40})\)")

APPLIANCE_KEYWORDS = [
    "dryer","washer","refrigerator","fridge","oven","stove","dishwasher",
    "microwave","freezer","range","cooktop","vent","hood","garbage disposal",
    "ice maker","wine cooler","small appliance"
]

def detect_appliance(text):
    t = text.lower()
    for kw in APPLIANCE_KEYWORDS:
        if kw in t:
            return kw
    return None

def parse_call(text):
    """Разбирает сообщение диспетчера любого формата."""
    if not PHONE_RE.search(text):
        return None

    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    data = {"full_text": text}

    # Телефон — ищем в первой строке с номером
    for line in lines:
        m = PHONE_RE.search(line)
        if m:
            data["phone"] = m.group().strip()
            break

    # Статус
    data["status"] = "unknown"
    for status_name, pattern in STATUS_MAP:
        if pattern.search(text):
            data["status"] = status_name
            break

    # Категория в скобках
    data["category"] = "other"
    for m in CATEGORY_RE.finditer(text):
        val = m.group(1).strip().lower()
        data["category"] = val
        break

    # Аппарат
    data["appliance"] = detect_appliance(text) or data["category"]

    # Email → значит есть карточка клиента
    email_match = EMAIL_RE.search(text)
    if email_match:
        email_line_idx = next((i for i, l in enumerate(lines) if "@" in l), -1)
        # Имя клиента — строка перед email (если не номер телефона)
        if email_line_idx > 0:
            prev = lines[email_line_idx - 1]
            if not PHONE_RE.search(prev):
                data["customer_name"] = prev

        # Адрес — строка после email
        if email_line_idx >= 0 and email_line_idx + 1 < len(lines):
            nxt = lines[email_line_idx + 1]
            if re.search(r"\d+.*[A-Za-z]", nxt):
                data["customer_address"] = nxt

        # Аппарат — ищем в строках после адреса
        for line in lines[email_line_idx:]:
            appl = detect_appliance(line)
            if appl:
                data["appliance"] = appl
                break

    # Время
    time_match = TIME_RE.search(text)
    if time_match:
        data["time_slot"] = time_match.group().strip()

    return data

# ── Отчёт ───────────────────────────────────────────────────────────────────

MONTHS_RU = {
    "01":"Январь","02":"Февраль","03":"Март","04":"Апрель",
    "05":"Май","06":"Июнь","07":"Июль","08":"Август",
    "09":"Сентябрь","10":"Октябрь","11":"Ноябрь","12":"Декабрь"
}

def build_report(month):
    rows = get_calls_by_month(month)
    year, m = month.split("-")
    month_name = MONTHS_RU.get(m, m)
    if not rows:
        return f"За {month_name} {year} звонков не найдено."

    total = len(rows)
    statuses = defaultdict(int)
    appliances = defaultdict(int)
    days = defaultdict(int)

    for _, status, _, appliance, _, d in rows:
        statuses[status or "unknown"] += 1
        if appliance:
            appliances[appliance] += 1
        days[d] += 1

    booked_cnt = statuses.get("booked", 0)
    conversion = round(booked_cnt / total * 100) if total else 0

    lines = [
        f"📊 Отчёт за {month_name} {year}",
        f"",
        f"📞 Всего звонков: {total}",
        f"✅ Записано: {booked_cnt} ({conversion}%)",
        f"📅 Активных дней: {len(days)}",
        f"",
        f"По статусу:",
    ]
    for s, cnt in sorted(statuses.items(), key=lambda x: -x[1]):
        pct = round(cnt / total * 100)
        lines.append(f"  {s}: {cnt} ({pct}%)")

    if appliances:
        lines.append("")
        lines.append("По технике:")
        for appl, cnt in sorted(appliances.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"  {appl}: {cnt}")

    if days:
        lines.append("")
        lines.append("Топ дни:")
        for d, cnt in sorted(days.items(), key=lambda x: -x[1])[:3]:
            dt = datetime.strptime(d, "%Y-%m-%d")
            lines.append(f"  {dt.strftime('%d.%m')} — {cnt} звонков")

    return "\n".join(lines)

# ── Хендлеры ────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    result = parse_call(msg.text.strip())
    if result:
        save_call(result, msg.chat_id)
        logger.info(f"Звонок: {result.get('phone')} | {result.get('status')} | {result.get('appliance')}")

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = context.args[0] if context.args else date.today().strftime("%Y-%m")
    await update.message.reply_text(build_report(month))

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = str(date.today())
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT phone, status, appliance, customer_name, time_slot FROM calls WHERE date=? ORDER BY created_at",
        (today,)).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Сегодня звонков нет.")
        return
    lines = [f"📞 Звонки за сегодня ({len(rows)}):",""]
    for phone, status, appliance, name, time_slot in rows:
        line = f"• {phone} — {status}"
        if appliance and appliance != "other":
            line += f" ({appliance})"
        if name:
            line += f" | {name}"
        if time_slot:
            line += f" | {time_slot}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines))

async def cmd_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    months = get_all_months()
    if not months:
        await update.message.reply_text("Данных пока нет.")
        return
    lines = ["📅 Доступные месяцы:"]
    for m in months:
        rows = get_calls_by_month(m)
        year, mn = m.split("-")
        lines.append(f"  {MONTHS_RU.get(mn,mn)} {year}: {len(rows)} зв. — /report {m}")
    await update.message.reply_text("\n".join(lines))

async def send_monthly_report(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    prev = f"{today.year-1}-12" if today.month == 1 else f"{today.year}-{str(today.month-1).zfill(2)}"
    await context.bot.send_message(chat_id=REPORT_CHAT_ID, text=build_report(prev))

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("months", cmd_months))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_monthly(
        send_monthly_report,
        when=datetime.strptime("09:00", "%H:%M").time(),
        day=1)
    logger.info("Бот работает!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
