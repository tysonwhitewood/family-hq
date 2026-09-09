"""Obligations engine for Family HQ.

Due dates, reserve maths and message text for recurring money obligations.
Pure functions over plain dicts and dates: no database, no network, no AI.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

DEFAULT_SETTINGS = {
    'gst_fraction': 1 / 11,
    'income_tax_reserve_rate': 0.15,
    'assumed_monthly_retainer': 10083.34,
    'gst_credit_allowance_monthly': 636.0,
    'payg_instalment_quarterly': 3188.0,
    'sl_trading_trust_bas': 545.0,
    'emergency_floor': 3000.0,
    'mortgage_repayment': 4810.38,
    'reserve_start_month': '2026-09',
    'post_hour_local': 7,
    'weekly_day': 6,          # Sunday (Monday == 0)
    'weekly_hour_local': 17,
    'quiet_hours': [21, 7],
    'timezone': 'Australia/Brisbane',
    'stale_balance_days': 14,
    'bas_lookahead_days': 30,
    'accounts': [],
}

AMOUNT_RULES = ('fixed', 'receipts_share', 'bas_formula', 'sinking_hold')
FREQUENCIES = ('once', 'monthly', 'quarterly', 'biannual', 'annual', 'rolling')
OCCURRENCE_STATES = ('upcoming', 'funds_confirmed', 'paid', 'skipped')
FREQUENCY_MONTHS = {'monthly': 1, 'quarterly': 3, 'biannual': 6, 'annual': 12}


def add_months(d: date, months: int) -> date:
    """Move `d` by whole months, clamping to the last day of the target month."""
    index = d.month - 1 + months
    year = d.year + index // 12
    month = index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def roll_weekend(d: date) -> date:
    """ATO-style: a Saturday or Sunday due date falls on the next Monday."""
    if d.weekday() == 5:
        return d + timedelta(days=2)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def month_key(d: date) -> str:
    return f'{d.year:04d}-{d.month:02d}'


def parse_month_key(ym: str) -> date:
    year, month = ym.split('-')
    return date(int(year), int(month), 1)


def _apply_due_rule(standard: date, due_rule: str) -> date:
    return roll_weekend(standard) if due_rule == 'standard' else standard


def generate_occurrences(obligation: dict, start: date, end: date) -> list[dict]:
    """Standard and rolled due dates for `obligation` with standard_date in [start, end].

    `once` needs an anchor date; `rolling` obligations (a floor that is always held) never
    generate occurrences. Monthly and longer frequencies step from the anchor even when the
    anchor is before the window.
    """
    frequency = obligation.get('frequency')
    anchor_raw = obligation.get('anchor_date')
    due_rule = obligation.get('due_rule') or 'standard'
    if frequency == 'rolling' or not anchor_raw:
        return []
    anchor = date.fromisoformat(anchor_raw) if isinstance(anchor_raw, str) else anchor_raw
    if frequency == 'once':
        if start <= anchor <= end:
            return [{'standard_date': anchor, 'due_date': _apply_due_rule(anchor, due_rule)}]
        return []
    step = FREQUENCY_MONTHS[frequency]
    occurrences = []
    k = 0
    while True:
        standard = add_months(anchor, step * k)
        if standard > end:
            break
        if standard >= start:
            occurrences.append({'standard_date': standard, 'due_date': _apply_due_rule(standard, due_rule)})
        k += 1
    return occurrences


BAS_DUE_MONTH_DAY = ((2, 28), (4, 28), (7, 28), (10, 28))


def bas_standard_dates(start: date, end: date) -> list[date]:
    """ATO quarterly BAS standard due dates (28 Feb, 28 Apr, 28 Jul, 28 Oct) within [start, end]."""
    dates = []
    for year in range(start.year, end.year + 1):
        for month, day in BAS_DUE_MONTH_DAY:
            candidate = date(year, month, day)
            if start <= candidate <= end:
                dates.append(candidate)
    return dates


# Due month -> last month of the quarter it covers. The October-December BAS is due at the
# end of February (the ATO's summer extension), every other quarter is due the month after.
BAS_QUARTER_END_MONTH = {10: 9, 2: 12, 4: 3, 7: 6}


def bas_quarter_months(standard_due: date) -> list[str]:
    """The three months a BAS covers, keyed off the ATO due month."""
    end_month = BAS_QUARTER_END_MONTH.get(standard_due.month, standard_due.month - 1 or 12)
    end_year = standard_due.year if end_month < standard_due.month else standard_due.year - 1
    quarter_end = date(end_year, end_month, 1)
    return [month_key(add_months(quarter_end, -k)) for k in (2, 1, 0)]


def _f(settings: dict, key: str) -> float:
    return float(settings.get(key, DEFAULT_SETTINGS[key]))


def receipts_for_month(ym: str, receipts_rows: dict, settings: dict) -> tuple[float, bool]:
    """Reported receipts for the month, else the assumed retainer. Returns (amount, assumed)."""
    if ym in receipts_rows and receipts_rows[ym] is not None:
        return float(receipts_rows[ym]), False
    return _f(settings, 'assumed_monthly_retainer'), True


def monthly_setaside(receipts_incl_gst: float, settings: dict) -> dict:
    """GST at the configured fraction of receipts, income tax at the reserve rate of ex-GST receipts."""
    receipts = float(receipts_incl_gst)
    gst = receipts * _f(settings, 'gst_fraction')
    income_tax = (receipts - gst) * _f(settings, 'income_tax_reserve_rate')
    return {
        'receipts': round(receipts, 2),
        'gst': round(gst, 2),
        'income_tax': round(income_tax, 2),
        'total': round(gst + income_tax, 2),
    }


def bas_estimate(standard_due: date, receipts_rows: dict, settings: dict) -> dict:
    """GST collected less the credit allowance, plus the PAYG instalment and the trust BAS."""
    months = bas_quarter_months(standard_due)
    receipts, assumed_months = {}, []
    for ym in months:
        amount, assumed = receipts_for_month(ym, receipts_rows, settings)
        receipts[ym] = amount
        if assumed:
            assumed_months.append(ym)
    gst_collected = sum(receipts.values()) * _f(settings, 'gst_fraction')
    credits = len(months) * _f(settings, 'gst_credit_allowance_monthly')
    gst_net = max(gst_collected - credits, 0.0)
    payg = _f(settings, 'payg_instalment_quarterly')
    trust = _f(settings, 'sl_trading_trust_bas')
    return {
        'months': months,
        'receipts': receipts,
        'assumed_months': assumed_months,
        'gst_collected': round(gst_collected, 2),
        'credits': round(credits, 2),
        'gst_net': round(gst_net, 2),
        'payg': round(payg, 2),
        'trust': round(trust, 2),
        'total': round(gst_net + payg + trust, 2),
    }


def sinking_accrual(amount: float, cycle_months: int, next_due: date, today: date) -> float:
    """How much of a bill should be saved so far, growing linearly over its cycle."""
    if today >= next_due:
        return round(float(amount), 2)
    previous_due = add_months(next_due, -cycle_months)
    if today <= previous_due:
        return 0.0
    fraction = (today - previous_due).days / (next_due - previous_due).days
    return round(float(amount) * fraction, 2)


def _months_between(start_ym: str, end_ym: str) -> list[str]:
    cursor, end = parse_month_key(start_ym), parse_month_key(end_ym)
    months = []
    while cursor <= end:
        months.append(month_key(cursor))
        cursor = add_months(cursor, 1)
    return months


def _counted_months(months: list[str], receipts_rows: dict, today: date) -> list[str]:
    """Completed months always count; the current month only once receipts are reported."""
    current = month_key(today)
    return [ym for ym in months if ym < current or (ym == current and ym in receipts_rows)]


def income_tax_pot(receipts_rows: dict, settings: dict, today: date, payg_paid: float) -> float:
    """Income-tax reserve accrued since `reserve_start_month`, less PAYG instalments paid from it."""
    start = str(settings.get('reserve_start_month', DEFAULT_SETTINGS['reserve_start_month']))
    total = 0.0
    for ym in _counted_months(_months_between(start, month_key(today)), receipts_rows, today):
        amount, _ = receipts_for_month(ym, receipts_rows, settings)
        total += monthly_setaside(amount, settings)['income_tax']
    return round(max(total - float(payg_paid or 0.0), 0.0), 2)


def gst_accrued_for_quarter(standard_due: date, receipts_rows: dict, settings: dict, today: date) -> float:
    """GST that should already be sitting aside for the BAS due on `standard_due`."""
    total = 0.0
    for ym in _counted_months(bas_quarter_months(standard_due), receipts_rows, today):
        amount, _ = receipts_for_month(ym, receipts_rows, settings)
        total += amount * _f(settings, 'gst_fraction')
    return round(total, 2)


def _next_open_occurrence(obligation_id: int, occurrences: list[dict]) -> dict | None:
    candidates = [
        o for o in occurrences
        if o['obligation_id'] == obligation_id and o.get('state', 'upcoming') in ('upcoming', 'funds_confirmed')
    ]
    return min(candidates, key=lambda o: o['due_date']) if candidates else None


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _is_live(obligation: dict) -> bool:
    return obligation.get('status', 'active') in ('active', 'pending_confirmation')


def household_setaside_lines(obligations: list[dict], occurrences: list[dict], settings: dict) -> list[dict]:
    """Monthly equivalents for every non-monthly fixed household bill with a known amount."""
    lines = []
    for item in obligations:
        cycle = FREQUENCY_MONTHS.get(item.get('frequency'))
        if (
            item.get('amount_rule') != 'fixed' or not cycle or cycle < 3
            or not item.get('amount') or not _is_live(item)
            or item.get('reserve_account') == 'ecomm_gst' or item.get('ownership') == 'business'
        ):
            continue
        lines.append({
            'name': item['name'],
            'reserve_account': item.get('reserve_account'),
            'amount': round(float(item['amount']), 2),
            'cycle_months': cycle,
            'monthly': round(float(item['amount']) / cycle, 2),
        })
    return lines


def account_targets(today: date, obligations: list[dict], occurrences: list[dict],
                    receipts_rows: dict, balances: dict, settings: dict) -> list[dict]:
    """What each reserve account should hold today, against the last known balance."""
    components: dict[str, list[dict]] = {}

    def add(account_key, label, amount):
        if account_key and amount and amount > 0:
            components.setdefault(account_key, []).append({'label': label, 'amount': round(float(amount), 2)})

    payg_paid = 0.0
    for occ in occurrences:
        if occ.get('state') == 'paid' and occ.get('estimate_detail'):
            try:
                payg_paid += float(json.loads(occ['estimate_detail']).get('payg', 0) or 0)
            except (ValueError, TypeError):
                pass

    for item in obligations:
        if not _is_live(item):
            continue
        rule, frequency, key = item.get('amount_rule'), item.get('frequency'), item.get('reserve_account')
        cycle = FREQUENCY_MONTHS.get(frequency)
        if rule == 'sinking_hold' and item.get('amount'):
            add(key, item['name'], item['amount'])
        elif rule == 'fixed' and frequency == 'once' and item.get('amount'):
            already_paid = any(
                o['obligation_id'] == item['id'] and o.get('state') == 'paid' for o in occurrences
            )
            if not already_paid:
                add(key, item['name'], item['amount'])
        elif rule == 'fixed' and cycle and cycle >= 3 and item.get('amount'):
            nxt = _next_open_occurrence(item['id'], occurrences)
            if nxt is not None:
                add(key, item['name'], sinking_accrual(item['amount'], cycle, _as_date(nxt['standard_date']), today))
        elif rule == 'bas_formula':
            nxt = _next_open_occurrence(item['id'], occurrences)
            if nxt is not None:
                standard_due = _as_date(nxt['standard_date'])
                add(key, 'GST accrued this quarter', gst_accrued_for_quarter(standard_due, receipts_rows, settings, today))
                if (standard_due - today).days <= int(settings.get('bas_lookahead_days', 30)):
                    add(key, 'SL Trading Trust BAS', _f(settings, 'sl_trading_trust_bas'))
            add(key, 'Income-tax pot', income_tax_pot(receipts_rows, settings, today, payg_paid))

    display = {a['key']: a.get('display', a['key']) for a in settings.get('accounts', [])}
    order = [a['key'] for a in settings.get('accounts', [])]
    keys = sorted(components, key=lambda k: (order.index(k) if k in order else len(order), k))
    stale_days = int(settings.get('stale_balance_days', 14))
    rows = []
    for key in keys:
        target = round(sum(c['amount'] for c in components[key]), 2)
        snapshot = balances.get(key) or {}
        balance = snapshot.get('balance')
        as_of = snapshot.get('as_of')
        age_days = (today - _as_date(as_of)).days if as_of else None
        rows.append({
            'account_key': key,
            'display': display.get(key, key),
            'target': target,
            'components': components[key],
            'balance': None if balance is None else round(float(balance), 2),
            'available': snapshot.get('available'),
            'as_of': as_of,
            'age_days': age_days,
            'stale': age_days is None or age_days > stale_days,
            'shortfall': None if balance is None else round(target - float(balance), 2),
        })
    return rows


def money(amount) -> str:
    if amount is None:
        return '—'
    value = int(round(float(amount)))
    sign = '-' if value < 0 else ''
    return f'{sign}${abs(value):,}'


def _long_date(d) -> str:
    d = _as_date(d)
    return f'{d.day} {d.strftime("%b %Y")}'


def compose_monthly_setaside(period_label: str, setaside: dict, assumed: bool,
                             home_lines: list[dict], mortgage_amount, settings: dict) -> str:
    receipts_note = (
        f'Eden received about {money(setaside["receipts"])} in {period_label} '
        f'(assumed: the Cheesecake Shop retainer only). Anything else paid in? Reply with the '
        f'amounts, e.g. *eden 5280*, or post a screenshot of Eden\'s transactions.'
        if assumed else
        f'Eden received {money(setaside["receipts"])} in {period_label} (as reported).'
    )
    lines = [f'**{period_label} set-aside**', '', receipts_note, '']
    lines.append(
        f'On what I know, move **{money(setaside["total"])} to EComm GST** '
        f'({money(setaside["gst"])} GST + {money(setaside["income_tax"])} income tax).'
    )
    home_total = round(sum(line['monthly'] for line in home_lines), 2)
    if home_lines:
        parts = ', '.join(f'{money(line["monthly"])} {line["name"]}' for line in home_lines)
        lines.append(f'Move **{money(home_total)} to ING Home** ({parts}).')
    if mortgage_amount:
        lines.append(
            f'The drawings to ING must include the {money(mortgage_amount)} mortgage repayment, '
            f'moved on to GSB Everyday before the 5th.'
        )
    lines += ['', 'Reply *done* when moved.']
    return '\n'.join(lines)


def compose_lead_warning(obligation: dict, occurrence: dict, days_out: int,
                         target_row, today: date) -> str:
    due = _as_date(occurrence['due_date'])
    when = 'today' if days_out == 0 else ('tomorrow' if days_out == 1 else f'in {days_out} days')
    lines = [f'**{obligation["name"]}** is due {_long_date(due)} ({when}).']
    extension_days = int(obligation.get('extension_days') or 0)
    if extension_days:
        extension = _as_date(occurrence['standard_date']) + timedelta(days=extension_days)
        lines[0] += f' Budget to that date; the agent extension to about {_long_date(extension)} is breathing room only.'
    estimate = occurrence.get('estimate')
    detail = None
    if occurrence.get('estimate_detail'):
        try:
            detail = json.loads(occurrence['estimate_detail'])
        except (TypeError, ValueError):
            detail = None
    if detail and obligation.get('amount_rule') == 'bas_formula':
        assumed = detail.get('assumed_months') or []
        assumed_note = f' (retainer assumed for {", ".join(assumed)})' if assumed else ''
        lines.append(
            f'Estimate {money(estimate)}: GST collected {money(detail["gst_collected"])} less credits '
            f'{money(detail["credits"])}, PAYG instalment {money(detail["payg"])}, '
            f'SL Trading Trust {money(detail["trust"])}{assumed_note}.'
        )
    elif estimate is not None:
        lines.append(f'Amount {money(estimate)}.')
    else:
        lines.append('Amount not yet known. Reply with it, e.g. *rego 965*.')
    if target_row and target_row.get('balance') is not None:
        age = target_row.get('age_days')
        age_text = 'from today' if age == 0 else f'{age} days old'
        shortfall = target_row.get('shortfall') or 0.0
        verdict = f'short {money(shortfall)}' if shortfall > 0 else 'covered'
        lines.append(
            f'{target_row["display"]} should hold {money(target_row["target"])}. Last known balance '
            f'{money(target_row["balance"])} ({age_text}), so it is {verdict}.'
        )
        if target_row.get('stale'):
            lines.append('Post a fresh screenshot to update the balance.')
    elif target_row:
        lines.append(
            f'{target_row["display"]} should hold {money(target_row["target"])}, but I have no balance '
            f'for it yet. Post a screenshot or reply with the amount.'
        )
    else:
        lines.append('I have no balance for the paying account yet. Post a screenshot or reply with the amount.')
    lines.append('Reply *paid* once it is paid.')
    return '\n'.join(lines)


def compose_weekly_position(targets: list[dict], upcoming: list[dict], today: date) -> str:
    lines = [f'**Position as at {today.strftime("%A")} {_long_date(today)}**', '']
    if not targets:
        lines.append('No reserve targets yet.')
    for row in targets:
        if row.get('balance') is None:
            status = 'no balance yet'
        else:
            shortfall = row.get('shortfall') or 0.0
            age = row.get('age_days')
            age_text = 'today' if age == 0 else f'{age}d old'
            status = (f'holds {money(row["balance"])} ({age_text}), '
                      + (f'short {money(shortfall)}' if shortfall > 0 else 'covered'))
        lines.append(f'• {row["display"]}: should hold {money(row["target"])}; {status}.')
    if upcoming:
        lines += ['', 'Next 30 days:']
        for item in upcoming:
            lines.append(f'• {_long_date(item["due_date"])} — {item["name"]} {money(item.get("estimate"))}')
    if any(row.get('stale') for row in targets):
        lines += ['', 'Some balances are stale or missing. Post screenshots of the CBA and ING apps to refresh.']
    return '\n'.join(lines)


def compose_bundle(parts: list[str]) -> str:
    return '\n\n---\n\n'.join(part for part in parts if part)
