#!/usr/bin/env python3
"""Family HQ — Whitewood Family Command Centre"""
import base64, json, os, sqlite3, re
from datetime import date, datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, send_file, abort, g, Response
import openpyxl

app = Flask(__name__)
ROOT = Path(__file__).parent
DATA_DIR = ROOT / 'data'
DB_PATH = DATA_DIR / 'family.db'
CONFIG_PATH = DATA_DIR / 'config.json'
BIRTHDAYS_PATH = DATA_DIR / 'Whitewood Family Birthdays.xlsx'
PORT = int(os.environ.get('PORT', 8282))

USERNAME = os.environ.get('FAMILY_HQ_USER', 'family')
PASSWORD = os.environ.get('FAMILY_HQ_PASS', 'Whitewood2026!')
_EXPECTED = base64.b64encode(f'{USERNAME}:{PASSWORD}'.encode()).decode()

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
XERO_CLIENT_ID = os.environ.get('XERO_CLIENT_ID', '')
XERO_CLIENT_SECRET = os.environ.get('XERO_CLIENT_SECRET', '')


# ── Auth ──────────────────────────────────────────────────────────────────────

def check_auth():
    auth = request.headers.get('Authorization', '')
    return auth.startswith('Basic ') and auth[6:] == _EXPECTED

@app.before_request
def require_auth():
    if request.path in ('/health',):
        return
    if not check_auth():
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="Family HQ"'})


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
        ''')
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
    today = date.today()
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
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 503
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

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=1024,
        system=build_family_context(),
        messages=messages
    )
    reply = response.content[0].text

    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute('INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)', ('user', user_msg, now))
        db.execute('INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)', ('assistant', reply, now))

    return jsonify({'reply': reply, 'model': 'claude-haiku'})

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
        'outlook': True,
    })

@app.route('/api/briefing')
def api_briefing():
    """Generate a morning briefing using Claude."""
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 503
    today = date.today()
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

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=512,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return jsonify({'briefing': response.content[0].text, 'date': today.isoformat()})


if __name__ == '__main__':
    init_db()
    print(f'Family HQ running on port {PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False)
