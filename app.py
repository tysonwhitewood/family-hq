#!/usr/bin/env python3
"""Family HQ — Whitewood Family Command Centre"""
import json, os, sqlite3, re, time, urllib.request, urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, send_file, abort, g, Response, redirect, url_for, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import openpyxl

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

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
XERO_CLIENT_ID = os.environ.get('XERO_CLIENT_ID', '')
XERO_CLIENT_SECRET = os.environ.get('XERO_CLIENT_SECRET', '')


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
    public = {'/health', '/login', '/logout'}
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
    return bool(ANTHROPIC_API_KEY or OPENROUTER_API_KEY)

def llm_chat(messages: list, system: str = '', max_tokens: int = 1024) -> str:
    """Call Claude via Anthropic SDK, or fall back to OpenRouter free model."""
    if ANTHROPIC_API_KEY:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        kwargs = dict(model='claude-haiku-4-5-20251001', max_tokens=max_tokens, messages=messages)
        if system:
            kwargs['system'] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text

    if OPENROUTER_API_KEY:
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
                    'Authorization': f'Bearer {OPENROUTER_API_KEY}',
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
        ''')
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
- If asked about Xero/invoices: explain the integration needs to be connected in Settings
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

    model = 'claude-haiku' if ANTHROPIC_API_KEY else 'llama-3.3-70b (openrouter)'
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
        'xero': (token_dir / 'xero_token.json').exists(),
        'anthropic': bool(ANTHROPIC_API_KEY),
        'openrouter': bool(OPENROUTER_API_KEY),
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
        rows = [dict(r) for r in db.execute('SELECT * FROM warranties ORDER BY expires_date ASC').fetchall()]
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


# ── Xero Integration ─────────────────────────────────────────────────────────

TOKEN_DIR = DATA_DIR / 'tokens'

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
        from datetime import date as _date
        run_date = _date.today().isoformat()
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
            'SELECT * FROM screener_cache ORDER BY score DESC'
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

XERO_SCOPES = 'openid profile email accounting.transactions.read accounting.reports.read offline_access'
XERO_CC_SCOPES = 'accounting.transactions.read accounting.reports.read'  # Custom Connection scopes (no OIDC)
XERO_REDIRECT_URI = 'https://family.edencommercial.au/api/xero/callback'
XERO_AUTH_URL = 'https://login.xero.com/identity/connect/authorize'
XERO_TOKEN_URL = 'https://identity.xero.com/connect/token'


def _xero_token():
    """Return a valid Xero access token dict, refreshing if expired."""
    TOKEN_DIR.mkdir(exist_ok=True)
    token_path = TOKEN_DIR / 'xero_token.json'

    if not token_path.exists():
        raise ValueError('not_connected')

    tok = json.loads(token_path.read_text())

    # Still valid
    if time.time() < tok.get('expires_at', 0) - 60:
        return tok

    # Refresh using refresh_token
    refresh_token = tok.get('refresh_token')
    if not refresh_token:
        token_path.unlink(missing_ok=True)
        raise ValueError('not_connected')

    creds = base64.b64encode(f'{XERO_CLIENT_ID}:{XERO_CLIENT_SECRET}'.encode()).decode()
    body = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
    }).encode()
    req = urllib.request.Request(
        XERO_TOKEN_URL, data=body,
        headers={'Authorization': f'Basic {creds}',
                 'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            new_tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8', errors='replace')
        token_path.unlink(missing_ok=True)
        raise ValueError(f'Refresh failed: {err}')

    new_tok['expires_at'] = time.time() + new_tok.get('expires_in', 1800)
    new_tok['tenant_id'] = tok.get('tenant_id', '')
    new_tok['org_name'] = tok.get('org_name', '')
    token_path.write_text(json.dumps(new_tok))
    return new_tok


def _xero_get(path):
    """Authenticated GET to the Xero Accounting API v2."""
    tok = _xero_token()
    req = urllib.request.Request(
        f'https://api.xero.com/api.xro/2.0/{path}',
        headers={
            'Authorization': f'Bearer {tok["access_token"]}',
            'Xero-tenant-id': tok.get('tenant_id', ''),
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


@app.route('/api/xero/auth')
def xero_auth():
    """Try client_credentials (Custom Connection) first; fall back to OAuth redirect."""
    from flask import redirect as _redirect
    if XERO_CLIENT_ID and XERO_CLIENT_SECRET:
        # Attempt Custom Connection (client_credentials) — works without user redirect
        creds = base64.b64encode(f'{XERO_CLIENT_ID}:{XERO_CLIENT_SECRET}'.encode()).decode()
        body = urllib.parse.urlencode({
            'grant_type': 'client_credentials',
            'scope': XERO_CC_SCOPES,
        }).encode()
        req = urllib.request.Request(
            XERO_TOKEN_URL, data=body,
            headers={'Authorization': f'Basic {creds}',
                     'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                tok = json.loads(r.read())
            tok['expires_at'] = time.time() + tok.get('expires_in', 1800)
            # For custom connections, fetch tenant from connections endpoint
            try:
                conn_req = urllib.request.Request(
                    'https://api.xero.com/connections',
                    headers={'Authorization': f'Bearer {tok["access_token"]}', 'Accept': 'application/json'},
                )
                with urllib.request.urlopen(conn_req, timeout=15) as r2:
                    connections = json.loads(r2.read())
                if connections:
                    tok['tenant_id'] = connections[0]['tenantId']
                    tok['org_name'] = connections[0].get('tenantName', '')
            except Exception:
                pass
            TOKEN_DIR.mkdir(exist_ok=True)
            (TOKEN_DIR / 'xero_token.json').write_text(json.dumps(tok))
            return _redirect('/?xero=connected')
        except urllib.error.HTTPError:
            pass  # Not a Custom Connection app — fall through to OAuth redirect

    # Standard OAuth (Web App)
    import secrets as _secrets
    state = _secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        'response_type': 'code',
        'client_id': XERO_CLIENT_ID,
        'redirect_uri': XERO_REDIRECT_URI,
        'scope': XERO_SCOPES,
        'state': state,
    })
    return _redirect(f'{XERO_AUTH_URL}?{params}')


@app.route('/api/xero/callback')
def xero_callback():
    """Handle Xero OAuth callback, exchange code for tokens."""
    code = request.args.get('code')
    error = request.args.get('error')
    if error or not code:
        return f'<h2>Xero connection failed: {error or "no code"}</h2><a href="/">Back</a>', 400

    creds = base64.b64encode(f'{XERO_CLIENT_ID}:{XERO_CLIENT_SECRET}'.encode()).decode()
    body = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': XERO_REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        XERO_TOKEN_URL, data=body,
        headers={'Authorization': f'Basic {creds}',
                 'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8', errors='replace')
        return f'<h2>Token exchange failed: {err}</h2><a href="/">Back</a>', 500

    tok['expires_at'] = time.time() + tok.get('expires_in', 1800)

    # Fetch tenant_id
    try:
        conn_req = urllib.request.Request(
            'https://api.xero.com/connections',
            headers={'Authorization': f'Bearer {tok["access_token"]}', 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(conn_req, timeout=15) as r:
            connections = json.loads(r.read())
        if connections:
            tok['tenant_id'] = connections[0]['tenantId']
            tok['org_name'] = connections[0].get('tenantName', '')
    except Exception:
        pass

    TOKEN_DIR.mkdir(exist_ok=True)
    (TOKEN_DIR / 'xero_token.json').write_text(json.dumps(tok))
    from flask import redirect as _redirect
    return _redirect('/?xero=connected')


@app.route('/api/xero/connect', methods=['POST'])
def xero_connect():
    """Check connection status (token exists and valid)."""
    try:
        tok = _xero_token()
        return jsonify({
            'ok': True,
            'org': tok.get('org_name', ''),
            'tenant_id': tok.get('tenant_id', ''),
        })
    except ValueError as e:
        if 'not_connected' in str(e):
            return jsonify({'ok': False, 'error': 'Not connected — use /api/xero/auth to connect'}), 401
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/xero/accounts')
def xero_accounts():
    """List bank accounts with current balances."""
    try:
        data = _xero_get('Accounts?where=Type%3D%3D%22BANK%22')
        accounts = [
            {
                'id': a['AccountID'],
                'name': a['Name'],
                'code': a.get('Code', ''),
                'balance': a.get('Balance', 0),
                'currency': a.get('CurrencyCode', 'AUD'),
                'status': a.get('Status', ''),
            }
            for a in data.get('Accounts', [])
        ]
        return jsonify(accounts)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/xero/transactions')
def xero_transactions():
    """Recent bank transactions, optionally filtered by account_id and from date."""
    account_id = request.args.get('account_id', '')
    from_date = request.args.get('from', (date.today() - timedelta(days=30)).isoformat())
    try:
        where = urllib.parse.quote('Status=="AUTHORISED"')
        path = f'BankTransactions?where={where}&fromDate={from_date}&order=Date+DESC'
        data = _xero_get(path)
        txns = []
        for t in data.get('BankTransactions', []):
            if account_id and t.get('BankAccount', {}).get('AccountID') != account_id:
                continue
            txns.append({
                'date': t.get('DateString', ''),
                'amount': t.get('Total', 0),
                'type': t.get('Type', ''),
                'ref': t.get('Reference', ''),
                'contact': t.get('Contact', {}).get('Name', ''),
                'account_id': t.get('BankAccount', {}).get('AccountID', ''),
            })
        return jsonify(txns[:100])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/xero/cashflow')
def xero_cashflow():
    """P&L report for the requested date range (default: last 90 days)."""
    from_date = request.args.get('from', (date.today() - timedelta(days=90)).isoformat())
    to_date = request.args.get('to', date.today().isoformat())
    try:
        data = _xero_get(f'Reports/ProfitAndLoss?fromDate={from_date}&toDate={to_date}')
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Always initialise DB — runs under both gunicorn and direct invocation
init_db()

if __name__ == '__main__':
    print(f'Family HQ running on port {PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False)
