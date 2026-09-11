"""Conversation handling for the Family Finance channel.

Turns a channel post into an action and a reply: short commands are parsed in code, images
are read by the LLM into JSON, anything else becomes a free-form question answered from the
money context. Arithmetic stays in `obligations.py`.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import date, timedelta

import holdings as hold
import obligations as ob

KEYWORDS = {
    'done': 'done', 'paid': 'paid', 'skip': 'skip', 'yes': 'yes', 'status': 'status',
    'position': 'status', 'help': 'help', '?': 'help',
}
MONTHS = {m: i for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], start=1)}
MONEY_RE = re.compile(r'\$?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(k)?(?![\d/-])', re.I)
AMOUNT_ONLY_RE = re.compile(r'^\$?\s*\d[\d,]*(?:\.\d{1,2})?\s*k?$', re.I)
ISO_DATE_RE = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')
DAY_FIRST_RE = re.compile(r'\b(\d{1,2})[ /-]([A-Za-z]{3,9}|\d{1,2})(?:[ /-](\d{2,4}))?\b')

IMAGE_PROMPT = """You are reading a photo or screenshot for a family finance app. Reply with JSON only, no prose.
Schema: {{"kind": "balances"|"bill"|"receipts"|"holdings"|"other",
 "balances": [{{"account_key": string|null, "name_seen": string, "balance": number, "available": number|null, "as_of": "YYYY-MM-DD"|null}}],
 "bill": {{"payee": string, "amount": number|null, "due_date": "YYYY-MM-DD"|null, "description": string}}|null,
 "receipts": [{{"date": "YYYY-MM-DD"|null, "amount": number, "description": string}}],
 "holdings": {{"account_key": string|null, "total": number|null, "as_of": "YYYY-MM-DD"|null,
              "items": [{{"name": string, "ticker": string|null, "units": number|null, "price": number|null, "value": number}}]}}|null,
 "note": string}}
Known accounts (match by name or by the digits shown; use the key, otherwise null): {accounts}
Rules: "balance" is the Current/Balance column and "available" the Available column when shown; money owed is negative; dates in ISO form; today is {today}. A bank app account list is "balances"; an invoice, rates or utility notice is "bill"; a list of payments received by the business is "receipts"; a superannuation or brokerage valuation listing investments with units and prices is "holdings" (use ASX tickers with .AX when you know them, e.g. VAS.AX, otherwise null)."""

ANSWER_SYSTEM = """You are Family HQ's money assistant for Tyson and Robyn Whitewood. Answer in Australian English, in under 120 words, using only the figures in the context below. If the context does not contain what is needed, say what to post instead (a screenshot or a figure). Never invent numbers, never give investment advice, and do not restate the whole context.

CONTEXT
{context}"""


# ── parsing ──────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return ' '.join(str(text or '').lower().split())


def account_aliases(settings: dict) -> dict[str, str]:
    """Every phrase that names an account, lower-cased, mapped to its key."""
    out = {}
    for account in settings.get('accounts', []) or []:
        key = account['key']
        names = [key, key.replace('_', ' '), account.get('display') or ''] + list(account.get('aliases') or [])
        for name in names:
            name = _norm(name)
            if name:
                out[name] = key
    return out


def parse_money(text: str) -> float | None:
    match = MONEY_RE.search(str(text or ''))
    if not match:
        return None
    value = float(match.group(1).replace(',', ''))
    if match.group(2):
        value *= 1000
    return value


def parse_date(text: str, today: date) -> tuple[str | None, str]:
    """Return (ISO date or None, text with the date removed). Day-first, year optional."""
    match = ISO_DATE_RE.search(text)
    if match:
        return match.group(1), (text[:match.start()] + text[match.end():]).strip()
    match = DAY_FIRST_RE.search(text)
    if not match:
        return None, text
    day = int(match.group(1))
    month_raw = match.group(2).lower()
    month = MONTHS.get(month_raw[:3]) if month_raw.isalpha() else int(month_raw)
    year_raw = match.group(3)
    if not month or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None, text
    remainder = (text[:match.start()] + text[match.end():]).strip()
    if year_raw:
        year = int(year_raw) + (2000 if len(year_raw) == 2 else 0)
    else:
        year = today.year
        try:
            if date(year, month, day) < today:
                year += 1
        except ValueError:
            return None, text
    try:
        return date(year, month, day).isoformat(), remainder
    except ValueError:
        return None, text


def parse_command(text: str, settings: dict, today: date | None = None) -> dict | None:
    """A short command from the channel, or None when the text is free-form."""
    today = today or date.today()
    low = _norm(text).rstrip('.!')
    if not low:
        return None
    if low in KEYWORDS:
        return {'kind': KEYWORDS[low]}
    if low in ('accounts', 'list accounts'):
        return {'kind': 'accounts'}
    match = re.fullmatch(r'add account\s+(.+?)\s+(\d[\d ]{2,})', low)
    if match:
        return {'kind': 'add_account', 'name': match.group(1).strip(), 'digits': match.group(2).replace(' ', '')}
    match = re.fullmatch(r'eden total\s+(.+)', low)
    if match and parse_money(match.group(1)) is not None:
        return {'kind': 'receipt_total', 'amount': parse_money(match.group(1))}
    match = re.fullmatch(r'eden\s+(.+)', low)
    if match and AMOUNT_ONLY_RE.match(match.group(1)):
        return {'kind': 'receipt', 'amount': parse_money(match.group(1))}
    for alias, key in sorted(account_aliases(settings).items(), key=lambda kv: -len(kv[0])):
        if low.startswith(alias + ' '):
            rest = low[len(alias):].strip()
            if AMOUNT_ONLY_RE.match(rest):
                return {'kind': 'balance', 'account_key': key, 'amount': parse_money(rest)}
    match = re.fullmatch(r'(.+?)\s+(paid|unpaid)', low)
    if match and len(match.group(1).split()) <= 4:
        return {'kind': 'mark', 'name': match.group(1), 'state': 'paid' if match.group(2) == 'paid' else 'upcoming'}
    match = re.match(r'(.+?)\s+due\s+(.+)', low)
    if match and len(match.group(1).split()) <= 5:
        due, rest = parse_date(match.group(2), today)
        return {'kind': 'bill', 'name': match.group(1).strip(), 'due_date': due, 'amount': parse_money(rest)}
    match = re.fullmatch(r'([a-z][a-z &/\-]{0,40}?)\s+(\$?\d[\d,]*(?:\.\d{1,2})?\s*k?)', low)
    if match and len(match.group(1).split()) <= 4:
        return {'kind': 'bill', 'name': match.group(1).strip(), 'due_date': None, 'amount': parse_money(match.group(2))}
    return None


def compose_help() -> str:
    return ('I understand: *status* (the position now), *accounts* (the account list), *add account <name> <digits>*, *done* (set-aside moved), *paid* or *skip* '
            '(the nearest bill), *yes* (PropVesting registered), an account and a balance like *gst 9262* or '
            '*ing home 2142*, *eden 5280* to add a receipt, *eden total 24500* to set the month, '
            '*rates due 27 Feb 2027 1614* to set a bill, *<bill> paid*, or a screenshot of a bank app or a '
            'bill. Anything else I answer as a question from the current position.')


def parse_image_result(text: str) -> dict:
    """Tolerant JSON extraction for the image prompt's reply."""
    empty = {'kind': 'other', 'balances': [], 'bill': None, 'receipts': [], 'holdings': None, 'note': ''}
    raw = str(text or '')
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return empty
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return empty
    if not isinstance(data, dict):
        return empty
    out = dict(empty)
    out['kind'] = data.get('kind') if data.get('kind') in ('balances', 'bill', 'receipts', 'holdings', 'other') else 'other'
    out['balances'] = [b for b in (data.get('balances') or []) if isinstance(b, dict) and b.get('balance') is not None]
    out['bill'] = data.get('bill') if isinstance(data.get('bill'), dict) else None
    out['receipts'] = [r for r in (data.get('receipts') or []) if isinstance(r, dict) and r.get('amount') is not None]
    holdings_raw = data.get('holdings') if isinstance(data.get('holdings'), dict) else None
    out['holdings'] = None
    if holdings_raw:
        items = [i for i in (holdings_raw.get('items') or []) if isinstance(i, dict) and i.get('name') and i.get('value') is not None]
        if items:
            out['holdings'] = {'account_key': holdings_raw.get('account_key'), 'total': holdings_raw.get('total'),
                               'as_of': holdings_raw.get('as_of'), 'items': items}
    out['note'] = str(data.get('note') or '')
    if out['kind'] == 'balances' and not out['balances']:
        out['kind'] = 'bill' if out['bill'] else ('receipts' if out['receipts'] else ('holdings' if out['holdings'] else 'other'))
    if out['kind'] == 'holdings' and not out['holdings']:
        out['kind'] = 'other'
    return out


# ── context and answers ──────────────────────────────────────────────────────

def money_context(service, today: date) -> str:
    position = service.position(today)
    settings = service.settings
    lines = [f'Today: {today.strftime("%A %d %B %Y")}', '', 'Reserve accounts (what each should hold vs last known balance):']
    for row in position['targets']:
        if row.get('balance') is None:
            lines.append(f'- {row["display"]}: should hold {ob.money(row["target"])}; no balance known')
        else:
            verdict = f'short {ob.money(row["shortfall"])}' if (row.get('shortfall') or 0) > 0 else 'covered'
            lines.append(f'- {row["display"]}: should hold {ob.money(row["target"])}; holds {ob.money(row["balance"])} '
                         f'({row.get("age_days", 0)} days old); {verdict}')
    lines += ['', 'Next 30 days of bills and set-asides:']
    for item in position['upcoming']:
        if item['days_out'] <= 30:
            flag = ' (OVERDUE)' if item.get('overdue') else ''
            lines.append(f'- {item["due_date"]} {item["name"]} {ob.money(item.get("estimate"))}{flag}')
    with service._db() as db:
        receipts = db.execute('SELECT year_month, amount_incl_gst FROM receipts_log ORDER BY year_month DESC LIMIT 4').fetchall()
        budget = db.execute("SELECT type, direction, SUM(monthly_target) AS total FROM budget_targets "
                            "WHERE frequency='monthly' GROUP BY type, direction").fetchall()
    lines += ['', 'Eden Commercial receipts recorded (GST inclusive):']
    for r in receipts:
        lines.append(f'- {r["year_month"]}: {ob.money(r["amount_incl_gst"])}')
    if not receipts:
        lines.append('- none recorded; the retainer of ' + ob.money(settings.get('assumed_monthly_retainer')) + ' a month is assumed')
    lines += ['', 'Monthly budget lines (monthly items only):']
    for b in budget:
        lines.append(f'- {b["type"]} {b["direction"]}: {ob.money(b["total"])} a month')
    lines += ['', f'Rules: GST reserve is 1/11 of receipts; income-tax reserve {int(float(settings.get("income_tax_reserve_rate", 0.15)) * 100)}% '
                  f'of ex-GST receipts; PAYG instalment {ob.money(settings.get("payg_instalment_quarterly"))} a quarter.']
    return '\n'.join(lines)


def answer_question(llm, question: str, context: str) -> str:
    reply = llm([{'role': 'user', 'content': question}], system=ANSWER_SYSTEM.format(context=context))
    return str(reply or '').strip() or 'I could not put an answer together. Try asking a shorter question.'


# ── handling ─────────────────────────────────────────────────────────────────

def _open_occurrences(service, today: date, window: int = 30) -> list[dict]:
    with service._db() as db:
        rows = db.execute(
            """SELECT o.id, o.due_date, o.estimate, o.state, b.name, b.id AS obligation_id, b.amount_rule
               FROM obligation_occurrences o JOIN obligations b ON b.id = o.obligation_id
               WHERE o.state IN ('upcoming','funds_confirmed') AND b.status != 'retired'
                 AND b.remind = 1 AND b.amount_rule != 'receipts_share'
                 AND o.due_date BETWEEN ? AND ?""",
            ((today - timedelta(days=window)).isoformat(), (today + timedelta(days=window)).isoformat()),
        ).fetchall()
    return sorted((dict(r) for r in rows), key=lambda r: abs((date.fromisoformat(r['due_date']) - today).days))


def _set_state(service, occ_id: int, state: str, who: str):
    with service._db() as db:
        db.execute("UPDATE obligation_occurrences SET state=?, state_changed_at=?, state_changed_by=? WHERE id=?",
                   (state, service.now().isoformat()[:19], who, occ_id))


def _store_balance(service, key: str, balance: float, available, as_of: str, source: str, post_id, raw: str):
    with service._db() as db:
        db.execute('INSERT INTO account_balances (account_key, balance, available, as_of, source, mattermost_post_id, raw, created_at) '
                   'VALUES (?,?,?,?,?,?,?,?)',
                   (key, float(balance), None if available in (None, '') else float(available), as_of, source, post_id, raw,
                    service.now().isoformat()[:19]))


def _add_receipt(service, today: date, amount: float, source: str, replace: bool = False) -> float:
    ym = ob.month_key(today)
    now = service.now().isoformat()[:19]
    with service._db() as db:
        row = db.execute('SELECT amount_incl_gst, detail FROM receipts_log WHERE year_month=?', (ym,)).fetchone()
        try:
            detail = json.loads(row['detail']) if row and row['detail'] else []
        except ValueError:
            detail = []
        detail.append({'source': source, 'amount': amount, 'replace': replace})
        total = amount if (replace or row is None) else float(row['amount_incl_gst']) + amount
        db.execute('INSERT INTO receipts_log (year_month, amount_incl_gst, detail, updated_at) VALUES (?,?,?,?) '
                   'ON CONFLICT(year_month) DO UPDATE SET amount_incl_gst=excluded.amount_incl_gst, '
                   'detail=excluded.detail, updated_at=excluded.updated_at',
                   (ym, total, json.dumps(detail), now))
    service.regenerate_occurrences(today)
    return total


def _find_obligation(service, name: str):
    words = [w for w in _norm(name).split() if w]
    with service._db() as db:
        rows = db.execute("SELECT * FROM obligations WHERE status != 'retired'").fetchall()
    matches = [dict(r) for r in rows if all(w in r['name'].lower() for w in words)]
    return matches[0] if len(matches) == 1 else (matches[0] if matches else None)


def _upsert_bill(service, today: date, name: str, amount, due_date, source: str) -> str:
    now = service.now().isoformat()[:19]
    existing = _find_obligation(service, name)
    with service._db() as db:
        if existing:
            fields, values = [], []
            if amount is not None:
                fields.append('amount=?'); values.append(float(amount))
            if due_date:
                fields.append('anchor_date=?'); values.append(due_date)
            if fields:
                fields.append('updated_at=?'); values.append(now)
                db.execute(f'UPDATE obligations SET {", ".join(fields)} WHERE id=?', (*values, existing['id']))
                db.execute("DELETE FROM obligation_occurrences WHERE obligation_id=? AND state='upcoming'", (existing['id'],))
            label = existing['name']
        else:
            label = name.strip().title()
            db.execute('''INSERT INTO obligations (name, ownership, pay_from_account, reserve_account, amount_rule, amount, frequency,
                          anchor_date, due_rule, extension_days, lead_days, remind, status, budget_category, source, notes, created_at, updated_at)
                          VALUES (?,?,?,?,'fixed',?,'once',?,'none',0,'[7, 1]',1,'pending_confirmation',NULL,?,?,?,?)''',
                       (label, 'personal', None, None, None if amount is None else float(amount), due_date, source,
                        'Created from a channel reply; confirm the account it is paid from.', now, now))
    service.regenerate_occurrences(today)
    parts = [label]
    if amount is not None:
        parts.append(ob.money(amount))
    if due_date:
        parts.append(f'due {ob._long_date(due_date)}')
    return ('Updated ' if existing else 'Added ') + ' '.join(parts) + ('' if existing else ' (needs confirming: which account pays it?)') + '.'


def _handle_images(service, post, settings, llm, images, today) -> tuple[str, bool]:
    if llm is None:
        return ('I can see an image, but no AI is configured to read it. Type the figures instead, e.g. *gst 9262*.', False)
    accounts = ', '.join(f'{a["key"]} = {a.get("display", a["key"])} ({a.get("match", "")})' for a in settings.get('accounts', []))
    keys = {a['key'] for a in settings.get('accounts', [])}
    replies, acted = [], False
    for content, mime, name in images:
        payload = [{'media_type': mime, 'data': base64.b64encode(content).decode()}]
        try:
            raw = llm([{'role': 'user', 'content': IMAGE_PROMPT.format(accounts=accounts, today=today.isoformat())}],
                      system='', images=payload)
        except Exception as exc:  # noqa: BLE001 — a model failure must become a reply, not a crash
            replies.append(f'I could not read {name}: {str(exc)[:120]}. Type the figures instead.')
            continue
        result = parse_image_result(raw)
        if result['kind'] == 'balances':
            got, unmatched = [], []
            for b in result['balances']:
                key = b.get('account_key')
                if key in keys:
                    as_of = b.get('as_of') or today.isoformat()
                    _store_balance(service, key, b['balance'], b.get('available'), as_of, 'screenshot', post.get('id'), json.dumps(b))
                    display = next((a.get('display', key) for a in settings.get('accounts', []) if a['key'] == key), key)
                    got.append(f'{display} {ob.money(b["balance"])}')
                else:
                    unmatched.append(b.get('name_seen') or 'an account')
            if got:
                acted = True
                replies.append('Got it: ' + ', '.join(got) + f' as at {ob._long_date(today)}.')
            if unmatched:
                replies.append('Not matched to a known account: ' + ', '.join(unmatched) + '.')
        elif result['kind'] == 'bill' and result['bill']:
            bill = result['bill']
            replies.append(_upsert_bill(service, today, bill.get('payee') or bill.get('description') or 'Bill',
                                        bill.get('amount'), bill.get('due_date'), f'photo in Mattermost post {post.get("id")}'))
            acted = True
        elif result['kind'] == 'holdings' and result['holdings']:
            data = result['holdings']
            investment_keys = [a['key'] for a in settings.get('accounts', []) if a.get('investment')]
            key = data.get('account_key') if data.get('account_key') in keys else (investment_keys[0] if investment_keys else None)
            if not key:
                replies.append('I read a holdings list but there is no investment account to attach it to. '
                               'Add one with *add account super 095236*.')
                continue
            now = service.now().isoformat()[:19]
            with service._db() as db:
                touched = hold.upsert_holdings(db, key, data['items'], f'screenshot post {post.get("id")}', now)
            total = data.get('total')
            if total is None:
                total = sum(float(i['value']) for i in data['items'])
            _store_balance(service, key, total, None, data.get('as_of') or today.isoformat(), 'screenshot', post.get('id'), json.dumps(data))
            display = next((a.get('display', key) for a in settings.get('accounts', []) if a['key'] == key), key)
            replies.append(f'Got it: {display} {ob.money(total)} across {len(touched)} holdings ({", ".join(touched)}).')
            acted = True
        elif result['kind'] == 'receipts' and result['receipts']:
            total = sum(float(r['amount']) for r in result['receipts'])
            month_total = _add_receipt(service, today, total, f'screenshot post {post.get("id")}')
            replies.append(f'Recorded {ob.money(total)} of Eden receipts from the screenshot. '
                           f'{today.strftime("%B")} so far: {ob.money(month_total)}.')
            acted = True
        else:
            replies.append(f'I could not find balances or a bill in {name}.' + (f' ({result["note"]})' if result['note'] else ''))
    return '\n'.join(replies), acted


def handle_post(service, post: dict, settings: dict, llm=None, images=None, today: date | None = None) -> dict:
    """Act on one channel post. Returns {'reply': str|None, 'acted': bool}."""
    today = today or service.today()
    text = str(post.get('message') or '').strip()
    if images:
        reply, acted = _handle_images(service, post, settings, llm, images, today)
        return {'reply': reply, 'acted': acted}
    if not text:
        return {'reply': None, 'acted': False}
    cmd = parse_command(text, settings, today)
    if cmd is None:
        if llm is None:
            return {'reply': 'I can only take commands at the moment (reply *help* for the list).', 'acted': False}
        return {'reply': answer_question(llm, text, money_context(service, today)), 'acted': False}
    kind = cmd['kind']
    if kind == 'help':
        return {'reply': compose_help(), 'acted': False}
    if kind == 'accounts':
        names = [f'{a.get("display", a["key"])} ({a.get("match", "")})' for a in settings.get('accounts', [])]
        return {'reply': 'Accounts I know: ' + ('; '.join(names) if names else 'none yet') + '.', 'acted': False}
    if kind == 'add_account':
        adder = getattr(service, 'account_adder', None)
        if adder is None:
            return {'reply': 'Adding accounts from here is not switched on.', 'acted': False}
        name = cmd['name']
        words = name.lower().split()
        bank = next((w.upper() for w in words if w in ('cba', 'ing', 'gsb', 'nab', 'anz', 'westpac', 'macquarie')), 'other')
        entry = {'key': hold.slugify(name), 'display': name.title(), 'bank': bank, 'match': cmd['digits'],
                 'aliases': [name.lower()], 'investment': any(w in ('super', 'superannuation', 'shares', 'brokerage') for w in words)}
        try:
            adder(entry)
        except ValueError as exc:
            return {'reply': f'Could not add that account: {exc}', 'acted': False}
        settings.setdefault('accounts', []).append(entry)
        return {'reply': f'Added {entry["display"]} (digits {entry["match"]}, {bank}). Screenshots showing those digits will now match it, '
                         f'and *{name.lower()} 1234* records a balance.', 'acted': True}
    if kind == 'status':
        position = service.position(today)
        upcoming = [u for u in position['upcoming'] if u['days_out'] <= 30]
        birthdays = []
        if getattr(service, 'birthdays_fn', None):
            try:
                birthdays = list(service.birthdays_fn(14))
            except Exception:  # noqa: BLE001
                birthdays = []
        return {'reply': ob.compose_weekly_position(position['targets'], upcoming, today, birthdays), 'acted': False}
    if kind == 'done':
        ym = ob.month_key(ob.add_months(today.replace(day=1), -1))
        service.set_state(f'setaside_done:{ym}', str(post.get('id') or 'channel'))
        return {'reply': f'Marked the {ob.parse_month_key(ym).strftime("%B")} set-aside as done. Post a screenshot when you can so the balances update.', 'acted': True}
    if kind in ('paid', 'skip'):
        open_rows = _open_occurrences(service, today)
        if not open_rows:
            return {'reply': 'Nothing is due within the next month to mark.', 'acted': False}
        target = open_rows[0]
        _set_state(service, target['id'], 'paid' if kind == 'paid' else 'skipped', 'channel')
        verb = 'paid' if kind == 'paid' else 'skipped'
        return {'reply': f'Marked {target["name"]} (due {ob._long_date(target["due_date"])}) as {verb}.', 'acted': True}
    if kind == 'mark':
        item = _find_obligation(service, cmd['name'])
        if not item:
            return {'reply': f'I could not find a bill called "{cmd["name"]}".', 'acted': False}
        rows = [r for r in _open_occurrences(service, today, 60) if r['obligation_id'] == item['id']]
        if not rows:
            return {'reply': f'{item["name"]} has nothing open to mark.', 'acted': False}
        _set_state(service, rows[0]['id'], cmd['state'], 'channel')
        return {'reply': f'Marked {item["name"]} (due {ob._long_date(rows[0]["due_date"])}) as {"paid" if cmd["state"] == "paid" else "still open"}.', 'acted': True}
    if kind == 'yes':
        item = _find_obligation(service, 'propvesting')
        if item and not item.get('anchor_date'):
            with service._db() as db:
                db.execute('UPDATE obligations SET anchor_date=?, status=?, updated_at=? WHERE id=?',
                           (today.isoformat(), 'active', service.now().isoformat()[:19], item['id']))
            service.regenerate_occurrences(today)
            return {'reply': f'PropVesting is registered: pay {ob.money(item.get("amount"))} from EComm GST now. Reply *paid* when it has gone.', 'acted': True}
        return {'reply': 'Yes to what? Reply *help* for the list of things I understand.', 'acted': False}
    if kind == 'balance':
        display = next((a.get('display', cmd['account_key']) for a in settings.get('accounts', []) if a['key'] == cmd['account_key']), cmd['account_key'])
        _store_balance(service, cmd['account_key'], cmd['amount'], None, today.isoformat(), 'typed', post.get('id'), text)
        return {'reply': f'Got it: {display} {ob.money(cmd["amount"])} as at {ob._long_date(today)}.', 'acted': True}
    if kind in ('receipt', 'receipt_total'):
        total = _add_receipt(service, today, cmd['amount'], f'channel post {post.get("id")}', replace=(kind == 'receipt_total'))
        setaside = ob.monthly_setaside(total, settings)
        return {'reply': f'{"Set" if kind == "receipt_total" else "Recorded"} {ob.money(cmd["amount"])}. {today.strftime("%B")} receipts so far: '
                         f'{ob.money(total)}, so the set-aside to EComm GST is {ob.money(setaside["total"])} '
                         f'({ob.money(setaside["gst"])} GST + {ob.money(setaside["income_tax"])} income tax).', 'acted': True}
    if kind == 'bill':
        return {'reply': _upsert_bill(service, today, cmd['name'], cmd.get('amount'), cmd.get('due_date'), f'channel post {post.get("id")}'), 'acted': True}
    return {'reply': compose_help(), 'acted': False}
