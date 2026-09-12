import asyncio
import os
import sqlite3
from flask import Flask, jsonify, render_template_string, request
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
                    is_class_fund INTEGER DEFAULT 0,
                    target_amount REAL DEFAULT 0
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
            "INSERT INTO collections (id, name, is_class_fund, target_amount)"
            " VALUES (1, 'Фонд класу', 1, 0)"
        )
        conn.commit()
    conn.close()


init_db()

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
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f4f4f7; padding: 10px; margin:0; }
        .nav { display: flex; gap: 5px; margin-bottom: 15px; }
        .nav button { flex: 1; padding: 10px; border: none; background: #e5e5ea; border-radius: 8px; font-weight: bold; cursor: pointer; }
        .nav button.active { background: #007aff; color: white; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .card { background: white; border-radius: 10px; padding: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 15px; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
        .stat-box { background: #f8f9fa; border-radius: 8px; padding: 10px; text-align: center; border: 1px solid #eee; }
        .stat-box .title { font-size: 11px; color: #666; font-weight: bold; text-transform: uppercase; }
        .stat-box .val { font-size: 16px; font-weight: bold; color: #007aff; margin-top: 4px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { border: 1px solid #e0e0e0; padding: 8px; text-align: left; }
        th { background: #007aff; color: white; }
        select, input, button.form-btn { width: 100%; padding: 10px; margin-top: 6px; margin-bottom: 12px; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }
        button.form-btn { background: #34c759; color: white; border: none; font-weight: bold; }
        .badge { padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .plus { background: #d4edda; color: #155724; }
        .minus { background: #f8d7da; color: #721c24; }
    </style>
</head>
<body>
    <div class="nav">
        <button class="active" onclick="switchTab('view-tab', this)">📊 Статистика зборів</button>
        <button onclick="switchTab('admin-tab', this)">⚙️ Адмін-панель</button>
    </div>

    <!-- ВКЛАДКА 1: ПЕРЕГЛЯД ТА СТАТИСТИКА БАТЬКІВ -->
    <div id="view-tab" class="tab-content active">
        <div class="card">
            <label><b>Оберіть збір для аналізу:</b></label>
            <select id="parent-collection-filter" onchange="renderParentView()"></select>
        </div>

        <div class="card" id="stats-card">
            <div class="stat-grid">
                <div class="stat-box">
                    <div class="title">План з дитини</div>
                    <div class="val" id="stat-target">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Всього зібрано</div>
                    <div class="val" id="stat-total-collected">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Загальний план класу</div>
                    <div class="val" id="stat-total-target">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Прогрес збору</div>
                    <div class="val" id="stat-progress">0%</div>
                </div>
            </div>
        </div>

        <div class="card" style="overflow-x:auto;">
            <table>
                <thead>
                    <tr>
                        <th>№</th>
                        <th>Учень</th>
                        <th>Сплачено</th>
                        <th>Статус / Залишок</th>
                    </tr>
                </thead>
                <tbody id="parent-table-body"></tbody>
            </table>
        </div>
    </div>

    <!-- ВКЛАДКА 2: АДМІНІСТРУВАННЯ -->
    <div id="admin-tab" class="tab-content">
        <div class="card">
            <h3>➕ Створити новий збір</h3>
            <label>Назва збору:</label>
            <input type="text" id="new-coll-name" placeholder="напр. Екскурсія в музей">
            <label>Потрібно з дитини (грн):</label>
            <input type="number" id="new-coll-target" placeholder="200">
            <button class="form-btn" onclick="createCollection()">Додати збір</button>
        </div>

        <div class="card">
            <h3>💳 Внести / оновити оплату</h3>
            <label>Оберіть збір:</label>
            <select id="select-collection"></select>
            
            <label>Оберіть учня:</label>
            <select id="select-student"></select>
            
            <label>Внести суму (грн):</label>
            <input type="number" id="pay-amount" placeholder="500">
            <button class="form-btn" onclick="savePayment()">Зберегти оплату</button>
        </div>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        let globalData = null;

        function switchTab(tabId, btn) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            btn.classList.add('active');
        }

        async function loadData() {
            const res = await fetch('/api/budget');
            globalData = await res.json();
            
            // Заповнення списку зборів для батьків
            const pSelect = document.getElementById('parent-collection-filter');
            pSelect.innerHTML = '<option value="all">🌐 Зведений звіт (Всі збори)</option>';
            globalData.collections.forEach(c => {
                pSelect.innerHTML += `<option value="${c.id}">📁 ${c.name}</option>`;
            });

            // Заповнення селектів для адмінки
            const collSelect = document.getElementById('select-collection');
            collSelect.innerHTML = '';
            globalData.collections.forEach(c => {
                collSelect.innerHTML += `<option value="${c.id}">${c.name} (${c.target_amount} грн/учень)</option>`;
            });

            const studSelect = document.getElementById('select-student');
            studSelect.innerHTML = '';
            globalData.students.forEach(s => {
                studSelect.innerHTML += `<option value="${s.id}">${s.full_name}</option>`;
            });

            renderParentView();
        }

        function renderParentView() {
            if(!globalData) return;
            const selectedId = document.getElementById('parent-collection-filter').value;
            const tbody = document.getElementById('parent-table-body');
            tbody.innerHTML = '';

            if (selectedId === 'all') {
                // Зведений режим
                let totalCollectedAll = 0;
                let totalTargetAll = 0;

                globalData.students.forEach(s => {
                    totalCollectedAll += s.total_paid;
                    totalTargetAll += s.total_required;
                    const balClass = s.balance >= 0 ? 'plus' : 'minus';
                    tbody.innerHTML += `
                        <tr>
                            <td>${s.id}</td>
                            <td><b>${s.full_name}</b><br><small style="color:#666">${s.parent_name}</small></td>
                            <td>${s.total_paid} грн</td>
                            <td><span class="badge ${balClass}">${s.balance >= 0 ? '+' : ''}${s.balance} грн</span></td>
                        </tr>
                    `;
                });

                document.getElementById('stat-target').innerText = '-';
                document.getElementById('stat-total-collected').innerText = `${totalCollectedAll} грн`;
                document.getElementById('stat-total-target').innerText = `${totalTargetAll} грн`;
                const progress = totalTargetAll > 0 ? Math.round((totalCollectedAll / totalTargetAll) * 100) : 100;
                document.getElementById('stat-progress').innerText = `${progress}%`;

            } else {
                // Режим конкретного збору
                const collId = parseInt(selectedId);
                const coll = globalData.collections.find(c => c.id === collId);
                let totalCollected = 0;
                const studentCount = globalData.students.length;
                const totalTarget = (coll.target_amount || 0) * studentCount;

                globalData.students.forEach(s => {
                    const pay = s.payments[collId] || { required: coll.target_amount, paid: 0 };
                    totalCollected += pay.paid;
                    const bal = pay.paid - pay.required;
                    const balClass = bal >= 0 ? 'plus' : 'minus';
                    const statusText = bal >= 0 ? (pay.required > 0 ? 'Сплачено' : 'Внесок') : `Заборгованість: ${Math.abs(bal)} грн`;

                    tbody.innerHTML += `
                        <tr>
                            <td>${s.id}</td>
                            <td><b>${s.full_name}</b><br><small style="color:#666">${s.parent_name}</small></td>
                            <td>${pay.paid} / ${pay.required} грн</td>
                            <td><span class="badge ${balClass}">${statusText}</span></td>
                        </tr>
                    `;
                });

                document.getElementById('stat-target').innerText = `${coll.target_amount} грн`;
                document.getElementById('stat-total-collected').innerText = `${totalCollected} грн`;
                document.getElementById('stat-total-target').innerText = `${totalTarget} грн`;
                const progress = totalTarget > 0 ? Math.round((totalCollected / totalTarget) * 100) : (totalCollected > 0 ? 100 : 0);
                document.getElementById('stat-progress').innerText = `${progress}%`;
            }
        }

        async function createCollection() {
            const name = document.getElementById('new-coll-name').value;
            const target = document.getElementById('new-coll-target').value;
            if(!name) return alert('Вкажіть назву збору!');
            
            await fetch('/api/add_collection', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, target_amount: parseFloat(target || 0)})
            });
            alert('Збір успішно створено!');
            document.getElementById('new-coll-name').value = '';
            document.getElementById('new-coll-target').value = '';
            loadData();
        }

        async function savePayment() {
            const collection_id = document.getElementById('select-collection').value;
            const student_id = document.getElementById('select-student').value;
            const paid = document.getElementById('pay-amount').value;
            if(!paid) return alert('Вкажіть суму!');

            await fetch('/api/save_payment', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({student_id, collection_id, paid: parseFloat(paid)})
            });
            alert('Оплату збережено!');
            document.getElementById('pay-amount').value = '';
            loadData();
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

    c.execute("SELECT id, name, target_amount FROM collections")
    colls = [
        {"id": row[0], "name": row[1], "target_amount": row[2]}
        for row in c.fetchall()
    ]

    c.execute("SELECT id, full_name, parent_name FROM students")
    students = c.fetchall()

    student_list = []
    for s in students:
        s_id, full_name, parent_name = s

        # Деталізація за кожним збором окремо
        c.execute(
            "SELECT collection_id, required, paid FROM payments WHERE"
            " student_id=?",
            (s_id,),
        )
        p_rows = c.fetchall()
        payments_dict = {}
        total_paid = 0
        total_required = 0

        for pr in p_rows:
            c_id, req, paid = pr
            payments_dict[c_id] = {"required": req, "paid": paid}
            total_paid += paid
            total_required += req

        student_list.append({
            "id": s_id,
            "full_name": full_name,
            "parent_name": parent_name,
            "payments": payments_dict,
            "total_paid": total_paid,
            "total_required": total_required,
            "balance": total_paid - total_required,
        })

    conn.close()
    return jsonify({"students": student_list, "collections": colls})


@app.route("/api/add_collection", methods=["POST"])
def add_collection():
    data = request.json
    name = data.get("name")
    target = data.get("target_amount", 0)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO collections (name, target_amount) VALUES (?, ?)",
        (name, target),
    )
    coll_id = c.lastrowid

    c.execute("SELECT id FROM students")
    students = c.fetchall()
    for s in students:
        c.execute(
            "INSERT INTO payments (student_id, collection_id, required, paid)"
            " VALUES (?, ?, ?, 0)",
            (s[0], coll_id, target),
        )

    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/save_payment", methods=["POST"])
def save_payment():
    data = request.json
    s_id = data.get("student_id")
    c_id = data.get("collection_id")
    paid = data.get("paid", 0)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO payments (student_id, collection_id, paid) VALUES (?,"
        " ?, ?) ON CONFLICT(student_id, collection_id) DO UPDATE SET"
        " paid=paid+EXCLUDED.paid",
        (s_id, c_id, paid),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


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
