#!/usr/bin/env python3
"""Family HQ — Whitewood Family Command Centre"""
import base64, fcntl, hashlib, json, math, os, shutil, sqlite3, re, tempfile, time, urllib.request, urllib.parse
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, send_file, abort, g, Response, redirect, url_for, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import openpyxl
from cashflow import (
    FORECAST_MONTHS,
    _normalise_description,
    build_forecast,
    infer_recurring_events,
)

app = Flask(__name__)
ROOT = Path(__file__).parent
DATA_DIR = ROOT / 'data'
DB_PATH = DATA_DIR / 'family.db'
CONFIG_PATH = DATA_DIR / 'config.json'
BIRTHDAYS_PATH = DATA_DIR / 'Whitewood Family Birthdays.xlsx'
PORT = int(os.environ.get('PORT', 3000))

USERNAME = os.environ.get('FAMILY_HQ_USER', 'family')
PASSWORD = os.environ.get('FAMILY_HQ_PASS', 'Whitewood2026!')
app.secret_key = os.environ.get('SECRET_KEY', f'family-hq-{USERNAME}-dev-key')

def _anthropic_key(): return os.environ.get('ANTHROPIC_API_KEY', '')
def _openrouter_key(): return os.environ.get('OPENROUTER_API_KEY', '')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')


# ── Auth ──────────────────────────────────────────────────────────────────────

limiter = Limiter(get_remote_address, app=app, default_limits=[])

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


class _User(UserMixin):
    def __init__(self, id):
        self.id = id


@login_manager.user_loader
def load_user(user_id):
    if user_id == USERNAME:
        return _User(user_id)
    return None


@login_manager.unauthorized_handler
def _unauthorized():
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Authentication required'}), 401
    return redirect(url_for('login', next=request.path))


_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Family HQ — Sign In</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { min-height: 100vh; display: flex; align-items: center; justify-content: center;
           background: #0f2419; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
    .card { background: #fff; border-radius: 16px; padding: 40px 36px; width: 100%; max-width: 380px;
            box-shadow: 0 8px 40px rgba(0,0,0,0.35); }
    .logo { text-align: center; margin-bottom: 28px; }
    .logo-icon { font-size: 40px; }
    .logo h1 { color: #1B4332; font-size: 22px; font-weight: 700; margin-top: 8px; }
    .logo p { color: #6b7280; font-size: 13px; margin-top: 4px; }
    label { display: block; font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 5px; }
    input[type=text], input[type=password] {
      width: 100%; padding: 11px 14px; border: 1.5px solid #d1d5db; border-radius: 8px;
      font-size: 15px; outline: none; transition: border-color .2s; margin-bottom: 18px; }
    input:focus { border-color: #1B4332; }
    .error { background: #FEF2F2; color: #DC2626; padding: 10px 14px; border-radius: 8px;
             font-size: 13px; margin-bottom: 16px; border: 1px solid #FECACA; }
    button { width: 100%; padding: 12px; background: #1B4332; color: #fff; border: none;
             border-radius: 8px; font-size: 15px; font-weight: 600; cursor: pointer;
             transition: background .2s; }
    button:hover { background: #145c2d; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-icon">🏡</div>
      <h1>Family HQ</h1>
      <p>Whitewood Family Command Centre</p>
    </div>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="post">
      <label for="username">Username</label>
      <input type="text" id="username" name="username" autocomplete="username" autofocus required>
      <label for="password">Password</label>
      <input type="password" id="password" name="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>"""


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def login():
    if current_user.is_authenticated:
        return redirect('/')
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == USERNAME and password == PASSWORD:
            login_user(_User(username), remember=True)
            next_url = request.args.get('next') or '/'
            if not next_url.startswith('/'):
                next_url = '/'
            return redirect(next_url)
        error = 'Invalid username or password'
    return render_template_string(_LOGIN_TEMPLATE, error=error)


@app.route('/logout')
def logout():
    logout_user()
    return redirect('/login')


@app.before_request
def require_auth():
    public = {'/health', '/login', '/logout', '/manifest.json', '/icon-192.png', '/icon-512.png'}
    if request.path in public:
        return
    if request.path.startswith('/static/'):
        return
    if not current_user.is_authenticated:
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('login', next=request.path))


# ── LLM helper (Anthropic → OpenRouter fallback) ─────────────────────────────

def llm_available():
    return bool(_anthropic_key() or _openrouter_key())

def llm_chat(messages: list, system: str = '', max_tokens: int = 1024) -> str:
    """Call Claude via Anthropic SDK, or fall back to OpenRouter free model."""
    anthropic_key = _anthropic_key()
    openrouter_key = _openrouter_key()

    if anthropic_key:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        kwargs = dict(model='claude-sonnet-4-6', max_tokens=max_tokens, messages=messages)
        if system:
            kwargs['system'] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text

    if openrouter_key:
        _models = [
            'meta-llama/llama-3.3-70b-instruct:free',
            'google/gemma-3-27b-it:free',
            'mistralai/mistral-7b-instruct:free',
        ]
        last_err = None
        for model in _models:
            payload = json.dumps({
                'model': model,
                'messages': ([{'role': 'system', 'content': system}] if system else []) + messages,
                'max_tokens': max_tokens,
            }).encode()
            req = urllib.request.Request(
                'https://openrouter.ai/api/v1/chat/completions',
                data=payload,
                headers={
                    'Authorization': f'Bearer {openrouter_key}',
                    'Content-Type': 'application/json',
                    'HTTP-Referer': 'https://family.edencommercial.au',
                },
                method='POST',
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                    return data['choices'][0]['message']['content']
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code != 429:
                    raise
        raise last_err

    raise ValueError('No LLM configured — set ANTHROPIC_API_KEY or OPENROUTER_API_KEY')


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    # Seed static data files from bundled defaults if volume mount started empty
    import shutil as _shutil
    if not CONFIG_PATH.exists():
        default = ROOT / 'config_default.json'
        if default.exists():
            _shutil.copy(default, CONFIG_PATH)
    if not BIRTHDAYS_PATH.exists():
        default_xl = ROOT / 'birthdays_default.xlsx'
        if default_xl.exists():
            _shutil.copy(default_xl, BIRTHDAYS_PATH)
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capital TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                target_date TEXT,
                status TEXT DEFAULT 'active',
                progress INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                content TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS calendar_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                event_date TEXT,
                title TEXT,
                time_str TEXT,
                all_day INTEGER DEFAULT 0,
                fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS property_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimated_value INTEGER,
                note TEXT,
                recorded_at TEXT
            );
            CREATE TABLE IF NOT EXISTS wishlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                estimated_cost INTEGER DEFAULT 0,
                cost_range TEXT,
                season TEXT DEFAULT 'anytime',
                timing_note TEXT,
                priority INTEGER DEFAULT 2,
                status TEXT DEFAULT 'pending',
                ai_note TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS warranties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT NOT NULL,
                provider TEXT,
                model_number TEXT,
                serial_number TEXT,
                purchased_date TEXT,
                expires_date TEXT,
                standard_expires_date TEXT,
                extended_expires_date TEXT,
                date_source TEXT,
                coverage TEXT,
                claim_info TEXT,
                notes TEXT,
                document_path TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS insurances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                provider TEXT,
                policy_number TEXT,
                premium TEXT,
                renewal_date TEXT,
                coverage TEXT,
                notes TEXT,
                document_path TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS briefing_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                briefing TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                company_name TEXT,
                action TEXT DEFAULT 'buy',
                qty REAL NOT NULL,
                entry_price REAL NOT NULL,
                entry_date TEXT NOT NULL,
                notes TEXT,
                closed INTEGER DEFAULT 0,
                close_price REAL,
                close_date TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS screener_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                company_name TEXT,
                score INTEGER DEFAULT 0,
                quality INTEGER DEFAULT 0,
                growth INTEGER DEFAULT 0,
                value_score INTEGER DEFAULT 0,
                momentum INTEGER DEFAULT 0,
                archetype TEXT,
                current_price REAL,
                details TEXT,
                run_date TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS finance_chat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS finance_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                ownership TEXT NOT NULL CHECK (ownership IN ('personal','business')),
                account_type TEXT NOT NULL CHECK (account_type IN ('cash','credit','loan')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS finance_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                account_id INTEGER NOT NULL REFERENCES finance_accounts(id),
                parsed_count INTEGER NOT NULL DEFAULT 0,
                earliest_date TEXT,
                latest_date TEXT,
                status TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS budget_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                monthly_target REAL NOT NULL, -- amount as entered, at `frequency`
                type TEXT DEFAULT 'personal',
                frequency TEXT DEFAULT 'monthly',
                direction TEXT DEFAULT 'outflow',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS budget_target_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL REFERENCES budget_targets(id) ON DELETE CASCADE,
                year_month TEXT NOT NULL,
                amount REAL,
                skipped INTEGER NOT NULL DEFAULT 0,
                UNIQUE (target_id, year_month)
            );
            CREATE TABLE IF NOT EXISTS savings_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target_amount REAL NOT NULL,
                current_amount REAL DEFAULT 0,
                priority INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                target_date TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS upcoming_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                due_date TEXT NOT NULL,
                recurring INTEGER DEFAULT 0,
                recurrence TEXT DEFAULT '',
                category TEXT,
                ownership TEXT DEFAULT 'personal',
                direction TEXT DEFAULT 'outflow',
                status TEXT DEFAULT 'pending',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS budget_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS merchant_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL UNIQUE COLLATE NOCASE,
                category TEXT NOT NULL,
                created_at TEXT
            );
        ''')
        for column, definition in [
            ('recurrence', "TEXT DEFAULT ''"),
            ('ownership', "TEXT DEFAULT 'personal'"),
            ('direction', "TEXT DEFAULT 'outflow'"),
        ]:
            try:
                db.execute(
                    f'ALTER TABLE upcoming_expenses ADD COLUMN {column} {definition}'
                )
            except sqlite3.OperationalError:
                pass
        for column, definition in [
            ('frequency', "TEXT DEFAULT 'monthly'"),
            ('direction', "TEXT DEFAULT 'outflow'"),
        ]:
            try:
                db.execute(
                    f'ALTER TABLE budget_targets ADD COLUMN {column} {definition}'
                )
            except sqlite3.OperationalError:
                pass
        for pattern, category in [
            ('our cow', 'Groceries'),
            ('harvest markets', 'Groceries'),
            ('zenrows', 'Software & Tools'),
            ('red bead', 'Education'),
            ('united', 'Fuel'),
        ]:
            db.execute(
                'INSERT OR IGNORE INTO merchant_rules (pattern, category, created_at) '
                'VALUES (?, ?, ?)',
                (pattern, category, datetime.now().isoformat()[:19]),
            )
        # Seed insurance records — insert each by policy_number if not already present
        now = datetime.now().isoformat()[:19]
        seed_insurances = [
            ('car',      'RACQ',    'Q24M8Z',          None,      None,
             'Motor vehicle insurance — comprehensive cover for Chery Tiggo 8 Pro Max', None, None),
            ('car',      'RACQ',    'Mbr 3083 6700 9579 8082', None, None,
             'Roadside assistance — towing, battery, fuel, lockout', None, None),
            ('house',    'RACQ',    '57054030PQ',      None,      None,
             'Home & contents insurance — building and contents cover', None, None),
            ('business', 'ProRisk', 'PI-003645-2025',  '2505.00', '2026-11-20',
             'Professional Indemnity — $1M limit ($3M aggregate) | Real Estate Agent / Buyers Advocate | Insurer: Swiss Re via ProRisk | Broker: GT Insurance Brokers',
             'Ref: QLPJWS-3 | Combined invoice $2,505 covers PI + PPL | Renewal 20/11/2026',
             None),
            ('business', 'ProRisk', 'PPL-013525-2025', None,      '2026-11-20',
             'Public & Products Liability — $20M per occurrence | Worldwide (ex USA/Canada) | Insurer: Swiss Re via ProRisk | Broker: GT Insurance Brokers',
             'Ref: QLPJWS-3 | Renewal 20/11/2026',
             None),
        ]
        for type_, provider, policy_number, premium, renewal_date, coverage, notes, document_path in seed_insurances:
            if not db.execute('SELECT 1 FROM insurances WHERE policy_number=?', (policy_number,)).fetchone():
                db.execute(
                    'INSERT INTO insurances (type,provider,policy_number,premium,renewal_date,coverage,notes,document_path,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (type_, provider, policy_number, premium, renewal_date, coverage, notes, document_path, now)
                )
        # Normalise insurance types (fix legacy full-name types from old seed)
        db.execute("UPDATE insurances SET type='house' WHERE type NOT IN ('house','car','business') AND (type LIKE '%Home%' OR type LIKE '%House%' OR type LIKE '%Content%')")
        db.execute("UPDATE insurances SET type='car' WHERE type NOT IN ('house','car','business') AND (type LIKE '%Car%' OR type LIKE '%Roadside%' OR type LIKE '%Vehicle%')")
        db.execute("UPDATE insurances SET type='business' WHERE type NOT IN ('house','car','business') AND type LIKE '%Business%'")
        # Migrate warranties table: add new columns if missing
        for col_def in [
            ('model_number', 'TEXT'), ('serial_number', 'TEXT'),
            ('standard_expires_date', 'TEXT'), ('extended_expires_date', 'TEXT'),
            ('date_source', 'TEXT'),
        ]:
            try:
                db.execute(f'ALTER TABLE warranties ADD COLUMN {col_def[0]} {col_def[1]}')
            except Exception:
                pass
        # Back-fill standard_expires_date from expires_date for existing rows
        db.execute('UPDATE warranties SET standard_expires_date = expires_date WHERE standard_expires_date IS NULL AND expires_date IS NOT NULL')
        # Seed RYOBI warranties if not already present (check by model_number + serial)
        now = datetime.now().isoformat()[:19]
        if True:
            ryobi_warranties = [
                ('RYOBI 18V ONE+ Inflator / Deflator', 'RYOBI', '#CIT1800G', '116172-09-2021', '2021-08-18', '2027-08-18', '2025-08-18', '2027-08-18', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 18V ONE+ 220mm Grass Edger', 'RYOBI', '#OED1850', '2201001267', '2022-03-22', '2028-03-22', '2026-03-22', '2028-03-22', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 18V ONE+ 25cm/30cm Line Trimmer', 'RYOBI', '#OLT1832', '2201004455', '2022-03-18', '2028-03-18', '2026-03-18', '2028-03-18', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 18V ONE+ 165mm Circular Saw', 'RYOBI', '#R18CS-0', '115279-31-2020', '2020-12-04', '2026-12-04', '2024-12-04', '2026-12-04', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 18V ONE+ Jigsaw', 'RYOBI', '#R16JS-0', '123752-45-2023', '2024-04-03', '2030-04-03', '2028-04-03', '2030-04-03', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 18V ONE+ Hammer Drill', 'RYOBI', '#R18PD3-0', '224193-07-2021', '2021-08-29', '2027-08-29', '2025-08-29', '2027-08-29', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 18V ONE+ HP BL Stick Vac', 'RYOBI', '#R18XSV9-FH3', '003486', '2023-09-11', '2029-09-11', '2027-09-11', '2029-09-11', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 18V ONE+ 4Ah Battery', 'RYOBI', '#RB1840C', '944640', '2024-08-30', '2027-08-30', '2027-08-30', None, 'Standard: 3yr', '1800 664 942 | ryobitools.com.au'),
                ('RYOBI 1800W 2000psi Pressure Washer', 'RYOBI', '#RPW140-G', '2106005168', '2021-08-25', '2027-08-25', '2025-08-25', '2027-08-25', 'Standard: 4yr | Extended: 2yr', '1800 664 942 | ryobitools.com.au'),
            ]
            for product, provider, model_number, serial_number, purchased_date, expires_date, standard_expires_date, extended_expires_date, coverage, claim_info in ryobi_warranties:
                exists = db.execute('SELECT 1 FROM warranties WHERE model_number=? AND serial_number=?', (model_number, serial_number)).fetchone()
                if not exists:
                    db.execute(
                        'INSERT INTO warranties (product,provider,model_number,serial_number,purchased_date,expires_date,standard_expires_date,extended_expires_date,date_source,coverage,claim_info,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (product, provider, model_number, serial_number, purchased_date, expires_date, standard_expires_date, extended_expires_date, 'receipt', coverage, claim_info, now)
                    )
        # Seed wishlist if empty
        wl_count = db.execute('SELECT COUNT(*) FROM wishlist').fetchone()[0]
        if wl_count == 0:
            now = datetime.now().isoformat()[:19]
            wishlist_seed = [
                ('Get James: clean up side wall, fill/extend road base', 'landscaping', 1200, '$800–$1,500', 'now', 'Can do any dry day — get it sorted before spring rush', 1),
                ('Paint retaining wall', 'exterior', 600, '$400–$800', 'spring', 'Best in spring (Sep 2026) for adhesion — avoid extreme heat', 2),
                ('Gardenia garden bed', 'garden', 450, '$300–$600', 'spring', 'Plant in Sep–Oct 2026 for best establishment before summer', 2),
                ('Put in plants (general)', 'garden', 750, '$500–$1,000', 'spring', 'Spring planting window opens Sep 2026 — order now from nursery', 2),
                ('Curtains', 'interior', 1400, '$800–$2,000', 'anytime', 'No seasonal constraint — check for EOFY sales June/July 2026', 3),
                ('Fix up gas box', 'exterior', 300, '$200–$400', 'anytime', 'Licensed plumber required — book now, no seasonal constraint', 2),
                ('Lay soil above retaining wall', 'landscaping', 600, '$400–$800', 'now', 'Do in autumn before winter rains compact the base', 1),
                ('Lay top dress of soil (lawn)', 'garden', 400, '$300–$600', 'spring', 'Apply top dress in Sep 2026 ahead of spring growth burst', 2),
                ('Built-in bookcase', 'interior', 2200, '$1,500–$3,000', 'anytime', 'Get quotes now — no seasonal constraint for interior work', 3),
                ('Garden beds (build/establish)', 'garden', 900, '$600–$1,200', 'now', 'Build beds NOW so soil settles and is ready for spring planting', 1),
                ('Extend walkway', 'exterior', 2000, '$1,500–$3,000', 'anytime', 'Dry weather ideal — current autumn window is perfect', 2),
                ('Install side retaining wall', 'landscaping', 4500, '$3,000–$6,000', 'now', 'Get quotes ASAP — tradies book up 3 months ahead before spring', 1),
                ('Install bed for water tank', 'landscaping', 600, '$400–$800', 'now', 'Must be done before tank delivery and irrigation install', 1),
                ('Kids wall art', 'interior', 350, '$200–$500', 'anytime', 'No seasonal constraint', 3),
                ('Foldaway bed (guest)', 'interior', 1200, '$800–$1,600', 'anytime', 'Check EOFY and Boxing Day sales for best price', 3),
                ('Bedroom deck', 'exterior', 8000, '$5,000–$12,000', 'spring', 'Build in Sep–Oct 2026 so it is ready for summer — book builder now', 2),
                ('Remove grass from out front', 'garden', 400, '$300–$600', 'now', 'Autumn is ideal — ground is soft and grass is slow-growing', 2),
                ('Seed and grow grass (new areas)', 'garden', 300, '$200–$400', 'spring', 'Sow lawn seed in Sep 2026 for best germination rate', 1),
                ('Aerate lawn (spring prep)', 'garden', 200, '$150–$300', 'now', 'Aerate in late autumn/early winter (now) so roots strengthen before spring', 1),
                ('Install irrigation system', 'garden', 2000, '$1,500–$3,000', 'now', 'Install NOW — critical path before spring planting; pipes in ground before spring', 1),
            ]
            for title, cat, cost, cost_range, season, timing_note, priority in wishlist_seed:
                db.execute(
                    'INSERT INTO wishlist (title,category,estimated_cost,cost_range,season,timing_note,priority,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (title, cat, cost, cost_range, season, timing_note, priority, 'pending', now)
                )
        # Seed default goals if empty
        count = db.execute('SELECT COUNT(*) FROM goals').fetchone()[0]
        if count == 0:
            default_goals = [
                ('Financial', 'Pay down mortgage to $600k', 'Reduce mortgage balance from $758k to $600k', '2028-01-01', 20),
                ('Financial', 'Build $50k share portfolio', 'Transition from paper trading to live portfolio', '2027-06-01', 5),
                ('Financial', 'Achieve positive cashflow on investment property', 'Cover mortgage repayments through rental income', '2027-01-01', 10),
                ('Human', 'Complete Family Wealth by James Hughes', 'Read and implement the 5 capitals framework', '2026-06-01', 50),
                ('Human', 'Annual family holiday', 'Plan and take at least one family trip per year', '2026-12-31', 0),
                ('Intellectual', 'Homeschool curriculum excellence', 'Kids achieve learning milestones across all subjects', '2026-12-31', 40),
                ('Social', 'Build family mission statement', 'Collaboratively draft the Whitewood family mission', '2026-09-01', 0),
                ('Spiritual', 'Weekly family reflection', 'Regular family values conversations and goal reviews', '2026-12-31', 20),
            ]
            now = datetime.now().isoformat()[:19]
            for capital, title, desc, target, progress in default_goals:
                db.execute('INSERT INTO goals (capital, title, description, target_date, progress, created_at) VALUES (?,?,?,?,?,?)',
                           (capital, title, desc, target, progress, now))
        # Seed budget targets if empty
        bt_count = db.execute('SELECT COUNT(*) FROM budget_targets').fetchone()[0]
        if bt_count == 0:
            now = datetime.now().isoformat()[:19]
            budget_seed = [
                # Business
                ('LegalVision',              1981.76, 'business'),
                ('Property Data Solutions',   194.23, 'business'),
                ('Claude.ai',                 154.54, 'business'),
                ('Starlink',                  139.00, 'business'),
                ('Google Cloud & Workspace',  128.78, 'business'),
                ('Car Insurance RACQ',        112.58, 'business'),
                ('ChatGPT / OpenAI',           68.84, 'business'),
                ('Microsoft 365',              49.37, 'business'),
                ('GoDaddy',                    48.00, 'business'),
                ('Xero',                       33.25, 'business'),
                ('OpenRouter',                 30.00, 'business'),
                ('Optus Mobile',               29.00, 'business'),
                ('Cursor AI',                  28.00, 'business'),
                ('Spotify (business)',         27.99, 'business'),
                # Personal
                ('Mortgage (GSB)',           3700.00, 'personal'),
                ('Groceries',               1500.00, 'personal'),
                ('Council Rates',            397.00, 'personal'),
                ('Dining Out',               300.00, 'personal'),
                ('Electricity (Alinta)',     280.00, 'personal'),
                ('Fuel (personal)',          200.00, 'personal'),
                ('Home & Contents (RACQ)',   165.90, 'personal'),
                ('Rackley Swimming',         107.00, 'personal'),
                ('GloBird Energy',           100.00, 'personal'),
                ('Kids Gym',                  80.00, 'personal'),
                ('Apple Subscriptions',       29.00, 'personal'),
                ('Netflix',                   20.99, 'personal'),
                ('Audible',                   16.45, 'personal'),
            ]
            for cat, target, btype in budget_seed:
                db.execute(
                    'INSERT INTO budget_targets (category, monthly_target, type, created_at, updated_at) VALUES (?,?,?,?,?)',
                    (cat, target, btype, now, now)
                )
        # Seed savings goals if empty
        sg_count = db.execute('SELECT COUNT(*) FROM savings_goals').fetchone()[0]
        if sg_count == 0:
            now = datetime.now().isoformat()[:19]
            savings_seed = [
                ('Emergency Fund',    7000, 0, 1, 'active', '2026-12-31'),
                ('Credit Card Payoff', 5000, 0, 2, 'active', '2026-09-30'),
            ]
            for name, target_amt, current, priority, status, target_dt in savings_seed:
                db.execute(
                    'INSERT INTO savings_goals (name, target_amount, current_amount, priority, status, target_date, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
                    (name, target_amt, current, priority, status, target_dt, now, now)
                )
        # Seed upcoming expenses if empty
        ue_count = db.execute('SELECT COUNT(*) FROM upcoming_expenses').fetchone()[0]
        if ue_count == 0:
            now = datetime.now().isoformat()[:19]
            upcoming_seed = [
                ('Adobe Renewal',   384.00, '2026-05-01', 1, 'Software & Tools'),
                ('Car Service',     299.00, '2026-05-15', 0, 'Fuel'),
                ('ATO BAS',        1500.00, '2026-05-28', 0, 'ATO / Tax'),
                ('SMS Insurance',  2955.00, '2026-09-01', 0, 'Insurance'),
                ('ASIC Annual Fee', 1798.00, '2027-04-14', 1, 'ASIC / Compliance'),
            ]
            for desc, amt, due, recurring, cat in upcoming_seed:
                db.execute(
                    'INSERT INTO upcoming_expenses (description, amount, due_date, recurring, category, status, created_at) VALUES (?,?,?,?,?,?,?)',
                    (desc, amt, due, recurring, cat, 'pending', now)
                )


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


# ── Birthdays ─────────────────────────────────────────────────────────────────

def load_birthdays(lookahead_days=60):
    if not BIRTHDAYS_PATH.exists():
        return []
    wb = openpyxl.load_workbook(BIRTHDAYS_PATH, read_only=True, data_only=True)
    ws = wb.active
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Australia/Brisbane')).date()
    results = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        birth_date = row[0]
        first = str(row[2] or '').strip()
        last = str(row[3] or '').strip()
        relationship = str(row[8] or '').strip()
        if not birth_date or not first:
            continue
        if isinstance(birth_date, datetime):
            birth_date = birth_date.date()
        elif not isinstance(birth_date, date):
            continue
        # Birthday this year
        try:
            this_year = birth_date.replace(year=today.year)
        except ValueError:
            continue  # Feb 29 in non-leap year
        if this_year < today:
            try:
                this_year = birth_date.replace(year=today.year + 1)
            except ValueError:
                continue
        days_until = (this_year - today).days
        if days_until <= lookahead_days:
            age = today.year - birth_date.year
            if today < this_year:
                age_upcoming = age
            else:
                age_upcoming = age
            results.append({
                'first': first, 'last': last,
                'name': f'{first} {last}'.strip(),
                'relationship': relationship,
                'birth_date': birth_date.isoformat(),
                'birthday_this_year': this_year.isoformat(),
                'days_until': days_until,
                'age_upcoming': this_year.year - birth_date.year,
            })
    results.sort(key=lambda x: x['days_until'])
    return results


# ── Property ──────────────────────────────────────────────────────────────────

def get_property_snapshot():
    cfg = load_config()
    prop = cfg.get('property', {})
    mortgage = prop.get('mortgage', {})
    purchase = prop.get('purchase_price', 0)
    estimated = prop.get('estimated_value', purchase)
    balance = mortgage.get('balance', 0)
    equity = estimated - balance
    equity_pct = round(equity / estimated * 100, 1) if estimated else 0
    rate = mortgage.get('rate', 0)
    annual_interest = round(balance * rate / 100)
    return {
        'address': prop.get('address', ''),
        'purchase_price': purchase,
        'estimated_value': estimated,
        'estimated_value_updated': prop.get('estimated_value_updated'),
        'mortgage_balance': balance,
        'equity': equity,
        'equity_pct': equity_pct,
        'rate': rate,
        'type': mortgage.get('type', ''),
        'repayment': mortgage.get('repayment', 0),
        'next_due': mortgage.get('next_due', ''),
        'annual_interest': annual_interest,
        'lender': mortgage.get('lender', ''),
    }


# ── Chat ──────────────────────────────────────────────────────────────────────

def build_family_context():
    """Build rich context for Claude about the family's current state."""
    cfg = load_config()
    today = date.today()
    birthdays = load_birthdays(30)
    prop = get_property_snapshot()

    with get_db() as db:
        goals = [dict(r) for r in db.execute(
            "SELECT * FROM goals WHERE status='active' ORDER BY capital, target_date").fetchall()]
        recent_notes = [dict(r) for r in db.execute(
            "SELECT * FROM notes ORDER BY created_at DESC LIMIT 10").fetchall()]

    bday_text = ''
    if birthdays:
        for b in birthdays[:5]:
            bday_text += f"  - {b['name']} ({b['relationship']}): {b['birthday_this_year']} — {b['days_until']} days away, turning {b['age_upcoming']}\n"

    goals_text = ''
    for capital in ['Financial', 'Human', 'Intellectual', 'Social', 'Spiritual']:
        caps_goals = [g for g in goals if g['capital'] == capital]
        if caps_goals:
            goals_text += f"  {capital} Capital:\n"
            for g in caps_goals:
                goals_text += f"    - {g['title']} ({g['progress']}% complete)"
                if g['target_date']:
                    goals_text += f" — target {g['target_date']}"
                goals_text += '\n'

    return f"""You are the Whitewood Family HQ assistant. You help Tyson and Robyn Whitewood manage their family life.

TODAY: {today.strftime('%A %d %B %Y')}

FAMILY:
- Tyson Whitewood (husband, property manager/business owner)
- Robyn Whitewood (wife)
- Children are homeschooled via guidepost.au

PROPERTY:
- Address: {prop['address']}
- Purchase price: ${prop['purchase_price']:,}
- Estimated current value: ${prop['estimated_value']:,}
- Mortgage balance: ${prop['mortgage_balance']:,.2f} ({prop['rate']}% interest only)
- Monthly repayment: ${prop['repayment']:,}
- Equity: ${prop['equity']:,.0f} ({prop['equity_pct']}%)
- Annual interest cost: ${prop['annual_interest']:,}

UPCOMING BIRTHDAYS (next 30 days):
{bday_text or "  None in the next 30 days"}

FAMILY GOALS (Hughes 5 Capitals Framework):
{goals_text}

CONTEXT: The family follows James E. Hughes Jr.'s framework from "Family Wealth: Keeping It in the Family" — prioritising human, intellectual, social, spiritual, and financial capital in that order.

CAPABILITIES:
- Answer questions about birthdays, property, net worth, goals
- Help plan gifts for upcoming birthdays
- Discuss financial goals and progress
- Help with weekly planning
- If asked about calendar: explain Google Calendar needs to be connected in Settings
- Be warm, practical, and family-focused in your responses
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/')
@app.route('/index.html')
def dashboard():
    html_path = ROOT / 'dashboard.html'
    if html_path.exists():
        return send_file(html_path)
    return '<h1>Family HQ — dashboard.html not found</h1>', 404

@app.route('/manifest.json')
def manifest():
    return send_file(ROOT / 'manifest.json', mimetype='application/manifest+json')

@app.route('/icon-192.png')
@app.route('/icon-512.png')
def icon():
    # Return a simple green SVG-based icon as PNG placeholder
    # In production, replace with actual PNG icons
    from flask import Response as R
    size = 192 if '192' in request.path else 512
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">
      <rect width="100" height="100" rx="20" fill="#1B4332"/>
      <text x="50" y="65" font-size="55" text-anchor="middle" fill="#D4A017">🏡</text>
    </svg>'''
    return R(svg, mimetype='image/svg+xml')

@app.route('/api/summary')
def api_summary():
    """Morning briefing summary."""
    today = date.today()
    birthdays = load_birthdays(14)
    prop = get_property_snapshot()
    with get_db() as db:
        goals_count = db.execute("SELECT COUNT(*) FROM goals WHERE status='active'").fetchone()[0]
    return jsonify({
        'date': today.isoformat(),
        'date_nice': today.strftime('%A %d %B %Y'),
        'birthdays_soon': birthdays,
        'property': prop,
        'goals_active': goals_count,
    })

@app.route('/api/birthdays')
def api_birthdays():
    days = int(request.args.get('days', 60))
    return jsonify(load_birthdays(days))

@app.route('/api/property', methods=['GET', 'PUT'])
def api_property():
    if request.method == 'PUT':
        data = request.get_json(force=True)
        cfg = load_config()
        if 'estimated_value' in data:
            cfg['property']['estimated_value'] = data['estimated_value']
            cfg['property']['estimated_value_updated'] = date.today().isoformat()
        if 'mortgage_balance' in data:
            cfg['property']['mortgage']['balance'] = data['mortgage_balance']
        if 'notes' in data:
            with get_db() as db:
                db.execute('INSERT INTO property_log (estimated_value, note, recorded_at) VALUES (?,?,?)',
                           (data.get('estimated_value'), data.get('notes', ''), datetime.now().isoformat()[:19]))
        save_config(cfg)
        return jsonify({'ok': True})
    return jsonify(get_property_snapshot())

@app.route('/api/goals', methods=['GET', 'POST'])
def api_goals():
    with get_db() as db:
        if request.method == 'POST':
            d = request.get_json(force=True)
            now = datetime.now().isoformat()[:19]
            cur = db.execute(
                'INSERT INTO goals (capital, title, description, target_date, progress, created_at) VALUES (?,?,?,?,?,?)',
                (d.get('capital', 'Financial'), d.get('title', ''), d.get('description', ''),
                 d.get('target_date'), d.get('progress', 0), now)
            )
            row = db.execute('SELECT * FROM goals WHERE id=?', (cur.lastrowid,)).fetchone()
            return jsonify(dict(row)), 201
        goals = [dict(r) for r in db.execute(
            "SELECT * FROM goals ORDER BY CASE capital WHEN 'Human' THEN 1 WHEN 'Intellectual' THEN 2 WHEN 'Social' THEN 3 WHEN 'Spiritual' THEN 4 ELSE 5 END, target_date"
        ).fetchall()]
        return jsonify(goals)

@app.route('/api/goals/<int:gid>', methods=['PUT', 'DELETE'])
def api_goal(gid):
    with get_db() as db:
        if request.method == 'DELETE':
            db.execute('UPDATE goals SET status="archived" WHERE id=?', (gid,))
            return jsonify({'ok': True})
        d = request.get_json(force=True)
        fields, params = [], []
        for col in ('title', 'description', 'capital', 'target_date', 'status', 'progress'):
            if col in d:
                fields.append(f'{col}=?'); params.append(d[col])
        if fields:
            params.append(gid)
            db.execute(f'UPDATE goals SET {",".join(fields)} WHERE id=?', params)
        row = db.execute('SELECT * FROM goals WHERE id=?', (gid,)).fetchone()
        return jsonify(dict(row))

@app.route('/api/chat', methods=['POST'])
def api_chat():
    if not llm_available():
        return jsonify({'error': 'No AI configured — add ANTHROPIC_API_KEY or OPENROUTER_API_KEY in settings'}), 503
    data = request.get_json(force=True)
    user_msg = (data.get('message') or '').strip()
    if not user_msg:
        return jsonify({'error': 'message required'}), 400

    with get_db() as db:
        history = [dict(r) for r in db.execute(
            "SELECT role, content FROM chat_history ORDER BY id DESC LIMIT 20"
        ).fetchall()]
        history.reverse()

    messages = [{'role': h['role'], 'content': h['content']} for h in history]
    messages.append({'role': 'user', 'content': user_msg})

    reply = llm_chat(messages, system=build_family_context())

    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute('INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)', ('user', user_msg, now))
        db.execute('INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)', ('assistant', reply, now))

    model = 'claude-sonnet-4-6' if _anthropic_key() else 'llama-3.3-70b (openrouter)'
    return jsonify({'reply': reply, 'model': model})

@app.route('/api/chat/history')
def api_chat_history():
    limit = int(request.args.get('limit', 50))
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM chat_history ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()]
    rows.reverse()
    return jsonify(rows)

@app.route('/api/chat/clear', methods=['POST'])
def api_chat_clear():
    with get_db() as db:
        db.execute('DELETE FROM chat_history')
    return jsonify({'ok': True})

@app.route('/api/notes', methods=['GET', 'POST'])
def api_notes():
    with get_db() as db:
        if request.method == 'POST':
            d = request.get_json(force=True)
            now = datetime.now().isoformat()[:19]
            cur = db.execute('INSERT INTO notes (category, content, created_at) VALUES (?,?,?)',
                             (d.get('category', 'general'), d.get('content', ''), now))
            row = db.execute('SELECT * FROM notes WHERE id=?', (cur.lastrowid,)).fetchone()
            return jsonify(dict(row)), 201
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM notes ORDER BY created_at DESC LIMIT 50').fetchall()]
        return jsonify(rows)

@app.route('/api/config', methods=['GET'])
def api_config():
    cfg = load_config()
    # Don't expose sensitive keys
    safe = {
        'family': cfg.get('family', {}),
        'integrations': cfg.get('integrations', {}),
        'homeschool': cfg.get('homeschool', {}),
    }
    return jsonify(safe)

@app.route('/api/integrations/status')
def api_integrations():
    token_dir = DATA_DIR / 'tokens'
    return jsonify({
        'google_calendar': (token_dir / 'google_token.json').exists(),
        'anthropic': bool(_anthropic_key()),
        'openrouter': bool(_openrouter_key()),
        'ai_ready': llm_available(),
        'outlook': True,
    })

@app.route('/api/briefing')
def api_briefing():
    """Generate a morning briefing using Claude or OpenRouter. Cached per day."""
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Australia/Brisbane')).date()
    today_str = today.isoformat()

    # Return cached briefing if already generated today
    with get_db() as db:
        cached = db.execute(
            "SELECT briefing FROM briefing_cache WHERE date=?", (today_str,)
        ).fetchone()
    if cached:
        return jsonify({'briefing': cached['briefing'], 'date': today_str, 'cached': True})

    if not llm_available():
        return jsonify({'error': 'AI not configured — add ANTHROPIC_API_KEY or OPENROUTER_API_KEY in Coolify'}), 503

    birthdays = load_birthdays(7)
    prop = get_property_snapshot()
    with get_db() as db:
        goals = [dict(r) for r in db.execute(
            "SELECT * FROM goals WHERE status='active' ORDER BY progress ASC LIMIT 5").fetchall()]

    prompt = f"""Generate a warm, concise morning family briefing for {today.strftime('%A %d %B %Y')}.

Include:
1. A friendly greeting for Tyson and Robyn
2. Any birthdays in the next 7 days with gift planning suggestions
3. One highlight from the family goals (pick the most relevant/urgent)
4. A brief property note (next repayment is {prop['next_due']})
5. A motivational closing line

Birthdays soon: {json.dumps(birthdays, indent=2) if birthdays else 'None this week'}
Goals progress: {json.dumps([{'title': g['title'], 'progress': g['progress']} for g in goals], indent=2)}

Keep it under 200 words, warm and personal."""

    try:
        briefing = llm_chat([{'role': 'user', 'content': prompt}], max_tokens=512)
    except Exception as e:
        # Fall back to most recent cached briefing if available
        with get_db() as db:
            fallback = db.execute(
                "SELECT briefing, date FROM briefing_cache ORDER BY date DESC LIMIT 1"
            ).fetchone()
        if fallback:
            return jsonify({'briefing': f"[From {fallback['date']}] {fallback['briefing']}", 'date': today_str, 'cached': True})
        return jsonify({'error': f'AI request failed: {str(e)[:300]}'}), 500

    # Cache for the day
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO briefing_cache (date, briefing, created_at) VALUES (?,?,?)",
            (today_str, briefing, now)
        )

    return jsonify({'briefing': briefing, 'date': today_str})


# ── Wishlist ──────────────────────────────────────────────────────────────────

@app.route('/api/wishlist', methods=['GET', 'POST'])
def api_wishlist():
    with get_db() as db:
        if request.method == 'POST':
            d = request.get_json(force=True)
            now = datetime.now().isoformat()[:19]
            cur = db.execute(
                'INSERT INTO wishlist (title,category,estimated_cost,cost_range,season,timing_note,priority,status,ai_note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (d.get('title',''), d.get('category','general'), int(d.get('estimated_cost',0) or 0),
                 d.get('cost_range',''), d.get('season','anytime'), d.get('timing_note',''),
                 int(d.get('priority',2)), d.get('status','pending'), d.get('ai_note',''), now)
            )
            row = db.execute('SELECT * FROM wishlist WHERE id=?', (cur.lastrowid,)).fetchone()
            return jsonify(dict(row)), 201
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM wishlist ORDER BY priority ASC, id ASC').fetchall()]
        return jsonify(rows)

@app.route('/api/wishlist/<int:wid>', methods=['PUT', 'DELETE'])
def api_wishlist_item(wid):
    with get_db() as db:
        if request.method == 'DELETE':
            db.execute('DELETE FROM wishlist WHERE id=?', (wid,))
            return jsonify({'ok': True})
        d = request.get_json(force=True)
        fields, params = [], []
        for col in ('title','category','estimated_cost','cost_range','season','timing_note','priority','status','ai_note'):
            if col in d:
                val = int(d[col]) if col in ('estimated_cost','priority') else d[col]
                fields.append(f'{col}=?'); params.append(val)
        if fields:
            params.append(wid)
            db.execute(f'UPDATE wishlist SET {",".join(fields)} WHERE id=?', params)
        row = db.execute('SELECT * FROM wishlist WHERE id=?', (wid,)).fetchone()
        return jsonify(dict(row))

@app.route('/api/wishlist/ai-estimate', methods=['POST'])
def api_wishlist_ai_estimate():
    if not llm_available():
        return jsonify({'error': 'No AI configured'}), 503
    d = request.get_json(force=True)
    item_title = (d.get('title') or '').strip()
    if not item_title:
        return jsonify({'error': 'title required'}), 400
    prompt = f"""You are helping an Australian homeowner (southeast Queensland, subtropical climate) estimate a home improvement task.
Task: "{item_title}"
Today is April 2026. Spring starts September 2026.

Respond in JSON only with these fields:
- estimated_cost: integer (mid-range AUD estimate for 2026)
- cost_range: string like "$X,XXX–$X,XXX"
- season: one of "now", "spring", "winter", "anytime"
- timing_note: one sentence of practical timing advice
- ai_note: one sentence on what to watch out for or how to save money

JSON only, no other text."""
    try:
        result = llm_chat([{'role': 'user', 'content': prompt}], max_tokens=256)
        # Extract JSON from response
        json_match = re.search(r'\{[^}]+\}', result, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return jsonify(data)
        return jsonify({'error': 'Could not parse AI response', 'raw': result}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Warranties ───────────────────────────────────────────────────────────────

@app.route('/api/warranties', methods=['GET', 'POST'])
@login_required
def api_warranties():
    with get_db() as db:
        if request.method == 'POST':
            d = request.get_json(force=True)
            now = datetime.now().isoformat()[:19]
            cur = db.execute(
                'INSERT INTO warranties (product,provider,model_number,serial_number,purchased_date,expires_date,standard_expires_date,extended_expires_date,date_source,coverage,claim_info,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (d.get('product',''), d.get('provider',''), d.get('model_number',''),
                 d.get('serial_number',''), d.get('purchased_date',''),
                 d.get('expires_date',''), d.get('standard_expires_date',''),
                 d.get('extended_expires_date','') or None,
                 d.get('date_source',''),
                 d.get('coverage',''), d.get('claim_info',''), d.get('notes',''), now)
            )
            row = db.execute('SELECT * FROM warranties WHERE id=?', (cur.lastrowid,)).fetchone()
            return jsonify(dict(row)), 201
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM warranties ORDER BY COALESCE(standard_expires_date, expires_date) ASC'
        ).fetchall()]
        return jsonify(rows)

@app.route('/api/warranties/alerts')
@login_required
def warranty_alerts():
    today = date.today()
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM warranties WHERE expires_date >= ? ORDER BY expires_date ASC',
            (today.isoformat(),)
        ).fetchall()]
    alerts = []
    for w in rows:
        exp = w['expires_date']
        if not exp:
            continue
        days_left = (date.fromisoformat(exp) - today).days
        months_left = days_left / 30.44
        if months_left <= 3:
            level = 3
        elif months_left <= 6:
            level = 6
        elif months_left <= 9:
            level = 9
        elif months_left <= 12:
            level = 12
        else:
            continue
        alerts.append({**w, 'days_left': days_left, 'months_left': round(months_left, 1), 'alert_level': level})
    return jsonify(alerts)

@app.route('/api/warranties/<int:wid>', methods=['PUT', 'DELETE'])
@login_required
def api_warranty_item(wid):
    with get_db() as db:
        if request.method == 'DELETE':
            db.execute('DELETE FROM warranties WHERE id=?', (wid,))
            return jsonify({'ok': True})
        d = request.get_json(force=True)
        fields, params = [], []
        for col in ('product','provider','model_number','serial_number','purchased_date','expires_date','standard_expires_date','extended_expires_date','date_source','coverage','claim_info','notes'):
            if col in d:
                fields.append(f'{col}=?'); params.append(d[col])
        if fields:
            params.append(wid)
            db.execute(f'UPDATE warranties SET {",".join(fields)} WHERE id=?', params)
        row = db.execute('SELECT * FROM warranties WHERE id=?', (wid,)).fetchone()
        return jsonify(dict(row))


# ── Insurances ────────────────────────────────────────────────────────────────

@app.route('/api/insurances', methods=['GET', 'POST'])
@login_required
def api_insurances():
    with get_db() as db:
        if request.method == 'POST':
            d = request.get_json(force=True)
            now = datetime.now().isoformat()[:19]
            cur = db.execute(
                'INSERT INTO insurances (type,provider,policy_number,premium,renewal_date,coverage,notes,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (d.get('type',''), d.get('provider',''), d.get('policy_number',''),
                 d.get('premium',''), d.get('renewal_date',''), d.get('coverage',''),
                 d.get('notes',''), now)
            )
            row = db.execute('SELECT * FROM insurances WHERE id=?', (cur.lastrowid,)).fetchone()
            return jsonify(dict(row)), 201
        rows = [dict(r) for r in db.execute('SELECT * FROM insurances ORDER BY type ASC').fetchall()]
        return jsonify(rows)

@app.route('/api/insurances/<int:iid>', methods=['PUT', 'DELETE'])
@login_required
def api_insurance_item(iid):
    with get_db() as db:
        if request.method == 'DELETE':
            db.execute('DELETE FROM insurances WHERE id=?', (iid,))
            return jsonify({'ok': True})
        d = request.get_json(force=True)
        fields, params = [], []
        for col in ('type','provider','policy_number','premium','renewal_date','coverage','notes'):
            if col in d:
                fields.append(f'{col}=?'); params.append(d[col])
        if fields:
            params.append(iid)
            db.execute(f'UPDATE insurances SET {",".join(fields)} WHERE id=?', params)
        row = db.execute('SELECT * FROM insurances WHERE id=?', (iid,)).fetchone()
        return jsonify(dict(row))


# ── Document Upload / Serve ──────────────────────────────────────────────────

DOCS_DIR = DATA_DIR / 'documents'

def _save_upload(file, prefix: str) -> str:
    """Save an uploaded file, return its stored filename."""
    DOCS_DIR.mkdir(exist_ok=True)
    import uuid
    ext = Path(file.filename).suffix.lower() if file.filename else '.pdf'
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"
    file.save(str(DOCS_DIR / filename))
    return filename

@app.route('/api/warranties/<int:wid>/upload', methods=['POST'])
@login_required
def api_warranty_upload(wid):
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'no file'}), 400
    filename = _save_upload(f, f'warranty_{wid}')
    with get_db() as db:
        # Remove old file if present
        row = db.execute('SELECT document_path FROM warranties WHERE id=?', (wid,)).fetchone()
        if row and row['document_path']:
            old = DOCS_DIR / row['document_path']
            if old.exists(): old.unlink()
        db.execute('UPDATE warranties SET document_path=? WHERE id=?', (filename, wid))
        row = db.execute('SELECT * FROM warranties WHERE id=?', (wid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/insurances/<int:iid>/upload', methods=['POST'])
@login_required
def api_insurance_upload(iid):
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'no file'}), 400
    filename = _save_upload(f, f'insurance_{iid}')
    with get_db() as db:
        row = db.execute('SELECT document_path FROM insurances WHERE id=?', (iid,)).fetchone()
        if row and row['document_path']:
            old = DOCS_DIR / row['document_path']
            if old.exists(): old.unlink()
        db.execute('UPDATE insurances SET document_path=? WHERE id=?', (filename, iid))
        row = db.execute('SELECT * FROM insurances WHERE id=?', (iid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/documents/<path:filename>')
@login_required
def api_document_serve(filename):
    filepath = DOCS_DIR / filename
    if not filepath.exists() or not filepath.resolve().is_relative_to(DOCS_DIR.resolve()):
        abort(404)
    return send_file(str(filepath))


# ── Discord Integration ───────────────────────────────────────────────────────

def send_discord_webhook(message: str, username: str = 'Family HQ'):
    """Send a message to the configured Discord channel via webhook."""
    cfg = load_config()
    webhook_url = cfg.get('discord', {}).get('webhook_url')
    if not webhook_url:
        return False
    payload = json.dumps({'content': message, 'username': username}).encode()
    req = urllib.request.Request(webhook_url, data=payload,
                                  headers={
                                      'Content-Type': 'application/json',
                                      'User-Agent': 'DiscordBot (family-hq, 1.0)',
                                  }, method='POST')
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f'Discord webhook error: {e}')
        return False

@app.route('/api/discord/chat', methods=['POST'])
def discord_chat():
    """Handle a message from Discord — reply via webhook."""
    if not llm_available():
        return jsonify({'error': 'AI not configured — add ANTHROPIC_API_KEY or OPENROUTER_API_KEY in Coolify'}), 503
    data = request.get_json(force=True)
    user_msg = (data.get('message') or '').strip()
    author = data.get('author', 'Family')
    if not user_msg:
        return jsonify({'error': 'message required'}), 400

    # Build context-aware chat
    with get_db() as db:
        history = [dict(r) for r in db.execute(
            "SELECT role, content FROM chat_history ORDER BY id DESC LIMIT 10"
        ).fetchall()]
        history.reverse()

    messages = [{'role': h['role'], 'content': h['content']} for h in history]
    messages.append({'role': 'user', 'content': f'[{author}]: {user_msg}'})

    reply = llm_chat(messages, system=build_family_context(), max_tokens=800)

    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute('INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)',
                   ('user', f'[{author}]: {user_msg}', now))
        db.execute('INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)',
                   ('assistant', reply, now))

    # Send reply to Discord
    send_discord_webhook(reply)
    return jsonify({'reply': reply})

@app.route('/api/discord/webhook-test', methods=['POST'])
def discord_webhook_test():
    """Test the Discord webhook."""
    ok = send_discord_webhook('✅ Family HQ Discord integration is working! You can now chat with me here.')
    return jsonify({'ok': ok})


TOKEN_DIR = DATA_DIR / 'tokens'

# ── Finance CSV + AI Chat ─────────────────────────────────────────────────────

# Check multiple paths — Coolify volume mount takes priority
_FINANCE_CSV_CANDIDATES = [
    Path('/app/family-wealth/2. Financial Capital/Banking & Cash Flow'),  # Coolify volume mount
    Path('/app/data/bank_statements'),                                     # manual uploads
    Path('/home/claude/family-wealth/2. Financial Capital/Banking & Cash Flow'),  # dev/host
]
FINANCE_CSV_DIR = next((p for p in _FINANCE_CSV_CANDIDATES if p.exists()), _FINANCE_CSV_CANDIDATES[1])
FINANCE_ACCOUNT_OWNERSHIPS = {'personal', 'business'}
FINANCE_ACCOUNT_TYPES = {'cash', 'credit', 'loan'}

def _folder_sort_key(p):
    """Sort dated subfolders chronologically. Supports dd.mm.yyyy and yyyy-mm-dd; falls back to name."""
    name = p.name
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%Y.%m.%d'):
        try:
            return datetime.strptime(name, fmt).date().isoformat()
        except ValueError:
            continue
    return name


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _trusted_finance_csv_roots() -> list[Path]:
    """Resolve the configured statement roots before accepting descendant paths."""
    roots = []
    for directory in (FINANCE_CSV_DIR, DATA_DIR / 'bank_statements'):
        try:
            resolved_directory = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved_directory.is_dir() and resolved_directory not in roots:
            roots.append(resolved_directory)
    return roots


def _finance_csv_search_dirs() -> list[Path]:
    """Return only statement directories contained by stable trusted roots."""
    trusted_roots = _trusted_finance_csv_roots()
    try:
        finance_root = FINANCE_CSV_DIR.resolve(strict=True)
    except (OSError, RuntimeError):
        finance_root = None

    subdirs = []
    if finance_root is not None:
        for directory in FINANCE_CSV_DIR.glob('*'):
            try:
                resolved_directory = directory.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved_directory.is_dir() and _is_path_within(
                resolved_directory, finance_root
            ):
                subdirs.append(resolved_directory)
    search_dirs = sorted(subdirs, key=_folder_sort_key, reverse=True)[:3]
    for root in trusted_roots:
        if root not in search_dirs:
            search_dirs.append(root)
    return search_dirs


def _find_finance_csv(stored_filename: str) -> Path | None:
    """Find an exact statement filename without allowing a caller-selected path."""
    filename = Path(stored_filename)
    if (
        not stored_filename
        or filename.name != stored_filename
        or filename.suffix.lower() != '.csv'
    ):
        return None
    trusted_roots = _trusted_finance_csv_roots()
    for directory in _finance_csv_search_dirs():
        candidate = directory / stored_filename
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if (
            resolved_candidate.is_file()
            and any(
                _is_path_within(resolved_candidate, root)
                for root in trusted_roots
            )
        ):
            return resolved_candidate
    return None


def _deduplicate_transactions(transactions):
    """Backward-compatible wrapper for callers with unregistered legacy rows."""
    from finance_imports import deduplicate_transactions
    prepared = []
    for transaction in transactions:
        if "account_key" in transaction:
            prepared.append(transaction)
            continue
        prepared.append({
            **transaction,
            "account_key": f"legacy:{str(transaction.get('account', '')).strip().lower()}",
        })
    return deduplicate_transactions(prepared)


def _parse_csv_files():
    """Parse all bank CSV files from the synced family-wealth folder. Returns list of transactions."""
    from finance_imports import deduplicate_transactions, parse_csv_file

    transactions = []
    import_map = _finance_import_map()
    seen_files = set()
    for folder in _finance_csv_search_dirs():
        for csv_path in sorted(folder.glob('*.csv')):
            if csv_path.name in seen_files:
                continue
            seen_files.add(csv_path.name)
            import_metadata = import_map.get(csv_path.name)
            if import_metadata:
                metadata = {
                    "id": import_metadata["account_id"],
                    "name": import_metadata["account_name"],
                    "ownership": import_metadata["ownership"],
                    "account_type": import_metadata["account_type"],
                }
            else:
                metadata = {
                    "id": None,
                    "name": csv_path.stem,
                    **_legacy_account_defaults(csv_path.stem),
                }
            transactions.extend(parse_csv_file(csv_path, metadata))
    transactions.sort(key=lambda x: x['date'], reverse=True)
    return deduplicate_transactions(transactions)


def _finance_context_summary(transactions, max_txns=80):
    """Build a text summary of finances for LLM context."""
    if not transactions:
        return "No financial data available."
    from collections import defaultdict
    # Per-account summary
    accounts = defaultdict(lambda: {'txns': [], 'latest_balance': None})
    for t in transactions:
        accounts[t['account']]['txns'].append(t)
        if accounts[t['account']]['latest_balance'] is None and t['balance']:
            accounts[t['account']]['latest_balance'] = t['balance']
    lines = ['== BANK ACCOUNT SUMMARIES ==']
    for acc, data in accounts.items():
        txns = data['txns']
        total_in  = sum(t['amount'] for t in txns if t['amount'] > 0)
        total_out = sum(t['amount'] for t in txns if t['amount'] < 0)
        bal = data['latest_balance']
        lines.append(f'\n[{acc}]')
        lines.append(f'  Transactions: {len(txns)}  |  Total in: +${total_in:,.2f}  |  Total out: -${abs(total_out):,.2f}')
        if bal:
            lines.append(f'  Latest balance: ${bal:,.2f}')
    lines.append('\n== RECENT TRANSACTIONS (last 80) ==')
    for t in transactions[:max_txns]:
        sign = '+' if t['amount'] >= 0 else ''
        lines.append(f"{t['date']}  {sign}{t['amount']:>10.2f}  [{t['account'][:20]}]  {t['description'][:60]}")
    return '\n'.join(lines)


CATEGORY_RULES = [
    # ── Groceries & Food ──────────────────────────────────────────────────────
    ('Groceries',         ['woolworth','coles','aldi','iga','spar','foodworks','spudshed','butcher','bakery','fruit shop','fresh market','harris farm','asian grocer','deli ']),
    ('Dining Out',        ['mcdonald','hungry jacks','kfc','subway','domino','pizza','cafe ','coffee','restaurant','bistro','canteen','grill','burger','sushi','noodle','thai','chinese','indian','hangi','donut','pastry','bakehouse','kebab','mexican','italian','tapas','food court','oporto','guzman','chatime','taco','roll\'d','bar & grill','pub meal']),
    # ── Transport ─────────────────────────────────────────────────────────────
    ('Fuel',              ['shell ','bp ','caltex','7-eleven','ampol','puma fuel','petrol',' servo','united petroleum','liberty oil']),
    ('Transport',         ['uber','ola ride','didi','taxi','rideshare','translink','opal','myki','limousine','bus ticket']),
    ('Parking / Tolls',   ['wilson parking','secure parking','care park','linkt','citylink','transurban','e-toll','infringement']),
    # ── Family & Kids ─────────────────────────────────────────────────────────
    ('School / Kids',     ['rackley','swim school','school fees','tutor','dance','martial arts','gymnastics','montessori','preschool','daycare','child care','kindy','little athletics','soccer club','football club','cricket club','netball','sport fee']),
    ('Education',         ['education','curriculum','homeschool','home school','school book','textbook']),
    ('Health & Medical',  ['chemist','pharmacy','priceline','terry white','amcal','doctor','medical centre','hospital','dental','dentist','physio','psychologist','health fund','medibank','bupa','nib ','optical','hearing','specialist','pathology']),
    # ── Home ──────────────────────────────────────────────────────────────────
    ('Home & Garden',     ['bunning','mitre 10','hardware store','nursery','garden centre','plumber','plumbing','electrician','reno','handyman','cleaners','cleaning service','pool ','pest control','locksmith','furniture','ikea','fantastic furn','nick scali','amart','harvey norm','callaway homes','sq *callaway']),
    ('Home Utilities',    ['agl','origin energy','energy australia','electricity','ergon','endeavour energy','council rates','water corp','synergy','seqwater','unitywater','origin gas','nt power']),
    ('Rent / Mortgage',   ['home loan repay','mortgage repay','rental payment','strata levy','body corporate','property manager']),
    # ── Lifestyle ─────────────────────────────────────────────────────────────
    ('Clothing',          ['kmart','target','big w','myer','david jones','cotton on','uniqlo','h&m','country road','clothing','fashion','shoes','nike','adidas','rebel sport','glue store','factorie','jeanswest','rivers']),
    ('Electronics',       ['jb hi-fi','harvey norman','officeworks','apple store','bing lee','jaycar','dji','sony','samsung','the good guys','microsoft store','camera house']),
    ('Online Shopping',   ['ebay','etsy','aliexpress','wish.com','catch.com','kogan','temu','shein','the iconic','net-a-porter']),
    ('Entertainment',     ['hoyts','event cinemas','village cinema','reading cinema','ticketek','ticketmaster','moshtix','oztix','theme park','dreamworld','movieworld','sea world','bowling','minigolf','escape room','laser tag','trampoline']),
    ('Beauty / Wellbeing',['salon','hairdresser','barber','nail bar','spa ','massage','waxing','blow dry','lash ','mecca cosme','sephora']),
    ('Sports / Fitness',  ['crossfit','f45','anytime fitness','jetts','goodlife','planet fitness','yoga','pilates','swim centre','aquatic centre','golf club','tennis club','sportsmans']),
    ('Travel',            ['airbnb','hotel','motel','jetstar','qantas','virgin australia','tigerair','bonza','booking.com','expedia','wotif','trivago','car hire','hertz','budget rent','avis','campervan','hireace','rental car','thrifty','europcar']),
    ('Streaming / TV',    ['netflix','spotify','disney+','foxtel','binge','stan ','paramount','apple tv','youtube premium','amazon prime','kindle','audible']),
    # ── Insurance & Finance ───────────────────────────────────────────────────
    ('Insurance',         ['racq','insurance','insur','iag','allianz','suncorp','nrma','gt insurance','aami','cgu ','qbe','zurich','woolworths insurance','budget direct']),
    ('Banking / Fees',    ['monthly fee','account fee','bank fee','dishonour fee','overdrawn fee','card fee','annual card fee','late payment fee']),
    ('ATM / Cash',        ['atm ','cash out','cash withdrawal','currency exchange','foreign atm']),
    # ── Digital / Software (personal + business) ─────────────────────────────
    ('AI & Cloud',        ['openai','anthropic','claude','perplexity','midjourney','runway','elevenlabs','aws ','google cloud','azure','digitalocean','linode','vultr','hetzner','coolify','cloudflare','vercel','railway']),
    ('Software & Tools',  ['github','dropbox','notion','slack','zoom','figma','loom','canva','adobe','1password','lastpass','bitwarden','namecheap','godaddy','squarespace','wix','mailchimp','hubspot','salesforce','shopify','klaviyo','xero','myob','quickbooks','reckon','property data solutions','propertyme','corelogic','pricefinder','rea group','domain.com.au','realestateview','pricespy']),
    ('Telco / Internet',  ['telstra','optus','vodafone','boost mobile','amaysim','circles.life','belong','aussie broadband','superloop','nbn','kogan mobile','dodo','iinet','internode','starlink','click data','click wifi','click network']),
    # ── Business ──────────────────────────────────────────────────────────────
    ('Payroll / Super',   ['payroll','salary payment','wages','superannuation','australiansuper','rest super','hostplus','sunsuper','colonial first','amp super','bt super','cbus','hesta']),
    ('ASIC / Compliance', ['asic ','asic/','company reg','business reg','abn reg','australian securities']),
    ('ATO / Tax',         ['ato ','australian taxation','bas payment','payg','gst payment','tax office','tax instalment','fringe benefit']),
    ('Commercial Rent',   ['commercial lease','shop lease','office lease','body corp levy','commercial property']),
    ('Marketing',         ['google ads','facebook ads','meta ads','instagram ads','tiktok ads','adwords','advertising','marketing agency','pr agency']),
    ('Staff / Contractors',['contractor pay','freelance','labour hire','staffing agency','recruitment fee','marina winterburn','imt nzd','nzd imt']),
    ('Accounting & Legal',['accountant','bookkeeper','solicitor','legal fee','consulting fee','advisory fee','audit fee','legalvision','legalzoom','doculivery']),
    ('POS & Payments',    ['squareup','tyro','eftpos merchant','stripe fee','pos system','rept.ai']),
    ('Business Supplies', ['stationery','packaging','signage','uniform','workwear','office supplies','boonah hardware','mitre 10 trade','total tools','sydney tools']),
    ('Freight & Post',    ['australia post','sendle','startrack','fastway','toll ipec','tnt ','courier please','zoom2u','freight','shipping cost']),
    ('Transfers',         ['transfer to','transfer from','pay id','osko','bpay','direct credit','autosave','linked saver']),
]

BUSINESS_ACCOUNT_KEYWORDS = ['eden', 'commercial', 'business', 'pty', 'company']

def _is_business_account(account_name: str) -> bool:
    lower = account_name.lower()
    return any(kw in lower for kw in BUSINESS_ACCOUNT_KEYWORDS)


def _legacy_account_defaults(name: str) -> dict:
    lower = str(name).lower()
    account_type = (
        "loan" if any(word in lower for word in ("loan", "mortgage"))
        else "credit" if "credit card" in lower
        else "cash"
    )
    ownership = "business" if _is_business_account(name) else "personal"
    return {"ownership": ownership, "account_type": account_type}


def _registered_finance_accounts() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT id, name, ownership, account_type, active, created_at, updated_at "
            "FROM finance_accounts ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(row) for row in rows]


def _finance_import_map() -> dict[str, dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT finance_imports.id, finance_imports.original_filename, "
            "finance_imports.stored_filename, finance_imports.account_id, "
            "finance_imports.parsed_count, finance_imports.earliest_date, "
            "finance_imports.latest_date, finance_imports.status, "
            "finance_imports.uploaded_at, finance_accounts.name AS account_name, "
            "finance_accounts.ownership, finance_accounts.account_type, "
            "finance_accounts.active "
            "FROM finance_imports "
            "JOIN finance_accounts ON finance_accounts.id = finance_imports.account_id"
        ).fetchall()
    return {row["stored_filename"]: dict(row) for row in rows}


def _finance_account_list() -> list[dict]:
    """Return registered accounts plus discovered CSVs that have not yet been linked."""
    import_map = _finance_import_map()
    source_filenames_by_account: dict[int, list[str]] = {}
    for import_metadata in import_map.values():
        source_filenames_by_account.setdefault(import_metadata['account_id'], []).append(
            import_metadata['stored_filename']
        )

    accounts = [
        {
            **account,
            'source_filenames': sorted(
                source_filenames_by_account.get(account['id'], []), key=str.lower
            ),
            'legacy': False,
        }
        for account in _registered_finance_accounts()
    ]
    legacy_files_by_name: dict[str, list[str]] = {}
    seen_files = set()
    for folder in _finance_csv_search_dirs():
        for csv_path in sorted(folder.glob('*.csv')):
            if csv_path.name in seen_files:
                continue
            seen_files.add(csv_path.name)
            if csv_path.name in import_map:
                continue
            legacy_files_by_name.setdefault(csv_path.stem, []).append(csv_path.name)

    accounts.extend(
        {
            'id': None,
            'name': name,
            **_legacy_account_defaults(name),
            'source_filenames': sorted(source_filenames, key=str.lower),
            'legacy': True,
        }
        for name, source_filenames in sorted(
            legacy_files_by_name.items(), key=lambda item: item[0].lower()
        )
    )
    return accounts


def _validate_finance_account_values(data: dict) -> tuple[dict | None, tuple | None]:
    """Normalise and validate values used by account create and edit routes."""
    name_value = data.get('name', '')
    name = name_value.strip() if isinstance(name_value, str) else ''
    ownership_value = data.get('ownership', '')
    ownership = (
        ownership_value.strip().lower()
        if isinstance(ownership_value, str)
        else ''
    )
    account_type_value = data.get('account_type', '')
    account_type = (
        account_type_value.strip().lower()
        if isinstance(account_type_value, str)
        else ''
    )
    if not name:
        return None, (jsonify({'error': 'name is required'}), 400)
    if ownership not in FINANCE_ACCOUNT_OWNERSHIPS:
        return None, (jsonify({'error': 'ownership must be personal or business'}), 400)
    if account_type not in FINANCE_ACCOUNT_TYPES:
        return None, (jsonify({'error': 'account_type must be cash, credit or loan'}), 400)
    return {
        'name': name,
        'ownership': ownership,
        'account_type': account_type,
    }, None


@contextmanager
def _finance_upload_lock(save_dir: Path, stored_filename: str):
    """Serialise final-file and import-metadata updates for one stored filename."""
    lock_dir = save_dir / '.upload-locks'
    lock_dir.mkdir(exist_ok=True)
    lock_name = hashlib.sha256(stored_filename.encode('utf-8')).hexdigest()
    with (lock_dir / f'{lock_name}.lock').open('a+b') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# Merchant rules are user corrections and always beat the keyword table.
# Loaded once per process and after every rule change, not per transaction.
_MERCHANT_RULES_CACHE = None


def _invalidate_merchant_rules():
    global _MERCHANT_RULES_CACHE
    _MERCHANT_RULES_CACHE = None


def _merchant_rules():
    global _MERCHANT_RULES_CACHE
    if _MERCHANT_RULES_CACHE is None:
        with get_db() as db:
            rows = db.execute(
                'SELECT pattern, category FROM merchant_rules'
            ).fetchall()
        # Longest pattern first so a specific rule beats a broad one.
        _MERCHANT_RULES_CACHE = sorted(
            ((row['pattern'].lower(), row['category']) for row in rows),
            key=lambda rule: len(rule[0]),
            reverse=True,
        )
    return _MERCHANT_RULES_CACHE


def _category_vocabulary():
    return [cat for cat, _ in CATEGORY_RULES]


def _categorise(description: str) -> str:
    normalised = _normalise_description(description)
    for pattern, category in _merchant_rules():
        if pattern in normalised:
            return category
    desc_l = description.lower()
    for cat, keywords in CATEGORY_RULES:
        if any(k in desc_l for k in keywords):
            return cat
    return 'Uncategorised'


@app.route('/api/finance/uncategorised')
@login_required
def api_finance_uncategorised():
    """Uncategorised spending grouped by merchant, most frequent first."""
    merchants = {}
    for t in _parse_csv_files():
        description = t.get('description', '')
        if _categorise(description) != 'Uncategorised':
            continue
        pattern = _normalise_description(description)[:40]
        if len(pattern) < 3:
            continue
        group = merchants.setdefault(pattern, {
            'pattern': pattern,
            'count': 0,
            'total': 0.0,
            'latest_date': '',
            'sample': description,
        })
        group['count'] += 1
        group['total'] = round(group['total'] + abs(float(t.get('amount', 0) or 0)), 2)
        if t.get('date', '') > group['latest_date']:
            group['latest_date'] = t['date']
            group['sample'] = description
    ordered = sorted(
        merchants.values(), key=lambda g: (-g['count'], -g['total'])
    )
    return jsonify({'merchants': ordered, 'categories': _category_vocabulary()})


@app.route('/api/finance/merchant-rules')
@login_required
def api_merchant_rules_list():
    with get_db() as db:
        rows = db.execute(
            'SELECT id, pattern, category, created_at FROM merchant_rules '
            'ORDER BY pattern'
        ).fetchall()
    return jsonify({'rules': [dict(row) for row in rows]})


@app.route('/api/finance/merchant-rules', methods=['POST'])
@login_required
def api_merchant_rules_save():
    data = request.get_json(force=True)
    pattern = _normalise_description(data.get('pattern', ''))
    category = str(data.get('category', '')).strip()
    if len(pattern) < 3:
        return jsonify({'error': 'pattern must be at least 3 characters'}), 400
    if category not in _category_vocabulary():
        return jsonify({'error': 'category is not recognised'}), 400
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute(
            'INSERT INTO merchant_rules (pattern, category, created_at) VALUES (?, ?, ?) '
            'ON CONFLICT(pattern) DO UPDATE SET category=excluded.category',
            (pattern, category, now),
        )
    _invalidate_merchant_rules()
    return jsonify({'ok': True, 'pattern': pattern, 'category': category})


@app.route('/api/finance/merchant-rules/<int:rid>', methods=['DELETE'])
@login_required
def api_merchant_rules_delete(rid):
    with get_db() as db:
        deleted = db.execute('DELETE FROM merchant_rules WHERE id=?', (rid,)).rowcount
    if not deleted:
        return jsonify({'error': 'rule not found'}), 404
    _invalidate_merchant_rules()
    return jsonify({'ok': True})


@app.route('/api/finance/accounts')
@login_required
def api_finance_accounts():
    return jsonify({'accounts': _finance_account_list()})


@app.route('/api/finance/accounts', methods=['POST'])
@login_required
def api_create_finance_account():
    values, error = _validate_finance_account_values(request.get_json(silent=True) or {})
    if error:
        return error

    now = datetime.now().isoformat()[:19]
    try:
        with get_db() as db:
            cursor = db.execute(
                "INSERT INTO finance_accounts "
                "(name, ownership, account_type, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (values['name'], values['ownership'], values['account_type'], 1, now, now),
            )
            account_id = cursor.lastrowid
            row = db.execute(
                "SELECT id, name, ownership, account_type, active, created_at, updated_at "
                "FROM finance_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'account name already exists'}), 409
    return jsonify({'account': dict(row)}), 201


@app.route('/api/finance/accounts/<int:account_id>', methods=['PUT'])
@login_required
def api_update_finance_account(account_id: int):
    values, error = _validate_finance_account_values(request.get_json(silent=True) or {})
    if error:
        return error

    now = datetime.now().isoformat()[:19]
    try:
        with get_db() as db:
            exists = db.execute(
                "SELECT 1 FROM finance_accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if exists is None:
                return jsonify({'error': 'account not found'}), 404
            db.execute(
                "UPDATE finance_accounts "
                "SET name = ?, ownership = ?, account_type = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    values['name'], values['ownership'], values['account_type'], now,
                    account_id,
                ),
            )
            row = db.execute(
                "SELECT id, name, ownership, account_type, active, created_at, updated_at "
                "FROM finance_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'account name already exists'}), 409
    return jsonify({'account': dict(row)})


@app.route('/api/finance/accounts/link-legacy', methods=['POST'])
@login_required
def api_link_legacy_finance_account():
    data = request.get_json(silent=True) or {}
    stored_filename_value = data.get('stored_filename', '')
    stored_filename = (
        stored_filename_value.strip()
        if isinstance(stored_filename_value, str)
        else ''
    )
    account_id = data.get('account_id')
    if not isinstance(account_id, int) or isinstance(account_id, bool):
        return jsonify({'error': 'account_id must be an integer'}), 400

    with get_db() as db:
        account_row = db.execute(
            "SELECT id, name, ownership, account_type, active, created_at, updated_at "
            "FROM finance_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    if account_row is None:
        return jsonify({'error': 'account_id not found'}), 404

    source_path = _find_finance_csv(stored_filename)
    if source_path is None:
        return jsonify({'error': 'stored_filename not found'}), 404

    from finance_imports import parse_csv_file
    account = dict(account_row)
    transactions = parse_csv_file(source_path, account)
    if not transactions:
        return jsonify({'error': 'No supported transactions found in this CSV'}), 422

    earliest_date = min(transaction['date'] for transaction in transactions)
    latest_date = max(transaction['date'] for transaction in transactions)
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute(
            "INSERT INTO finance_imports "
            "(original_filename, stored_filename, account_id, parsed_count, "
            "earliest_date, latest_date, status, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(stored_filename) DO UPDATE SET "
            "original_filename = excluded.original_filename, "
            "account_id = excluded.account_id, "
            "parsed_count = excluded.parsed_count, "
            "earliest_date = excluded.earliest_date, "
            "latest_date = excluded.latest_date, "
            "status = excluded.status, "
            "uploaded_at = excluded.uploaded_at",
            (
                source_path.name, stored_filename, account_id, len(transactions), earliest_date,
                latest_date, 'parsed', now,
            ),
        )
    return jsonify({
        'account': account,
        'stored_filename': stored_filename,
        'parsed_count': len(transactions),
        'earliest_date': earliest_date,
        'latest_date': latest_date,
        'status': 'parsed',
    })


@app.route('/api/finance/upload-csv', methods=['POST'])
@login_required
def api_finance_upload_csv():
    """Validate, parse and register a bank CSV before preserving it."""
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'no file'}), 400
    safe_name = re.sub(r'[^\w\s\-.]', '', f.filename).strip()
    if not safe_name.lower().endswith('.csv'):
        return jsonify({'error': 'CSV files only'}), 400
    safe_name = f'{safe_name[:-4]}.csv'

    confirm_reassign = request.form.get('confirm_reassign', '').strip().lower() == 'true'
    requested_account_id = request.form.get('account_id', '').strip()
    account = None
    if requested_account_id:
        try:
            account_id = int(requested_account_id)
        except ValueError:
            return jsonify({'error': 'account_id must be an integer'}), 400
        with get_db() as db:
            row = db.execute(
                "SELECT id, name, ownership, account_type, active, created_at, updated_at "
                "FROM finance_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return jsonify({'error': 'account_id not found'}), 400
        account = dict(row)
        metadata = account
    else:
        account_name = request.form.get('account_name', '').strip()
        ownership = request.form.get('ownership', '').strip().lower()
        account_type = request.form.get('account_type', '').strip().lower()
        if ownership not in FINANCE_ACCOUNT_OWNERSHIPS:
            return jsonify({'error': 'ownership must be personal or business'}), 400
        if account_type not in FINANCE_ACCOUNT_TYPES:
            return jsonify({'error': 'account_type must be cash, credit or loan'}), 400
        if not account_name:
            return jsonify({'error': 'account_name is required'}), 400
        metadata = {
            'id': None,
            'name': account_name,
            'ownership': ownership,
            'account_type': account_type,
        }

    save_dir = DATA_DIR / 'bank_statements'
    save_dir.mkdir(exist_ok=True)
    temporary_path = None
    backup_path = None
    replaced_destination = False
    destination = save_dir / safe_name
    try:
        with tempfile.NamedTemporaryFile(
            dir=save_dir,
            prefix='upload-',
            suffix='.csv',
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        f.save(str(temporary_path))

        from finance_imports import parse_csv_file
        transactions = parse_csv_file(temporary_path, metadata)
        if not transactions:
            temporary_path.unlink(missing_ok=True)
            return jsonify({'error': 'No supported transactions found in this CSV'}), 422

        upload_lock = _finance_upload_lock(save_dir, safe_name)
        upload_lock.__enter__()
        try:
            now = datetime.now().isoformat()[:19]
            if destination.exists():
                backup_fd, backup_name = tempfile.mkstemp(
                    dir=save_dir,
                    prefix='backup-',
                    suffix='.csv',
                )
                os.close(backup_fd)
                backup_path = Path(backup_name)
                backup_path.unlink()
                try:
                    os.link(destination, backup_path)
                except OSError:
                    shutil.copy2(destination, backup_path)

            with get_db() as db:
                held = db.execute(
                    "SELECT a.id, a.name FROM finance_imports i "
                    "JOIN finance_accounts a ON a.id = i.account_id "
                    "WHERE i.stored_filename = ?",
                    (safe_name,),
                ).fetchone()
                if held is not None and not confirm_reassign:
                    if account is not None:
                        target_id = account['id']
                    else:
                        named = db.execute(
                            "SELECT id FROM finance_accounts WHERE name = ?",
                            (metadata['name'],),
                        ).fetchone()
                        target_id = named['id'] if named is not None else None
                    if target_id != held['id']:
                        return jsonify({
                            'error': (
                                f"{safe_name} already belongs to {held['name']}. "
                                "Re-upload it to that account, or rename the file "
                                "before uploading it here."
                            ),
                            'conflict': 'account_reassignment',
                            'current_account': held['name'],
                        }), 409

                if account is None:
                    row = db.execute(
                        "SELECT id, name, ownership, account_type, active, created_at, updated_at "
                        "FROM finance_accounts WHERE name = ?",
                        (metadata['name'],),
                    ).fetchone()
                    if row is None:
                        cursor = db.execute(
                            "INSERT INTO finance_accounts "
                            "(name, ownership, account_type, active, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                metadata['name'], metadata['ownership'], metadata['account_type'],
                                1, now, now,
                            ),
                        )
                        account = {
                            'id': cursor.lastrowid,
                            'name': metadata['name'],
                            'ownership': metadata['ownership'],
                            'account_type': metadata['account_type'],
                            'active': 1,
                            'created_at': now,
                            'updated_at': now,
                        }
                    else:
                        account = dict(row)

                earliest_date = min(transaction['date'] for transaction in transactions)
                latest_date = max(transaction['date'] for transaction in transactions)
                os.replace(temporary_path, destination)
                temporary_path = None
                replaced_destination = True
                db.execute(
                    "INSERT INTO finance_imports "
                    "(original_filename, stored_filename, account_id, parsed_count, "
                    "earliest_date, latest_date, status, uploaded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(stored_filename) DO UPDATE SET "
                    "original_filename = excluded.original_filename, "
                    "account_id = excluded.account_id, "
                    "parsed_count = excluded.parsed_count, "
                    "earliest_date = excluded.earliest_date, "
                    "latest_date = excluded.latest_date, "
                    "status = excluded.status, "
                    "uploaded_at = excluded.uploaded_at",
                    (
                        f.filename, safe_name, account['id'], len(transactions), earliest_date,
                        latest_date, 'parsed', now,
                    ),
                )
        except Exception:
            if replaced_destination:
                if backup_path is None:
                    destination.unlink(missing_ok=True)
                else:
                    os.replace(backup_path, destination)
                    backup_path = None
            raise
        finally:
            upload_lock.__exit__(None, None, None)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)

    return jsonify({
        'account': account,
        'saved': safe_name,
        'parsed_count': len(transactions),
        'earliest_date': earliest_date,
        'latest_date': latest_date,
        'message': f"{len(transactions)} transactions loaded from {account['name']}",
    })

@app.route('/api/finance/summary')
@login_required
def api_finance_summary():
    from collections import defaultdict
    transactions = _parse_csv_files()
    accounts = defaultdict(lambda: {
        'name': '',
        'account_key': '',
        'count': 0,
        'balance': None,
        'last_date': None,
        'is_credit': False,
        'ownership': 'personal',
        'account_type': 'cash',
    })
    for t in transactions:
        account_key = t.get('account_key') or f"legacy:{str(t.get('account', '')).strip().lower()}"
        account = accounts[account_key]
        account['name'] = t.get('account', '')
        account['account_key'] = account_key
        account['count'] += 1
        account_type = t.get('account_type', 'cash')
        account['is_credit'] = account_type == 'credit'
        account['ownership'] = t.get('ownership', 'personal')
        account['account_type'] = account_type
        if account['balance'] is None and t['balance']:
            # Credit card: positive balance = debt, show as negative
            account['balance'] = -abs(t['balance']) if account['is_credit'] else t['balance']
        if account['last_date'] is None:
            account['last_date'] = t['date']

    # Category spending (last 90 days, expenses only) — split business vs personal
    from datetime import timedelta
    SKIP_CATS = {'Transfers'}
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    cat_spend_business = defaultdict(float)
    cat_spend_personal = defaultdict(float)
    monthly_in = defaultdict(float)
    monthly_out = defaultdict(float)
    for t in transactions:
        if t.get('account_type', 'cash') == 'loan' or t['date'] < cutoff:
            continue
        cat = _categorise(t['description'])
        month = t['date'][:7]
        is_biz = t.get('ownership', 'personal') == 'business'
        if t['amount'] < 0 and cat not in SKIP_CATS:
            if is_biz:
                cat_spend_business[cat] += abs(t['amount'])
            else:
                cat_spend_personal[cat] += abs(t['amount'])
            monthly_out[month] += abs(t['amount'])
        elif t['amount'] > 0:
            monthly_in[month] += t['amount']

    # Annotate recent with category
    recent = []
    for t in transactions[:50]:
        recent.append({**t, 'category': _categorise(t['description'])})

    return jsonify({
        'accounts': list(accounts.values()),
        'total_transactions': len(transactions),
        'recent': recent,
        'category_spend_business': dict(sorted(cat_spend_business.items(), key=lambda x: -x[1])),
        'category_spend_personal': dict(sorted(cat_spend_personal.items(), key=lambda x: -x[1])),
        'monthly_income': dict(sorted(monthly_in.items())),
        'monthly_expenses': dict(sorted(monthly_out.items())),
    })


@app.route('/api/finance/savings-tips', methods=['POST'])
@login_required
def api_finance_savings_tips():
    """AI-generated cost-saving suggestions based on transaction data."""
    transactions = _parse_csv_files()
    context = _finance_context_summary(transactions, max_txns=100)

    prompt = f"""You are a personal finance advisor for the Whitewood family. Analyse their bank transactions and provide specific, actionable cost-saving suggestions.

Focus on:
1. Subscriptions that could be cancelled or reduced
2. Recurring charges that could be negotiated or DIY'd
3. Spending categories with high amounts that could be reduced
4. Business expenses that could be optimised
5. Any duplicate or redundant services

Format your response as a JSON array of suggestions, each with:
- "item": the specific charge or category
- "saving": estimated monthly saving in AUD
- "action": what to do
- "type": "cancel" | "reduce" | "diy" | "negotiate" | "switch"

Transaction data:
{context}

Return ONLY valid JSON array, nothing else. Example:
[{{"item": "Netflix", "saving": 22, "action": "Cancel or share plan", "type": "cancel"}}]"""

    messages = [{'role': 'user', 'content': prompt}]
    reply = None

    if _anthropic_key():
        try:
            body = json.dumps({
                'model': 'claude-sonnet-4-6',
                'max_tokens': 1500,
                'messages': messages,
            }).encode()
            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=body,
                headers={
                    'x-api-key': _anthropic_key(),
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            reply = resp['content'][0]['text']
        except Exception:
            pass

    if not reply and _openrouter_key():
        for model in ['deepseek/deepseek-r1:free', 'meta-llama/llama-3.3-70b-instruct:free']:
            try:
                body = json.dumps({
                    'model': model,
                    'messages': messages,
                    'max_tokens': 1500,
                }).encode()
                req = urllib.request.Request(
                    'https://openrouter.ai/api/v1/chat/completions',
                    data=body,
                    headers={
                        'Authorization': f'Bearer {_openrouter_key()}',
                        'Content-Type': 'application/json',
                        'HTTP-Referer': 'https://family.edencommercial.au',
                    },
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=45) as r:
                    resp = json.loads(r.read())
                reply = resp['choices'][0]['message']['content']
                break
            except Exception:
                continue

    if not reply:
        return jsonify({'error': 'No AI available'}), 503

    # Extract JSON from response
    try:
        start = reply.find('[')
        end   = reply.rfind(']') + 1
        tips  = json.loads(reply[start:end])
    except Exception:
        tips = []

    return jsonify({'tips': tips})


@app.route('/api/finance/chat', methods=['POST'])
@login_required
def api_finance_chat():
    d = request.get_json(force=True)
    message = (d.get('message') or '').strip()
    history = d.get('history') or []
    if not message:
        return jsonify({'error': 'empty message'}), 400

    transactions = _parse_csv_files()
    context = _finance_context_summary(transactions)

    system_prompt = f"""You are a personal finance assistant for the Whitewood family. You have access to their real bank transaction data below.

Be concise, practical, and warm. When asked about spending, reference actual transactions. Format dollar amounts clearly. If asked about something not in the data, say so.

{context}

Today's date: {date.today().isoformat()}"""

    messages = [{'role': 'system', 'content': system_prompt}]
    for h in history[-10:]:  # last 10 turns for context
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({'role': h['role'], 'content': h['content']})
    messages.append({'role': 'user', 'content': message})

    # Save to DB
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute(
            'INSERT INTO finance_chat (role, content, created_at) VALUES (?,?,?)',
            ('user', message, now)
        )

    # Try free OpenRouter models in order
    free_models = [
        'deepseek/deepseek-r1:free',
        'meta-llama/llama-3.3-70b-instruct:free',
        'google/gemma-3-27b-it:free',
        'mistralai/mistral-7b-instruct:free',
    ]
    reply = None

    if _anthropic_key():
        # Try Anthropic first (haiku — cheapest)
        try:
            body = json.dumps({
                'model': 'claude-sonnet-4-6',
                'max_tokens': 1024,
                'system': system_prompt,
                'messages': [m for m in messages if m['role'] != 'system'],
            }).encode()
            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=body,
                headers={
                    'x-api-key': _anthropic_key(),
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            reply = resp['content'][0]['text']
        except Exception:
            pass

    if not reply and _openrouter_key():
        for model in free_models:
            try:
                body = json.dumps({
                    'model': model,
                    'messages': messages,
                    'max_tokens': 1024,
                }).encode()
                req = urllib.request.Request(
                    'https://openrouter.ai/api/v1/chat/completions',
                    data=body,
                    headers={
                        'Authorization': f'Bearer {_openrouter_key()}',
                        'Content-Type': 'application/json',
                        'HTTP-Referer': 'https://family.edencommercial.au',
                    },
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    resp = json.loads(r.read())
                reply = resp['choices'][0]['message']['content']
                break
            except Exception:
                continue

    if not reply:
        return jsonify({'error': 'No AI model available. Add OPENROUTER_API_KEY or ANTHROPIC_API_KEY in Coolify.'}), 503

    with get_db() as db:
        db.execute(
            'INSERT INTO finance_chat (role, content, created_at) VALUES (?,?,?)',
            ('assistant', reply, datetime.now().isoformat()[:19])
        )

    return jsonify({'reply': reply})


@app.route('/api/finance/chat-history')
@login_required
def api_finance_chat_history():
    with get_db() as db:
        rows = db.execute(
            'SELECT role, content, created_at FROM finance_chat ORDER BY id DESC LIMIT 50'
        ).fetchall()
    return jsonify([dict(r) for r in reversed(rows)])


# ── Budget ───────────────────────────────────────────────────────────────────

def _bdgt_detect_recurring(transactions):
    """Detect recurring expenses: transactions with similar descriptions appearing in 2+ months."""
    from collections import defaultdict
    desc_months = defaultdict(lambda: {'months': set(), 'amounts': []})
    for t in transactions:
        if t['amount'] >= 0:
            continue
        # Normalise description: strip digits/spaces, lowercase, first 40 chars
        norm = re.sub(r'[0-9]+', '', t['description']).strip().lower()[:40]
        if len(norm) < 4:
            continue
        month = t['date'][:7]
        desc_months[norm]['months'].add(month)
        desc_months[norm]['amounts'].append(abs(t['amount']))
    recurring = []
    for desc, data in desc_months.items():
        if len(data['months']) >= 2:
            avg_amt = round(sum(data['amounts']) / len(data['amounts']), 2)
            recurring.append({
                'description': desc,
                'avg_amount': avg_amt,
                'frequency': len(data['months']),
            })
    recurring.sort(key=lambda x: -x['avg_amount'])
    return recurring[:20]


def _get_budget_safety_buffer():
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM budget_settings WHERE key='personal_safety_buffer'"
        ).fetchone()
    if row:
        try:
            return float(row['value'])
        except (TypeError, ValueError):
            pass
    return float(os.environ.get('FAMILY_HQ_SAFETY_BUFFER', '1000'))


def _forecast_month_starts(start_date):
    """First day of each calendar month the forecast covers."""
    starts = []
    for offset in range(FORECAST_MONTHS):
        month_index = start_date.year * 12 + start_date.month - 1 + offset
        year, zero_based_month = divmod(month_index, 12)
        starts.append(date(year, zero_based_month + 1, 1))
    return starts


BUDGET_FREQUENCIES = {
    'weekly': 52 / 12,
    'fortnightly': 26 / 12,
    'monthly': 1.0,
    'quarterly': 1 / 3,
    'biannual': 1 / 6,
    'annual': 1 / 12,
}


def _monthly_equivalent(amount, frequency):
    """Sinking-fund monthly set-aside for an amount entered at `frequency`."""
    factor = BUDGET_FREQUENCIES.get(frequency or 'monthly', 1.0)
    return round(float(amount) * factor, 2)


def _budget_target_events(scheduled_events, start_date):
    """Turn budget targets into expected monthly cash events.

    Amounts are normalised to their monthly equivalent (a $3,500 annual bill
    drips as ~$291/month set aside, never a due-date spike). A target is
    skipped when its category is already represented by a confirmed upcoming
    expense or a payment inferred from transaction history in the same
    direction, so real spending is never counted twice against its own budget.
    """
    covered = set()
    for event in scheduled_events:
        category = event.get('category') or _categorise(event.get('description', ''))
        covered.add((
            str(category).strip().lower(),
            event.get('ownership', 'personal'),
            event.get('direction', 'outflow'),
        ))

    with get_db() as db:
        targets = db.execute(
            'SELECT id, category, monthly_target, type, frequency, direction '
            'FROM budget_targets'
        ).fetchall()
        overrides = {
            (row['target_id'], row['year_month']): row
            for row in db.execute(
                'SELECT target_id, year_month, amount, skipped FROM budget_target_overrides'
            ).fetchall()
        }

    events = []
    for target in targets:
        ownership = target['type'] or 'personal'
        category = str(target['category'] or '').strip()
        direction = target['direction'] or 'outflow'
        if (category.lower(), ownership, direction) in covered:
            continue
        monthly_amount = _monthly_equivalent(
            target['monthly_target'], target['frequency']
        )
        for month_start in _forecast_month_starts(start_date):
            override = overrides.get((target['id'], month_start.strftime('%Y-%m')))
            if override is not None and override['skipped']:
                continue
            amount = float(
                monthly_amount if override is None or override['amount'] is None
                else override['amount']
            )
            if amount <= 0:
                continue
            events.append({
                'description': f'{category} (budgeted)',
                'amount': amount,
                'due_date': max(month_start, start_date).isoformat(),
                'recurring': '',
                'category': category,
                'ownership': ownership,
                'direction': direction,
                'source': 'budget_target',
                'confidence': 'budgeted',
            })
    return events


def _budget_cash_flow(
    transactions,
    upcoming,
    forecast_date=None,
    safety_buffer=None,
):
    """Build the deterministic six-month personal and business forecast."""
    from zoneinfo import ZoneInfo

    start_date = forecast_date or datetime.now(
        ZoneInfo('Australia/Brisbane')
    ).date()
    forecast_transactions = [
        {
            **row,
            'account': row.get('account_key')
            or f"legacy:{str(row.get('account', '')).strip().lower()}",
        }
        for row in transactions
    ]
    cash_transactions = [
        row
        for row in forecast_transactions
        if row.get('account_type', 'cash') == 'cash'
    ]
    recurrence_transactions = [
        row for row in forecast_transactions
        if row.get('account_type', 'cash') in {'cash', 'credit'}
        and _categorise(row.get('description', '')) != 'Transfers'
    ]
    ownership = {
        row['account']: row.get('ownership', 'personal')
        for row in forecast_transactions
    }
    scheduled_events = []
    for row in upcoming:
        event = dict(row)
        raw_recurring = event.get('recurring', 0)
        recurrence = event.get('recurrence') or (
            'annual' if raw_recurring else ''
        )
        scheduled_events.append({
            'description': event.get('description', 'Upcoming expense'),
            'amount': event.get('amount', 0),
            'due_date': event.get('due_date', ''),
            'recurring': recurrence,
            'category': event.get('category', ''),
            'ownership': event.get('ownership') or (
                'business'
                if str(event.get('category', '')).lower() == 'business'
                else 'personal'
            ),
            'direction': event.get('direction') or 'outflow',
            'source': 'upcoming_expense',
            'confidence': 'confirmed',
        })
    recurring_history = infer_recurring_events(
        recurrence_transactions,
        ownership,
        start_date,
    )
    manual_descriptions = {
        re.sub(r'\s+', ' ', event['description'].strip().lower())
        for event in scheduled_events
    }
    scheduled_events.extend(
        event
        for event in recurring_history
        if re.sub(r'\s+', ' ', event['description'].strip().lower())
        not in manual_descriptions
    )
    scheduled_events.extend(
        _budget_target_events(scheduled_events, start_date)
    )
    if safety_buffer is None:
        safety_buffer = _get_budget_safety_buffer()
    return build_forecast(
        cash_transactions,
        scheduled_events,
        ownership,
        start_date,
        safety_buffer,
    )


@app.route('/api/budget/summary')
@login_required
def api_budget_summary():
    """Main budget endpoint — returns everything the Budget page needs."""
    from collections import defaultdict
    transactions = _parse_csv_files()
    today = date.today()
    current_month = today.strftime('%Y-%m')

    # ── Current month actual spend per category ──
    cat_actuals = defaultdict(float)  # category -> total spend (positive number)
    SKIP_CATS = {'Transfers'}

    for t in transactions:
        if t['date'][:7] != current_month:
            continue
        cat = _categorise(t['description'])
        is_biz = _is_business_account(t['account'])
        if t['amount'] < 0 and cat not in SKIP_CATS:
            cat_actuals[(cat, 'business' if is_biz else 'personal')] += abs(t['amount'])

    # ── Budget targets from SQLite ──
    with get_db() as db:
        targets = db.execute('SELECT * FROM budget_targets ORDER BY monthly_target DESC').fetchall()
        goals = db.execute("SELECT * FROM savings_goals WHERE status='active' ORDER BY priority").fetchall()
        upcoming = db.execute("SELECT * FROM upcoming_expenses WHERE status='pending' ORDER BY due_date").fetchall()

    # Steady-state monthly figures the family runs on, from targets alone.
    headline = {
        'business_income': 0.0,
        'business_expenses': 0.0,
        'personal_income': 0.0,
        'personal_expenses': 0.0,
    }
    budget_vs_actuals = []
    for tgt in targets:
        cat = tgt['category']
        btype = tgt['type'] or 'personal'
        frequency = tgt['frequency'] or 'monthly'
        direction = tgt['direction'] or 'outflow'
        monthly_eq = _monthly_equivalent(tgt['monthly_target'], frequency)
        headline_key = f"{btype}_{'income' if direction == 'inflow' else 'expenses'}"
        headline[headline_key] = round(headline[headline_key] + monthly_eq, 2)
        actual = round(cat_actuals.get((cat, btype), 0), 2)
        remaining = round(monthly_eq - actual, 2)
        pct = round((actual / monthly_eq * 100), 1) if monthly_eq > 0 else 0
        budget_vs_actuals.append({
            'id': tgt['id'],
            'category': cat,
            'type': btype,
            'target': tgt['monthly_target'],
            'frequency': frequency,
            'direction': direction,
            'monthly_equivalent': monthly_eq,
            'actual': actual,
            'remaining': remaining,
            'percent_used': pct,
        })

    # ── 3-month forecast — ING personal account only ──
    monthly_in = defaultdict(float)
    monthly_out = defaultdict(float)
    for t in transactions:
        if _is_business_account(t['account']):
            continue  # personal forecast only — exclude CBA/business accounts
        month = t['date'][:7]
        if t['amount'] > 0:
            monthly_in[month] += t['amount']  # includes transfers in from business (salary)
        elif t['amount'] < 0 and _categorise(t['description']) not in SKIP_CATS:
            monthly_out[month] += abs(t['amount'])

    # Get last 3 complete months (not current month)
    past_months = sorted([m for m in set(list(monthly_in.keys()) + list(monthly_out.keys())) if m < current_month], reverse=True)[:3]
    avg_in = round(sum(monthly_in[m] for m in past_months) / max(len(past_months), 1), 2)
    avg_out = round(sum(monthly_out[m] for m in past_months) / max(len(past_months), 1), 2)

    # Sum upcoming expenses per future month
    upcoming_by_month = defaultdict(float)
    for ue in upcoming:
        m = ue['due_date'][:7] if ue['due_date'] else ''
        if m:
            upcoming_by_month[m] += ue['amount']

    forecast = []
    for i in range(1, 4):
        fm = (today.replace(day=1) + timedelta(days=32 * i)).strftime('%Y-%m')
        proj_out = round(avg_out + upcoming_by_month.get(fm, 0), 2)
        net = round(avg_in - proj_out, 2)
        forecast.append({
            'month': fm,
            'income': avg_in,
            'expenses': proj_out,
            'net': net,
            'flagged': net < 0,
        })

    # ── Recurring detection ──
    recurring = _bdgt_detect_recurring(transactions)
    cash_flow = _budget_cash_flow(transactions, upcoming)

    return jsonify({
        'current_month': current_month,
        'headline': headline,
        'categories': _category_vocabulary(),
        'budget_vs_actuals': budget_vs_actuals,
        'savings_goals': [dict(g) for g in goals],
        'upcoming_expenses': [dict(u) for u in upcoming],
        'recurring_detected': recurring,
        'forecast': forecast,
        'cash_flow': cash_flow,
        'budget_settings': {
            'safety_buffer': cash_flow['safety_buffer'],
        },
    })


@app.route('/api/budget/forecast')
@login_required
def api_budget_forecast():
    transactions = _parse_csv_files()
    with get_db() as db:
        upcoming = db.execute(
            "SELECT * FROM upcoming_expenses WHERE status='pending' ORDER BY due_date"
        ).fetchall()
    requested_start = request.args.get('start', '').strip()
    forecast_date = None
    if requested_start:
        try:
            forecast_date = date.fromisoformat(requested_start)
        except ValueError:
            return jsonify({'error': 'start must be an ISO date'}), 400
    return jsonify(_budget_cash_flow(transactions, upcoming, forecast_date))


@app.route('/api/budget/settings', methods=['POST'])
@login_required
def api_budget_settings():
    data = request.get_json(force=True)
    try:
        safety_buffer = float(data.get('safety_buffer'))
    except (TypeError, ValueError):
        return jsonify({'error': 'safety_buffer must be a non-negative number'}), 400
    if not math.isfinite(safety_buffer) or safety_buffer < 0:
        return jsonify({'error': 'safety_buffer must be a non-negative number'}), 400
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute(
            '''INSERT INTO budget_settings (key, value, updated_at)
               VALUES ('personal_safety_buffer', ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
               updated_at=excluded.updated_at''',
            (str(round(safety_buffer, 2)), now),
        )
    return jsonify({'ok': True, 'safety_buffer': round(safety_buffer, 2)})


@app.route('/api/budget/targets', methods=['POST'])
@login_required
def api_budget_targets_save():
    """Create or update a budget target."""
    data = request.get_json(force=True)
    cat = data.get('category', '').strip()
    target = data.get('monthly_target', 0)
    btype = data.get('type', 'personal')
    frequency = (data.get('frequency') or 'monthly').strip().lower()
    direction = (data.get('direction') or 'outflow').strip().lower()
    tid = data.get('id')
    now = datetime.now().isoformat()[:19]
    if not cat or not target:
        return jsonify({'error': 'category and monthly_target required'}), 400
    if frequency not in BUDGET_FREQUENCIES:
        return jsonify({'error': 'frequency must be one of: ' + ', '.join(BUDGET_FREQUENCIES)}), 400
    if direction not in {'inflow', 'outflow'}:
        return jsonify({'error': 'direction must be inflow or outflow'}), 400
    with get_db() as db:
        if tid:
            db.execute('UPDATE budget_targets SET category=?, monthly_target=?, type=?, frequency=?, direction=?, updated_at=? WHERE id=?',
                       (cat, target, btype, frequency, direction, now, tid))
        else:
            db.execute('INSERT INTO budget_targets (category, monthly_target, type, frequency, direction, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
                       (cat, target, btype, frequency, direction, now, now))
    return jsonify({'ok': True})


@app.route('/api/budget/targets/<int:tid>/months')
@login_required
def api_budget_target_months(tid):
    """Per-month skips and amount overrides for one budget target."""
    with get_db() as db:
        if db.execute('SELECT id FROM budget_targets WHERE id=?', (tid,)).fetchone() is None:
            return jsonify({'error': 'target not found'}), 404
        rows = db.execute(
            'SELECT year_month, amount, skipped FROM budget_target_overrides '
            'WHERE target_id=? ORDER BY year_month',
            (tid,),
        ).fetchall()
    return jsonify({'months': [
        {
            'year_month': row['year_month'],
            'amount': row['amount'],
            'skipped': bool(row['skipped']),
        }
        for row in rows
    ]})


@app.route('/api/budget/targets/<int:tid>/months', methods=['POST'])
@login_required
def api_budget_target_months_save(tid):
    """Replace the per-month overrides for one budget target."""
    months = (request.get_json(silent=True) or {}).get('months')
    if not isinstance(months, list):
        return jsonify({'error': 'months must be a list'}), 400

    cleaned = []
    for entry in months:
        if not isinstance(entry, dict):
            return jsonify({'error': 'each month must be an object'}), 400
        year_month = str(entry.get('year_month', '')).strip()
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', year_month):
            return jsonify({'error': f'invalid year_month: {year_month}'}), 400
        skipped = bool(entry.get('skipped'))
        amount = entry.get('amount')
        if skipped:
            amount = None
        elif amount is not None:
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                return jsonify({'error': f'invalid amount for {year_month}'}), 400
            if amount < 0:
                return jsonify({'error': f'amount for {year_month} must not be negative'}), 400
        cleaned.append((year_month, amount, 1 if skipped else 0))

    with get_db() as db:
        if db.execute('SELECT id FROM budget_targets WHERE id=?', (tid,)).fetchone() is None:
            return jsonify({'error': 'target not found'}), 404
        db.execute('DELETE FROM budget_target_overrides WHERE target_id=?', (tid,))
        for year_month, amount, skipped in cleaned:
            db.execute(
                'INSERT INTO budget_target_overrides (target_id, year_month, amount, skipped) '
                'VALUES (?,?,?,?)',
                (tid, year_month, amount, skipped),
            )
    return jsonify({'ok': True})


@app.route('/api/budget/targets/<int:tid>', methods=['DELETE'])
@login_required
def api_budget_targets_delete(tid):
    with get_db() as db:
        db.execute('DELETE FROM budget_targets WHERE id=?', (tid,))
    return jsonify({'ok': True})


@app.route('/api/budget/goals', methods=['POST'])
@login_required
def api_budget_goals_save():
    """Create or update a savings goal."""
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    target_amount = data.get('target_amount', 0)
    gid = data.get('id')
    now = datetime.now().isoformat()[:19]
    if not name or not target_amount:
        return jsonify({'error': 'name and target_amount required'}), 400
    with get_db() as db:
        if gid:
            db.execute('UPDATE savings_goals SET name=?, target_amount=?, target_date=?, updated_at=? WHERE id=?',
                       (name, target_amount, data.get('target_date', ''), now, gid))
        else:
            db.execute(
                'INSERT INTO savings_goals (name, target_amount, current_amount, priority, status, target_date, created_at, updated_at) VALUES (?,?,0,1,\'active\',?,?,?)',
                (name, target_amount, data.get('target_date', ''), now, now)
            )
    return jsonify({'ok': True})


@app.route('/api/budget/goals/<int:gid>', methods=['DELETE'])
@login_required
def api_budget_goals_delete(gid):
    with get_db() as db:
        db.execute('DELETE FROM savings_goals WHERE id=?', (gid,))
    return jsonify({'ok': True})


@app.route('/api/budget/goals/<int:gid>/contribute', methods=['POST'])
@login_required
def api_budget_goals_contribute(gid):
    """Add a contribution to a savings goal."""
    data = request.get_json(force=True)
    amount = data.get('amount', 0)
    if not amount or amount <= 0:
        return jsonify({'error': 'positive amount required'}), 400
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute('UPDATE savings_goals SET current_amount = current_amount + ?, updated_at=? WHERE id=?',
                   (amount, now, gid))
    return jsonify({'ok': True})


@app.route('/api/budget/upcoming', methods=['POST'])
@login_required
def api_budget_upcoming_save():
    """Create or update an upcoming expense."""
    data = request.get_json(force=True)
    desc = data.get('description', '').strip()
    amount = data.get('amount', 0)
    due_date = data.get('due_date', '')
    ownership = data.get('ownership', 'personal')
    direction = data.get('direction', 'outflow')
    recurrence = data.get('recurrence', '')
    uid = data.get('id')
    now = datetime.now().isoformat()[:19]
    if not desc or amount in (None, '') or not due_date:
        return jsonify({'error': 'description, amount, and due_date required'}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a non-negative number'}), 400
    if not math.isfinite(amount) or amount < 0:
        return jsonify({'error': 'amount must be a non-negative number'}), 400
    try:
        date.fromisoformat(due_date)
    except (TypeError, ValueError):
        return jsonify({'error': 'due_date must be an ISO date'}), 400
    if ownership not in {'personal', 'business'}:
        return jsonify({'error': 'ownership must be personal or business'}), 400
    if direction not in {'inflow', 'outflow'}:
        return jsonify({'error': 'direction must be inflow or outflow'}), 400
    valid_recurrence = {'', 'weekly', 'fortnightly', 'monthly', 'quarterly', 'biannual', 'annual'}
    if recurrence not in valid_recurrence:
        return jsonify({'error': 'recurrence is not supported'}), 400
    recurring = 1 if recurrence else 0
    with get_db() as db:
        if uid:
            db.execute(
                '''UPDATE upcoming_expenses
                   SET description=?, amount=?, due_date=?, recurring=?,
                       recurrence=?, category=?, ownership=?, direction=?, status=?
                   WHERE id=?''',
                (
                    desc, amount, due_date, recurring, recurrence,
                    data.get('category', ''), ownership, direction, 'pending', uid,
                ),
            )
        else:
            cursor = db.execute(
                '''INSERT INTO upcoming_expenses
                   (description, amount, due_date, recurring, recurrence, category,
                    ownership, direction, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (
                    desc, amount, due_date, recurring, recurrence,
                    data.get('category', ''), ownership, direction, 'pending', now,
                ),
            )
            uid = cursor.lastrowid
    return jsonify({'ok': True, 'id': uid})


@app.route('/api/budget/upcoming/<int:uid>', methods=['DELETE'])
@login_required
def api_budget_upcoming_delete(uid):
    with get_db() as db:
        db.execute('DELETE FROM upcoming_expenses WHERE id=?', (uid,))
    return jsonify({'ok': True})


# ── Paper Trading & Screener ──────────────────────────────────────────────────

VALUE_WATCHLIST = [
    "AAPL","MSFT","V","MA","KO","JNJ","PG","UNH","HD","COST",
    "BRK-B","JPM","BAC","AXP","CVX","OXY","MCO","SPGI","TMO","ISRG",
    "NKE","ADBE","INTU","NVDA","AMZN",
]

def _cgg_score(ticker: str) -> dict:
    """Simplified CGG 4-factor score using yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period='1y', auto_adjust=True)

        score = 0
        details = {}

        # Quality (0-25): net margin + ROE + FCF positive
        margin = (info.get('profitMargins') or 0) * 100
        roe = (info.get('returnOnEquity') or 0) * 100
        fcf = info.get('freeCashflow') or 0
        q = min(10, max(0, int(margin / 3))) + min(10, max(0, int(roe / 5))) + (5 if fcf > 0 else 0)
        score += q; details['quality'] = q

        # Growth (0-25): earnings + revenue growth
        eg = (info.get('earningsGrowth') or info.get('earningsQuarterlyGrowth') or 0) * 100
        rg = (info.get('revenueGrowth') or 0) * 100
        g = min(15, max(0, int(eg / 3))) + min(10, max(0, int(rg / 3)))
        score += g; details['growth'] = g

        # Value (0-25): PEG + FCF yield
        peg = info.get('pegRatio') or 99
        mcap = info.get('marketCap') or 1
        fcf_yield = (fcf / mcap * 100) if mcap > 0 and fcf > 0 else 0
        v = (15 if peg < 1 else 10 if peg < 2 else 5 if peg < 3 else 0) + min(10, max(0, int(fcf_yield * 2)))
        score += v; details['value_score'] = v

        # Momentum (0-25): above 200MA + 12m return
        mom = 0
        if len(hist) >= 200:
            price = hist['Close'].iloc[-1]
            ma200 = hist['Close'].rolling(200).mean().iloc[-1]
            ret12 = (price / hist['Close'].iloc[0] - 1) * 100
            mom = (10 if price > ma200 else 0) + min(15, max(0, int(ret12 / 5)))
        score += mom; details['momentum'] = mom

        price_now = info.get('currentPrice') or info.get('regularMarketPrice') or 0
        archetype = ('Quality Compounder' if q >= 18 else
                     'Momentum Leader' if mom >= 18 else
                     'Income Grower' if v >= 18 else 'Developing')

        return {
            'ticker': ticker,
            'company_name': info.get('shortName') or info.get('longName') or ticker,
            'score': score,
            'quality': q, 'growth': g, 'value_score': v, 'momentum': mom,
            'archetype': archetype,
            'current_price': round(price_now, 2),
            'details': json.dumps(details),
        }
    except Exception as e:
        return {'ticker': ticker, 'company_name': ticker, 'score': 0,
                'quality': 0, 'growth': 0, 'value_score': 0, 'momentum': 0,
                'archetype': 'Error', 'current_price': 0, 'details': str(e)}


@app.route('/api/screener/run', methods=['POST'])
@login_required
def api_screener_run():
    """Run CGG screener on value watchlist and cache results."""
    import threading
    def _run():
        from zoneinfo import ZoneInfo
        run_date = datetime.now(ZoneInfo('Australia/Brisbane')).date().isoformat()
        results = [_cgg_score(t) for t in VALUE_WATCHLIST]
        results.sort(key=lambda x: x['score'], reverse=True)
        now = datetime.now().isoformat()[:19]
        with get_db() as db:
            db.execute('DELETE FROM screener_cache WHERE run_date=?', (run_date,))
            for r in results:
                db.execute(
                    'INSERT INTO screener_cache (ticker,company_name,score,quality,growth,value_score,momentum,archetype,current_price,details,run_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (r['ticker'],r['company_name'],r['score'],r['quality'],r['growth'],r['value_score'],r['momentum'],r['archetype'],r['current_price'],r['details'],run_date,now)
                )
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Screener running in background — refresh in ~2 minutes'})


@app.route('/api/screener/results')
@login_required
def api_screener_results():
    with get_db() as db:
        rows = db.execute(
            'SELECT * FROM screener_cache WHERE run_date = (SELECT MAX(run_date) FROM screener_cache) ORDER BY score DESC'
        ).fetchall()
    if not rows:
        return jsonify({'results': [], 'run_date': None})
    run_date = rows[0]['run_date']
    return jsonify({'results': [dict(r) for r in rows], 'run_date': run_date})


@app.route('/api/paper-trades', methods=['GET', 'POST'])
@login_required
def api_paper_trades():
    with get_db() as db:
        if request.method == 'POST':
            d = request.get_json(force=True)
            now = datetime.now().isoformat()[:19]
            cur = db.execute(
                'INSERT INTO paper_trades (ticker,company_name,action,qty,entry_price,entry_date,notes,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (d['ticker'].upper(), d.get('company_name',''), d.get('action','buy'),
                 float(d['qty']), float(d['entry_price']), d.get('entry_date', now[:10]),
                 d.get('notes',''), now)
            )
            row = db.execute('SELECT * FROM paper_trades WHERE id=?', (cur.lastrowid,)).fetchone()
            return jsonify(dict(row)), 201
        rows = db.execute('SELECT * FROM paper_trades ORDER BY entry_date DESC').fetchall()
        return jsonify([dict(r) for r in rows])


@app.route('/api/paper-trades/<int:tid>', methods=['PUT', 'DELETE'])
@login_required
def api_paper_trade_item(tid):
    with get_db() as db:
        if request.method == 'DELETE':
            db.execute('DELETE FROM paper_trades WHERE id=?', (tid,))
            return jsonify({'ok': True})
        d = request.get_json(force=True)
        fields = [f'{k}=?' for k in d if k in ('qty','entry_price','entry_date','notes','closed','close_price','close_date')]
        params = [d[k] for k in d if k in ('qty','entry_price','entry_date','notes','closed','close_price','close_date')]
        if fields:
            db.execute(f'UPDATE paper_trades SET {",".join(fields)} WHERE id=?', params + [tid])
        row = db.execute('SELECT * FROM paper_trades WHERE id=?', (tid,)).fetchone()
        return jsonify(dict(row))


@app.route('/api/stock-price/<ticker>')
@login_required
def api_stock_price(ticker):
    """Live price for a ticker via yfinance."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker.upper()).info
        price = info.get('currentPrice') or info.get('regularMarketPrice') or 0
        name = info.get('shortName') or info.get('longName') or ticker
        return jsonify({'ticker': ticker.upper(), 'price': price, 'name': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500



# Always initialise DB — runs under both gunicorn and direct invocation
init_db()

# ── Daily 6am AEST screener run ───────────────────────────────────────────────
def _start_daily_screener():
    import threading
    from zoneinfo import ZoneInfo

    def _run_screener_now():
        run_date = datetime.now(ZoneInfo('Australia/Brisbane')).date().isoformat()
        print(f'[screener] running scan for {run_date}...', flush=True)
        try:
            results = [_cgg_score(t) for t in VALUE_WATCHLIST]
            results.sort(key=lambda x: x['score'], reverse=True)
            ts = datetime.now().isoformat()[:19]
            with get_db() as db:
                db.execute('DELETE FROM screener_cache WHERE run_date=?', (run_date,))
                # Keep only the last 2 days of data to prevent duplicate display
                db.execute("DELETE FROM screener_cache WHERE run_date < date('now', '-2 days')")
                for r in results:
                    db.execute(
                        'INSERT INTO screener_cache (ticker,company_name,score,quality,growth,value_score,momentum,archetype,current_price,details,run_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (r['ticker'],r['company_name'],r['score'],r['quality'],r['growth'],r['value_score'],r['momentum'],r['archetype'],r['current_price'],r['details'],run_date,ts)
                    )
            print(f'[screener] scan complete — {len(results)} stocks scored for {run_date}', flush=True)
        except Exception as e:
            print(f'[screener] scan failed for {run_date}: {e}', flush=True)
            raise

    def _loop():
        # On startup: if today's scan hasn't run yet and it's past 6am, catch up immediately
        try:
            now = datetime.now(ZoneInfo('Australia/Brisbane'))
            today = now.date().isoformat()
            with get_db() as db:
                row = db.execute('SELECT 1 FROM screener_cache WHERE run_date=? LIMIT 1', (today,)).fetchone()
            if not row and now.hour >= 6:
                print(f'[screener] catch-up: no scan for {today}, running now...', flush=True)
                _run_screener_now()
        except Exception as e:
            print(f'[screener] catch-up failed: {e}', flush=True)

        while True:
            try:
                now = datetime.now(ZoneInfo('Australia/Brisbane'))
                # Next 6am AEST
                target = now.replace(hour=6, minute=0, second=0, microsecond=0)
                if now >= target:
                    target = target + timedelta(days=1)
                wait = (target - now).total_seconds()
                time.sleep(wait)
                _run_screener_now()
            except Exception as e:
                print(f'[screener] loop error: {e} — retrying in 1hr', flush=True)
                time.sleep(3600)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()

_start_daily_screener()

if __name__ == '__main__':
    print(f'Family HQ running on port {PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False)
