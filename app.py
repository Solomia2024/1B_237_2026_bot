import asyncio
import os
import sqlite3
from flask import Flask, jsonify, render_template_string
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-app.onrender.com")

DB_FILE = "class_budget.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    parent_name TEXT,
                    phone TEXT
                )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    is_class_fund INTEGER DEFAULT 0
                )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS payments (
                    student_id INTEGER,
                    collection_id INTEGER,
                    required REAL DEFAULT 0,
                    paid REAL DEFAULT 0,
                    PRIMARY KEY (student_id, collection_id)
                )"""
    )

    c.execute("SELECT COUNT(*) FROM students")
    if c.fetchone()[0] == 0:
        students = [
            (1, "Акобян Мане", "Лілія Маргарян", "+380671818081"),
            (2, "Ахмедова Мілана", "Севіна Ахмедова", "+380972845351"),
            (3, "Бурнаш Анастасія", "Ірина Лісняк", "+380504103930"),
            (4, "Головко Вікторія", "Головко Антоніна", "+380979689265"),
            (5, "Горова Катерина", "Сетько Тетяна", "+380973122645"),
            (6, "Коваленко Дмитро", "Сніжана Коваленко", "+380953050512"),
            (7, "Коваленко Еліна", "Коваленко Анастасія", "+380992825447"),
            (8, "Негода Софія", "Дар'я Негода", "+380673181265"),
            (9, "Носач Орест", "Анна Носач", "+380639719067"),
            (10, "Ображей Денис", "Марія Ображей", "+380636029707"),
            (11, "Покотецький Дмитро", "Савчук Олександра", "+380507374130"),
            (12, "Рябих Кьяра", "Рябих Тетяна", "+380992070428"),
            (13, "Скидан Матвій", "Ганна Скидан", "+380978410845"),
            (14, "Стародуб Кирило", "Стародуб Ірина", "+380678400930"),
            (15, "Улізько Дмитро", "Оксана Улізько", "+380637663841"),
            (16, "Чиж Анна", "Чиж Аліна", "+380635046004"),
            (17, "Школяр Тимофій", "Школяр Анастасія", "+380991224316"),
            (18, "Шовнадзе Арсен", "Шовнадзе Суліко", "+380671773179"),
        ]
        c.executemany("INSERT INTO students VALUES (?, ?, ?, ?)", students)
        c.execute(
            "INSERT INTO collections (id, name, is_class_fund) VALUES (1,"
            " 'Фонд класу', 1)"
        )
        conn.commit()
    conn.close()


init_db()

# --- FLASK WEB SERVER ---
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Бюджет 1-Б класу</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f4f4f7; padding: 10px; margin:0; }
        h2 { color: #333; text-align: center; font-size: 18px; margin-bottom: 15px; }
        .table-container { overflow-x: auto; background: #fff; border-radius: 10px; padding: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { border: 1px solid #e0e0e0; padding: 8px; text-align: left; }
        th { background: #007aff; color: #fff; position: sticky; top: 0; }
        tr:nth-child(even) { background: #f9f9f9; }
        .badge { padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .plus { background: #d4edda; color: #155724; }
        .minus { background: #f8d7da; color: #721c24; }
    </style>
</head>
<body>
    <h2>📊 Реєстр бюджету 1-Б класу</h2>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>№</th>
                    <th>Учень</th>
                    <th>Батьки</th>
                    <th>Фонд класу</th>
                    <th>Загалом сплачено</th>
                    <th>Залишок</th>
                </tr>
            </thead>
            <tbody id="table-body"></tbody>
        </table>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        async function loadData() {
            const res = await fetch('/api/budget');
            const data = await res.json();
            const tbody = document.getElementById('table-body');
            tbody.innerHTML = '';

            data.forEach(s => {
                const balClass = s.balance >= 0 ? 'plus' : 'minus';
                tbody.innerHTML += `
                    <tr>
                        <td>${s.id}</td>
                        <td><b>${s.full_name}</b></td>
                        <td>${s.parent_name}</td>
                        <td>${s.fund_paid} грн</td>
                        <td>${s.total_paid} грн</td>
                        <td><span class="badge ${balClass}">${s.balance} грн</span></td>
                    </tr>
                `;
            });
        }
        loadData();
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/budget")
def get_budget():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, full_name, parent_name FROM students")
    students = c.fetchall()

    result = []
    for s in students:
        s_id, full_name, parent_name = s
        c.execute(
            "SELECT SUM(paid), SUM(required) FROM payments WHERE student_id=?",
            (s_id,),
        )
        row = c.fetchone()
        total_paid = row[0] or 0
        total_req = row[1] or 0

        c.execute(
            "SELECT SUM(p.paid) FROM payments p JOIN collections c ON"
            " p.collection_id=c.id WHERE p.student_id=? AND c.is_class_fund=1",
            (s_id,),
        )
        fund_paid = c.fetchone()[0] or 0

        result.append({
            "id": s_id,
            "full_name": full_name,
            "parent_name": parent_name,
            "fund_paid": fund_paid,
            "total_paid": total_paid,
            "balance": total_paid - total_req,
        })
    conn.close()
    return jsonify(result)


# --- TELEGRAM BOT HANDLER ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [
            InlineKeyboardButton(
                "📊 Відкрити бюджет класу", web_app=WebAppInfo(url=WEB_APP_URL)
            )
        ]
    ]
    await update.message.reply_text(
        "👋 Вітаємо в системі обліку бюджету 1-Б класу!\nНатисніть кнопку нижче"
        " для перегляду:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    telegram_app = ApplicationBuilder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.run_polling(drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    from threading import Thread

    Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
