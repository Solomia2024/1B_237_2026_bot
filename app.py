import os
import io
import json
import traceback
from datetime import datetime
import psycopg
from psycopg.rows import dict_row
from flask import Flask, render_template_string, request, jsonify, redirect
from werkzeug.utils import secure_filename

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "channel_binding=" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("&channel_binding=")[0]

# 🔴 Параметри Google Drive (OAuth 2.0) з очисткою пробілів
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()

# 🔴 Головні адміністратори
DEFAULT_ADMIN_IDS = [945268466, 114251065]

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Робота з Google Drive (через OAuth Refresh Token особистого акаунта) ---
def get_drive_service():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        print("❌ DRIVE ERROR: Відсутні OAuth параметри (CLIENT_ID, CLIENT_SECRET або REFRESH_TOKEN) у Render!", flush=True)
        return None
    try:
        creds = Credentials(
            token=None,
            refresh_token=GOOGLE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"❌ DRIVE ERROR: Помилка авторизації OAuth: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return None

def upload_file_to_drive(file_storage, filename):
    print(f"🚀 ПОЧАТОК ЗАВАНТАЖЕННЯ НА GOOGLE DRIVE (OAuth v2): {filename}", flush=True)

    folder_id = GOOGLE_DRIVE_FOLDER_ID
    if not folder_id:
        print("❌ DRIVE ERROR: Відсутня змінна GOOGLE_DRIVE_FOLDER_ID у Render!", flush=True)
        return None

    service = get_drive_service()
    if not service:
        print("❌ DRIVE ERROR: Не вдалося створити Google Drive Service!", flush=True)
        return None

    try:
        file_storage.seek(0)
        file_bytes = file_storage.read()
        
        if not file_bytes:
            print("❌ DRIVE ERROR: Файл порожній (0 байт)!", flush=True)
            return None

        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        
        media = MediaIoBaseUpload(
            io.BytesIO(file_bytes),
            mimetype=file_storage.mimetype or 'application/octet-stream',
            resumable=False
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()

        file_id = file.get('id')

        # Надаємо доступ на читання всім за посиланням
        service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()

        web_link = file.get('webViewLink')
        print(f"✅ DRIVE SUCCESS! URL: {web_link}", flush=True)
        return web_link
    except Exception as e:
        print(f"❌ DRIVE EXCEPTION: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return None

# --- Робота з БД ---
def get_db_connection():
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        return conn
    except Exception as e:
        print(f"Помилка БД: {e}", flush=True)
        return None

def is_user_admin(user_id):
    if not user_id:
        return False
    try:
        u_id = int(user_id)
    except (ValueError, TypeError):
        return False

    if u_id in DEFAULT_ADMIN_IDS:
        return True

    conn = get_db_connection()
    if not conn:
        return False
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id=%s", (u_id,))
    res = c.fetchone()
    c.close()
    conn.close()
    return True if res else False

def init_db():
    conn = get_db_connection()
    if not conn:
        return

    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS admins (user_id BIGINT PRIMARY KEY, comment TEXT)''')
    for admin_id in DEFAULT_ADMIN_IDS:
        c.execute("INSERT INTO admins (user_id, comment) VALUES (%s, %s) ON CONFLICT DO NOTHING", (admin_id, 'Головний адмін'))

    c.execute('''CREATE TABLE IF NOT EXISTS students (
                    id SERIAL PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    parent_name TEXT,
                    phone TEXT,
                    date_added TEXT NOT NULL,
                    is_active INT DEFAULT 1
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS collections (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_class_fund INT DEFAULT 0,
                    is_optional INT DEFAULT 0,
                    is_selective INT DEFAULT 0,
                    target_amount NUMERIC DEFAULT 0,
                    created_at TEXT NOT NULL
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS payments (
                    student_id INT,
                    collection_id INT,
                    required NUMERIC DEFAULT 0,
                    paid NUMERIC DEFAULT 0,
                    receipt_filename TEXT,
                    PRIMARY KEY (student_id, collection_id)
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    collection_id INT NOT NULL,
                    purpose TEXT NOT NULL,
                    amount NUMERIC NOT NULL,
                    date_str TEXT NOT NULL,
                    receipt_filename TEXT
                )''')

    conn.commit()

    c.execute("SELECT COUNT(*) as count FROM students")
    count_res = c.fetchone()
    if count_res['count'] == 0:
        students = [
            ('Акобян Мане', 'Лілія Маргарян', '+380671818081', '2026-01-01', 1),
            ('Ахмедова Мілана', 'Севіна Ахмедова', '+380972845351', '2026-01-01', 1),
            ('Бурнаш Анастасія', 'Ірина Лісняк', '+380504103930', '2026-01-01', 1),
            ('Головко Вікторія', 'Головко Антоніна', '+380979689265', '2026-01-01', 1),
            ('Горова Катерина', 'Сетько Тетяна', '+380973122645', '2026-01-01', 1),
            ('Коваленко Дмитро', 'Сніжана Коваленко', '+380953050512', '2026-01-01', 1),
            ('Коваленко Еліна', 'Коваленко Анастасія', '+380992825447', '2026-01-01', 1),
            ('Негода Софія', "Дар'я Негода", '+380673181265', '2026-01-01', 1),
            ('Носач Орест', 'Анна Носач', '+380639719067', '2026-01-01', 1),
            ('Ображей Денис', 'Марія Ображей', '+380636029707', '2026-01-01', 1),
            ('Покотецький Дмитро', 'Савчук Олександра', '+380507374130', '2026-01-01', 1),
            ('Рябих Кьяра', 'Рябих Тетяна', '+380992070428', '2026-01-01', 1),
            ('Скидан Матвій', 'Ганна Скидан', '+380978410845', '2026-01-01', 1),
            ('Стародуб Кирило', 'Стародуб Ірина', '+380678400930', '2026-01-01', 1),
            ('Улізько Дмитро', 'Оксана Улізько', '+380637663841', '2026-01-01', 1),
            ('Чиж Анна', 'Чиж Аліна', '+380635046004', '2026-01-01', 1),
            ('Школяр Тимофій', 'Школяр Анастасія', '+380991224316', '2026-01-01', 1),
            ('Шовнадзе Арсен', 'Шовнадзе Суліко', '+380671773179', '2026-01-01', 1)
        ]
        c.executemany("INSERT INTO students (full_name, parent_name, phone, date_added, is_active) VALUES (%s, %s, %s, %s, %s)", students)
        conn.commit()

    c.close()
    conn.close()

try:
    init_db()
except Exception as e:
    print(f"Помилка БД: {e}", flush=True)

app = Flask(__name__)

# --- 🔐 АВТОМАТИЧНИЙ ГЕНЕРАТОР REFRESH TOKEN ---
@app.route('/auth')
def auth():
    client_id = GOOGLE_CLIENT_ID
    client_secret = GOOGLE_CLIENT_SECRET
    
    print(f"📋 [AUTH DEBUG] GOOGLE_CLIENT_ID: '{client_id[:20]}...' (Довжина: {len(client_id)})", flush=True)

    redirect_uri = "https://oneb-237-2026-bot.onrender.com/oauth2callback"
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=['https://www.googleapis.com/auth/drive.file'],
        redirect_uri=redirect_uri
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent',
        include_granted_scopes='true'
    )
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    client_id = GOOGLE_CLIENT_ID
    client_secret = GOOGLE_CLIENT_SECRET

    redirect_uri = "https://oneb-237-2026-bot.onrender.com/oauth2callback"
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=['https://www.googleapis.com/auth/drive.file'],
        redirect_uri=redirect_uri
    )
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    
    return f"""
    <h2>✅ Авторизація успішна!</h2>
    <p>Ось ваш новий <b>GOOGLE_REFRESH_TOKEN</b>:</p>
    <textarea rows="4" cols="80" style="font-size:14px; padding:10px;">{credentials.refresh_token}</textarea>
    <p>Скопіюйте його та вставте у змінну <b>GOOGLE_REFRESH_TOKEN</b> у Render!</p>
    """

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Бюджет 1-Б класу (v6.0 OAuth2)</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f4f4f7; padding: 10px; margin:0; }
        .nav { display: flex; gap: 4px; margin-bottom: 15px; }
        .nav button { flex: 1; padding: 10px 2px; border: none; background: #e5e5ea; border-radius: 8px; font-weight: bold; font-size: 11px; cursor: pointer; }
        .nav button.active { background: #007aff; color: white; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .card { background: white; border-radius: 10px; padding: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 15px; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
        .stat-box { background: #f8f9fa; border-radius: 8px; padding: 10px; text-align: center; border: 1px solid #eee; }
        .stat-box .title { font-size: 11px; color: #666; font-weight: bold; text-transform: uppercase; }
        .stat-box .val { font-size: 15px; font-weight: bold; color: #007aff; margin-top: 4px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { border: 1px solid #e0e0e0; padding: 8px; text-align: left; }
        th { background: #007aff; color: white; }
        tfoot tr td { background: #f8f9fa; font-weight: bold; }
        select, input, button.form-btn { width: 100%; padding: 10px; margin-top: 6px; margin-bottom: 12px; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }
        button.form-btn { background: #34c759; color: white; border: none; font-weight: bold; cursor: pointer; }
        button.danger-btn { background: #ff3b30; color: white; border: none; font-weight: bold; cursor: pointer; }
        button.action-btn { padding: 4px 8px; border-radius: 4px; font-size: 11px; border: none; cursor: pointer; font-weight: bold; }
        .badge { padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .plus { background: #d4edda; color: #155724; }
        .minus { background: #f8d7da; color: #721c24; }
        .opt-badge { background: #fff3cd; color: #856404; }
        .neutral-badge { background: #e2e3e5; color: #383d41; }
        .info-text { font-size: 12px; color: #007aff; margin-top: -6px; margin-bottom: 10px; font-weight: 500; }
        .receipt-link { font-size: 12px; color: #007aff; text-decoration: underline; font-weight: bold; display: block; margin-top: 4px; }
        .cat-title { font-size: 15px; font-weight: bold; margin-bottom: 8px; border-bottom: 2px solid #007aff; padding-bottom: 4px; color: #333; }
        .date-range-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .student-checkbox-list { max-height: 180px; overflow-y: auto; border: 1px solid #ccc; padding: 8px; border-radius: 6px; margin-bottom: 12px; background: #fafafa; }
        .student-checkbox-item { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; cursor: pointer; }
        .student-checkbox-item input { width: auto; margin: 0; }
        .admin-item { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px dashed #eee; }
    </style>
</head>
<body>
    <div class="nav">
        <button class="active" onclick="switchTab('view-tab', this)">📋 Учні</button>
        <button onclick="switchTab('categories-tab', this)">📊 Категорії</button>
        <button onclick="switchTab('expenses-tab', this)">📉 Витрати</button>
        <button id="student-detail-tab-btn" style="display:none;" onclick="switchTab('student-detail-tab', this)">👤 Деталізація</button>
        <button id="contacts-tab-btn" style="display:none;" onclick="switchTab('contacts-tab', this)">👥 Контакти</button>
        <button id="admin-tab-btn" style="display:none;" onclick="switchTab('admin-tab', this)">⚙️ Адмінка</button>
    </div>

    <!-- ВКЛАДКА 1: УЧНІ -->
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

    <!-- ВКЛАДКА 2: КАТЕГОРІЇ ТА ГРАФІК -->
    <div id="categories-tab" class="tab-content">
        <div class="card">
            <label><b>📅 Період аналізу даних:</b></label>
            <div class="date-range-grid">
                <div>
                    <small>Від:</small>
                    <input type="date" id="cat-start-date" onchange="loadData()">
                </div>
                <div>
                    <small>До:</small>
                    <input type="date" id="cat-end-date" onchange="loadData()">
                </div>
            </div>
        </div>

        <div class="card">
            <div class="cat-title">📈 Порівняльний графік бюджету</div>
            <canvas id="budgetChart" style="max-height: 250px;"></canvas>
        </div>

        <div class="card">
            <div class="cat-title">🏫 Фонд класу</div>
            <div class="stat-grid">
                <div class="stat-box">
                    <div class="title">Потрібно зібрати</div>
                    <div class="val" id="cat-fund-target">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Зібрано коштів</div>
                    <div class="val" id="cat-fund-paid" style="color:#34c759;">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Витрачено</div>
                    <div class="val" id="cat-fund-exp" style="color:#d9534f;">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Залишок</div>
                    <div class="val" id="cat-fund-bal">0 грн</div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="cat-title">🎯 Інші збори загалом</div>
            <div class="stat-grid">
                <div class="stat-box">
                    <div class="title">Потрібно зібрати</div>
                    <div class="val" id="cat-other-target">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Зібрано коштів</div>
                    <div class="val" id="cat-other-paid" style="color:#34c759;">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Витрачено</div>
                    <div class="val" id="cat-other-exp" style="color:#d9534f;">0 грн</div>
                </div>
                <div class="stat-box">
                    <div class="title">Залишок</div>
                    <div class="val" id="cat-other-bal">0 грн</div>
                </div>
            </div>
        </div>
    </div>

    <!-- ВКЛАДКА 3: ВИРАТИ -->
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
                        <th id="exp-actions-th" style="display:none;">Дії</th>
                    </tr>
                </thead>
                <tbody id="expenses-table-body"></tbody>
            </table>
        </div>
    </div>

    <!-- ВКЛАДКА 4: ДЕТАЛІЗАЦІЯ ПО УЧНЮ (АДМІН) -->
    <div id="student-detail-tab" class="tab-content">
        <div class="card" style="border: 2px solid #007aff;">
            <h3>👤 Персональна статистика учня</h3>
            <label>Оберіть учня для перегляду:</label>
            <select id="admin-student-report-select" onchange="renderStudentReport()"></select>

            <div style="overflow-x:auto; margin-top: 15px;">
                <table>
                    <thead>
                        <tr>
                            <th>Збір</th>
                            <th>Потрібно</th>
                            <th>Здано</th>
                            <th>Баланс</th>
                        </tr>
                    </thead>
                    <tbody id="admin-student-report-body"></tbody>
                    <tfoot>
                        <tr>
                            <td><b>Всього</b></td>
                            <td id="stud-rep-tot-req">0 грн</td>
                            <td id="stud-rep-tot-paid">0 грн</td>
                            <td id="stud-rep-tot-bal">0 грн</td>
                        </tr>
                    </tfoot>
                </table>
            </div>
        </div>
    </div>

    <!-- ВКЛАДКА 5: КОНТАКТИ (АДМІН) -->
    <div id="contacts-tab" class="tab-content">
        <div class="card" style="border: 1px solid #34c759;">
            <h3 id="student-form-title">➕ Додати нового учня</h3>
            <input type="hidden" id="edit-student-id" value="">
            
            <label>ПІБ Учня:</label>
            <input type="text" id="stud-fullname" placeholder="напр. Іванов Іван">

            <label>ПІБ Батьків / Представника:</label>
            <input type="text" id="stud-parentname" placeholder="напр. Іванова Олена">

            <label>Телефон:</label>
            <input type="text" id="stud-phone" placeholder="+380XXXXXXXXX">

            <label>Дата зарахування у клас:</label>
            <input type="date" id="stud-date-added">

            <button class="form-btn" id="save-student-btn" onclick="saveStudent()">Зберегти учня</button>
            <button class="danger-btn" id="cancel-edit-btn" style="display:none;" onclick="resetStudentForm()">Скасувати редагування</button>
        </div>

        <div class="card" style="overflow-x:auto;">
            <h3>👥 Реєстр учнів класу</h3>
            <table>
                <thead>
                    <tr>
                        <th>Учень</th>
                        <th>Батьки / Телефон</th>
                        <th>Дата вступу</th>
                        <th>Статус</th>
                        <th>Дії</th>
                    </tr>
                </thead>
                <tbody id="contacts-table-body"></tbody>
            </table>
        </div>
    </div>

    <!-- ВКЛАДКА 6: АДМІНІСТРУВАННЯ -->
    <div id="admin-tab" class="tab-content">
        <div class="card" style="border: 2px solid #ff9500;">
            <h3>👑 Управління адміністраторами</h3>
            <label>Telegram ID нового адміна:</label>
            <input type="number" id="new-admin-id" placeholder="напр. 123456789">
            
            <label>Примітка / Ім'я:</label>
            <input type="text" id="new-admin-comment" placeholder="напр. Олена (Голова ПК)">

            <button class="form-btn" style="background:#ff9500;" onclick="addNewAdmin()">Додати адміністратора</button>

            <h4 style="margin-top:15px; margin-bottom:8px;">Список діючих адмінів:</h4>
            <div id="admin-list-container" style="font-size:13px;"></div>
        </div>

        <div class="card">
            <h3>➕ Створити новий збір</h3>
            <label>Тип збору:</label>
            <select id="new-coll-type" onchange="toggleTargetInput()">
                <option value="0">🎯 Інший цільовий збір (всі учні класу)</option>
                <option value="1">🏫 Фонд класу</option>
                <option value="2">💛 За бажанням / Хто скільки зможе</option>
                <option value="3">👥 Збір для окремих учнів (підгрупа)</option>
            </select>

            <label>Назва / призначення збору:</label>
            <input type="text" id="new-coll-name" placeholder="напр. Поїздка групи або Театр">
            
            <div id="target-amount-box">
                <label>Потрібно з дитини (грн):</label>
                <input type="number" id="new-coll-target" placeholder="200">
            </div>

            <div id="student-selection-box" style="display:none; margin-top:10px;">
                <label><b>Оберіть учнів, які беруть участь у зборі:</b></label>
                <div style="margin-bottom:6px;">
                    <button type="button" class="action-btn" style="background:#007aff; color:white;" onclick="selectAllStudentsForColl(true)">Обрати всіх</button>
                    <button type="button" class="action-btn" style="background:#6c757d; color:white;" onclick="selectAllStudentsForColl(false)">Зняти всіх</button>
                </div>
                <div class="student-checkbox-list" id="coll-students-checkboxes"></div>
            </div>

            <label>Дата оголошення збору:</label>
            <input type="date" id="new-coll-date">
            
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

            <label>🧾 Квитанція / Чек (Google Drive):</label>
            <input type="file" id="receipt-file" accept="image/*,.pdf">
            <div class="info-text" id="current-receipt-hint"></div>

            <button class="form-btn" onclick="savePayment()">Зберегти оплату</button>
        </div>

        <div class="card" style="border: 1px solid #ffd700;">
            <h3 id="expense-form-title">📉 Додати / Редагувати витрату</h3>
            <input type="hidden" id="edit-expense-id" value="">

            <label>Призначення (Збір):</label>
            <select id="expense-collection-select"></select>

            <label>Мета витрати (на що витрачено):</label>
            <input type="text" id="expense-purpose" placeholder="напр. Закупівля зошитів або Подарки">

            <label>Сума витрати (грн):</label>
            <input type="number" id="expense-amount" placeholder="450">

            <label>Дата витрати:</label>
            <input type="date" id="expense-date">

            <label>🧾 Чек / Квитанція (Google Drive):</label>
            <input type="file" id="expense-receipt-file" accept="image/*,.pdf">
            <div class="info-text" id="current-exp-receipt-hint"></div>

            <button class="form-btn" id="save-expense-btn" style="background:#007aff;" onclick="saveExpense()">Зберегти витрату</button>
            <button class="danger-btn" id="cancel-exp-edit-btn" style="display:none;" onclick="resetExpenseForm()">Скасувати редагування</button>
        </div>

        <div class="card" style="border: 2px solid #34c759;">
            <h3>🔄 Перенести залишок збору до Фонду класу</h3>
            <div class="info-text" style="color:#666;">Цей функціонал діє лише для зборів категорії «Інші збори».</div>
            
            <label>Оберіть цільовий збір:</label>
            <select id="transfer-collection-select" onchange="updateTransferHint()"></select>
            <div class="info-text" id="transfer-balance-hint">Доступний залишок для перенесення: 0 грн</div>

            <button class="form-btn" style="background:#34c759;" onclick="transferBalanceToFund()">Перенести залишок у фонд класу</button>
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
        let myChart = null;
        let currentUserId = 0;

        function getUserId() {
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('user_id')) {
                return parseInt(urlParams.get('user_id')) || 0;
            }
            if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
                return parseInt(tg.initDataUnsafe.user.id) || 0;
            }
            return 0;
        }

        currentUserId = getUserId();

        document.getElementById('expense-date').valueAsDate = new Date();
        document.getElementById('stud-date-added').valueAsDate = new Date();
        document.getElementById('new-coll-date').valueAsDate = new Date();

        const today = new Date();
        const startOfYear = new Date(today.getFullYear(), 0, 1);
        document.getElementById('cat-start-date').valueAsDate = startOfYear;
        document.getElementById('cat-end-date').valueAsDate = today;

        function switchTab(tabId, btn) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            btn.classList.add('active');
        }

        function toggleTargetInput() {
            const typeVal = document.getElementById('new-coll-type').value;
            const targetBox = document.getElementById('target-amount-box');
            const studentSelectionBox = document.getElementById('student-selection-box');

            if(typeVal === '2') {
                targetBox.style.display = 'none';
                studentSelectionBox.style.display = 'none';
                document.getElementById('new-coll-target').value = '0';
            } else if(typeVal === '3') {
                targetBox.style.display = 'block';
                studentSelectionBox.style.display = 'block';
                renderStudentCheckboxes();
            } else {
                targetBox.style.display = 'block';
                studentSelectionBox.style.display = 'none';
            }
        }

        function renderStudentCheckboxes() {
            if(!globalData || !globalData.all_students) return;
            const container = document.getElementById('coll-students-checkboxes');
            container.innerHTML = '';
            globalData.all_students.filter(s => s.is_active === 1).forEach(s => {
                container.innerHTML += `
                    <label class="student-checkbox-item">
                        <input type="checkbox" class="coll-student-cb" value="${s.id}" checked>
                        ${s.full_name}
                    </label>
                `;
            });
        }

        function selectAllStudentsForColl(status) {
            document.querySelectorAll('.coll-student-cb').forEach(cb => cb.checked = status);
        }

        async function loadData() {
            currentUserId = getUserId();
            const startD = document.getElementById('cat-start-date').value || '';
            const endD = document.getElementById('cat-end-date').value || '';

            try {
                const res = await fetch(`/api/budget?user_id=${currentUserId}&start_date=${startD}&end_date=${endD}`);
                globalData = await res.json();
            } catch(e) {
                console.error("Помилка при отриманні даних:", e);
                return;
            }
            
            if(globalData.is_admin) {
                document.getElementById('admin-tab-btn').style.display = 'block';
                document.getElementById('student-detail-tab-btn').style.display = 'block';
                document.getElementById('contacts-tab-btn').style.display = 'block';
                const expActions = document.getElementById('exp-actions-th');
                if(expActions) expActions.style.display = 'table-cell';
                loadAdminsList();
            }

            const pSelect = document.getElementById('parent-collection-filter');
            pSelect.innerHTML = '<option value="all">🌐 Зведений звіт (Всі загальні збори)</option>';
            pSelect.innerHTML += '<option value="optional_all">💛 Всі збори за бажанням</option>';
            
            const expFilter = document.getElementById('expense-collection-filter');
            expFilter.innerHTML = '<option value="all">🌐 Всі витрати</option>';

            if (globalData.collections) {
                globalData.collections.forEach(c => {
                    let typePrefix = c.is_class_fund ? '🏫' : '📁';
                    if(c.is_optional) typePrefix = '💛';
                    if(c.is_selective) typePrefix = '👥';
                    pSelect.innerHTML += `<option value="${c.id}">${typePrefix} ${c.name}</option>`;
                    expFilter.innerHTML += `<option value="${c.id}">${typePrefix} ${c.name}</option>`;
                });
            }

            if(globalData.is_admin) {
                const collSelect = document.getElementById('select-collection');
                const expCollSelect = document.getElementById('expense-collection-select');
                const delSelect = document.getElementById('delete-collection-select');
                const studReportSelect = document.getElementById('admin-student-report-select');
                const transferSelect = document.getElementById('transfer-collection-select');
                
                collSelect.innerHTML = '';
                expCollSelect.innerHTML = '';
                delSelect.innerHTML = '';
                studReportSelect.innerHTML = '';
                transferSelect.innerHTML = '';

                const otherCollections = (globalData.collections || []).filter(c => c.is_class_fund === 0 && !c.is_optional);

                if(!globalData.collections || globalData.collections.length === 0) {
                    collSelect.innerHTML = '<option value="">Немає активних зборів</option>';
                    expCollSelect.innerHTML = '<option value="">Немає активних зборів</option>';
                    delSelect.innerHTML = '<option value="">Немає активних зборів</option>';
                } else {
                    globalData.collections.forEach(c => {
                        let optText = c.is_optional ? 'за бажанням' : `${c.target_amount} грн/учень`;
                        if(c.is_selective) optText = `${c.target_amount} грн/учень (підгрупа)`;
                        collSelect.innerHTML += `<option value="${c.id}">${c.name} (${optText})</option>`;
                        expCollSelect.innerHTML += `<option value="${c.id}">${c.name}</option>`;
                        delSelect.innerHTML += `<option value="${c.id}">${c.name}</option>`;
                    });
                }

                if(otherCollections.length === 0) {
                    transferSelect.innerHTML = '<option value="">Немає зборів категорії "Інші збори"</option>';
                } else {
                    otherCollections.forEach(c => {
                        transferSelect.innerHTML += `<option value="${c.id}">${c.name}</option>`;
                    });
                }

                const studSelect = document.getElementById('select-student');
                studSelect.innerHTML = '';
                (globalData.all_students || []).filter(s => s.is_active === 1).forEach(s => {
                    studSelect.innerHTML += `<option value="${s.id}">${s.full_name}</option>`;
                });

                (globalData.all_students || []).forEach(s => {
                    studReportSelect.innerHTML += `<option value="${s.id}">${s.full_name} ${s.is_active ? '' : '(вибув)'}</option>`;
                });

                updatePaymentInput();
                updateTransferHint();
                renderStudentReport();
                renderContactsView();
            }

            renderParentView();
            renderCategoriesView();
            renderExpensesView();
        }

        async function loadAdminsList() {
            if(!globalData || !globalData.is_admin) return;
            const res = await fetch(`/api/get_admins?user_id=${currentUserId}`);
            const data = await res.json();
            
            const container = document.getElementById('admin-list-container');
            container.innerHTML = '';
            
            if(data.admins) {
                data.admins.forEach(a => {
                    let delBtn = `<button class="action-btn danger-btn" onclick="removeAdmin(${a.user_id})">🗑 Видалити</button>`;
                    if(data.default_admins && data.default_admins.includes(a.user_id)) {
                        delBtn = `<span class="badge neutral-badge">Головний</span>`;
                    }
                    container.innerHTML += `
                        <div class="admin-item">
                            <div><b>ID: ${a.user_id}</b> <small style="color:#666">(${a.comment || 'без примітки'})</small></div>
                            <div>${delBtn}</div>
                        </div>
                    `;
                });
            }
        }

        async function addNewAdmin() {
            const new_admin_id = document.getElementById('new-admin-id').value;
            const comment = document.getElementById('new-admin-comment').value;

            if(!new_admin_id) return alert('Вкажіть Telegram ID нового адміна!');

            const res = await fetch('/api/add_admin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ user_id: currentUserId, new_admin_id, comment })
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Адміністратора успішно додано!');
            document.getElementById('new-admin-id').value = '';
            document.getElementById('new-admin-comment').value = '';
            loadAdminsList();
        }

        async function removeAdmin(del_admin_id) {
            if(!confirm(`Ви дійсно бажаєте видалити права адміністратора для ID ${del_admin_id}?`)) return;

            const res = await fetch('/api/delete_admin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ user_id: currentUserId, del_admin_id })
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Адміністратора видалено!');
            loadAdminsList();
        }

        function updateTransferHint() {
            if(!globalData || !globalData.is_admin) return;
            const cId = parseInt(document.getElementById('transfer-collection-select').value);
            const hintElem = document.getElementById('transfer-balance-hint');
            
            if(!cId) {
                hintElem.innerText = 'Доступний залишок для перенесення: 0 грн';
                return;
            }

            let paidTot = 0;
            (globalData.all_students || []).forEach(s => {
                if(s.payments && s.payments[cId]) { paidTot += s.payments[cId].paid; }
            });

            let expTot = 0;
            (globalData.expenses || []).filter(e => e.collection_id === cId).forEach(e => {
                expTot += e.amount;
            });

            let remBal = paidTot - expTot;
            hintElem.innerText = `Доступний залишок для перенесення: ${remBal} грн`;
            hintElem.style.color = remBal > 0 ? '#34c759' : (remBal < 0 ? '#d9534f' : '#666');
        }

        async function transferBalanceToFund() {
            const collection_id = parseInt(document.getElementById('transfer-collection-select').value);
            if(!collection_id) return alert('Оберіть збір для перенесення залишку!');

            const coll = globalData.collections.find(c => c.id === collection_id);
            if(!coll) return;

            let paidTot = 0;
            globalData.all_students.forEach(s => {
                if(s.payments[collection_id]) { paidTot += s.payments[collection_id].paid; }
            });

            let expTot = 0;
            globalData.expenses.filter(e => e.collection_id === collection_id).forEach(e => {
                expTot += e.amount;
            });

            let remBal = paidTot - expTot;

            if(remBal <= 0) {
                return alert('У даного збору немає позитивного залишку для перенесення!');
            }

            if(!confirm(`Ви дійсно бажаєте перенести залишок у розмірі ${remBal} грн зі збору "${coll.name}" до Фонду класу?`)) return;

            const res = await fetch('/api/transfer_to_fund', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_id: currentUserId,
                    collection_id,
                    amount: remBal
                })
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Залишок успішно перенесено до Фонду класу!');
            loadData();
        }

        function renderContactsView() {
            if(!globalData || !globalData.is_admin) return;
            const tbody = document.getElementById('contacts-table-body');
            tbody.innerHTML = '';

            (globalData.all_students || []).forEach(s => {
                const statusBadge = s.is_active ? '<span class="badge plus">Активний</span>' : '<span class="badge minus">Вибув</span>';
                const toggleBtnText = s.is_active ? 'Деактивувати' : 'Активувати';
                const toggleBtnClass = s.is_active ? 'danger-btn' : 'form-btn';

                tbody.innerHTML += `
                    <tr>
                        <td><b>${s.full_name}</b></td>
                        <td>${s.parent_name || '-'}<br><small style="color:#666">${s.phone || '-'}</small></td>
                        <td>${s.date_added}</td>
                        <td>${statusBadge}</td>
                        <td>
                            <button class="action-btn" style="background:#007aff; color:white; margin-bottom:4px;" onclick="editStudent(${s.id})">✏️ Редагувати</button>
                            <button class="action-btn ${toggleBtnClass}" onclick="toggleStudentActive(${s.id}, ${s.is_active})">${toggleBtnText}</button>
                        </td>
                    </tr>
                `;
            });
        }

        function editStudent(id) {
            const student = globalData.all_students.find(s => s.id === id);
            if(!student) return;

            document.getElementById('edit-student-id').value = student.id;
            document.getElementById('stud-fullname').value = student.full_name;
            document.getElementById('stud-parentname').value = student.parent_name || '';
            document.getElementById('stud-phone').value = student.phone || '';
            document.getElementById('stud-date-added').value = student.date_added;

            document.getElementById('student-form-title').innerText = '✏️ Редагувати дані учня';
            document.getElementById('save-student-btn').innerText = 'Зберегти зміни';
            document.getElementById('cancel-edit-btn').style.display = 'block';
        }

        function resetStudentForm() {
            document.getElementById('edit-student-id').value = '';
            document.getElementById('stud-fullname').value = '';
            document.getElementById('stud-parentname').value = '';
            document.getElementById('stud-phone').value = '';
            document.getElementById('stud-date-added').valueAsDate = new Date();

            document.getElementById('student-form-title').innerText = '➕ Додати нового учня';
            document.getElementById('save-student-btn').innerText = 'Зберегти учня';
            document.getElementById('cancel-edit-btn').style.display = 'none';
        }

        async function saveStudent() {
            const id = document.getElementById('edit-student-id').value;
            const full_name = document.getElementById('stud-fullname').value;
            const parent_name = document.getElementById('stud-parentname').value;
            const phone = document.getElementById('stud-phone').value;
            const date_added = document.getElementById('stud-date-added').value;

            if(!full_name) return alert('Вкажіть ПІБ учня!');

            const res = await fetch('/api/save_student', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_id: currentUserId,
                    id: id ? parseInt(id) : null,
                    full_name,
                    parent_name,
                    phone,
                    date_added
                })
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Дані учня збережено!');
            resetStudentForm();
            loadData();
        }

        async function toggleStudentActive(student_id, current_status) {
            const new_status = current_status ? 0 : 1;
            const confirmMsg = current_status ? 
                'Ви дійсно бажаєте перевести учня у статус "Вибув"? Його не буде видно в списках батьків, але історія збережеться.' : 
                'Відновити активний статус учня?';

            if(!confirm(confirmMsg)) return;

            const res = await fetch('/api/toggle_student_active', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_id: currentUserId,
                    student_id,
                    is_active: new_status
                })
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            loadData();
        }

        function renderStudentReport() {
            if(!globalData || !globalData.is_admin) return;
            const sId = parseInt(document.getElementById('admin-student-report-select').value);
            if(!sId) return;

            const student = globalData.all_students.find(s => s.id === sId);
            if(!student) return;

            const tbody = document.getElementById('admin-student-report-body');
            tbody.innerHTML = '';

            let totReq = 0;
            let totPaid = 0;

            (globalData.collections || []).forEach(c => {
                const pay = student.payments ? student.payments[c.id] : null;
                const isOpt = c.is_optional === 1;

                if(pay) {
                    const bal = pay.paid - pay.required;
                    if(!isOpt) { totReq += pay.required; }
                    totPaid += pay.paid;

                    let balBadge = `<span class="badge ${bal >= 0 ? 'plus' : 'minus'}">${bal >= 0 ? '+' : ''}${bal} грн</span>`;
                    if(isOpt) {
                        balBadge = `<span class="badge opt-badge">Внесок ${pay.paid} грн</span>`;
                    }

                    let typePrefix = c.is_class_fund ? '🏫' : '🎯';
                    if(isOpt) typePrefix = '💛';
                    if(c.is_selective) typePrefix = '👥';

                    tbody.innerHTML += `
                        <tr>
                            <td><b>${typePrefix} ${c.name}</b></td>
                            <td>${isOpt ? 'Добровільно' : pay.required + ' грн'}</td>
                            <td>${pay.paid} грн</td>
                            <td>${balBadge}</td>
                        </tr>
                    `;
                } else if(c.is_selective) {
                    tbody.innerHTML += `
                        <tr>
                            <td><b>👥 ${c.name}</b></td>
                            <td>-</td>
                            <td>0 грн</td>
                            <td><span class="badge neutral-badge">Не бере участь</span></td>
                        </tr>
                    `;
                }
            });

            const totBal = totPaid - totReq;
            document.getElementById('stud-rep-tot-req').innerText = `${totReq} грн`;
            document.getElementById('stud-rep-tot-paid').innerText = `${totPaid} грн`;
            
            const totBalElem = document.getElementById('stud-rep-tot-bal');
            totBalElem.innerText = `${totBal >= 0 ? '+' : ''}${totBal} грн`;
            totBalElem.className = `badge ${totBal >= 0 ? 'plus' : 'minus'}`;
        }

        function updatePaymentInput() {
            if(!globalData || !globalData.is_admin) return;
            const cId = parseInt(document.getElementById('select-collection').value);
            const sId = parseInt(document.getElementById('select-student').value);
            
            if(!cId || !sId) return;

            const student = globalData.all_students.find(s => s.id === sId);
            const payData = (student && student.payments && student.payments[cId]) ? student.payments[cId] : { paid: 0, receipt: null };

            document.getElementById('pay-amount').value = payData.paid;
            document.getElementById('current-paid-hint').innerText = `Поточна сплачена сума у базі: ${payData.paid} грн`;

            const rHint = document.getElementById('current-receipt-hint');
            if(payData.receipt) {
                rHint.innerHTML = `📎 Чек на Google Drive: <a href="${payData.receipt}" target="_blank">Переглянути</a>`;
            } else {
                rHint.innerText = 'Квитанцію ще не прикріплено';
            }
        }

        function renderParentView() {
            if(!globalData || !globalData.students) return;
            const selectedVal = document.getElementById('parent-collection-filter').value;
            const tbody = document.getElementById('parent-table-body');
            tbody.innerHTML = '';

            const collections = globalData.collections || [];

            if (selectedVal === 'all' || !selectedVal) {
                let totalCollectedAll = 0;
                let totalTargetAll = 0;

                globalData.students.forEach(s => {
                    let sPaidMandatory = 0;
                    let sReqMandatory = 0;

                    collections.filter(c => c.is_optional === 0).forEach(c => {
                        const p = s.payments ? s.payments[c.id] : null;
                        if(p) {
                            sPaidMandatory += p.paid;
                            sReqMandatory += p.required;
                        }
                    });

                    totalCollectedAll += sPaidMandatory;
                    totalTargetAll += sReqMandatory;

                    const bal = sPaidMandatory - sReqMandatory;
                    let balBadge = '';

                    if (collections.length === 0) {
                        balBadge = '<span class="badge neutral-badge">Заборгованість відсутня</span>';
                    } else {
                        const balClass = bal >= 0 ? 'plus' : 'minus';
                        balBadge = `<span class="badge ${balClass}">${bal >= 0 ? '+' : ''}${bal} грн</span>`;
                    }

                    tbody.innerHTML += `
                        <tr>
                            <td>${s.id}</td>
                            <td><b>${s.full_name}</b><br><small style="color:#666">${s.parent_name || ''}</small></td>
                            <td>${sPaidMandatory} грн</td>
                            <td>${balBadge}</td>
                        </tr>
                    `;
                });

                document.getElementById('stat-target').innerText = '0 грн';
                document.getElementById('stat-total-collected').innerText = `${totalCollectedAll} грн`;
                document.getElementById('stat-total-target').innerText = `${totalTargetAll} грн`;
                const progress = totalTargetAll > 0 ? Math.round((totalCollectedAll / totalTargetAll) * 100) : 100;
                document.getElementById('stat-progress').innerText = `${progress}%`;

            } else if (selectedVal === 'optional_all') {
                let totalOptCollected = 0;

                globalData.students.forEach(s => {
                    let sPaidOpt = 0;

                    collections.filter(c => c.is_optional === 1).forEach(c => {
                        const p = (s.payments && s.payments[c.id]) ? s.payments[c.id] : { paid: 0 };
                        sPaidOpt += p.paid;
                    });

                    totalOptCollected += sPaidOpt;

                    tbody.innerHTML += `
                        <tr>
                            <td>${s.id}</td>
                            <td><b>${s.full_name}</b><br><small style="color:#666">${s.parent_name || ''}</small></td>
                            <td>${sPaidOpt} грн</td>
                            <td><span class="badge opt-badge">Внесок за бажанням</span></td>
                        </tr>
                    `;
                });

                document.getElementById('stat-target').innerText = 'За бажанням';
                document.getElementById('stat-total-collected').innerText = `${totalOptCollected} грн`;
                document.getElementById('stat-total-target').innerText = 'Без ліміту';
                document.getElementById('stat-progress').innerText = `100%`;

            } else {
                const collId = parseInt(selectedVal);
                const coll = collections.find(c => c.id === collId);
                if(!coll) return;

                const isOpt = coll.is_optional === 1;
                const isSel = coll.is_selective === 1;
                let totalCollected = 0;
                
                let participantCount = globalData.students.length;
                if(isSel) {
                    participantCount = globalData.students.filter(s => s.payments && s.payments[collId]).length;
                }
                const totalTarget = isOpt ? 'Без ліміту' : (coll.target_amount || 0) * participantCount;

                globalData.students.forEach(s => {
                    const pay = s.payments ? s.payments[collId] : null;

                    if(!pay && isSel) {
                        tbody.innerHTML += `
                            <tr>
                                <td>${s.id}</td>
                                <td><b>${s.full_name}</b><br><small style="color:#666">${s.parent_name || ''}</small></td>
                                <td>-</td>
                                <td><span class="badge neutral-badge">Не бере участь</span></td>
                            </tr>
                        `;
                        return;
                    }

                    const currentPay = pay || { required: coll.target_amount, paid: 0, receipt: null };
                    totalCollected += currentPay.paid;
                    
                    let statusHtml = '';
                    if(isOpt) {
                        statusHtml = `<span class="badge opt-badge">${currentPay.paid > 0 ? 'Внесок зроблено' : 'Добровільно'}</span>`;
                    } else {
                        const bal = currentPay.paid - currentPay.required;
                        const balClass = bal >= 0 ? 'plus' : 'minus';
                        const statusText = bal >= 0 ? (currentPay.required > 0 ? 'Сплачено' : 'Внесок') : `Заборгованість: ${Math.abs(bal)} грн`;
                        statusHtml = `<span class="badge ${balClass}">${statusText}</span>`;
                    }

                    let receiptHtml = '';
                    if(currentPay.receipt) {
                        receiptHtml = `<br><a class="receipt-link" href="${currentPay.receipt}" target="_blank">🧾 Переглянути чек</a>`;
                    }

                    tbody.innerHTML += `
                        <tr>
                            <td>${s.id}</td>
                            <td><b>${s.full_name}</b><br><small style="color:#666">${s.parent_name || ''}</small></td>
                            <td>${currentPay.paid} ${isOpt ? '' : '/ ' + currentPay.required} грн ${receiptHtml}</td>
                            <td>${statusHtml}</td>
                        </tr>
                    `;
                });

                document.getElementById('stat-target').innerText = isOpt ? 'За бажанням' : `${coll.target_amount} грн`;
                document.getElementById('stat-total-collected').innerText = `${totalCollected} грн`;
                document.getElementById('stat-total-target').innerText = typeof totalTarget === 'number' ? `${totalTarget} грн` : totalTarget;
                
                let progressText = '100%';
                if(!isOpt && typeof totalTarget === 'number' && totalTarget > 0) {
                    progressText = `${Math.round((totalCollected / totalTarget) * 100)}%`;
                }
                document.getElementById('stat-progress').innerText = progressText;
            }
        }

        function renderCategoriesView() {
            if(!globalData || !globalData.category_stats) return;

            const cs = globalData.category_stats;

            document.getElementById('cat-fund-target').innerText = `${cs.fund.target} грн`;
            document.getElementById('cat-fund-paid').innerText = `${cs.fund.paid} грн`;
            document.getElementById('cat-fund-exp').innerText = `${cs.fund.exp} грн`;
            
            const fundBalElem = document.getElementById('cat-fund-bal');
            fundBalElem.innerText = `${cs.fund.balance} грн`;
            fundBalElem.style.color = cs.fund.balance >= 0 ? '#34c759' : '#d9534f';

            document.getElementById('cat-other-target').innerText = `${cs.other.target} грн`;
            document.getElementById('cat-other-paid').innerText = `${cs.other.paid} грн`;
            document.getElementById('cat-other-exp').innerText = `${cs.other.exp} грн`;

            const otherBalElem = document.getElementById('cat-other-bal');
            otherBalElem.innerText = `${cs.other.balance} грн`;
            otherBalElem.style.color = cs.other.balance >= 0 ? '#34c759' : '#d9534f';

            const ctx = document.getElementById('budgetChart').getContext('2d');
            if (myChart) { myChart.destroy(); }

            myChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['🏫 Фонд класу', '🎯 Інші збори'],
                    datasets: [
                        {
                            label: 'Потрібно',
                            data: [cs.fund.target, cs.other.target],
                            backgroundColor: '#007aff'
                        },
                        {
                            label: 'Зібрано',
                            data: [cs.fund.paid, cs.other.paid],
                            backgroundColor: '#34c759'
                        },
                        {
                            label: 'Витрачено',
                            data: [cs.fund.exp, cs.other.exp],
                            backgroundColor: '#ff3b30'
                        }
                    ]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { position: 'top' }
                    },
                    scales: {
                        y: { beginAtZero: true }
                    }
                }
            });
        }

        function renderExpensesView() {
            if(!globalData) return;
            const filterId = document.getElementById('expense-collection-filter').value;
            const tbody = document.getElementById('expenses-table-body');
            tbody.innerHTML = '';

            let filteredExpenses = globalData.expenses || [];
            if(filterId !== 'all') {
                filteredExpenses = filteredExpenses.filter(e => e.collection_id === parseInt(filterId));
            }

            let sumExp = 0;
            filteredExpenses.forEach(e => {
                sumExp += parseFloat(e.amount);
                let rHtml = e.receipt ? `<a class="receipt-link" href="${e.receipt}" target="_blank">🧾 Чек</a>` : '-';
                
                let actionsHtml = '';
                if(globalData.is_admin) {
                    actionsHtml = `
                        <td>
                            <button class="action-btn" style="background:#007aff; color:white; margin-bottom:4px;" onclick="editExpense(${e.id})">✏️ Редагувати</button>
                            <button class="action-btn danger-btn" onclick="deleteExpense(${e.id})">🗑 Видалити</button>
                        </td>
                    `;
                }

                tbody.innerHTML += `
                    <tr>
                        <td>${e.date_str}</td>
                        <td><b>${e.collection_name}</b></td>
                        <td>${e.purpose}</td>
                        <td style="color:${e.amount >= 0 ? '#d9534f' : '#34c759'}; font-weight:bold;">${e.amount >= 0 ? '-' : '+'}${Math.abs(e.amount)} грн</td>
                        <td>${rHtml}</td>
                        ${actionsHtml}
                    </tr>
                `;
            });

            document.getElementById('exp-total-amount').innerText = `${sumExp} грн`;
            
            let totalCollectedAll = (globalData.all_students || []).reduce((acc, s) => acc + s.total_paid, 0);
            let totalExpAll = (globalData.expenses || []).reduce((acc, e) => acc + parseFloat(e.amount), 0);
            let netBal = totalCollectedAll - totalExpAll;
            
            const netElem = document.getElementById('exp-net-balance');
            netElem.innerText = `${netBal} грн`;
            netElem.style.color = netBal >= 0 ? '#34c759' : '#d9534f';
        }

        function editExpense(id) {
            const exp = globalData.expenses.find(e => e.id === id);
            if(!exp) return;

            document.getElementById('edit-expense-id').value = exp.id;
            document.getElementById('expense-collection-select').value = exp.collection_id;
            document.getElementById('expense-purpose').value = exp.purpose;
            document.getElementById('expense-amount').value = exp.amount;
            document.getElementById('expense-date').value = exp.date_str;

            const hint = document.getElementById('current-exp-receipt-hint');
            if(exp.receipt) {
                hint.innerHTML = `📎 Чек на Google Drive: <a href="${exp.receipt}" target="_blank">Переглянути</a>`;
            } else {
                hint.innerText = '';
            }

            document.getElementById('expense-form-title').innerText = '✏️ Редагувати витрату';
            document.getElementById('save-expense-btn').innerText = 'Зберегти зміни';
            document.getElementById('cancel-exp-edit-btn').style.display = 'block';

            const adminBtn = document.getElementById('admin-tab-btn');
            switchTab('admin-tab', adminBtn);
        }

        function resetExpenseForm() {
            document.getElementById('edit-expense-id').value = '';
            document.getElementById('expense-purpose').value = '';
            document.getElementById('expense-amount').value = '';
            document.getElementById('expense-date').valueAsDate = new Date();
            document.getElementById('expense-receipt-file').value = '';
            document.getElementById('current-exp-receipt-hint').innerText = '';

            document.getElementById('expense-form-title').innerText = '📉 Додати / Редагувати витрату';
            document.getElementById('save-expense-btn').innerText = 'Зберегти витрату';
            document.getElementById('cancel-exp-edit-btn').style.display = 'none';
        }

        async function saveExpense() {
            const id = document.getElementById('edit-expense-id').value;
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
            if(id) formData.append('id', id);
            formData.append('collection_id', collection_id);
            formData.append('purpose', purpose);
            formData.append('amount', amount);
            formData.append('date_str', date_str);

            if(fileInput.files && fileInput.files.length > 0) {
                formData.append('receipt', fileInput.files[0], fileInput.files[0].name);
            }

            const res = await fetch('/api/add_expense', {
                method: 'POST',
                body: formData
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            if(ans.drive_status === 'error') {
                alert('⚠️ Витрату збережено, але НЕ вдалося завантажити чек на Google Диск!');
            } else if(ans.drive_status === 'success') {
                alert('✅ Витрату та чек на Google Диск успішно збережено!');
            } else {
                alert('Витрату збережено (без чеку).');
            }

            resetExpenseForm();
            loadData();
        }

        async function deleteExpense(id) {
            if(!confirm('Ви дійсно бажаєте видалити цю витрату?')) return;

            const res = await fetch('/api/delete_expense', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_id: currentUserId,
                    expense_id: id
                })
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            alert('Витрату видалено!');
            loadData();
        }

        async function createCollection() {
            const typeVal = document.getElementById('new-coll-type').value;
            const name = document.getElementById('new-coll-name').value;
            const target = document.getElementById('new-coll-target').value;
            const created_at = document.getElementById('new-coll-date').value;

            if(!name) return alert('Вкажіть назву/призначення збору!');

            const is_class_fund = typeVal === '1' ? 1 : 0;
            const is_optional = typeVal === '2' ? 1 : 0;
            const is_selective = typeVal === '3' ? 1 : 0;
            const target_amount = is_optional ? 0 : parseFloat(target || 0);

            let selected_students = [];
            if(is_selective) {
                document.querySelectorAll('.coll-student-cb:checked').forEach(cb => {
                    selected_students.push(parseInt(cb.value));
                });

                if(selected_students.length === 0) {
                    return alert('Оберіть хоча б одного учня для збору підгрупи!');
                }
            }

            const res = await fetch('/api/add_collection', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_id: currentUserId,
                    name,
                    is_class_fund,
                    is_optional,
                    is_selective,
                    target_amount,
                    created_at,
                    selected_students
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

            if(fileInput.files && fileInput.files.length > 0) {
                formData.append('receipt', fileInput.files[0], fileInput.files[0].name);
            }

            const res = await fetch('/api/save_payment', {
                method: 'POST',
                body: formData
            });

            const ans = await res.json();
            if(ans.error) return alert(ans.error);

            if(ans.drive_status === 'error') {
                alert('⚠️ Оплату збережено, але НЕ вдалося завантажити чек на Google Диск!');
            } else if(ans.drive_status === 'success') {
                alert('✅ Оплату та чек на Google Диск успішно збережено!');
            } else {
                alert('Оплату успішно збережено!');
            }

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

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/budget')
def get_budget():
    try:
        user_id = int(request.args.get('user_id', 0))
    except (ValueError, TypeError):
        user_id = 0

    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    admin_status = is_user_admin(user_id)

    conn = get_db_connection()
    if not conn:
        return jsonify({
            'students': [],
            'all_students': [],
            'collections': [],
            'expenses': [],
            'category_stats': {
                'fund': {'target': 0, 'paid': 0, 'exp': 0, 'balance': 0},
                'other': {'target': 0, 'paid': 0, 'exp': 0, 'balance': 0}
            },
            'is_admin': admin_status
        })

    c = conn.cursor()

    c.execute("SELECT id, name, is_class_fund, is_optional, is_selective, target_amount, created_at FROM collections ORDER BY id ASC")
    colls = [{
        'id': row['id'],
        'name': row['name'],
        'is_class_fund': row['is_class_fund'],
        'is_optional': row['is_optional'] or 0,
        'is_selective': row['is_selective'] or 0,
        'target_amount': float(row['target_amount'] or 0),
        'created_at': row['created_at'] or '2026-01-01'
    } for row in c.fetchall()]

    c.execute("SELECT id, full_name, parent_name, phone, date_added, is_active FROM students ORDER BY full_name ASC")
    all_students_raw = c.fetchall()

    all_student_list = []
    active_student_list = []

    for s in all_students_raw:
        s_id = s['id']
        c.execute("SELECT collection_id, required, paid, receipt_filename FROM payments WHERE student_id=%s", (s_id,))
        p_rows = c.fetchall()
        payments_dict = {}
        total_paid = 0
        total_required = 0

        for pr in p_rows:
            c_id = pr['collection_id']
            req = float(pr['required'] or 0)
            paid = float(pr['paid'] or 0)
            receipt = pr['receipt_filename']
            payments_dict[c_id] = {'required': req, 'paid': paid, 'receipt': receipt}
            total_paid += paid
            total_required += req

        student_obj = {
            'id': s_id,
            'full_name': s['full_name'],
            'parent_name': s['parent_name'],
            'phone': s['phone'],
            'date_added': s['date_added'] or '2026-01-01',
            'is_active': s['is_active'] if s['is_active'] is not None else 1,
            'payments': payments_dict,
            'total_paid': total_paid,
            'total_required': total_required,
            'balance': total_paid - total_required
        }

        all_student_list.append(student_obj)
        if student_obj['is_active'] == 1:
            active_student_list.append(student_obj)

    if start_date and end_date:
        c.execute("SELECT e.id, e.collection_id, c.name as collection_name, e.purpose, e.amount, e.date_str, e.receipt_filename FROM expenses e JOIN collections c ON e.collection_id = c.id WHERE e.date_str >= %s AND e.date_str <= %s ORDER BY e.id DESC", (start_date, end_date))
    else:
        c.execute("SELECT e.id, e.collection_id, c.name as collection_name, e.purpose, e.amount, e.date_str, e.receipt_filename FROM expenses e JOIN collections c ON e.collection_id = c.id ORDER BY e.id DESC")

    expenses_list = [{
        'id': row['id'],
        'collection_id': row['collection_id'],
        'collection_name': row['collection_name'],
        'purpose': row['purpose'],
        'amount': float(row['amount']),
        'date_str': row['date_str'],
        'receipt': row['receipt_filename']
    } for row in c.fetchall()]

    category_stats = {
        'fund': {'target': 0, 'paid': 0, 'exp': 0, 'balance': 0},
        'other': {'target': 0, 'paid': 0, 'exp': 0, 'balance': 0}
    }

    for coll in colls:
        c_id = coll['id']
        is_fund = coll['is_class_fund']
        cat_key = 'fund' if is_fund == 1 else 'other'

        c.execute("SELECT SUM(required) as tot FROM payments WHERE collection_id=%s", (c_id,))
        res_req = c.fetchone()
        category_stats[cat_key]['target'] += float(res_req['tot'] or 0)

        c.execute("SELECT SUM(paid) as tot FROM payments WHERE collection_id=%s", (c_id,))
        res_paid = c.fetchone()
        category_stats[cat_key]['paid'] += float(res_paid['tot'] or 0)

        if start_date and end_date:
            c.execute("SELECT SUM(amount) as tot FROM expenses WHERE collection_id=%s AND date_str >= %s AND date_str <= %s", (c_id, start_date, end_date))
        else:
            c.execute("SELECT SUM(amount) as tot FROM expenses WHERE collection_id=%s", (c_id,))

        res_exp = c.fetchone()
        category_stats[cat_key]['exp'] += float(res_exp['tot'] or 0)

    category_stats['fund']['balance'] = category_stats['fund']['paid'] - category_stats['fund']['exp']
    category_stats['other']['balance'] = category_stats['other']['paid'] - category_stats['other']['exp']

    c.close()
    conn.close()
    return jsonify({
        'students': active_student_list,
        'all_students': all_student_list if admin_status else active_student_list,
        'collections': colls,
        'expenses': expenses_list,
        'category_stats': category_stats,
        'is_admin': admin_status
    })

@app.route('/api/get_admins')
def get_admins():
    user_id = int(request.args.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено!'}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({'admins': [], 'default_admins': DEFAULT_ADMIN_IDS})
    c = conn.cursor()
    c.execute("SELECT user_id, comment FROM admins ORDER BY user_id ASC")
    admins = c.fetchall()
    c.close()
    conn.close()
    return jsonify({'admins': admins, 'default_admins': DEFAULT_ADMIN_IDS})

@app.route('/api/add_admin', methods=['POST'])
def add_admin():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено!'})

    new_admin_id = data.get('new_admin_id')
    comment = data.get('comment', 'Адміністратор')

    try:
        new_admin_id = int(new_admin_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'Вкажіть числовий Telegram ID!'})

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()
    c.execute("INSERT INTO admins (user_id, comment) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET comment=EXCLUDED.comment", (new_admin_id, comment))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/delete_admin', methods=['POST'])
def delete_admin():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено!'})

    del_admin_id = int(data.get('del_admin_id', 0))

    if del_admin_id in DEFAULT_ADMIN_IDS:
        return jsonify({'error': 'Неможливо видалити головного адміністратора!'})

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=%s", (del_admin_id,))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/add_expense', methods=['POST'])
def add_expense():
    user_id = int(request.form.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено! Ви не є адміністратором.'})

    exp_id = request.form.get('id')
    c_id = request.form.get('collection_id')
    purpose = request.form.get('purpose')
    amount = float(request.form.get('amount', 0))
    date_str = request.form.get('date_str', datetime.now().strftime("%Y-%m-%d"))

    print("--------------------------------------------------", flush=True)
    print(f"📋 [EXPENSE v6.0] request.files: {list(request.files.keys())}", flush=True)

    drive_link = None
    drive_status = 'none'

    if 'receipt' in request.files:
        file = request.files['receipt']
        if file and file.filename != '':
            print(f"📥 [EXPENSE v6.0] Зчитано файл: {file.filename}", flush=True)
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
            timestamp = int(datetime.now().timestamp())
            filename = secure_filename(f"exp_{c_id}_{timestamp}.{ext}")
            drive_link = upload_file_to_drive(file, filename)
            
            if drive_link:
                drive_status = 'success'
            else:
                drive_status = 'error'
        else:
            print("⚠️ [EXPENSE v6.0] Файл порожній!", flush=True)
    else:
        print("⚠️ [EXPENSE v6.0] 'receipt' ВІДСУТНІЙ!", flush=True)
    print("--------------------------------------------------", flush=True)

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()

    if exp_id:
        if drive_link:
            c.execute("UPDATE expenses SET collection_id=%s, purpose=%s, amount=%s, date_str=%s, receipt_filename=%s WHERE id=%s", 
                      (c_id, purpose, amount, date_str, drive_link, exp_id))
        else:
            c.execute("UPDATE expenses SET collection_id=%s, purpose=%s, amount=%s, date_str=%s WHERE id=%s", 
                      (c_id, purpose, amount, date_str, exp_id))
    else:
        c.execute("INSERT INTO expenses (collection_id, purpose, amount, date_str, receipt_filename) VALUES (%s, %s, %s, %s, %s)",
                  (c_id, purpose, amount, date_str, drive_link))

    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok', 'drive_status': drive_status})

@app.route('/api/delete_expense', methods=['POST'])
def delete_expense():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено! Ви не є адміністратором.'})

    exp_id = data.get('expense_id')

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()
    c.execute("DELETE FROM expenses WHERE id=%s", (exp_id,))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/transfer_to_fund', methods=['POST'])
def transfer_to_fund():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено! Ви не є адміністратором.'})

    c_id = data.get('collection_id')
    amount = float(data.get('amount', 0))
    date_str = datetime.now().strftime("%Y-%m-%d")

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()

    c.execute("SELECT name, is_class_fund FROM collections WHERE id=%s", (c_id,))
    coll_info = c.fetchone()

    if not coll_info or coll_info['is_class_fund'] == 1:
        c.close()
        conn.close()
        return jsonify({'error': 'Перенесення залишку можливе лише для зборів категорії "Інші збори"!'})

    coll_name = coll_info['name']

    c.execute("SELECT id FROM collections WHERE is_class_fund=1 ORDER BY id ASC LIMIT 1")
    fund_row = c.fetchone()

    if not fund_row:
        c.execute("INSERT INTO collections (name, is_class_fund, target_amount, created_at) VALUES ('Фонд класу', 1, 0, %s) RETURNING id", (date_str,))
        fund_id = c.fetchone()['id']
    else:
        fund_id = fund_row['id']

    c.execute("INSERT INTO expenses (collection_id, purpose, amount, date_str) VALUES (%s, %s, %s, %s)",
              (c_id, 'Перенесено до фонду класу', amount, date_str))

    c.execute("INSERT INTO expenses (collection_id, purpose, amount, date_str) VALUES (%s, %s, %s, %s)",
              (fund_id, f"Поповнення з залишка збору: {coll_name}", -amount, date_str))

    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/save_student', methods=['POST'])
def save_student():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено! Ви не є адміністратором.'})

    s_id = data.get('id')
    full_name = data.get('full_name')
    parent_name = data.get('parent_name', '')
    phone = data.get('phone', '')
    date_added = data.get('date_added', datetime.now().strftime("%Y-%m-%d"))

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()

    if s_id:
        c.execute("UPDATE students SET full_name=%s, parent_name=%s, phone=%s, date_added=%s WHERE id=%s",
                  (full_name, parent_name, phone, date_added, s_id))
    else:
        c.execute("INSERT INTO students (full_name, parent_name, phone, date_added, is_active) VALUES (%s, %s, %s, %s, 1) RETURNING id",
                  (full_name, parent_name, phone, date_added))
        new_student_id = c.fetchone()['id']

        c.execute("SELECT id, target_amount, created_at FROM collections WHERE created_at >= %s AND is_selective=0", (date_added,))
        colls = c.fetchall()

        for col in colls:
            c.execute("INSERT INTO payments (student_id, collection_id, required, paid) VALUES (%s, %s, %s, 0)",
                      (new_student_id, col['id'], col['target_amount']))

    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/toggle_student_active', methods=['POST'])
def toggle_student_active():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено! Ви не є адміністратором.'})

    s_id = data.get('student_id')
    is_active = data.get('is_active', 1)

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()
    c.execute("UPDATE students SET is_active=%s WHERE id=%s", (is_active, s_id))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/add_collection', methods=['POST'])
def add_collection():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено! Ви не є адміністратором.'})

    name = data.get('name')
    is_fund = data.get('is_class_fund', 0)
    is_optional = data.get('is_optional', 0)
    is_selective = data.get('is_selective', 0)
    target = data.get('target_amount', 0)
    created_at = data.get('created_at', datetime.now().strftime("%Y-%m-%d"))
    selected_students = data.get('selected_students', [])

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()
    c.execute("INSERT INTO collections (name, is_class_fund, is_optional, is_selective, target_amount, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
              (name, is_fund, is_optional, is_selective, target, created_at))
    coll_id = c.fetchone()['id']

    if is_selective:
        for s_id in selected_students:
            c.execute("INSERT INTO payments (student_id, collection_id, required, paid) VALUES (%s, %s, %s, 0)",
                      (s_id, coll_id, target))
    else:
        c.execute("SELECT id FROM students WHERE is_active=1 AND date_added <= %s", (created_at,))
        students = c.fetchall()
        for s in students:
            c.execute("INSERT INTO payments (student_id, collection_id, required, paid) VALUES (%s, %s, %s, 0)",
                      (s['id'], coll_id, target))

    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/save_payment', methods=['POST'])
def save_payment():
    user_id = int(request.form.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено! Ви не є адміністратором.'})

    s_id = request.form.get('student_id')
    c_id = request.form.get('collection_id')
    paid = float(request.form.get('paid', 0))

    print("--------------------------------------------------", flush=True)
    print(f"📋 [PAYMENT v6.0] request.files: {list(request.files.keys())}", flush=True)

    drive_link = None
    drive_status = 'none'

    if 'receipt' in request.files:
        file = request.files['receipt']
        if file and file.filename != '':
            print(f"📥 [PAYMENT v6.0] Зчитано квитанцію: {file.filename}", flush=True)
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
            filename = secure_filename(f"receipt_{s_id}_{c_id}.{ext}")
            drive_link = upload_file_to_drive(file, filename)
            
            if drive_link:
                drive_status = 'success'
            else:
                drive_status = 'error'
        else:
            print("⚠️ [PAYMENT v6.0] Файл порожній!", flush=True)
    else:
        print("⚠️ [PAYMENT v6.0] 'receipt' ВІДСУТНІЙ!", flush=True)
    print("--------------------------------------------------", flush=True)

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()

    if drive_link:
        c.execute("""INSERT INTO payments (student_id, collection_id, paid, receipt_filename) 
                     VALUES (%s, %s, %s, %s) 
                     ON CONFLICT (student_id, collection_id) 
                     DO UPDATE SET paid=EXCLUDED.paid, receipt_filename=EXCLUDED.receipt_filename""",
                  (s_id, c_id, paid, drive_link))
    else:
        c.execute("""INSERT INTO payments (student_id, collection_id, paid) 
                     VALUES (%s, %s, %s) 
                     ON CONFLICT (student_id, collection_id) 
                     DO UPDATE SET paid=EXCLUDED.paid""",
                  (s_id, c_id, paid))

    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok', 'drive_status': drive_status})

@app.route('/api/delete_collection', methods=['POST'])
def delete_collection():
    data = request.json
    user_id = int(data.get('user_id', 0))
    if not is_user_admin(user_id):
        return jsonify({'error': 'Доступ заборонено!'})

    c_id = data.get('collection_id')

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'База даних недоступна!'})
    c = conn.cursor()
    c.execute("DELETE FROM collections WHERE id=%s", (c_id,))
    c.execute("DELETE FROM payments WHERE collection_id=%s", (c_id,))
    c.execute("DELETE FROM expenses WHERE collection_id=%s", (c_id,))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
