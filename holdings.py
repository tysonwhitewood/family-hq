"""Share holdings inside an account (the ING super fund).

Units come from screenshots or typed entries; prices come from a supplied price fetcher
(the app uses yfinance). Values are units x price, or the value read from the screenshot for
cash lines with no ticker. No AI here.
"""
from __future__ import annotations

import re
from datetime import date

import obligations as ob


def slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(text or '').lower()).strip('_')


def _norm(text) -> str:
    return ' '.join(str(text or '').lower().split())


def list_holdings(db, account_key: str | None = None) -> list[dict]:
    if account_key:
        rows = db.execute('SELECT * FROM holdings WHERE account_key=? ORDER BY value DESC', (account_key,)).fetchall()
    else:
        rows = db.execute('SELECT * FROM holdings ORDER BY account_key, value DESC').fetchall()
    return [dict(r) for r in rows]


def upsert_holdings(db, account_key: str, items: list[dict], source: str, now: str) -> list[str]:
    """Insert or update holdings by name (case-insensitive) or ticker. Returns the names touched."""
    touched = []
    existing = list_holdings(db, account_key)
    by_name = {_norm(h['name']): h for h in existing}
    by_ticker = {str(h['ticker'] or '').upper(): h for h in existing if h.get('ticker')}
    for item in items:
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        ticker = (str(item.get('ticker') or '').strip().upper() or None)
        units = item.get('units')
        price = item.get('price')
        value = item.get('value')
        if value is None and units is not None and price is not None:
            value = float(units) * float(price)
        match = by_name.get(_norm(name)) or (by_ticker.get(ticker) if ticker else None)
        if match:
            db.execute(
                'UPDATE holdings SET ticker=COALESCE(?, ticker), units=COALESCE(?, units), price=COALESCE(?, price), '
                'value=COALESCE(?, value), price_at=?, source=?, updated_at=? WHERE id=?',
                (ticker, units, price, value, now[:10], source, now, match['id']),
            )
        else:
            db.execute(
                'INSERT INTO holdings (account_key, name, ticker, units, price, value, price_at, source, created_at, updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (account_key, name, ticker, units, price, value, now[:10], source, now, now),
            )
        touched.append(name)
    return touched


def refresh_prices(db, fetch_price, now: str, account_key: str | None = None) -> dict:
    """Update price and value for every holding with a ticker. `fetch_price(ticker) -> float | None`."""
    updated, failed = [], []
    for h in list_holdings(db, account_key):
        if not h.get('ticker') or h.get('units') in (None, 0):
            continue
        try:
            price = fetch_price(h['ticker'])
        except Exception:  # noqa: BLE001 — a price feed failure is not fatal
            price = None
        if not price:
            failed.append(h['ticker'])
            continue
        value = round(float(h['units']) * float(price), 2)
        db.execute('UPDATE holdings SET price=?, value=?, price_at=?, updated_at=? WHERE id=?',
                   (float(price), value, now[:10], now, h['id']))
        updated.append(h['ticker'])
    return {'updated': updated, 'failed': failed}


def summary(db, account_key: str, last_balance: dict | None) -> dict:
    """Holdings with weights, the live total, and the movement since the last screenshot total."""
    rows = list_holdings(db, account_key)
    total = round(sum(float(h['value'] or 0) for h in rows), 2)
    for h in rows:
        h['weight'] = round(100 * float(h['value'] or 0) / total, 1) if total else 0.0
    screenshot_total = float(last_balance['balance']) if last_balance and last_balance.get('balance') is not None else None
    change = round(total - screenshot_total, 2) if screenshot_total is not None and total else None
    return {
        'account_key': account_key, 'holdings': rows, 'total': total,
        'screenshot_total': screenshot_total, 'screenshot_as_of': (last_balance or {}).get('as_of'),
        'change_since_screenshot': change,
        'price_at': max((h.get('price_at') or '' for h in rows), default=None) or None,
    }


def compose_super_line(display: str, summ: dict) -> str | None:
    if not summ['holdings']:
        return None
    parts = [f'• {display}: holdings worth {ob.money(summ["total"])}']
    if summ.get('price_at'):
        parts[0] += f' at {ob._long_date(summ["price_at"])} prices'
    if summ.get('change_since_screenshot') is not None:
        change = summ['change_since_screenshot']
        direction = 'up' if change >= 0 else 'down'
        parts[0] += f', {direction} {ob.money(abs(change))} since the {ob._long_date(summ["screenshot_as_of"])} statement'
    top = sorted(summ['holdings'], key=lambda h: -(h.get('value') or 0))[:3]
    parts.append('  ' + ', '.join(f'{h["name"]} {h["weight"]}%' for h in top))
    return '\n'.join(parts)
