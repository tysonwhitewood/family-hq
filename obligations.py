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
