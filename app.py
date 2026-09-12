import asyncio
from datetime import datetime
import os
import sqlite3
from flask import (
    Flask,
    jsonify,
    render_template_string,
    request,
    send_from_directory,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from werkzeug.utils import secure_filename

TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-app.onrender.com")

# 🔴 Список Telegram ID адміністраторів
ADMIN_IDS = [945268466]

DB_FILE = "class_budget.db"
UPLOAD_FOLDER = "receipts"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf"}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


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
                    receipt_filename TEXT,
                    PRIMARY KEY (student_id, collection_id)
                )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id INTEGER NOT NULL,
                    purpose TEXT NOT NULL,
                    amount REAL NOT NULL,
                    date_str TEXT NOT NULL,
                    receipt_filename TEXT
                )"""
    )

    try:
        c.execute("ALTER TABLE payments ADD COLUMN receipt_filename TEXT")
    except sqlite3.OperationalError:
        pass

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
        conn.commit()
    conn.close()


init_db()

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

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
        .nav button { flex: 1; padding: 10px 4px; border: none; background: #e5e5ea; border-radius: 8px; font-weight: bold; font-size: 12px; cursor: pointer; }
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
        button.form-btn { background: #34c759; color: white; border: none; font-weight: bold; cursor: pointer; }
        button.danger-btn { background: #ff3b30; color: white; border: none; font-weight: bold; cursor: pointer; }
        .badge { padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .plus { background: #d4edda; color: #155724; }
        .minus { background: #f8d7da; color: #721c24; }
        .info-text { font-size: 12px; color: #007aff; margin-top: -6px; margin-bottom: 10px; font-weight: 500; }
        .receipt-link { font-size: 12px; color: #007aff; text-decoration: underline; font-weight: bold; display: block; margin-top: 4px; }
    </style>
</head>
<body>
    <div class="nav">
        <button class="active" onclick="switchTab('view-tab', this)">📊 Збори</button>
        <button onclick="switchTab('expenses-tab', this)">📉 Витрати</button>
        <button id="admin-tab-btn" style="display:none;" onclick="switchTab('admin-tab', this)">⚙️ Адмінка</button>
    </div>

    <!-- ВКЛАДКА 1: ДЛЯ БАТЬКІВ (ЗБОРИ) -->
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

    <!-- ВКЛАДКА 2: ДЛЯ БАТЬКІВ (ВИРАТИ) -->
    <div id="expenses-tab" class="tab-content">
        <div class="card">
            <label><b>Фільтр витрат за збором:</b></label>
            <select id="expense-collection-filter" onchange="renderExpensesView()"></select>
        </div>

        <div class="card">
            <div class="stat-grid">
                <div class="stat-box">
                    <div class="title">Загальні витрати</div>
                    <div class="val" id="exp-total-amount" style="color:#d9534f;">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Чистий залишок</div>
                    <div class="val" id="exp-net-balance">0 грн</div>
                </div>
            </div>
        </div>

        <div class="card" style="overflow-x:auto;">
            <h3>📋 Реєстр використаних коштів</h3>
            <table>
                <thead>
                    <tr>
                        <th>Дата</th>
                        <th>Призначення (Збір)</th>
                        <th>Мета витрати</th>
                        <th>Сума</th>
                        <th>Чек</th>
                    </tr>
                </thead>
                <tbody id="expenses-table-body"></tbody>
            </table>
        </div>
    </div>

    <!-- ВКЛАДКА 3: АДМІНІСТРУВАННЯ -->
    <div id="admin-tab" class="tab-content">
        <div class="card">
            <h3>➕ Створити новий збір</h3>
            <label>Тип збору:</label>
            <select id="new-coll-type">
                <option value="0">🎯 Інший цільовий збір (екскурсія, театр тощо)</option>
                <option value="1">🏫 Фонд класу</option>
            </select>

            <label>Назва / призначення збору:</label>
            <input type="text" id="new-coll-name" placeholder="напр. Екскурсія в музей">
            
            <label>Потрібно з дитини (грн):</label>
            <input type="number" id="new-coll-target" placeholder="200">
            
            <button class="form-btn" onclick="createCollection()">Додати збір</button>
        </div>

        <div class="card">
            <h3>💳 Внести / Редагувати оплату</h3>
            <label>Оберіть збір:</label>
            <select id="select-collection" onchange="updatePaymentInput()"></select>
            
            <label>Оберіть учня:</label>
            <select id="select-student" onchange="updatePaymentInput()"></select>
            
            <label>Сума внеску (грн):</label>
            <div class="info-text" id="current-paid-hint">Поточна сплачена сума: 0 грн</div>
            <input type="number" id="pay-amount" placeholder="200">

            <label>🧾 Квитанція / Чек (опційно):</label>
            <input type="file" id="receipt-file" accept="image/*,.pdf">
            <div class="info-text" id="current-receipt-hint"></div>

            <button class="form-btn" onclick="savePayment()">Зберегти оплату</button>
        </div>

        <div class="card" style="border: 1px solid #ffd700;">
            <h3>📉 Додати витрату</h3>
            <label>Призначення (Збір):</label>
            <select id="expense-collection-select"></select>

            <label>Мета витрати (на що витрачено):</label>
            <input type="text" id="expense-purpose" placeholder="напр. Закупівля зошитів або Квитки">

            <label>Сума витрати (грн):</label>
            <input type="number" id="expense-amount" placeholder="450">

            <label>Дата витрати:</label>
            <input type="date" id="expense-date">

            <label>🧾 Чек / Квитанція (опційно):</label>
            <input type="file" id="expense-receipt-file" accept="image/*,.pdf">

            <button class="form-btn" style="background:#007aff;" onclick="saveExpense()">Зберегти витрату</button>
        </div>

        <div class="card" style="border: 1px solid #ffcccc;">
            <h3 style="color: #d9534f;">🗑 Видалити збір</h3>
            <label>Оберіть збір для видалення:</label>
            <select id="delete-collection-select"></select>
            <button class="danger-btn" onclick="deleteCollection()">Видалити збір</button>
        </div>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        let globalData = null;
        let currentUserId = tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 0;

        // Встановлення сьогоднішньої дати за замовчуванням
        document.getElementById('expense-date').valueAsDate = new Date();

        function switchTab(tabId, btn) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            btn.classList.add('active');
        }

        async function loadData() {
            const res = await fetch(`/api/budget?user_id=${currentUserId}`);
            globalData = await res.json();
            
            if(globalData.is_admin) {
                document.getElementById('admin-tab-btn').style.display = 'block';
            }

            // Селект для зборів (вкладка 1)
            const pSelect = document.getElementById('parent-collection-filter');
            pSelect.innerHTML = '<option value="all">🌐 Зведений звіт (Всі збори)</option>';
            
            // Селект для фільтру витрат (вкладка 2)
            const expFilter = document.getElementById('expense-collection-filter');
            expFilter.innerHTML = '<option value="all">🌐 Всі витрати</option>';

            globalData.collections.forEach(c => {
                const typePrefix = c.is_class_fund ? '🏫' : '📁';
                pSelect.innerHTML += `<option value="${c.id}">${typePrefix} ${c.name}</option>`;
                expFilter.innerHTML += `<option value="${c.id}">${typePrefix} ${c.name}</option>`;
            });

            // Адмінські селекти
            if(globalData.is_admin) {
                const collSelect = document.getElementById('select-collection');
                const expCollSelect = document.getElementById('expense-collection-select');
                const delSelect = document.getElementById('delete-collection-select');
                
                collSelect.innerHTML = '';
                expCollSelect.innerHTML = '';
                delSelect.innerHTML = '';

                if(globalData.collections.length === 0) {
                    collSelect.innerHTML = '<option value="">Немає активних зборів</option>';
                    expCollSelect.innerHTML = '<option value="">Немає активних зборів</option>';
                    delSelect.innerHTML = '<option value="">Немає активних зборів</option>';
                } else {
                    globalData.collections.forEach(c => {
                        collSelect.innerHTML += `<option value="${c.id}">${c.name} (${c.target_amount} грн/учень)</option>`;
                        expCollSelect.innerHTML += `<option value="${c.id}">${c.name}</option>`;
                        delSelect.innerHTML += `<option value="${c.id}">${c.name}</option>`;
                    });
                }

                const studSelect = document.getElementById('select-student');
                studSelect.innerHTML = '';
                globalData.students.forEach(s => {
                    studSelect.innerHTML += `<option value="${s.id}">${s.full_name}</option>`;
                });

                updatePaymentInput();
            }

            renderParentView();
            renderExpensesView();
        }

        function updatePaymentInput() {
            if(!globalData || !globalData.is_admin) return;
            const cId = parseInt(document.getElementById('select-collection').value);
            const sId = parseInt(document.getElementById('select-student').value);
            
            if(!cId || !sId) return;

            const student = globalData.students.find(s => s.id === sId);
            const payData = (student && student.payments[cId]) ? student.payments[cId] : { paid: 0, receipt: null };

            document.getElementById('pay-amount').value = payData.paid;
            document.getElementById('current-paid-hint').innerText = `Поточна сплачена сума у базі: ${payData.paid} грн`;

            const rHint = document.getElementById('current-receipt-hint');
            if(payData.receipt) {
                rHint.innerHTML = `📎 Є завантажена квитанція: <a href="/uploads/${payData.receipt}" target="_blank">Переглянути</a>`;
            } else {
                rHint.innerText = 'Квитанцію ще не прикріплено';
            }
        }

        function renderParentView() {
            if(!globalData) return;
            const selectedId = document.getElementById('parent-collection-filter').value;
            const tbody = document.getElementById('parent-table-body');
            tbody.innerHTML = '';

            if (selectedId === 'all') {
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
                const collId = parseInt(selectedId);
                const coll = globalData.collections.find(c => c.id === collId);
                if(!coll) return;

                let totalCollected = 0;
                const studentCount = globalData.students.length;
                const totalTarget = (coll.target_amount || 0) * studentCount;

                globalData.students.forEach(s => {
                    const pay = s.payments[collId] || { required: coll.target_amount, paid: 0, receipt: null };
                    totalCollected += pay.paid;
                    const bal = pay.paid - pay.required;
                    const balClass = bal >= 0 ? 'plus' : 'minus';
                    const statusText = bal >= 0 ? (pay.required > 0 ? 'Сплачено' : 'Внесок') : `Заборгованість: ${Math.abs(bal)} грн`;

                    let receiptHtml = '';
                    if(pay.receipt) {
                        receiptHtml = `<br><a class="receipt-link" href="/uploads/${pay.receipt}" target="_blank">🧾 Переглянути чек</a>`;
                    }

                    tbody.innerHTML += `
                        <tr>
                            <td>${s.id}</td>
                            <td><b>${s.full_name}</b><br><small style="color:#666">${s.parent_name}</small></td>
                            <td>${pay.paid} / ${pay.required} грн ${receiptHtml}</td>
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

        function renderExpensesView() {
            if(!globalData) return;
            const filterId = document.getElementById('expense-collection-filter').value;
            const tbody = document.getElementById('expenses-table-body');
            tbody.innerHTML = '';

            let filteredExpenses = globalData.expenses;
            if(filterId !== 'all') {
                filteredExpenses = globalData.expenses.filter(e => e.collection_id === parseInt(filterId));
            }

            let sumExp = 0;
            filteredExpenses.forEach(e => {
                sumExp += e.amount;
                let rHtml = e.receipt ? `<a class="receipt-link" href="/uploads/${e.receipt}" target="_blank">🧾 Чек</a>` : '-';
                tbody.innerHTML += `
                    <tr>
                        <td>${e.date_str}</td>
                        <td><b>${e.collection_name}</b></td>
                        <td>${e.purpose}</td>
                        <td style="color:#d9534f; font-weight:bold;">-${e.amount} грн</td>
                        <td>${rHtml}</td>
                    </tr>
                `;
            });

            document.getElementById('exp-total-amount').innerText = `${sumExp} грн`;
            
            // Чистий залишок = Всього зібрано - Всього витрачено
            let totalCollectedAll = globalData.students.reduce((acc, s) => acc + s.total_paid, 0);
            let totalExpAll = globalData.expenses.reduce((acc, e) => acc + e.amount, 0);
            let netBal = totalCollectedAll - totalExpAll;
            
            const netElem = document.getElementById('exp-net-balance');
            netElem.innerText = `${netBal} грн`;
            netElem.style.color = netBal >= 0 ? '#34c759' : '#d9534f';
        }

        async function createCollection() {
            const is_class_fund = document.getElementById('new-coll-type').value;
            const name = document.getElementById('new-coll-name').value;
            const target = document.getElementById('new-coll-target').value;
            if(!name) return alert('Вкажіть назву/призначення збору!');
            
            const res = await fetch('/api/add_collection', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_id: currentUserId,
                    name,
                    is_class_fund: parseInt(is_class_fund),
                    target_amount: parseFloat(target || 0)
                })
            });
            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Збір успішно створено!');
            document.getElementById('new-coll-name').value = '';
            document.getElementById('new-coll-target').value = '';
            loadData();
        }

        async function savePayment() {
            const collection_id = document.getElementById('select-collection').value;
            const student_id = document.getElementById('select-student').value;
            const paid = document.getElementById('pay-amount').value;
            const fileInput = document.getElementById('receipt-file');

            if(!collection_id) return alert('Оберіть активний збір!');
            if(paid === '') return alert('Вкажіть суму!');

            const formData = new FormData();
            formData.append('user_id', currentUserId);
            formData.append('student_id', student_id);
            formData.append('collection_id', collection_id);
            formData.append('paid', paid);

            if(fileInput.files.length > 0) {
                formData.append('receipt', fileInput.files[0]);
            }

            const res = await fetch('/api/save_payment', {
                method: 'POST',
                body: formData
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Оплату успішно збережено!');
            fileInput.value = '';
            loadData();
        }

        async function saveExpense() {
            const collection_id = document.getElementById('expense-collection-select').value;
            const purpose = document.getElementById('expense-purpose').value;
            const amount = document.getElementById('expense-amount').value;
            const date_str = document.getElementById('expense-date').value;
            const fileInput = document.getElementById('expense-receipt-file');

            if(!collection_id) return alert('Оберіть збір призначення!');
            if(!purpose) return alert('Вкажіть мету витрати!');
            if(!amount) return alert('Вкажіть суму витрати!');

            const formData = new FormData();
            formData.append('user_id', currentUserId);
            formData.append('collection_id', collection_id);
            formData.append('purpose', purpose);
            formData.append('amount', amount);
            formData.append('date_str', date_str);

            if(fileInput.files.length > 0) {
                formData.append('receipt', fileInput.files[0]);
            }

            const res = await fetch('/api/add_expense', {
                method: 'POST',
                body: formData
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Витрату успішно додано!');
            document.getElementById('expense-purpose').value = '';
            document.getElementById('expense-amount').value = '';
            fileInput.value = '';
            loadData();
        }

        async function deleteCollection() {
            const collection_id = document.getElementById('delete-collection-select').value;
            if(!collection_id) return alert('Немає збору для видалення!');

            if(!confirm('Ви дійсно бажаєте видалити цей збір та всі дані про його сплату й витрати?')) return;

            const res = await fetch('/api/delete_collection', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: currentUserId, collection_id})
            });
            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Збір видалено!');
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


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/api/budget")
def get_budget():
    user_id = int(request.args.get("user_id", 0))
    is_admin = user_id in ADMIN_IDS

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT id, name, is_class_fund, target_amount FROM collections")
    colls = [
        {
            "id": row[0],
            "name": row[1],
            "is_class_fund": row[2],
            "target_amount": row[3],
        }
        for row in c.fetchall()
    ]

    c.execute("SELECT id, full_name, parent_name FROM students")
    students = c.fetchall()

    student_list = []
    for s in students:
        s_id, full_name, parent_name = s

        c.execute(
            "SELECT collection_id, required, paid, receipt_filename FROM"
            " payments WHERE student_id=?",
            (s_id,),
        )
        p_rows = c.fetchall()
        payments_dict = {}
        total_paid = 0
        total_required = 0

        for pr in p_rows:
            c_id, req, paid, receipt = pr
            payments_dict[c_id] = {
                "required": req,
                "paid": paid,
                "receipt": receipt,
            }
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

    # Отримання списку всіх витрат
    c.execute(
        "SELECT e.id, e.collection_id, c.name, e.purpose, e.amount, e.date_str,"
        " e.receipt_filename FROM expenses e JOIN collections c ON"
        " e.collection_id = c.id ORDER BY e.id DESC"
    )
    expenses_list = [
        {
            "id": row[0],
            "collection_id": row[1],
            "collection_name": row[2],
            "purpose": row[3],
            "amount": row[4],
            "date_str": row[5],
            "receipt": row[6],
        }
        for row in c.fetchall()
    ]

    conn.close()
    return jsonify({
        "students": student_list,
        "collections": colls,
        "expenses": expenses_list,
        "is_admin": is_admin,
    })


@app.route("/api/add_collection", methods=["POST"])
def add_collection():
    data = request.json
    user_id = int(data.get("user_id", 0))

    if user_id not in ADMIN_IDS:
        return jsonify({"error": "Доступ заборонено! Ви не є адміністратором."})

    name = data.get("name")
    is_fund = data.get("is_class_fund", 0)
    target = data.get("target_amount", 0)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO collections (name, is_class_fund, target_amount) VALUES"
        " (?, ?, ?)",
        (name, is_fund, target),
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
    user_id = int(request.form.get("user_id", 0))

    if user_id not in ADMIN_IDS:
        return jsonify({"error": "Доступ заборонено! Ви не є адміністратором."})

    s_id = request.form.get("student_id")
    c_id = request.form.get("collection_id")
    paid = float(request.form.get("paid", 0))

    filename = None
    if "receipt" in request.files:
        file = request.files["receipt"]
        if file and allowed_file(file.filename):
            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = secure_filename(f"receipt_{s_id}_{c_id}.{ext}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    if filename:
        c.execute(
            "INSERT INTO payments (student_id, collection_id, paid,"
            " receipt_filename) VALUES (?, ?, ?, ?) ON CONFLICT(student_id,"
            " collection_id) DO UPDATE SET paid=EXCLUDED.paid,"
            " receipt_filename=EXCLUDED.receipt_filename",
            (s_id, c_id, paid, filename),
        )
    else:
        c.execute(
            "INSERT INTO payments (student_id, collection_id, paid) VALUES (?,"
            " ?, ?) ON CONFLICT(student_id, collection_id) DO UPDATE SET"
            " paid=EXCLUDED.paid",
            (s_id, c_id, paid),
        )

    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/add_expense", methods=["POST"])
def add_expense():
    user_id = int(request.form.get("user_id", 0))

    if user_id not in ADMIN_IDS:
        return jsonify({"error": "Доступ заборонено! Ви не є адміністратором."})

    c_id = request.form.get("collection_id")
    purpose = request.form.get("purpose")
    amount = float(request.form.get("amount", 0))
    date_str = request.form.get(
        "date_str", datetime.now().strftime("%Y-%m-%d")
    )

    filename = None
    if "receipt" in request.files:
        file = request.files["receipt"]
        if file and allowed_file(file.filename):
            ext = file.filename.rsplit(".", 1)[1].lower()
            timestamp = int(datetime.now().timestamp())
            filename = secure_filename(f"exp_{c_id}_{timestamp}.{ext}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO expenses (collection_id, purpose, amount, date_str,"
        " receipt_filename) VALUES (?, ?, ?, ?, ?)",
        (c_id, purpose, amount, date_str, filename),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/delete_collection", methods=["POST"])
def delete_collection():
    data = request.json
    user_id = int(data.get("user_id", 0))

    if user_id not in ADMIN_IDS:
        return jsonify({"error": "Доступ заборонено! Ви не є адміністратором."})

    c_id = data.get("collection_id")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM collections WHERE id=?", (c_id,))
    c.execute("DELETE FROM payments WHERE collection_id=?", (c_id,))
    c.execute("DELETE FROM expenses WHERE collection_id=?", (c_id,))
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
