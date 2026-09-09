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
