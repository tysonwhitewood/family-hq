# Obligations Engine and One-Way Mattermost Reminders — Implementation Plan (Step 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Family HQ knows every recurring money obligation, works out what each reserve account should hold, and posts the monthly set-aside, bill warnings and a Sunday position to the Family Finance Mattermost channel.

**Architecture:** Three new pure-ish modules beside `app.py`: `obligations.py` (dates, maths, message text; no I/O), `mattermost.py` (REST v4 client over `requests`), `reminders.py` (scheduler + message dedupe, given a `get_db` callable and a client). `app.py` gains tables, seed data, `/api/obligations/*` routes, a forecast hook, and starts the scheduler thread the way `_start_daily_screener` does. `dashboard.html` gains an Obligations page.

**Tech Stack:** Python 3.12, Flask, sqlite3, `requests` (already in requirements), `unittest` (existing test style), vanilla JS in `dashboard.html`.

**Spec:** `docs/superpowers/specs/2026-09-09-obligations-and-mattermost-design.md`

## Global Constraints

- Run tests with `.venv/bin/python -m unittest discover -s tests -q` from the repo root. Baseline: 128 tests pass. Never leave the suite red at a commit.
- Tests import the app with `with patch("threading.Thread.start"): import app as family_app` and isolate the database by pointing `family_app.DB_PATH` at a temp file, then calling `family_app.init_db()`. Copy that pattern exactly.
- Australian English in every user-facing string and doc ("organise", "set-aside", "colour").
- Secrets (`MATTERMOST_URL`, `MATTERMOST_BOT_TOKEN`, `MATTERMOST_CHANNEL_ID`, `MATTERMOST_WEBHOOK_URL`) come from environment variables only. Never write them to `data/config.json` or any committed file.
- Non-secret settings live under `obligations` and `mattermost` keys in `data/config.json`; code falls back to `obligations.DEFAULT_SETTINGS` when a key is absent. The account list is read from config, not hard-coded (empty list if absent).
- Timezone `Australia/Brisbane` via `zoneinfo`. All "today" values are `date` objects computed in that zone.
- No AI in arithmetic. `obligations.py` must not import `anthropic` or call `llm_chat`.
- Do not refactor existing code. Add beside it.
- Amount rules: `fixed`, `receipts_share`, `bas_formula`, `sinking_hold`. Frequencies: `once`, `monthly`, `quarterly`, `biannual`, `annual`, `rolling`. Occurrence states: `upcoming`, `funds_confirmed`, `paid`, `skipped`.
- Commit after every task with a `feat:`/`test:`/`docs:` message ending in the attribution block:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01AjD4c8Kp1UGQPXfTM8jC9X
  ```

## File Structure

| File | Responsibility |
|---|---|
| `obligations.py` (new) | `DEFAULT_SETTINGS`, date helpers, occurrence generation, set-aside and BAS maths, sinking accrual, account targets, message composition. Pure functions on dicts/dates. |
| `mattermost.py` (new) | `MattermostClient` with `ping()`, `post()`; `client_from_env()`. Nothing else knows the Mattermost URL shapes. |
| `reminders.py` (new) | `ReminderService` (regenerate occurrences, decide what is due, dedupe, bundle, send, log) and `run_scheduler_loop`. Takes `get_db`, client, settings, clock. |
| `app.py` (modify) | New tables in `init_db()`, `_seed_obligations()`, `obligation_settings()`, `mattermost_client()`, `reminder_service()`, routes under `/api/obligations` and `/api/mattermost/test`, `_obligation_events()` hooked into `_budget_cash_flow`, `_start_reminder_scheduler()`. |
| `dashboard.html` (modify) | `page-obligations` with three cards and an edit modal, nav entries, `loadObligations()`, Settings Mattermost block. |
| `data/config.json` (modify) | `obligations` and `mattermost` keys with defaults. |
| `README.md` (new) | Project overview stub plus full documentation of the new settings and env vars. |
| `docs/cash-flow-operations.md` (modify) | Operator section for Obligations. |
| `tests/test_obligations.py` (new) | Engine maths and dates. |
| `tests/test_mattermost.py` (new) | Client against a local fake HTTP server. |
| `tests/test_reminders.py` (new) | Service dedupe, bundling, quiet hours, scheduler decisions. |
| `tests/test_obligations_api.py` (new) | Schema, seed, routes, forecast hook. |

---

### Task 1: Schema, settings and seed data

**Files:**
- Modify: `app.py` — `init_db()` (the `executescript` block, currently ending around line 460 with `merchant_rules`), a new `_seed_obligations()` called at the end of `init_db()`, new helpers `obligation_settings()` after `load_config()`.
- Create: `obligations.py` (only `DEFAULT_SETTINGS` for now; later tasks add functions).
- Modify: `data/config.json` — add `obligations` and `mattermost` keys.
- Test: `tests/test_obligations_api.py`

**Interfaces:**
- Produces: tables `obligations`, `obligation_occurrences`, `account_balances`, `receipts_log`, `reminder_log`, `reminder_state`; `family_app.obligation_settings() -> dict`; `family_app._seed_obligations(db, now_iso: str) -> None`; `obligations.DEFAULT_SETTINGS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_obligations_api.py`:

```python
import json
import unittest
import warnings
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

warnings.filterwarnings("ignore", message="Using the in-memory storage for tracking rate limits.*")
warnings.filterwarnings("ignore", message="datetime.datetime.utcnow\\(\\) is deprecated.*")

with patch("threading.Thread.start"):
    import app as family_app


class ObligationsDbCase(unittest.TestCase):
    """Fresh database per test, authenticated client."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({
            "obligations": {
                "accounts": [
                    {"key": "eden_operating", "display": "Eden Commercial", "bank": "CBA", "match": "1027 8937"},
                    {"key": "ecomm_gst", "display": "EComm GST", "bank": "CBA", "match": "1027 8945"},
                    {"key": "ing_home", "display": "ING Home", "bank": "ING", "match": "48305167"},
                    {"key": "ing_emergency", "display": "ING Emergency", "bank": "ING", "match": "46789692"},
                    {"key": "gsb_everyday", "display": "GSB Everyday", "bank": "GSB", "match": "51978620"},
                ]
            }
        }))
        family_app.init_db()
        family_app.app.config.update(TESTING=True)
        self.client = family_app.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = family_app.USERNAME
            session["_fresh"] = True

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()


class SchemaAndSeedTests(ObligationsDbCase):
    def test_obligation_tables_exist_with_required_columns(self):
        with family_app.get_db() as db:
            cols = lambda t: {r["name"] for r in db.execute(f"PRAGMA table_info({t})")}
            self.assertTrue({"name", "ownership", "pay_from_account", "reserve_account", "amount_rule",
                             "amount", "frequency", "anchor_date", "due_rule", "extension_days",
                             "lead_days", "remind", "status", "source", "budget_category"} <= cols("obligations"))
            self.assertTrue({"obligation_id", "due_date", "standard_date", "estimate", "estimate_detail",
                             "actual", "state"} <= cols("obligation_occurrences"))
            self.assertTrue({"account_key", "balance", "available", "as_of", "source", "raw"} <= cols("account_balances"))
            self.assertTrue({"year_month", "amount_incl_gst", "detail"} <= cols("receipts_log"))
            self.assertTrue({"kind", "dedupe_key", "obligation_id", "occurrence_id",
                             "mattermost_post_id", "body", "sent_at"} <= cols("reminder_log"))
            self.assertTrue({"key", "value"} <= cols("reminder_state"))

    def test_seed_creates_obligations_and_opening_balances_once(self):
        with family_app.get_db() as db:
            names = {r["name"] for r in db.execute("SELECT name FROM obligations")}
            balances = db.execute("SELECT COUNT(*) FROM account_balances").fetchone()[0]
            seeded = db.execute("SELECT value FROM reminder_state WHERE key='seeded_v1'").fetchone()
        self.assertIn("Monthly tax reserve transfer", names)
        self.assertIn("Quarterly BAS + PAYG instalment", names)
        self.assertIn("Council rates (Scenic Rim)", names)
        self.assertIn("Mortgage repayment", names)
        self.assertEqual(balances, 9)
        self.assertEqual(seeded["value"], "1")

        family_app.init_db()  # second start-up must not duplicate
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM obligations").fetchone()[0], len(names))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM account_balances").fetchone()[0], 9)

    def test_seeded_bas_obligation_matches_spec(self):
        with family_app.get_db() as db:
            row = db.execute("SELECT * FROM obligations WHERE name='Quarterly BAS + PAYG instalment'").fetchone()
        self.assertEqual(row["amount_rule"], "bas_formula")
        self.assertEqual(row["frequency"], "quarterly")
        self.assertEqual(row["anchor_date"], "2026-10-28")
        self.assertEqual(row["reserve_account"], "ecomm_gst")
        self.assertEqual(json.loads(row["lead_days"]), [30, 7, 1])
        self.assertEqual(row["extension_days"], 28)

    def test_settings_merge_config_over_defaults(self):
        settings = family_app.obligation_settings()
        self.assertEqual(settings["payg_instalment_quarterly"], 3188.0)
        self.assertEqual(settings["timezone"], "Australia/Brisbane")
        self.assertEqual(settings["accounts"][1]["key"], "ecomm_gst")

    def test_settings_without_config_keys_use_defaults_and_empty_accounts(self):
        family_app.CONFIG_PATH.write_text("{}")
        settings = family_app.obligation_settings()
        self.assertEqual(settings["income_tax_reserve_rate"], 0.15)
        self.assertEqual(settings["accounts"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_obligations_api -q`
Expected: FAIL — `no such table: obligations` / `AttributeError: obligation_settings`.

- [ ] **Step 3: Create `obligations.py` with the defaults**

```python
"""Obligations engine for Family HQ.

Due dates, reserve maths and message text for recurring money obligations.
Pure functions over plain dicts and dates: no database, no network, no AI.
"""
from __future__ import annotations

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
```

- [ ] **Step 4: Add the tables to `init_db()`**

In `app.py`, inside the `db.executescript('''...''')` in `init_db()`, after the `merchant_rules` table and before the closing `''')`, add:

```sql
            CREATE TABLE IF NOT EXISTS obligations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                ownership TEXT NOT NULL CHECK (ownership IN ('personal','business')),
                pay_from_account TEXT,
                reserve_account TEXT,
                amount_rule TEXT NOT NULL CHECK (amount_rule IN ('fixed','receipts_share','bas_formula','sinking_hold')),
                amount REAL,
                frequency TEXT NOT NULL CHECK (frequency IN ('once','monthly','quarterly','biannual','annual','rolling')),
                anchor_date TEXT,
                due_rule TEXT NOT NULL DEFAULT 'standard',
                extension_days INTEGER NOT NULL DEFAULT 0,
                lead_days TEXT NOT NULL DEFAULT '[30, 7]',
                remind INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending_confirmation','retired')),
                budget_category TEXT,
                source TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS obligation_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                obligation_id INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
                standard_date TEXT NOT NULL,
                due_date TEXT NOT NULL,
                estimate REAL,
                estimate_detail TEXT,
                actual REAL,
                state TEXT NOT NULL DEFAULT 'upcoming' CHECK (state IN ('upcoming','funds_confirmed','paid','skipped')),
                state_changed_at TEXT,
                state_changed_by TEXT,
                UNIQUE (obligation_id, standard_date)
            );
            CREATE TABLE IF NOT EXISTS account_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_key TEXT NOT NULL,
                balance REAL NOT NULL,
                available REAL,
                as_of TEXT NOT NULL,
                source TEXT NOT NULL,
                mattermost_post_id TEXT,
                raw TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts_log (
                year_month TEXT PRIMARY KEY,
                amount_incl_gst REAL NOT NULL,
                detail TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminder_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE,
                obligation_id INTEGER,
                occurrence_id INTEGER,
                mattermost_post_id TEXT,
                body TEXT NOT NULL,
                sent_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminder_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
```

- [ ] **Step 5: Add the seed and the settings helper**

Immediately after the `executescript` call's enclosing `with get_db() as db:` block finishes its existing seeds (the last existing seed is `upcoming_expenses`, ending around line 646 with the `for desc, amt, due, recurring, cat in upcoming_seed:` loop), add inside the same `with` block, at the same indentation as the other `# Seed ...` comments:

```python
        _seed_obligations(db, datetime.now().isoformat()[:19])
```

Then add these module-level functions directly after `init_db()` (before the `# ── Config` section):

```python
OBLIGATION_SEED = [
    # name, ownership, pay_from, reserve, rule, amount, frequency, anchor, extension_days, lead_days, remind, status, budget_category, source
    ('Monthly tax reserve transfer', 'business', 'eden_operating', 'ecomm_gst', 'receipts_share', None,
     'monthly', '2026-10-01', 0, [0], 1, 'active', None, 'schedule-doc 2026-09-09'),
    ('Quarterly BAS + PAYG instalment', 'business', 'ecomm_gst', 'ecomm_gst', 'bas_formula', None,
     'quarterly', '2026-10-28', 28, [30, 7, 1], 1, 'active', 'Tax/ATO', 'schedule-doc 2026-09-09'),
    ('PropVesting BAS + final return', 'business', 'ecomm_gst', 'ecomm_gst', 'fixed', 7755.88,
     'once', None, 0, [7, 1], 1, 'active', None, 'schedule-doc 2026-09-09'),
    ('ProRisk PI/PL renewal', 'business', 'eden_operating', 'eden_operating', 'fixed', 2505.00,
     'annual', '2026-11-20', 0, [30, 7], 1, 'active', 'Insurance', 'insurances table'),
    ('ASIC annual fee', 'business', 'eden_operating', 'eden_operating', 'fixed', 1798.00,
     'annual', '2027-04-14', 0, [30, 7], 1, 'pending_confirmation', 'Govt Fees (ASIC etc)', 'upcoming_expenses'),
    ('RACQ car insurance', 'business', 'eden_operating', 'eden_operating', 'fixed', 98.41,
     'monthly', '2026-10-03', 0, [], 0, 'active', None, 'statement:Eden Commercial MAIN.csv:2026-08-03'),
    ('Council rates (Scenic Rim)', 'personal', 'ing_home', 'ing_home', 'fixed', 1614.19,
     'biannual', '2027-02-27', 0, [30, 7], 1, 'pending_confirmation', 'Council Rates', 'statement:ING Main.csv:2026-04-26; upcoming_expenses 2026-08-27'),
    ('Water (Urban Utilities)', 'personal', 'ing_home', 'ing_home', 'fixed', 713.45,
     'quarterly', '2026-11-28', 0, [30, 7], 1, 'active', 'Water (Qld Urban Util)', 'statement:ING Main.csv:2026-05-28'),
    ('SMS Insurance', 'personal', 'ing_home', 'ing_home', 'fixed', 2955.00,
     'annual', '2027-03-05', 0, [30, 7], 1, 'pending_confirmation', None, 'statement:Eden Commercial MAIN.csv:2026-03-05'),
    ('RACQ roadside assistance', 'personal', 'ing_home', 'ing_home', 'fixed', 310.00,
     'annual', '2027-07-17', 0, [30, 7], 1, 'active', None, 'statement:Eden Commercial MAIN.csv:2026-07-17'),
    ('Car registration (TMR)', 'personal', 'ing_home', 'ing_home', 'fixed', None,
     'once', None, 0, [30, 7], 1, 'pending_confirmation', None, 'statements: TMR payments 2026-04-28 $964.96, 2026-05-26 $438.74, 2026-05-27 $502.45, 2026-06-20 $334.63'),
    ('Home & contents (RACQ)', 'personal', 'ing_everyday', 'ing_everyday', 'fixed', 165.90,
     'monthly', '2026-09-22', 0, [], 0, 'active', 'Insurance', 'statement:ING Main.csv'),
    ('Electricity (GloBird)', 'personal', 'ing_everyday', 'ing_everyday', 'fixed', 230.00,
     'monthly', '2026-09-13', 0, [], 0, 'active', 'Power (Globird)', 'statement:ING Main.csv average Apr-Jul 2026'),
    ('Mortgage repayment', 'personal', 'gsb_everyday', 'gsb_everyday', 'sinking_hold', 4810.38,
     'monthly', '2026-10-05', 0, [7], 1, 'active', 'Mortgage', 'GSB statement; Tyson 2026-09-09'),
    ('Food / tight-month buffer', 'personal', 'ing_emergency', 'ing_emergency', 'sinking_hold', 3000.00,
     'rolling', None, 0, [], 0, 'active', None, 'schedule-doc 2026-09-09'),
]

OPENING_BALANCES = [
    # account_key, balance, available
    ('eden_operating', 7324.64, 7295.64),
    ('ecomm_gst', 9048.03, 92.03),
    ('cba_utilities', 81.74, 81.74),
    ('ing_everyday', 7779.02, 7102.16),
    ('ing_emergency', 3001.03, 3001.03),
    ('ing_home', 0.41, 0.41),
    ('ing_savings', 3.50, 3.50),
    ('gsb_everyday', 5246.45, 5246.45),
    ('gsb_mortgage', -756265.54, 0.0),
]


def _seed_obligations(db, now):
    """Seed the obligation list and opening balances exactly once."""
    done = db.execute("SELECT value FROM reminder_state WHERE key='seeded_v1'").fetchone()
    if done:
        return
    for (name, ownership, pay_from, reserve, rule, amount, frequency, anchor,
         extension_days, lead_days, remind, status, budget_category, source) in OBLIGATION_SEED:
        db.execute(
            '''INSERT INTO obligations
               (name, ownership, pay_from_account, reserve_account, amount_rule, amount, frequency,
                anchor_date, due_rule, extension_days, lead_days, remind, status, budget_category,
                source, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (name, ownership, pay_from, reserve, rule, amount, frequency, anchor, 'standard',
             extension_days, json.dumps(lead_days), remind, status, budget_category, source, '', now, now),
        )
    for account_key, balance, available in OPENING_BALANCES:
        db.execute(
            '''INSERT INTO account_balances
               (account_key, balance, available, as_of, source, raw, created_at)
               VALUES (?,?,?,?,?,?,?)''',
            (account_key, balance, available, '2026-09-09', 'screenshot',
             'Seeded from screenshots Tyson posted on 2026-09-09', now),
        )
    db.execute(
        "INSERT INTO reminder_state (key, value, updated_at) VALUES ('seeded_v1', '1', ?)", (now,)
    )
```

After `save_config()` in the `# ── Config` section add:

```python
def obligation_settings() -> dict:
    """Engine settings: documented defaults overlaid by the `obligations` key in config.json."""
    import obligations as _obligations
    settings = dict(_obligations.DEFAULT_SETTINGS)
    settings['accounts'] = []
    configured = load_config().get('obligations') or {}
    for key, value in configured.items():
        settings[key] = value
    return settings
```

- [ ] **Step 6: Add the config keys to `data/config.json`**

Add two top-level keys (keep everything else unchanged):

```json
  "obligations": {
    "gst_fraction": 0.0909090909,
    "income_tax_reserve_rate": 0.15,
    "assumed_monthly_retainer": 10083.34,
    "gst_credit_allowance_monthly": 636,
    "payg_instalment_quarterly": 3188,
    "sl_trading_trust_bas": 545,
    "emergency_floor": 3000,
    "mortgage_repayment": 4810.38,
    "reserve_start_month": "2026-09",
    "post_hour_local": 7,
    "weekly_day": 6,
    "weekly_hour_local": 17,
    "quiet_hours": [21, 7],
    "timezone": "Australia/Brisbane",
    "stale_balance_days": 14,
    "bas_lookahead_days": 30,
    "accounts": [
      {"key": "eden_operating", "display": "Eden Commercial", "bank": "CBA", "match": "1027 8937"},
      {"key": "ecomm_gst", "display": "EComm GST", "bank": "CBA", "match": "1027 8945"},
      {"key": "cba_utilities", "display": "Utilities / Vehicles", "bank": "CBA", "match": "2645 1922"},
      {"key": "ing_everyday", "display": "ING Orange Everyday", "bank": "ING", "match": "65683967"},
      {"key": "ing_emergency", "display": "ING Emergency", "bank": "ING", "match": "46789692"},
      {"key": "ing_home", "display": "ING Home", "bank": "ING", "match": "48305167"},
      {"key": "ing_savings", "display": "ING Savings", "bank": "ING", "match": "804020739"},
      {"key": "gsb_everyday", "display": "GSB Everyday", "bank": "GSB", "match": "51978620"},
      {"key": "gsb_mortgage", "display": "GSB Basic Variable Inv P&I", "bank": "GSB", "match": "51991707", "loan": true}
    ]
  },
  "mattermost": {
    "enabled": true,
    "allowed_users": ["tyson", "robyn"]
  }
```

Use `python3 -c "import json; json.load(open('data/config.json'))"` to confirm the file still parses.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_obligations_api -q`
Expected: 5 tests PASS. Then run the whole suite: `.venv/bin/python -m unittest discover -s tests -q` — expected 133 pass.

- [ ] **Step 8: Commit**

```bash
git add app.py obligations.py data/config.json tests/test_obligations_api.py
git commit -m "feat: obligations schema, settings and seed data"
```

---

### Task 2: Due dates and occurrence generation

**Files:**
- Modify: `obligations.py`
- Test: `tests/test_obligations.py`

**Interfaces:**
- Produces:
  - `add_months(d: date, months: int) -> date`
  - `roll_weekend(d: date) -> date`
  - `month_key(d: date) -> str` (`'2026-09'`)
  - `parse_month_key(ym: str) -> date` (first of month)
  - `generate_occurrences(obligation: dict, start: date, end: date) -> list[dict]` where each dict is `{'standard_date': date, 'due_date': date}`; `obligation` has keys `frequency`, `anchor_date` (ISO string or None), `due_rule`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_obligations.py`:

```python
import unittest
from datetime import date

import obligations as ob


class DateHelperTests(unittest.TestCase):
    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(ob.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(ob.add_months(date(2026, 11, 30), 3), date(2027, 2, 28))
        self.assertEqual(ob.add_months(date(2026, 10, 28), -3), date(2026, 7, 28))

    def test_roll_weekend_moves_saturday_and_sunday_to_monday(self):
        self.assertEqual(ob.roll_weekend(date(2027, 2, 28)), date(2027, 3, 1))   # Sunday
        self.assertEqual(ob.roll_weekend(date(2026, 11, 28)), date(2026, 11, 30))  # Saturday
        self.assertEqual(ob.roll_weekend(date(2026, 10, 28)), date(2026, 10, 28))  # Wednesday

    def test_month_key_round_trip(self):
        self.assertEqual(ob.month_key(date(2026, 9, 9)), "2026-09")
        self.assertEqual(ob.parse_month_key("2026-09"), date(2026, 9, 1))


class OccurrenceGenerationTests(unittest.TestCase):
    def test_quarterly_bas_dates_roll_the_february_sunday(self):
        bas = {"frequency": "quarterly", "anchor_date": "2026-10-28", "due_rule": "standard"}
        occurrences = ob.generate_occurrences(bas, date(2026, 9, 9), date(2027, 9, 9))
        self.assertEqual(
            [o["standard_date"] for o in occurrences],
            [date(2026, 10, 28), date(2027, 1, 28), date(2027, 4, 28), date(2027, 7, 28)],
        )

    def test_quarterly_from_february_anchor_rolls_to_monday(self):
        q2 = {"frequency": "quarterly", "anchor_date": "2027-02-28", "due_rule": "standard"}
        occurrences = ob.generate_occurrences(q2, date(2027, 1, 1), date(2027, 3, 31))
        self.assertEqual(occurrences[0]["standard_date"], date(2027, 2, 28))
        self.assertEqual(occurrences[0]["due_date"], date(2027, 3, 1))

    def test_due_rule_none_keeps_weekend_dates(self):
        item = {"frequency": "annual", "anchor_date": "2026-11-28", "due_rule": "none"}
        occurrences = ob.generate_occurrences(item, date(2026, 9, 1), date(2026, 12, 31))
        self.assertEqual(occurrences[0]["due_date"], date(2026, 11, 28))

    def test_once_without_anchor_and_rolling_generate_nothing(self):
        self.assertEqual(ob.generate_occurrences({"frequency": "once", "anchor_date": None, "due_rule": "standard"},
                                                 date(2026, 9, 1), date(2027, 9, 1)), [])
        self.assertEqual(ob.generate_occurrences({"frequency": "rolling", "anchor_date": None, "due_rule": "standard"},
                                                 date(2026, 9, 1), date(2027, 9, 1)), [])

    def test_once_with_anchor_inside_window_generates_one(self):
        occurrences = ob.generate_occurrences({"frequency": "once", "anchor_date": "2026-11-20", "due_rule": "standard"},
                                              date(2026, 9, 1), date(2027, 9, 1))
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["due_date"], date(2026, 11, 20))

    def test_monthly_includes_anchor_before_window_start(self):
        monthly = {"frequency": "monthly", "anchor_date": "2026-03-05", "due_rule": "standard"}
        occurrences = ob.generate_occurrences(monthly, date(2026, 9, 9), date(2026, 12, 31))
        self.assertEqual([o["standard_date"] for o in occurrences],
                         [date(2026, 10, 5), date(2026, 11, 5), date(2026, 12, 5)])


if __name__ == "__main__":
    unittest.main()
```

Note the anchor for the BAS is the standard date; the quarterly step from 28 Oct gives 28 Jan, 28 Apr, 28 Jul. The Q2 BAS in the spec is "28 Feb 2027" because the ATO's Q2 due date is 28 February; the seed's anchor of 28 Oct steps to 28 Jan, which is wrong for Q2. Handle this in Task 3 with a BAS-specific schedule (see `bas_standard_dates`); `generate_occurrences` stays generic.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_obligations -q`
Expected: FAIL with `AttributeError: module 'obligations' has no attribute 'add_months'`.

- [ ] **Step 3: Implement the date helpers and generator**

Append to `obligations.py`:

```python
import calendar
from datetime import date, timedelta


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
```

Move the `import calendar` / `from datetime import ...` lines to the top of the file under `from __future__ import annotations` (imports belong at the top; the `DEFAULT_SETTINGS` block stays after them).

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_obligations -q`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add obligations.py tests/test_obligations.py
git commit -m "feat: obligation due dates and occurrence generation"
```

---

### Task 3: BAS schedule and the money maths

**Files:**
- Modify: `obligations.py`
- Test: `tests/test_obligations.py`

**Interfaces:**
- Produces:
  - `bas_standard_dates(start: date, end: date) -> list[date]` — 28 Oct, 28 Feb, 28 Apr, 28 Jul each year in range.
  - `bas_quarter_months(standard_due: date) -> list[str]` — three month keys before the due month.
  - `receipts_for_month(ym: str, receipts_rows: dict[str, float], settings) -> tuple[float, bool]` — `(amount, assumed)`.
  - `monthly_setaside(receipts_incl_gst: float, settings) -> dict` with `receipts, gst, income_tax, total`.
  - `bas_estimate(standard_due: date, receipts_rows, settings) -> dict` with `months, receipts, assumed_months, gst_collected, credits, gst_net, payg, trust, total`.
  - `sinking_accrual(amount: float, cycle_months: int, next_due: date, today: date) -> float`.
  - `income_tax_pot(receipts_rows, settings, today: date, payg_paid: float) -> float`.
  - `gst_accrued_for_quarter(standard_due: date, receipts_rows, settings, today: date) -> float`.
  - `household_setaside_lines(obligations: list[dict], occurrences: list[dict], settings) -> list[dict]` with `name, monthly, amount, cycle_months` for non-monthly fixed household items.
  - `account_targets(today: date, obligations, occurrences, receipts_rows, balances: dict, settings) -> list[dict]` with `account_key, display, target, components: list[{label, amount}], balance, available, as_of, age_days, shortfall, stale`.

`receipts_rows` is `{'2026-07': 40587.0, ...}`. `balances` is `{'ecomm_gst': {'balance': 9048.03, 'available': 92.03, 'as_of': '2026-09-09'}, ...}`. `obligations`/`occurrences` are lists of dicts shaped like the DB rows (occurrence has `obligation_id`, `standard_date`, `due_date` as ISO strings, `state`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_obligations.py` (before `if __name__`):

```python
SETTINGS = dict(ob.DEFAULT_SETTINGS)
SETTINGS["accounts"] = [
    {"key": "eden_operating", "display": "Eden Commercial"},
    {"key": "ecomm_gst", "display": "EComm GST"},
    {"key": "ing_home", "display": "ING Home"},
    {"key": "ing_emergency", "display": "ING Emergency"},
    {"key": "gsb_everyday", "display": "GSB Everyday"},
]


class BasScheduleTests(unittest.TestCase):
    def test_bas_standard_dates_follow_the_ato_calendar(self):
        self.assertEqual(
            ob.bas_standard_dates(date(2026, 9, 9), date(2027, 9, 9)),
            [date(2026, 10, 28), date(2027, 2, 28), date(2027, 4, 28), date(2027, 7, 28)],
        )

    def test_bas_quarter_months_are_the_three_before_the_due_month(self):
        self.assertEqual(ob.bas_quarter_months(date(2026, 10, 28)), ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(ob.bas_quarter_months(date(2027, 2, 28)), ["2026-10", "2026-11", "2026-12"])


class MoneyMathsTests(unittest.TestCase):
    def test_monthly_setaside_on_retainer_only_month(self):
        result = ob.monthly_setaside(10083.34, SETTINGS)
        self.assertAlmostEqual(result["gst"], 916.67, places=2)
        self.assertAlmostEqual(result["income_tax"], 1375.00, places=2)
        self.assertAlmostEqual(result["total"], 2291.67, places=2)

    def test_receipts_for_month_uses_reported_then_assumed(self):
        self.assertEqual(ob.receipts_for_month("2026-07", {"2026-07": 40587.0}, SETTINGS), (40587.0, False))
        self.assertEqual(ob.receipts_for_month("2026-08", {"2026-07": 40587.0}, SETTINGS), (10083.34, True))

    def test_bas_estimate_for_q1_with_reported_july(self):
        estimate = ob.bas_estimate(date(2026, 10, 28), {"2026-07": 40587.0, "2026-09": 15363.34}, SETTINGS)
        self.assertEqual(estimate["assumed_months"], ["2026-08"])
        total_receipts = 40587.0 + 10083.34 + 15363.34
        self.assertAlmostEqual(estimate["gst_collected"], total_receipts / 11, places=2)
        self.assertAlmostEqual(estimate["credits"], 3 * 636.0, places=2)
        self.assertAlmostEqual(estimate["payg"], 3188.0)
        self.assertAlmostEqual(estimate["trust"], 545.0)
        self.assertAlmostEqual(estimate["total"], estimate["gst_net"] + 3188.0 + 545.0, places=2)
        self.assertGreater(estimate["total"], 6000)

    def test_bas_estimate_never_returns_negative_gst(self):
        estimate = ob.bas_estimate(date(2026, 10, 28), {"2026-07": 0.0, "2026-08": 0.0, "2026-09": 0.0}, SETTINGS)
        self.assertEqual(estimate["gst_net"], 0.0)

    def test_sinking_accrual_grows_linearly_and_caps(self):
        next_due = date(2027, 2, 27)
        self.assertEqual(ob.sinking_accrual(1614.19, 6, next_due, date(2026, 8, 27)), 0.0)
        mid = ob.sinking_accrual(1614.19, 6, next_due, date(2026, 11, 27))
        self.assertTrue(780 < mid < 830, mid)
        self.assertEqual(ob.sinking_accrual(1614.19, 6, next_due, date(2027, 3, 1)), 1614.19)

    def test_income_tax_pot_accrues_from_reserve_start_and_nets_payg(self):
        rows = {"2026-09": 15363.34, "2026-10": 10083.34}
        pot = ob.income_tax_pot(rows, SETTINGS, date(2026, 11, 3), payg_paid=0.0)
        expected = sum(ob.monthly_setaside(v, SETTINGS)["income_tax"] for v in rows.values())
        self.assertAlmostEqual(pot, expected, places=2)
        self.assertEqual(ob.income_tax_pot(rows, SETTINGS, date(2026, 11, 3), payg_paid=10000.0), 0.0)

    def test_income_tax_pot_skips_the_current_month_unless_reported(self):
        pot_unreported = ob.income_tax_pot({}, SETTINGS, date(2026, 9, 20), payg_paid=0.0)
        self.assertEqual(pot_unreported, 0.0)
        pot_reported = ob.income_tax_pot({"2026-09": 5280.0}, SETTINGS, date(2026, 9, 20), payg_paid=0.0)
        self.assertAlmostEqual(pot_reported, ob.monthly_setaside(5280.0, SETTINGS)["income_tax"], places=2)

    def test_gst_accrued_for_quarter_counts_completed_or_reported_months(self):
        accrued = ob.gst_accrued_for_quarter(date(2026, 10, 28), {"2026-07": 40587.0}, SETTINGS, date(2026, 9, 9))
        self.assertAlmostEqual(accrued, (40587.0 + 10083.34) / 11, places=2)


class AccountTargetTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 9, 9)
        self.obligations = [
            {"id": 1, "name": "Quarterly BAS + PAYG instalment", "reserve_account": "ecomm_gst", "amount_rule": "bas_formula",
             "amount": None, "frequency": "quarterly", "status": "active", "remind": 1},
            {"id": 2, "name": "PropVesting BAS + final return", "reserve_account": "ecomm_gst", "amount_rule": "fixed",
             "amount": 7755.88, "frequency": "once", "status": "active", "remind": 1},
            {"id": 3, "name": "Council rates (Scenic Rim)", "reserve_account": "ing_home", "amount_rule": "fixed",
             "amount": 1614.19, "frequency": "biannual", "status": "pending_confirmation", "remind": 1},
            {"id": 4, "name": "Water (Urban Utilities)", "reserve_account": "ing_home", "amount_rule": "fixed",
             "amount": 713.45, "frequency": "quarterly", "status": "active", "remind": 1},
            {"id": 5, "name": "Food / tight-month buffer", "reserve_account": "ing_emergency", "amount_rule": "sinking_hold",
             "amount": 3000.0, "frequency": "rolling", "status": "active", "remind": 0},
            {"id": 6, "name": "Mortgage repayment", "reserve_account": "gsb_everyday", "amount_rule": "sinking_hold",
             "amount": 4810.38, "frequency": "monthly", "status": "active", "remind": 1},
            {"id": 7, "name": "Home & contents (RACQ)", "reserve_account": "ing_everyday", "amount_rule": "fixed",
             "amount": 165.90, "frequency": "monthly", "status": "active", "remind": 0},
        ]
        self.occurrences = [
            {"id": 10, "obligation_id": 1, "standard_date": "2026-10-28", "due_date": "2026-10-28", "state": "upcoming"},
            {"id": 11, "obligation_id": 3, "standard_date": "2027-02-27", "due_date": "2027-03-01", "state": "upcoming"},
            {"id": 12, "obligation_id": 4, "standard_date": "2026-11-28", "due_date": "2026-11-30", "state": "upcoming"},
            {"id": 13, "obligation_id": 6, "standard_date": "2026-10-05", "due_date": "2026-10-05", "state": "upcoming"},
        ]
        self.receipts = {"2026-07": 40587.0, "2026-09": 5280.0}
        self.balances = {
            "ecomm_gst": {"balance": 9048.03, "available": 92.03, "as_of": "2026-09-09"},
            "ing_home": {"balance": 0.41, "available": 0.41, "as_of": "2026-08-20"},
            "ing_emergency": {"balance": 3001.03, "available": 3001.03, "as_of": "2026-09-09"},
        }

    def test_ecomm_gst_target_is_hold_plus_gst_accrued_plus_pot(self):
        rows = {t["account_key"]: t for t in ob.account_targets(
            self.today, self.obligations, self.occurrences, self.receipts, self.balances, SETTINGS)}
        gst = rows["ecomm_gst"]
        labels = [c["label"] for c in gst["components"]]
        self.assertIn("PropVesting BAS + final return", labels)
        self.assertIn("GST accrued this quarter", labels)
        self.assertIn("Income-tax pot", labels)
        expected = 7755.88 + ob.gst_accrued_for_quarter(date(2026, 10, 28), self.receipts, SETTINGS, self.today) \
            + ob.income_tax_pot(self.receipts, SETTINGS, self.today, 0.0)
        self.assertAlmostEqual(gst["target"], round(expected, 2), places=2)
        self.assertAlmostEqual(gst["shortfall"], round(expected - 9048.03, 2), places=2)
        self.assertEqual(gst["age_days"], 0)
        self.assertFalse(gst["stale"])

    def test_trust_bas_is_added_within_the_lookahead_window(self):
        rows = {t["account_key"]: t for t in ob.account_targets(
            date(2026, 10, 10), self.obligations, self.occurrences, self.receipts, self.balances, SETTINGS)}
        labels = [c["label"] for c in rows["ecomm_gst"]["components"]]
        self.assertIn("SL Trading Trust BAS", labels)

    def test_ing_home_is_a_sinking_fund_of_non_monthly_bills_and_flags_stale_balance(self):
        rows = {t["account_key"]: t for t in ob.account_targets(
            self.today, self.obligations, self.occurrences, self.receipts, self.balances, SETTINGS)}
        home = rows["ing_home"]
        expected = ob.sinking_accrual(1614.19, 6, date(2027, 2, 27), self.today) \
            + ob.sinking_accrual(713.45, 3, date(2026, 11, 28), self.today)
        self.assertAlmostEqual(home["target"], round(expected, 2), places=2)
        self.assertEqual(home["age_days"], 20)
        self.assertTrue(home["stale"])

    def test_holds_are_always_the_full_amount_and_missing_balance_gives_no_shortfall(self):
        rows = {t["account_key"]: t for t in ob.account_targets(
            self.today, self.obligations, self.occurrences, self.receipts, self.balances, SETTINGS)}
        self.assertEqual(rows["ing_emergency"]["target"], 3000.0)
        self.assertAlmostEqual(rows["ing_emergency"]["shortfall"], -1.03, places=2)
        self.assertEqual(rows["gsb_everyday"]["target"], 4810.38)
        self.assertIsNone(rows["gsb_everyday"]["balance"])
        self.assertIsNone(rows["gsb_everyday"]["shortfall"])

    def test_monthly_direct_debits_do_not_create_a_reserve_target(self):
        keys = [t["account_key"] for t in ob.account_targets(
            self.today, self.obligations, self.occurrences, self.receipts, self.balances, SETTINGS)]
        self.assertNotIn("ing_everyday", keys)

    def test_household_setaside_lines_use_monthly_equivalents(self):
        lines = ob.household_setaside_lines(self.obligations, self.occurrences, SETTINGS)
        by_name = {l["name"]: l for l in lines}
        self.assertAlmostEqual(by_name["Council rates (Scenic Rim)"]["monthly"], 1614.19 / 6, places=2)
        self.assertAlmostEqual(by_name["Water (Urban Utilities)"]["monthly"], 713.45 / 3, places=2)
        self.assertNotIn("Home & contents (RACQ)", by_name)
        self.assertNotIn("Mortgage repayment", by_name)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_obligations -q`
Expected: FAIL with `AttributeError ... bas_standard_dates`.

- [ ] **Step 3: Implement the maths**

Append to `obligations.py`:

```python
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


def bas_quarter_months(standard_due: date) -> list[str]:
    """The three months a BAS covers: the quarter ending the month before the due month."""
    first_of_due_month = standard_due.replace(day=1)
    return [month_key(add_months(first_of_due_month, -k)) for k in (3, 2, 1)]


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
                import json as _json
                payg_paid += float(_json.loads(occ['estimate_detail']).get('payg', 0) or 0)
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_obligations -q`
Expected: all PASS (9 from Task 2 + 17 new = 26).

- [ ] **Step 5: Commit**

```bash
git add obligations.py tests/test_obligations.py
git commit -m "feat: BAS schedule, set-aside maths and reserve account targets"
```

---

### Task 4: Message composition

**Files:**
- Modify: `obligations.py`
- Test: `tests/test_obligations.py`

**Interfaces:**
- Produces:
  - `money(amount: float | None) -> str` — `'$2,292'`, `'-$1'`, `'—'` for None.
  - `compose_monthly_setaside(period_label: str, setaside: dict, assumed: bool, home_lines: list[dict], mortgage_amount: float | None, settings) -> str`
  - `compose_lead_warning(obligation: dict, occurrence: dict, days_out: int, target_row: dict | None, today: date) -> str` — `occurrence` carries `due_date`, `standard_date`, `estimate`, `estimate_detail` (JSON string or None).
  - `compose_weekly_position(targets: list[dict], upcoming: list[dict], today: date) -> str` — `upcoming` items have `name`, `due_date`, `estimate`.
  - `compose_bundle(parts: list[str]) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_obligations.py`:

```python
import json as _json


class MessageTests(unittest.TestCase):
    def test_money_formats_whole_dollars(self):
        self.assertEqual(ob.money(2291.67), "$2,292")
        self.assertEqual(ob.money(-1.03), "-$1")
        self.assertEqual(ob.money(None), "—")

    def test_monthly_setaside_message_names_both_transfers(self):
        setaside = ob.monthly_setaside(10083.34, SETTINGS)
        home_lines = [
            {"name": "Council rates (Scenic Rim)", "monthly": 269.03, "amount": 1614.19, "cycle_months": 6},
            {"name": "Water (Urban Utilities)", "monthly": 237.82, "amount": 713.45, "cycle_months": 3},
        ]
        text = ob.compose_monthly_setaside("September 2026", setaside, True, home_lines, 4810.38, SETTINGS)
        self.assertIn("September 2026", text)
        self.assertIn("$10,083", text)
        self.assertIn("assumed", text.lower())
        self.assertIn("**$2,292 to EComm GST**", text)
        self.assertIn("$917 GST", text)
        self.assertIn("$1,375 income tax", text)
        self.assertIn("**$507 to ING Home**", text)
        self.assertIn("$4,810", text)  # mortgage inside the drawings
        self.assertIn("Reply *done*", text)

    def test_lead_warning_shows_estimate_extension_and_shortfall(self):
        obligation = {"name": "Quarterly BAS + PAYG instalment", "amount_rule": "bas_formula",
                      "extension_days": 28, "reserve_account": "ecomm_gst"}
        detail = ob.bas_estimate(date(2026, 10, 28), {"2026-07": 40587.0}, SETTINGS)
        occurrence = {"due_date": "2026-10-28", "standard_date": "2026-10-28",
                      "estimate": detail["total"], "estimate_detail": _json.dumps(detail)}
        target = {"display": "EComm GST", "target": 15000.0, "balance": 9048.03, "as_of": "2026-09-09",
                  "age_days": 19, "stale": True, "shortfall": 5951.97}
        text = ob.compose_lead_warning(obligation, occurrence, 30, target, date(2026, 9, 28))
        self.assertIn("due 28 Oct 2026", text)
        self.assertIn("agent extension to about 25 Nov 2026", text)
        self.assertIn(ob.money(detail["total"]), text)
        self.assertIn("GST collected", text)
        self.assertIn("PAYG instalment $3,188", text)
        self.assertIn("EComm GST should hold $15,000", text)
        self.assertIn("$9,048", text)
        self.assertIn("19 days old", text)
        self.assertIn("short $5,952", text)
        self.assertIn("screenshot", text.lower())

    def test_lead_warning_without_balance_asks_for_one(self):
        obligation = {"name": "Water (Urban Utilities)", "amount_rule": "fixed", "extension_days": 0,
                      "reserve_account": "ing_home"}
        occurrence = {"due_date": "2026-11-30", "standard_date": "2026-11-28", "estimate": 713.45, "estimate_detail": None}
        text = ob.compose_lead_warning(obligation, occurrence, 7, None, date(2026, 11, 23))
        self.assertIn("Water (Urban Utilities)", text)
        self.assertIn("$713", text)
        self.assertNotIn("extension", text)
        self.assertIn("no balance", text.lower())

    def test_weekly_position_lists_every_account_and_next_bills(self):
        targets = [
            {"display": "EComm GST", "target": 15000.0, "balance": 9048.03, "age_days": 3, "stale": False, "shortfall": 5951.97},
            {"display": "ING Home", "target": 507.0, "balance": None, "age_days": None, "stale": True, "shortfall": None},
        ]
        upcoming = [{"name": "Quarterly BAS + PAYG instalment", "due_date": "2026-10-28", "estimate": 6900.0}]
        text = ob.compose_weekly_position(targets, upcoming, date(2026, 9, 13))
        self.assertIn("Sunday 13 Sep 2026", text)
        self.assertIn("EComm GST", text)
        self.assertIn("short $5,952", text)
        self.assertIn("ING Home", text)
        self.assertIn("no balance yet", text.lower())
        self.assertIn("28 Oct", text)
        self.assertIn("screenshot", text.lower())

    def test_bundle_joins_with_rule(self):
        self.assertEqual(ob.compose_bundle(["a", "b"]), "a\n\n---\n\nb")
        self.assertEqual(ob.compose_bundle(["only"]), "only")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_obligations.MessageTests -q`
Expected: FAIL with `AttributeError ... money`.

- [ ] **Step 3: Implement composition**

Append to `obligations.py`:

```python
import json


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
```

Move `import json` to the top of the file with the other imports. In `account_targets`, replace the inline `import json as _json` with the module-level `json`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_obligations -q`
Expected: 32 PASS. If the `$507` assertion fails on rounding, check `home_total` sums 269.03 + 237.82 = 506.85 → `money` gives `$507`.

- [ ] **Step 5: Commit**

```bash
git add obligations.py tests/test_obligations.py
git commit -m "feat: compose set-aside, warning and weekly position messages"
```

---

### Task 5: Mattermost client

**Files:**
- Create: `mattermost.py`
- Test: `tests/test_mattermost.py`

**Interfaces:**
- Produces:
  - `class MattermostError(Exception)`
  - `class MattermostClient(base_url: str, token: str | None = None, channel_id: str | None = None, webhook_url: str | None = None, timeout: int = 10)` with `can_read -> bool`, `can_post -> bool`, `ping() -> bool`, `post(message: str) -> str | None` (post id via bot API, `None` via webhook).
  - `client_from_env(environ=os.environ) -> MattermostClient | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mattermost.py`:

```python
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mattermost


class FakeMattermost(BaseHTTPRequestHandler):
    requests = []
    fail_next = False

    def log_message(self, *args):
        pass

    def _read(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        FakeMattermost.requests.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/api/v4/system/ping":
            return self._reply(200, {"status": "OK"})
        return self._reply(404, {"message": "not found"})

    def do_POST(self):
        body = self._read()
        FakeMattermost.requests.append(("POST", self.path, dict(self.headers), body))
        if FakeMattermost.fail_next:
            FakeMattermost.fail_next = False
            return self._reply(500, {"message": "boom"})
        if self.path == "/api/v4/posts":
            return self._reply(201, {"id": "post123", "channel_id": body["channel_id"], "message": body["message"]})
        if self.path == "/hooks/abc":
            return self._reply(200, {"status": "ok"})
        return self._reply(404, {"message": "not found"})


class MattermostClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMattermost)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeMattermost.requests = []
        FakeMattermost.fail_next = False

    def test_ping_hits_system_ping(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        self.assertTrue(client.ping())
        self.assertEqual(FakeMattermost.requests[0][1], "/api/v4/system/ping")

    def test_post_with_bot_token_returns_post_id_and_sends_bearer(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        post_id = client.post("hello")
        self.assertEqual(post_id, "post123")
        method, path, headers, body = FakeMattermost.requests[0]
        self.assertEqual((method, path), ("POST", "/api/v4/posts"))
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(body, {"channel_id": "chan", "message": "hello"})

    def test_post_falls_back_to_webhook_without_token(self):
        client = mattermost.MattermostClient(self.base, webhook_url=f"{self.base}/hooks/abc")
        self.assertIsNone(client.post("hello"))
        method, path, _, body = FakeMattermost.requests[0]
        self.assertEqual((method, path), ("POST", "/hooks/abc"))
        self.assertEqual(body, {"text": "hello"})
        self.assertFalse(client.can_read)
        self.assertTrue(client.can_post)

    def test_server_error_raises_mattermost_error(self):
        FakeMattermost.fail_next = True
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        with self.assertRaises(mattermost.MattermostError):
            client.post("hello")

    def test_client_without_any_credentials_cannot_post(self):
        client = mattermost.MattermostClient(self.base)
        self.assertFalse(client.can_post)
        with self.assertRaises(mattermost.MattermostError):
            client.post("hello")

    def test_client_from_env_requires_url_and_either_token_or_webhook(self):
        self.assertIsNone(mattermost.client_from_env({}))
        self.assertIsNone(mattermost.client_from_env({"MATTERMOST_URL": self.base}))
        bot = mattermost.client_from_env({"MATTERMOST_URL": self.base + "/", "MATTERMOST_BOT_TOKEN": "t",
                                          "MATTERMOST_CHANNEL_ID": "c"})
        self.assertEqual(bot.base_url, self.base)
        self.assertTrue(bot.can_read)
        hook = mattermost.client_from_env({"MATTERMOST_URL": self.base, "MATTERMOST_WEBHOOK_URL": self.base + "/hooks/abc"})
        self.assertTrue(hook.can_post)
        self.assertFalse(hook.can_read)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_mattermost -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mattermost'`.

- [ ] **Step 3: Implement the client**

Create `mattermost.py`:

```python
"""Thin Mattermost REST v4 client for Family HQ.

Posts as a bot account when a token and channel are configured, otherwise through an
incoming webhook. Reading the channel (step 2) needs the bot token.
"""
from __future__ import annotations

import os

import requests


class MattermostError(Exception):
    """Raised when Mattermost rejects a request or is unreachable."""


class MattermostClient:
    def __init__(self, base_url: str, token: str | None = None, channel_id: str | None = None,
                 webhook_url: str | None = None, timeout: int = 10):
        self.base_url = base_url.rstrip('/')
        self.token = token or None
        self.channel_id = channel_id or None
        self.webhook_url = webhook_url or None
        self.timeout = timeout

    @property
    def can_read(self) -> bool:
        return bool(self.token and self.channel_id)

    @property
    def can_post(self) -> bool:
        return self.can_read or bool(self.webhook_url)

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}

    def _request(self, method: str, url: str, **kwargs):
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise MattermostError(f'Mattermost unreachable: {exc}') from exc
        if response.status_code >= 400:
            raise MattermostError(f'Mattermost {response.status_code} for {method} {url}: {response.text[:200]}')
        return response

    def ping(self) -> bool:
        try:
            self._request('GET', f'{self.base_url}/api/v4/system/ping')
            return True
        except MattermostError:
            return False

    def post(self, message: str) -> str | None:
        """Post `message` to the channel. Returns the post id (bot) or None (webhook)."""
        if self.can_read:
            response = self._request(
                'POST', f'{self.base_url}/api/v4/posts', headers=self._headers(),
                json={'channel_id': self.channel_id, 'message': message},
            )
            return response.json().get('id')
        if self.webhook_url:
            self._request('POST', self.webhook_url, json={'text': message})
            return None
        raise MattermostError('No Mattermost bot token or webhook configured')


def client_from_env(environ=os.environ) -> MattermostClient | None:
    """Build a client from MATTERMOST_* variables, or None when sending is not configured."""
    url = (environ.get('MATTERMOST_URL') or '').strip()
    token = (environ.get('MATTERMOST_BOT_TOKEN') or '').strip()
    channel_id = (environ.get('MATTERMOST_CHANNEL_ID') or '').strip()
    webhook = (environ.get('MATTERMOST_WEBHOOK_URL') or '').strip()
    if not url:
        return None
    client = MattermostClient(url, token=token, channel_id=channel_id, webhook_url=webhook)
    return client if client.can_post else None
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_mattermost -q`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add mattermost.py tests/test_mattermost.py
git commit -m "feat: Mattermost client with bot and webhook posting"
```

---

### Task 6: Reminder service and scheduler

**Files:**
- Create: `reminders.py`
- Test: `tests/test_reminders.py`

**Interfaces:**
- Consumes: `obligations.*` from Tasks 2–4; `mattermost.MattermostClient.post`; `family_app.get_db` (any zero-arg callable returning a `sqlite3.Connection` with `row_factory = sqlite3.Row`).
- Produces:
  - `class ReminderService(get_db, client, settings, now_fn=None)` with `now() -> datetime` (tz-aware), `today() -> date`, `get_state(key) -> str | None`, `set_state(key, value)`, `load(today) -> dict` (`obligations`, `occurrences`, `receipts_rows`, `balances`), `regenerate_occurrences(today=None) -> int`, `pending_daily_messages(today) -> list[dict]`, `run_daily(today=None, dry_run=False) -> dict`, `run_weekly(today=None, dry_run=False) -> dict`, `in_quiet_hours(now) -> bool`, `position(today=None) -> dict` (`targets`, `upcoming`).
  - `scheduler_tick(service, now) -> list[str]` and `run_scheduler_loop(service_factory, sleep=time.sleep, log=print)`.
  - Result dict of `run_*`: `{'sent': [dedupe_keys], 'skipped': [dedupe_keys already sent], 'body': str | None, 'post_id': str | None, 'reason': str | None}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reminders.py`:

```python
import json
import unittest
import warnings
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore", message="Using the in-memory storage for tracking rate limits.*")
warnings.filterwarnings("ignore", message="datetime.datetime.utcnow\\(\\) is deprecated.*")

with patch("threading.Thread.start"):
    import app as family_app

import reminders

BRISBANE = ZoneInfo("Australia/Brisbane")


class FakeClient:
    def __init__(self, fail=False):
        self.posts = []
        self.fail = fail
        self.can_post = True

    def post(self, message):
        if self.fail:
            import mattermost
            raise mattermost.MattermostError("down")
        self.posts.append(message)
        return f"post{len(self.posts)}"


class ReminderCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({"obligations": {"accounts": [
            {"key": "eden_operating", "display": "Eden Commercial"},
            {"key": "ecomm_gst", "display": "EComm GST"},
            {"key": "ing_home", "display": "ING Home"},
            {"key": "ing_emergency", "display": "ING Emergency"},
            {"key": "gsb_everyday", "display": "GSB Everyday"},
        ]}}))
        family_app.init_db()
        self.client = FakeClient()
        self.settings = family_app.obligation_settings()

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    def service(self, now):
        return reminders.ReminderService(family_app.get_db, self.client, self.settings, now_fn=lambda: now)

    def at(self, y, m, d, hour=7):
        return datetime(y, m, d, hour, 0, tzinfo=BRISBANE)


class OccurrenceRegenerationTests(ReminderCase):
    def test_bas_occurrences_follow_ato_dates_with_estimates(self):
        svc = self.service(self.at(2026, 9, 9))
        created = svc.regenerate_occurrences()
        self.assertGreater(created, 0)
        with family_app.get_db() as db:
            rows = db.execute(
                "SELECT o.standard_date, o.due_date, o.estimate, o.estimate_detail FROM obligation_occurrences o "
                "JOIN obligations b ON b.id = o.obligation_id WHERE b.amount_rule='bas_formula' ORDER BY o.standard_date"
            ).fetchall()
        self.assertEqual([r["standard_date"] for r in rows][:4], ["2026-10-28", "2027-02-28", "2027-04-28", "2027-07-28"])
        self.assertEqual(rows[1]["due_date"], "2027-03-01")
        self.assertGreater(rows[0]["estimate"], 3188 + 545)
        self.assertEqual(json.loads(rows[0]["estimate_detail"])["months"], ["2026-07", "2026-08", "2026-09"])

    def test_regeneration_is_idempotent_and_fixed_items_carry_their_amount(self):
        svc = self.service(self.at(2026, 9, 9))
        svc.regenerate_occurrences()
        again = svc.regenerate_occurrences()
        self.assertEqual(again, 0)
        with family_app.get_db() as db:
            water = db.execute(
                "SELECT o.due_date, o.estimate FROM obligation_occurrences o JOIN obligations b ON b.id=o.obligation_id "
                "WHERE b.name='Water (Urban Utilities)' ORDER BY o.standard_date LIMIT 1"
            ).fetchone()
            rolling = db.execute(
                "SELECT COUNT(*) FROM obligation_occurrences o JOIN obligations b ON b.id=o.obligation_id "
                "WHERE b.frequency='rolling'"
            ).fetchone()[0]
        self.assertEqual(water["due_date"], "2026-11-30")
        self.assertEqual(water["estimate"], 713.45)
        self.assertEqual(rolling, 0)


class DailyRunTests(ReminderCase):
    def test_first_of_month_sends_the_setaside_once(self):
        svc = self.service(self.at(2026, 10, 1))
        result = svc.run_daily()
        self.assertIn("monthly_setaside:2026-09", result["sent"])
        self.assertEqual(len(self.client.posts), 1)
        self.assertIn("September 2026 set-aside", self.client.posts[0])
        self.assertIn("EComm GST", self.client.posts[0])
        second = svc.run_daily()
        self.assertEqual(second["sent"], [])
        self.assertIn("monthly_setaside:2026-09", second["skipped"])
        self.assertEqual(len(self.client.posts), 1)

    def test_lead_warning_fires_at_thirty_and_seven_days_and_catches_up_missed_days(self):
        svc = self.service(self.at(2026, 9, 28))  # 30 days before 28 Oct
        result = svc.run_daily()
        self.assertTrue(any(k.startswith("lead_warning:occ:") and k.endswith(":30") for k in result["sent"]), result)
        self.assertIn("Quarterly BAS + PAYG instalment", self.client.posts[-1])
        self.assertIn("EComm GST should hold", self.client.posts[-1])

        svc = self.service(self.at(2026, 10, 3))  # 25 days out: 30-day key already sent, nothing new
        self.assertEqual([k for k in svc.run_daily()["sent"] if "lead_warning" in k], [])

        svc = self.service(self.at(2026, 10, 23))  # 5 days out: missed the 7-day mark, still sends it once
        result = svc.run_daily()
        self.assertTrue(any(k.endswith(":7") for k in result["sent"]), result)
        self.assertEqual([k for k in svc.run_daily()["sent"] if "lead_warning" in k], [])

    def test_receipts_share_obligation_does_not_double_post_on_the_first(self):
        svc = self.service(self.at(2026, 10, 1))
        svc.run_daily()
        self.assertEqual(len(self.client.posts), 1)
        self.assertNotIn("Monthly tax reserve transfer** is due", self.client.posts[0])

    def test_several_items_are_bundled_into_one_post(self):
        svc = self.service(self.at(2026, 10, 1))
        with family_app.get_db() as db:
            db.execute("UPDATE obligations SET anchor_date='2026-10-31' WHERE name='ProRisk PI/PL renewal'")
        result = svc.run_daily()
        self.assertGreaterEqual(len(result["sent"]), 2)
        self.assertEqual(len(self.client.posts), 1)
        self.assertIn("---", self.client.posts[0])
        with family_app.get_db() as db:
            post_ids = {r["mattermost_post_id"] for r in db.execute("SELECT mattermost_post_id FROM reminder_log")}
        self.assertEqual(post_ids, {"post1"})

    def test_dry_run_returns_body_without_sending_or_logging(self):
        svc = self.service(self.at(2026, 10, 1))
        result = svc.run_daily(dry_run=True)
        self.assertIn("September 2026 set-aside", result["body"])
        self.assertEqual(self.client.posts, [])
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0], 0)

    def test_quiet_hours_block_sending(self):
        svc = self.service(self.at(2026, 10, 1, hour=22))
        result = svc.run_daily()
        self.assertEqual(result["sent"], [])
        self.assertEqual(result["reason"], "quiet hours")
        self.assertEqual(self.client.posts, [])

    def test_mattermost_failure_logs_nothing_so_it_retries(self):
        self.client.fail = True
        svc = self.service(self.at(2026, 10, 1))
        result = svc.run_daily()
        self.assertEqual(result["sent"], [])
        self.assertIn("Mattermost", result["reason"])
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0], 0)

    def test_paid_occurrence_gets_no_more_warnings(self):
        svc = self.service(self.at(2026, 9, 28))
        svc.regenerate_occurrences()
        with family_app.get_db() as db:
            db.execute("UPDATE obligation_occurrences SET state='paid' WHERE standard_date='2026-10-28'")
            bas_id = db.execute("SELECT id FROM obligation_occurrences WHERE standard_date='2026-10-28'").fetchone()["id"]
        result = svc.run_daily()
        self.assertEqual([k for k in result["sent"] if k.startswith(f"lead_warning:occ:{bas_id}:")], [])


class WeeklyRunTests(ReminderCase):
    def test_weekly_position_posts_once_per_sunday(self):
        svc = self.service(self.at(2026, 9, 13, hour=17))
        result = svc.run_weekly()
        self.assertEqual(result["sent"], ["weekly_position:2026-09-13"])
        self.assertIn("EComm GST", self.client.posts[0])
        self.assertIn("ING Emergency", self.client.posts[0])
        self.assertEqual(svc.run_weekly()["sent"], [])

    def test_position_exposes_targets_and_upcoming(self):
        svc = self.service(self.at(2026, 9, 9))
        position = svc.position()
        keys = {t["account_key"] for t in position["targets"]}
        self.assertTrue({"ecomm_gst", "ing_home", "ing_emergency", "gsb_everyday"} <= keys)
        self.assertTrue(all("name" in u and "due_date" in u for u in position["upcoming"]))


class SchedulerTickTests(ReminderCase):
    def test_tick_runs_daily_once_after_post_hour_and_weekly_on_sunday(self):
        svc = self.service(self.at(2026, 9, 13, hour=6))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), [])
        svc = self.service(self.at(2026, 9, 13, hour=7, ))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["daily"])
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), [])
        svc = self.service(self.at(2026, 9, 13, hour=17))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["weekly"])
        svc = self.service(self.at(2026, 9, 14, hour=17))  # Monday: daily (new day) but no weekly
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["daily"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_reminders -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'reminders'`.

- [ ] **Step 3: Implement the service**

Create `reminders.py`:

```python
"""Scheduled money reminders for Family HQ.

Decides what to say each day, never says the same thing twice, bundles a day's items into
one Mattermost post, and records everything in `reminder_log`. Arithmetic lives in
`obligations.py`; network calls live in `mattermost.py`.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import obligations as ob
from mattermost import MattermostError

REGENERATE_BACK_DAYS = 35
REGENERATE_FORWARD_DAYS = 400
UPCOMING_WINDOW_DAYS = 30
POSITION_WINDOW_DAYS = 90


def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


class ReminderService:
    def __init__(self, get_db, client, settings: dict, now_fn=None):
        self.get_db = get_db
        self.client = client
        self.settings = settings
        self.tz = ZoneInfo(settings.get('timezone', ob.DEFAULT_SETTINGS['timezone']))
        self._now_fn = now_fn

    # ── clock ────────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self._now_fn() if self._now_fn else datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def in_quiet_hours(self, now: datetime) -> bool:
        start, end = self.settings.get('quiet_hours', ob.DEFAULT_SETTINGS['quiet_hours'])
        hour = now.hour
        return hour >= start or hour < end if start > end else start <= hour < end

    # ── state ────────────────────────────────────────────────────────────────
    def get_state(self, key: str):
        with self.get_db() as db:
            row = db.execute('SELECT value FROM reminder_state WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None

    def set_state(self, key: str, value: str):
        with self.get_db() as db:
            db.execute(
                'INSERT INTO reminder_state (key, value, updated_at) VALUES (?,?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                (key, value, self.now().isoformat()[:19]),
            )

    # ── data ─────────────────────────────────────────────────────────────────
    def load(self, today: date | None = None) -> dict:
        with self.get_db() as db:
            obligations = _rows(db.execute("SELECT * FROM obligations WHERE status != 'retired'"))
            occurrences = _rows(db.execute('SELECT * FROM obligation_occurrences ORDER BY due_date'))
            receipts_rows = {
                r['year_month']: r['amount_incl_gst']
                for r in db.execute('SELECT year_month, amount_incl_gst FROM receipts_log')
            }
            balances = {}
            for r in db.execute(
                'SELECT account_key, balance, available, as_of FROM account_balances '
                'ORDER BY as_of ASC, id ASC'
            ):
                balances[r['account_key']] = {
                    'balance': r['balance'], 'available': r['available'], 'as_of': r['as_of'],
                }
        return {
            'obligations': obligations, 'occurrences': occurrences,
            'receipts_rows': receipts_rows, 'balances': balances,
        }

    # ── occurrences ──────────────────────────────────────────────────────────
    def regenerate_occurrences(self, today: date | None = None) -> int:
        """Insert any missing occurrences in the window and refresh open estimates."""
        today = today or self.today()
        start = today - timedelta(days=REGENERATE_BACK_DAYS)
        end = today + timedelta(days=REGENERATE_FORWARD_DAYS)
        data = self.load(today)
        created = 0
        with self.get_db() as db:
            for item in data['obligations']:
                if item['amount_rule'] == 'bas_formula':
                    pairs = [{'standard_date': d, 'due_date': ob.roll_weekend(d)} for d in ob.bas_standard_dates(start, end)]
                else:
                    pairs = ob.generate_occurrences(item, start, end)
                for pair in pairs:
                    cursor = db.execute(
                        'INSERT OR IGNORE INTO obligation_occurrences (obligation_id, standard_date, due_date, state) '
                        "VALUES (?,?,?,'upcoming')",
                        (item['id'], pair['standard_date'].isoformat(), pair['due_date'].isoformat()),
                    )
                    created += cursor.rowcount
            # refresh estimates on everything not yet paid or skipped
            for item in data['obligations']:
                open_rows = db.execute(
                    "SELECT id, standard_date FROM obligation_occurrences "
                    "WHERE obligation_id=? AND state IN ('upcoming','funds_confirmed')",
                    (item['id'],),
                ).fetchall()
                for row in open_rows:
                    if item['amount_rule'] == 'bas_formula':
                        detail = ob.bas_estimate(date.fromisoformat(row['standard_date']), data['receipts_rows'], self.settings)
                        db.execute('UPDATE obligation_occurrences SET estimate=?, estimate_detail=? WHERE id=?',
                                   (detail['total'], json.dumps(detail), row['id']))
                    elif item['amount_rule'] in ('fixed', 'sinking_hold') and item.get('amount') is not None:
                        db.execute('UPDATE obligation_occurrences SET estimate=? WHERE id=?',
                                   (float(item['amount']), row['id']))
        return created

    # ── what to say ──────────────────────────────────────────────────────────
    def _sent_keys(self) -> set[str]:
        with self.get_db() as db:
            return {r['dedupe_key'] for r in db.execute('SELECT dedupe_key FROM reminder_log')}

    def position(self, today: date | None = None) -> dict:
        today = today or self.today()
        data = self.load(today)
        targets = ob.account_targets(today, data['obligations'], data['occurrences'],
                                     data['receipts_rows'], data['balances'], self.settings)
        by_id = {o['id']: o for o in data['obligations']}
        upcoming = []
        for occ in data['occurrences']:
            item = by_id.get(occ['obligation_id'])
            due = date.fromisoformat(occ['due_date'])
            if not item or occ['state'] in ('paid', 'skipped') or due < today or (due - today).days > POSITION_WINDOW_DAYS:
                continue
            if item['amount_rule'] in ('receipts_share',) or not item.get('remind'):
                continue
            upcoming.append({
                'occurrence_id': occ['id'], 'obligation_id': item['id'], 'name': item['name'],
                'due_date': occ['due_date'], 'standard_date': occ['standard_date'], 'estimate': occ['estimate'],
                'state': occ['state'], 'reserve_account': item.get('reserve_account'),
                'days_out': (due - today).days,
            })
        return {'today': today.isoformat(), 'targets': targets, 'upcoming': upcoming}

    def pending_daily_messages(self, today: date) -> list[dict]:
        data = self.load(today)
        messages = []
        if today.day == 1:
            previous = ob.add_months(today.replace(day=1), -1)
            ym = ob.month_key(previous)
            receipts, assumed = ob.receipts_for_month(ym, data['receipts_rows'], self.settings)
            setaside = ob.monthly_setaside(receipts, self.settings)
            home_lines = ob.household_setaside_lines(data['obligations'], data['occurrences'], self.settings)
            mortgage = next((o['amount'] for o in data['obligations']
                             if o['amount_rule'] == 'sinking_hold' and o['reserve_account'] == 'gsb_everyday'
                             and o['status'] != 'retired'), None)
            messages.append({
                'kind': 'monthly_setaside', 'dedupe_key': f'monthly_setaside:{ym}',
                'obligation_id': None, 'occurrence_id': None,
                'body': ob.compose_monthly_setaside(previous.strftime('%B %Y'), setaside, assumed,
                                                    home_lines, mortgage, self.settings),
            })
        targets = {t['account_key']: t for t in ob.account_targets(
            today, data['obligations'], data['occurrences'], data['receipts_rows'], data['balances'], self.settings)}
        by_id = {o['id']: o for o in data['obligations']}
        sent = self._sent_keys()
        for occ in data['occurrences']:
            item = by_id.get(occ['obligation_id'])
            if not item or not item.get('remind') or item['amount_rule'] == 'receipts_share':
                continue
            if occ['state'] in ('paid', 'skipped'):
                continue
            lead_days = json.loads(item.get('lead_days') or '[]')
            if not lead_days:
                continue
            days_out = (date.fromisoformat(occ['due_date']) - today).days
            eligible = [lead for lead in lead_days if days_out <= lead]
            if days_out < 0 or not eligible:
                continue
            lead = min(eligible)
            key = f'lead_warning:occ:{occ["id"]}:{lead}'
            if key in sent:
                continue
            messages.append({
                'kind': 'lead_warning', 'dedupe_key': key,
                'obligation_id': item['id'], 'occurrence_id': occ['id'],
                'body': ob.compose_lead_warning(item, occ, days_out, targets.get(item.get('reserve_account')), today),
            })
        return messages

    # ── sending ──────────────────────────────────────────────────────────────
    def _deliver(self, messages: list[dict], dry_run: bool) -> dict:
        sent_keys = self._sent_keys()
        fresh = [m for m in messages if m['dedupe_key'] not in sent_keys]
        skipped = [m['dedupe_key'] for m in messages if m['dedupe_key'] in sent_keys]
        result = {'sent': [], 'skipped': skipped, 'body': None, 'post_id': None, 'reason': None}
        if not fresh:
            result['reason'] = 'nothing new'
            return result
        body = ob.compose_bundle([m['body'] for m in fresh])
        result['body'] = body
        if dry_run:
            result['reason'] = 'dry run'
            return result
        if self.in_quiet_hours(self.now()):
            result['reason'] = 'quiet hours'
            return result
        if self.client is None or not getattr(self.client, 'can_post', False):
            result['reason'] = 'Mattermost not configured'
            return result
        try:
            post_id = self.client.post(body)
        except MattermostError as exc:
            result['reason'] = f'Mattermost error: {exc}'
            return result
        now = self.now().isoformat()[:19]
        with self.get_db() as db:
            for m in fresh:
                db.execute(
                    'INSERT OR IGNORE INTO reminder_log (kind, dedupe_key, obligation_id, occurrence_id, '
                    'mattermost_post_id, body, sent_at) VALUES (?,?,?,?,?,?,?)',
                    (m['kind'], m['dedupe_key'], m['obligation_id'], m['occurrence_id'], post_id, m['body'], now),
                )
        result['sent'] = [m['dedupe_key'] for m in fresh]
        result['post_id'] = post_id
        return result

    def run_daily(self, today: date | None = None, dry_run: bool = False) -> dict:
        today = today or self.today()
        self.regenerate_occurrences(today)
        return self._deliver(self.pending_daily_messages(today), dry_run)

    def run_weekly(self, today: date | None = None, dry_run: bool = False) -> dict:
        today = today or self.today()
        self.regenerate_occurrences(today)
        position = self.position(today)
        upcoming = [u for u in position['upcoming'] if u['days_out'] <= UPCOMING_WINDOW_DAYS]
        message = {
            'kind': 'weekly_position', 'dedupe_key': f'weekly_position:{today.isoformat()}',
            'obligation_id': None, 'occurrence_id': None,
            'body': ob.compose_weekly_position(position['targets'], upcoming, today),
        }
        return self._deliver([message], dry_run)


def scheduler_tick(service: ReminderService, now: datetime) -> list[str]:
    """Run whichever jobs are due at `now`; each job runs at most once per local day."""
    ran = []
    today = now.date().isoformat()
    settings = service.settings
    post_hour = int(settings.get('post_hour_local', ob.DEFAULT_SETTINGS['post_hour_local']))
    weekly_day = int(settings.get('weekly_day', ob.DEFAULT_SETTINGS['weekly_day']))
    weekly_hour = int(settings.get('weekly_hour_local', ob.DEFAULT_SETTINGS['weekly_hour_local']))
    if now.hour >= post_hour and service.get_state('last_daily_run') != today:
        service.run_daily(now.date())
        service.set_state('last_daily_run', today)
        ran.append('daily')
    if now.weekday() == weekly_day and now.hour >= weekly_hour and service.get_state('last_weekly_run') != today:
        service.run_weekly(now.date())
        service.set_state('last_weekly_run', today)
        ran.append('weekly')
    return ran


def run_scheduler_loop(service_factory, sleep=time.sleep, log=print):
    """Daemon loop: build a fresh service each minute and run what is due. Never raises."""
    while True:
        try:
            service = service_factory()
            if service is not None:
                ran = scheduler_tick(service, service.now())
                if ran:
                    log(f'[reminders] ran {", ".join(ran)}', flush=True)
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            log(f'[reminders] tick failed: {exc}', flush=True)
        sleep(60)
```

Note on `in_quiet_hours`: with `quiet_hours = [21, 7]`, hours 21–23 and 0–6 are quiet. Daily runs at 7 are allowed. Wall-clock quiet hours are checked against `self.now()`, which is why the quiet-hours test passes `hour=22` to `now_fn`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_reminders -q`
Expected: 13 PASS. If `test_lead_warning_fires...` fails on the 30-day step, check that `regenerate_occurrences` inserted the 28 Oct row (the window is today − 35 days to today + 400 days) and that `lead_days` for the BAS row is `[30, 7, 1]`.

- [ ] **Step 5: Run the whole suite and commit**

Run: `.venv/bin/python -m unittest discover -s tests -q` — expected 128 + 5 + 32 + 6 + 13 = 184 pass.

```bash
git add reminders.py tests/test_reminders.py
git commit -m "feat: reminder service with dedupe, bundling and scheduler tick"
```

---

### Task 7: API routes, Mattermost test endpoint and scheduler start-up

**Files:**
- Modify: `app.py` — imports at top; new section `# ── Obligations & reminders` placed after the `# ── Paper Trading & Screener` routes and before `_start_daily_screener`; one line after `_start_daily_screener()` near the bottom.
- Test: `tests/test_obligations_api.py`

**Interfaces:**
- Consumes: `reminders.ReminderService`, `mattermost.client_from_env`, `obligations.AMOUNT_RULES/FREQUENCIES/OCCURRENCE_STATES`.
- Produces: `family_app.mattermost_client() -> MattermostClient | None`, `family_app.reminder_service() -> ReminderService`, routes listed below, `family_app._start_reminder_scheduler()`.

Routes (all `@login_required`):

| Route | Body / query | Returns |
|---|---|---|
| `GET /api/obligations` | — | `{obligations: [row + next_occurrence], accounts, settings: {payg_instalment_quarterly, income_tax_reserve_rate, assumed_monthly_retainer}}` |
| `POST /api/obligations` | obligation fields, optional `id` | `{ok, id}`; regenerates occurrences |
| `DELETE /api/obligations/<id>` | — | `{ok}`; sets `status='retired'` |
| `GET /api/obligations/position` | optional `today=YYYY-MM-DD` | `ReminderService.position()` |
| `GET /api/obligations/log` | optional `limit` (default 30, max 200) | `{log: [...]}` newest first |
| `POST /api/obligations/occurrences/<id>/state` | `{state}` | `{ok}` |
| `POST /api/obligations/receipts` | `{year_month, amount, note?}` | `{ok}` (upsert) |
| `POST /api/obligations/balances` | `{account_key, balance, available?, as_of?}` | `{ok, id}` |
| `POST /api/obligations/run` | `{job: 'daily'|'weekly', dry_run?: bool, today?: 'YYYY-MM-DD'}` | run result dict |
| `GET /api/mattermost/status` | — | `{configured, can_read, can_post, reachable}` |
| `POST /api/mattermost/test` | — | `{ok, post_id}` or 503 |

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_obligations_api.py` (before `if __name__`):

```python
class ObligationRoutesTests(ObligationsDbCase):
    def test_list_returns_seed_with_next_occurrence_and_accounts(self):
        r = self.client.get("/api/obligations")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        bas = next(o for o in data["obligations"] if o["name"] == "Quarterly BAS + PAYG instalment")
        self.assertEqual(bas["next_occurrence"]["due_date"], "2026-10-28")
        self.assertEqual(bas["lead_days"], [30, 7, 1])
        self.assertEqual(data["accounts"][0]["key"], "eden_operating")
        self.assertEqual(data["settings"]["payg_instalment_quarterly"], 3188.0)

    def test_create_validates_and_generates_occurrences(self):
        bad = self.client.post("/api/obligations", json={"name": "", "amount_rule": "fixed"})
        self.assertEqual(bad.status_code, 400)
        bad_rule = self.client.post("/api/obligations", json={"name": "X", "ownership": "personal",
                                                              "amount_rule": "magic", "frequency": "annual"})
        self.assertEqual(bad_rule.status_code, 400)
        bad_account = self.client.post("/api/obligations", json={
            "name": "X", "ownership": "personal", "amount_rule": "fixed", "amount": 10,
            "frequency": "annual", "anchor_date": "2026-12-01", "reserve_account": "nope"})
        self.assertEqual(bad_account.status_code, 400)

        ok = self.client.post("/api/obligations", json={
            "name": "Car registration (Tiggo)", "ownership": "personal", "amount_rule": "fixed",
            "amount": 964.96, "frequency": "annual", "anchor_date": "2027-04-28",
            "pay_from_account": "ing_home", "reserve_account": "ing_home", "lead_days": [30, 7],
            "remind": True, "status": "active", "source": "typed"})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        oid = ok.get_json()["id"]
        with family_app.get_db() as db:
            occ = db.execute("SELECT due_date, estimate FROM obligation_occurrences WHERE obligation_id=?", (oid,)).fetchall()
        self.assertEqual([o["due_date"] for o in occ], ["2027-04-28"])
        self.assertEqual(occ[0]["estimate"], 964.96)

    def test_update_replaces_open_occurrences_and_delete_retires(self):
        with family_app.get_db() as db:
            water = db.execute("SELECT id FROM obligations WHERE name='Water (Urban Utilities)'").fetchone()["id"]
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        r = self.client.post("/api/obligations", json={
            "id": water, "name": "Water (Urban Utilities)", "ownership": "personal", "amount_rule": "fixed",
            "amount": 750.0, "frequency": "quarterly", "anchor_date": "2026-12-05",
            "pay_from_account": "ing_home", "reserve_account": "ing_home", "lead_days": [30, 7],
            "remind": True, "status": "active"})
        self.assertEqual(r.status_code, 200, r.get_json())
        with family_app.get_db() as db:
            dates = [o["due_date"] for o in db.execute(
                "SELECT due_date FROM obligation_occurrences WHERE obligation_id=? AND state='upcoming' ORDER BY due_date", (water,))]
        self.assertEqual(dates[0], "2026-12-07")  # 5 Dec 2026 is a Saturday
        self.assertNotIn("2026-11-30", dates)

        d = self.client.delete(f"/api/obligations/{water}")
        self.assertEqual(d.status_code, 200)
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM obligations WHERE id=?", (water,)).fetchone()["status"], "retired")
        names = [o["name"] for o in self.client.get("/api/obligations").get_json()["obligations"]]
        self.assertNotIn("Water (Urban Utilities)", names)

    def test_position_log_state_receipts_and_balances(self):
        position = self.client.get("/api/obligations/position?today=2026-09-09").get_json()
        keys = {t["account_key"] for t in position["targets"]}
        self.assertIn("ecomm_gst", keys)
        gst = next(t for t in position["targets"] if t["account_key"] == "ecomm_gst")
        self.assertEqual(gst["balance"], 9048.03)

        occ_id = position["upcoming"][0]["occurrence_id"]
        bad = self.client.post(f"/api/obligations/occurrences/{occ_id}/state", json={"state": "lost"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(f"/api/obligations/occurrences/{occ_id}/state", json={"state": "paid"})
        self.assertEqual(ok.status_code, 200)
        with family_app.get_db() as db:
            row = db.execute("SELECT state, state_changed_by FROM obligation_occurrences WHERE id=?", (occ_id,)).fetchone()
        self.assertEqual((row["state"], row["state_changed_by"]), ("paid", "app"))

        self.assertEqual(self.client.post("/api/obligations/receipts", json={"year_month": "2026-9", "amount": 1}).status_code, 400)
        self.assertEqual(self.client.post("/api/obligations/receipts", json={"year_month": "2026-09", "amount": 15363.34}).status_code, 200)
        self.assertEqual(self.client.post("/api/obligations/receipts", json={"year_month": "2026-09", "amount": 16000}).status_code, 200)
        with family_app.get_db() as db:
            rows = db.execute("SELECT amount_incl_gst FROM receipts_log WHERE year_month='2026-09'").fetchall()
        self.assertEqual([r["amount_incl_gst"] for r in rows], [16000.0])

        self.assertEqual(self.client.post("/api/obligations/balances", json={"account_key": "nope", "balance": 1}).status_code, 400)
        ok = self.client.post("/api/obligations/balances", json={"account_key": "ing_home", "balance": 780.0})
        self.assertEqual(ok.status_code, 200)
        position = self.client.get("/api/obligations/position?today=2026-09-09").get_json()
        home = next(t for t in position["targets"] if t["account_key"] == "ing_home")
        self.assertEqual(home["balance"], 780.0)

        log = self.client.get("/api/obligations/log").get_json()["log"]
        self.assertEqual(log, [])

    def test_run_dry_run_returns_body_without_mattermost(self):
        with patch.object(family_app, "mattermost_client", return_value=None):
            r = self.client.post("/api/obligations/run", json={"job": "daily", "dry_run": True, "today": "2026-10-01"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("September 2026 set-aside", r.get_json()["body"])
        bad = self.client.post("/api/obligations/run", json={"job": "hourly"})
        self.assertEqual(bad.status_code, 400)

    def test_run_without_dry_run_reports_missing_mattermost(self):
        with patch.object(family_app, "mattermost_client", return_value=None):
            r = self.client.post("/api/obligations/run", json={"job": "weekly", "today": "2026-09-13"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["reason"], "Mattermost not configured")

    def test_mattermost_status_and_test_message(self):
        with patch.object(family_app, "mattermost_client", return_value=None):
            status = self.client.get("/api/mattermost/status").get_json()
            self.assertEqual(status, {"configured": False, "can_read": False, "can_post": False, "reachable": False})
            r = self.client.post("/api/mattermost/test")
            self.assertEqual(r.status_code, 503)

        class Fake:
            can_read, can_post = True, True
            def ping(self): return True
            def post(self, message): self.message = message; return "p1"
        fake = Fake()
        with patch.object(family_app, "mattermost_client", return_value=fake):
            status = self.client.get("/api/mattermost/status").get_json()
            self.assertTrue(status["configured"] and status["reachable"])
            r = self.client.post("/api/mattermost/test")
        self.assertEqual(r.get_json(), {"ok": True, "post_id": "p1"})
        self.assertIn("Family HQ", fake.message)

    def test_routes_require_login(self):
        anonymous = family_app.app.test_client()
        self.assertIn(anonymous.get("/api/obligations").status_code, (302, 401))
        self.assertIn(anonymous.post("/api/obligations/run", json={"job": "daily"}).status_code, (302, 401))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_obligations_api -q`
Expected: the 8 new tests FAIL (404s / AttributeError on `reminder_service`).

- [ ] **Step 3: Implement**

At the top of `app.py`, after `import openpyxl`, add:

```python
import mattermost
import obligations as ob
import reminders
```

Add the new section before `def _start_daily_screener():`:

```python
# ── Obligations & reminders ──────────────────────────────────────────────────

def mattermost_client():
    """Mattermost client from environment variables, or None when disabled/unconfigured."""
    cfg = load_config().get('mattermost') or {}
    if cfg.get('enabled') is False:
        return None
    return mattermost.client_from_env()


def reminder_service():
    return reminders.ReminderService(get_db, mattermost_client(), obligation_settings())


def _account_keys() -> set[str]:
    return {a['key'] for a in obligation_settings().get('accounts', [])}


def _validate_obligation(data: dict) -> tuple[dict | None, str | None]:
    name = str(data.get('name') or '').strip()
    if not name:
        return None, 'name is required'
    ownership = data.get('ownership', 'personal')
    if ownership not in ('personal', 'business'):
        return None, 'ownership must be personal or business'
    amount_rule = data.get('amount_rule', 'fixed')
    if amount_rule not in ob.AMOUNT_RULES:
        return None, 'amount_rule must be one of: ' + ', '.join(ob.AMOUNT_RULES)
    frequency = data.get('frequency', 'once')
    if frequency not in ob.FREQUENCIES:
        return None, 'frequency must be one of: ' + ', '.join(ob.FREQUENCIES)
    amount = data.get('amount')
    if amount in ('', None):
        amount = None
    else:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return None, 'amount must be a number'
        if not math.isfinite(amount) or amount < 0:
            return None, 'amount must be a non-negative number'
    anchor = data.get('anchor_date') or None
    if anchor:
        try:
            date.fromisoformat(anchor)
        except (TypeError, ValueError):
            return None, 'anchor_date must be an ISO date'
    keys = _account_keys()
    for field in ('pay_from_account', 'reserve_account'):
        value = data.get(field) or None
        if value and keys and value not in keys:
            return None, f'{field} must be a configured account key'
    lead_days = data.get('lead_days', [30, 7])
    if not isinstance(lead_days, list) or not all(isinstance(x, int) and x >= 0 for x in lead_days):
        return None, 'lead_days must be a list of non-negative whole numbers'
    status = data.get('status', 'active')
    if status not in ('active', 'pending_confirmation', 'retired'):
        return None, 'status must be active, pending_confirmation or retired'
    due_rule = data.get('due_rule', 'standard')
    if due_rule not in ('standard', 'none'):
        return None, 'due_rule must be standard or none'
    try:
        extension_days = int(data.get('extension_days') or 0)
    except (TypeError, ValueError):
        return None, 'extension_days must be a whole number'
    return {
        'name': name, 'ownership': ownership, 'pay_from_account': data.get('pay_from_account') or None,
        'reserve_account': data.get('reserve_account') or None, 'amount_rule': amount_rule, 'amount': amount,
        'frequency': frequency, 'anchor_date': anchor, 'due_rule': due_rule, 'extension_days': extension_days,
        'lead_days': json.dumps(lead_days), 'remind': 1 if data.get('remind', True) else 0, 'status': status,
        'budget_category': (data.get('budget_category') or '').strip() or None,
        'source': (data.get('source') or 'typed').strip(), 'notes': (data.get('notes') or '').strip(),
    }, None


@app.route('/api/obligations')
@login_required
def api_obligations_list():
    settings = obligation_settings()
    reminder_service().regenerate_occurrences()
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM obligations WHERE status != 'retired' ORDER BY ownership, name")]
        nxt = {}
        for r in db.execute(
            "SELECT obligation_id, id, due_date, standard_date, estimate, state FROM obligation_occurrences "
            "WHERE state IN ('upcoming','funds_confirmed') ORDER BY due_date"
        ):
            nxt.setdefault(r['obligation_id'], dict(r))
    for row in rows:
        row['lead_days'] = json.loads(row.get('lead_days') or '[]')
        row['next_occurrence'] = nxt.get(row['id'])
    return jsonify({
        'obligations': rows,
        'accounts': settings.get('accounts', []),
        'settings': {k: settings[k] for k in (
            'payg_instalment_quarterly', 'income_tax_reserve_rate', 'assumed_monthly_retainer',
            'gst_credit_allowance_monthly', 'sl_trading_trust_bas')},
    })


@app.route('/api/obligations', methods=['POST'])
@login_required
def api_obligations_save():
    data = request.get_json(force=True) or {}
    fields, error = _validate_obligation(data)
    if error:
        return jsonify({'error': error}), 400
    oid = data.get('id')
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        if oid:
            if not db.execute('SELECT 1 FROM obligations WHERE id=?', (oid,)).fetchone():
                return jsonify({'error': 'obligation not found'}), 404
            db.execute(
                '''UPDATE obligations SET name=?, ownership=?, pay_from_account=?, reserve_account=?, amount_rule=?,
                   amount=?, frequency=?, anchor_date=?, due_rule=?, extension_days=?, lead_days=?, remind=?, status=?,
                   budget_category=?, source=?, notes=?, updated_at=? WHERE id=?''',
                (*fields.values(), now, oid),
            )
            db.execute("DELETE FROM obligation_occurrences WHERE obligation_id=? AND state='upcoming'", (oid,))
        else:
            cursor = db.execute(
                '''INSERT INTO obligations (name, ownership, pay_from_account, reserve_account, amount_rule, amount,
                   frequency, anchor_date, due_rule, extension_days, lead_days, remind, status, budget_category,
                   source, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (*fields.values(), now, now),
            )
            oid = cursor.lastrowid
    reminder_service().regenerate_occurrences()
    return jsonify({'ok': True, 'id': oid})


@app.route('/api/obligations/<int:oid>', methods=['DELETE'])
@login_required
def api_obligations_delete(oid):
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        db.execute("UPDATE obligations SET status='retired', updated_at=? WHERE id=?", (now, oid))
        db.execute("DELETE FROM obligation_occurrences WHERE obligation_id=? AND state='upcoming'", (oid,))
    return jsonify({'ok': True})


def _today_param():
    raw = (request.args.get('today') or (request.get_json(silent=True) or {}).get('today') or '').strip()
    if not raw:
        return None, None
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, 'today must be an ISO date'


@app.route('/api/obligations/position')
@login_required
def api_obligations_position():
    today, error = _today_param()
    if error:
        return jsonify({'error': error}), 400
    service = reminder_service()
    service.regenerate_occurrences(today)
    return jsonify(service.position(today))


@app.route('/api/obligations/log')
@login_required
def api_obligations_log():
    try:
        limit = max(1, min(int(request.args.get('limit', 30)), 200))
    except ValueError:
        return jsonify({'error': 'limit must be a whole number'}), 400
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            'SELECT id, kind, dedupe_key, obligation_id, occurrence_id, mattermost_post_id, body, sent_at '
            'FROM reminder_log ORDER BY sent_at DESC, id DESC LIMIT ?', (limit,))]
    return jsonify({'log': rows})


@app.route('/api/obligations/occurrences/<int:occ_id>/state', methods=['POST'])
@login_required
def api_obligations_occurrence_state(occ_id):
    data = request.get_json(force=True) or {}
    state = data.get('state')
    if state not in ob.OCCURRENCE_STATES:
        return jsonify({'error': 'state must be one of: ' + ', '.join(ob.OCCURRENCE_STATES)}), 400
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        cursor = db.execute(
            "UPDATE obligation_occurrences SET state=?, state_changed_at=?, state_changed_by='app' WHERE id=?",
            (state, now, occ_id),
        )
        if cursor.rowcount == 0:
            return jsonify({'error': 'occurrence not found'}), 404
    return jsonify({'ok': True})


@app.route('/api/obligations/receipts', methods=['POST'])
@login_required
def api_obligations_receipts():
    data = request.get_json(force=True) or {}
    ym = str(data.get('year_month') or '').strip()
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', ym):
        return jsonify({'error': 'year_month must look like 2026-09'}), 400
    try:
        amount = float(data.get('amount'))
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a number'}), 400
    if not math.isfinite(amount) or amount < 0:
        return jsonify({'error': 'amount must be a non-negative number'}), 400
    now = datetime.now().isoformat()[:19]
    detail = json.dumps([{'source': 'app', 'amount': amount, 'note': (data.get('note') or '').strip()}])
    with get_db() as db:
        db.execute(
            'INSERT INTO receipts_log (year_month, amount_incl_gst, detail, updated_at) VALUES (?,?,?,?) '
            'ON CONFLICT(year_month) DO UPDATE SET amount_incl_gst=excluded.amount_incl_gst, '
            'detail=excluded.detail, updated_at=excluded.updated_at',
            (ym, amount, detail, now),
        )
    reminder_service().regenerate_occurrences()
    return jsonify({'ok': True})


@app.route('/api/obligations/balances', methods=['POST'])
@login_required
def api_obligations_balances():
    data = request.get_json(force=True) or {}
    key = str(data.get('account_key') or '').strip()
    keys = _account_keys()
    if not key or (keys and key not in keys):
        return jsonify({'error': 'account_key must be a configured account'}), 400
    try:
        balance = float(data.get('balance'))
    except (TypeError, ValueError):
        return jsonify({'error': 'balance must be a number'}), 400
    available = data.get('available')
    if available not in (None, ''):
        try:
            available = float(available)
        except (TypeError, ValueError):
            return jsonify({'error': 'available must be a number'}), 400
    else:
        available = None
    as_of = (data.get('as_of') or '').strip() or reminder_service().today().isoformat()
    try:
        date.fromisoformat(as_of)
    except ValueError:
        return jsonify({'error': 'as_of must be an ISO date'}), 400
    now = datetime.now().isoformat()[:19]
    with get_db() as db:
        cursor = db.execute(
            'INSERT INTO account_balances (account_key, balance, available, as_of, source, raw, created_at) '
            "VALUES (?,?,?,?,'typed',?,?)",
            (key, balance, available, as_of, (data.get('note') or '').strip(), now),
        )
    return jsonify({'ok': True, 'id': cursor.lastrowid})


@app.route('/api/obligations/run', methods=['POST'])
@login_required
def api_obligations_run():
    data = request.get_json(force=True) or {}
    job = data.get('job')
    if job not in ('daily', 'weekly'):
        return jsonify({'error': 'job must be daily or weekly'}), 400
    today, error = _today_param()
    if error:
        return jsonify({'error': error}), 400
    service = reminder_service()
    runner = service.run_daily if job == 'daily' else service.run_weekly
    return jsonify(runner(today, dry_run=bool(data.get('dry_run'))))


@app.route('/api/mattermost/status')
@login_required
def api_mattermost_status():
    client = mattermost_client()
    if client is None:
        return jsonify({'configured': False, 'can_read': False, 'can_post': False, 'reachable': False})
    return jsonify({'configured': True, 'can_read': client.can_read, 'can_post': client.can_post,
                    'reachable': client.ping()})


@app.route('/api/mattermost/test', methods=['POST'])
@login_required
def api_mattermost_test():
    client = mattermost_client()
    if client is None:
        return jsonify({'error': 'Mattermost is not configured. Set MATTERMOST_URL and a bot token or webhook in Coolify.'}), 503
    try:
        post_id = client.post('Family HQ is connected. Money reminders will post here.')
    except mattermost.MattermostError as exc:
        return jsonify({'error': str(exc)}), 502
    return jsonify({'ok': True, 'post_id': post_id})


def _start_reminder_scheduler():
    import threading
    thread = threading.Thread(
        target=reminders.run_scheduler_loop, args=(reminder_service,), daemon=True, name='reminders'
    )
    thread.start()
```

At the bottom of `app.py`, directly after the existing `_start_daily_screener()` call, add:

```python
_start_reminder_scheduler()
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_obligations_api -q`
Expected: 13 PASS. Then the whole suite: expected 192 pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_obligations_api.py
git commit -m "feat: obligations API, Mattermost test endpoint and reminder scheduler"
```

---

### Task 8: Feed obligations into the cash-flow forecast

**Files:**
- Modify: `app.py` — new `_obligation_events(start_date)` beside `_budget_target_events`; one line in `_budget_cash_flow`.
- Test: `tests/test_obligations_api.py`

**Interfaces:**
- Produces: `family_app._obligation_events(start_date: date) -> list[dict]` shaped like the other scheduled events (`description, amount, due_date, recurring, category, ownership, direction, source='obligation', confidence='confirmed'`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_obligations_api.py`:

```python
class ForecastHookTests(ObligationsDbCase):
    def test_obligation_events_include_dated_bills_but_not_monthly_or_transfers(self):
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        events = family_app._obligation_events(date(2026, 9, 9))
        names = [e["description"] for e in events]
        self.assertIn("Quarterly BAS + PAYG instalment", names)
        self.assertIn("Water (Urban Utilities)", names)
        self.assertNotIn("Monthly tax reserve transfer", names)
        self.assertNotIn("Home & contents (RACQ)", names)
        self.assertNotIn("Car registration (TMR)", names)  # no amount yet
        bas = next(e for e in events if e["description"] == "Quarterly BAS + PAYG instalment")
        self.assertEqual((bas["ownership"], bas["direction"], bas["source"], bas["category"]),
                         ("business", "outflow", "obligation", "Tax/ATO"))
        self.assertGreater(bas["amount"], 3733)
        self.assertEqual(bas["due_date"], "2026-10-28")

    def test_obligation_category_suppresses_the_matching_budget_target(self):
        with family_app.get_db() as db:
            db.execute("DELETE FROM budget_targets")
            db.execute("INSERT INTO budget_targets (category, monthly_target, type, frequency, direction, created_at, updated_at) "
                       "VALUES ('Water (Qld Urban Util)', 200, 'personal', 'monthly', 'outflow', 'x', 'x')")
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        events = family_app._obligation_events(date(2026, 9, 9))
        budgeted = family_app._budget_target_events(events, date(2026, 9, 9))
        self.assertEqual([b for b in budgeted if b["category"] == "Water (Qld Urban Util)"], [])

    def test_cash_flow_includes_obligation_events(self):
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        with patch.object(family_app, "_obligation_events", wraps=family_app._obligation_events) as hook:
            family_app._budget_cash_flow([], [], forecast_date=date(2026, 9, 9), safety_buffer=0)
        hook.assert_called_once_with(date(2026, 9, 9))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_obligations_api.ForecastHookTests -q`
Expected: FAIL with `AttributeError: _obligation_events`.

- [ ] **Step 3: Implement**

Add before `def _budget_cash_flow(`:

```python
def _obligation_events(start_date):
    """Dated obligation payments as confirmed forecast events.

    Monthly direct debits are already budget items, and the tax-reserve transfer is a move
    between Eden's own accounts, so only non-monthly fixed bills and the BAS are included.
    An obligation without an amount yet contributes nothing.
    """
    month_starts = _forecast_month_starts(start_date)
    horizon_end = ob.add_months(month_starts[-1], 1) - timedelta(days=1)
    with get_db() as db:
        rows = db.execute(
            '''SELECT o.due_date, o.estimate, b.name, b.amount, b.ownership, b.budget_category
               FROM obligation_occurrences o JOIN obligations b ON b.id = o.obligation_id
               WHERE b.status != 'retired' AND b.amount_rule IN ('fixed', 'bas_formula')
                 AND b.frequency != 'monthly' AND o.state IN ('upcoming', 'funds_confirmed')
                 AND o.due_date >= ? AND o.due_date <= ?
               ORDER BY o.due_date''',
            (start_date.isoformat(), horizon_end.isoformat()),
        ).fetchall()
    events = []
    for row in rows:
        amount = row['estimate'] if row['estimate'] is not None else row['amount']
        if not amount or float(amount) <= 0:
            continue
        events.append({
            'description': row['name'],
            'amount': float(amount),
            'due_date': row['due_date'],
            'recurring': '',
            'category': row['budget_category'] or '',
            'ownership': row['ownership'],
            'direction': 'outflow',
            'source': 'obligation',
            'confidence': 'confirmed',
        })
    return events
```

In `_budget_cash_flow`, immediately before the line `scheduled_events.extend(\n        _budget_target_events(scheduled_events, start_date)\n    )`, add:

```python
    scheduled_events.extend(_obligation_events(start_date))
```

Check that `_forecast_month_starts` returns a non-empty list (it does: six month starts). `timedelta` is already imported at the top of `app.py`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest discover -s tests -q`
Expected: 195 pass. `tests/test_dashboard_contract.py` and `tests/test_cashflow.py` must still pass untouched.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_obligations_api.py
git commit -m "feat: include obligation payments in the cash-flow forecast"
```

---

### Task 9: Obligations page in the dashboard

**Files:**
- Modify: `dashboard.html` — nav (desktop list around line 583–596, mobile list around line 1480–1505), a new `page-obligations` block placed directly before `<div class="page" id="page-budget">`, a new modal, JS after `loadBudget()`'s block, and a Mattermost block inside `page-settings`.
- Test: `tests/test_dashboard_contract.py` (add one test class in the same style as the existing ones — read that file first to copy its pattern for reading `dashboard.html`).

**Interfaces:**
- Consumes: the routes from Task 7.
- Produces: `loadObligations()`, `oblEdit(id)`, `oblSave()`, `oblRetire(id)`, `oblSetState(occId, state)`, `oblAddBalance(accountKey)`, `oblAddReceipts()`, `oblRunPreview(job)`, `mmTest()`, `mmStatus()`.

- [ ] **Step 1: Write the failing contract test**

Open `tests/test_dashboard_contract.py`, note how it reads the HTML (it uses `Path(__file__).resolve().parent.parent / "dashboard.html"` or similar — reuse the same helper), then append:

```python
class ObligationsPageContractTests(unittest.TestCase):
    def setUp(self):
        self.html = (Path(__file__).resolve().parent.parent / "dashboard.html").read_text()

    def test_obligations_page_and_nav_exist(self):
        self.assertIn('id="page-obligations"', self.html)
        self.assertIn("showPage('obligations',this);loadObligations()", self.html)
        self.assertIn('data-page="obligations"', self.html)

    def test_obligations_page_has_three_cards_and_actions(self):
        for element_id in ("obl-upcoming", "obl-targets", "obl-log", "obl-modal"):
            self.assertIn(f'id="{element_id}"', self.html)
        for fn in ("async function loadObligations", "function oblEdit", "async function oblSave",
                   "async function oblSetState", "async function oblAddBalance", "async function oblAddReceipts",
                   "async function oblRunPreview", "async function mmTest", "async function mmStatus"):
            self.assertIn(fn, self.html)

    def test_settings_has_mattermost_block(self):
        self.assertIn('id="mm-status"', self.html)
        self.assertIn("Send test message", self.html)
```

If the file has no `from pathlib import Path` / `import unittest`, add them at the top.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_dashboard_contract -q`
Expected: 3 new FAIL.

- [ ] **Step 3: Add the navigation entries**

Desktop nav: directly before the `<div class="nav-item" onclick="showPage('budget',this);loadBudget()">` block, add:

```html
    <div class="nav-item" onclick="showPage('obligations',this);loadObligations()">
      <span class="nav-icon">🧾</span> Obligations
    </div>
```

Mobile nav: replace the `Investments` entry (`data-page="property"`) with the Obligations entry so the bar stays at eight items (Investments remains reachable from the desktop nav):

```html
    <div class="mnav-item" onclick="showPage('obligations',this);loadObligations()" data-page="obligations">
      <span class="micon">🧾</span><span>Owed</span>
    </div>
```

- [ ] **Step 4: Add the page markup**

Directly before `<div class="page" id="page-budget">`:

```html
<!-- OBLIGATIONS -->
<div class="page" id="page-obligations">
  <div class="topbar">
    <div class="topbar-title">🧾 Obligations & Reserves</div>
    <button onclick="oblEdit(null)" style="margin-left:auto;background:#1B4332;color:#fff;border:none;padding:7px 16px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">+ Add</button>
  </div>
  <div class="page-body">

    <div class="card" id="obl-targets-card">
      <div class="card-header">
        <div class="card-title">Reserve accounts</div>
        <span style="font-size:11px;color:#9CA3AF;margin-left:8px">What each account should hold today</span>
        <button onclick="oblAddReceipts()" style="margin-left:auto;background:#fff;color:#1B4332;border:1px solid #1B4332;padding:5px 10px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">Eden receipts</button>
      </div>
      <div id="obl-targets" style="padding:0 16px 16px"><div class="empty-state">Loading…</div></div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">What's coming</div>
        <span style="font-size:11px;color:#9CA3AF;margin-left:8px">Next 90 days</span>
        <button onclick="oblRunPreview('daily')" style="margin-left:auto;background:#fff;color:#1B4332;border:1px solid #1B4332;padding:5px 10px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">Preview today's message</button>
      </div>
      <div id="obl-upcoming" style="padding:0 16px 16px"><div class="empty-state">Loading…</div></div>
    </div>

    <details class="card">
      <summary class="bdgt-collapsible-header">
        <span class="card-title">All obligations</span>
      </summary>
      <div id="obl-list" style="padding:0 16px 16px"><div class="empty-state">Loading…</div></div>
    </details>

    <details class="card">
      <summary class="bdgt-collapsible-header">
        <span class="card-title">Conversation</span>
        <span style="font-size:11px;color:#9CA3AF">Messages Family HQ has sent</span>
      </summary>
      <div id="obl-log" style="padding:0 16px 16px"><div class="empty-state">Loading…</div></div>
    </details>

    <div id="obl-preview" class="card" style="display:none">
      <div class="card-header"><div class="card-title">Message preview</div>
        <button onclick="document.getElementById('obl-preview').style.display='none'" style="margin-left:auto;background:none;border:none;font-size:16px;cursor:pointer">✕</button>
      </div>
      <pre id="obl-preview-body" style="white-space:pre-wrap;font-family:inherit;font-size:13px;padding:0 20px 20px;margin:0"></pre>
    </div>
  </div>
</div>

<div id="obl-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:1000;align-items:center;justify-content:center">
  <div style="background:#fff;border-radius:12px;padding:24px;width:min(520px,94vw);max-height:92vh;overflow:auto">
    <h3 id="obl-modal-title" style="margin:0 0 16px;font-size:16px">Add obligation</h3>
    <input type="hidden" id="obl-id">
    <label style="font-size:12px;color:#6B7280">Name</label>
    <input id="obl-name" style="width:100%;padding:8px;margin:4px 0 12px;border:1px solid #E5E7EB;border-radius:6px">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div><label style="font-size:12px;color:#6B7280">Personal or business</label>
        <select id="obl-ownership" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px">
          <option value="personal">Personal</option><option value="business">Business</option></select></div>
      <div><label style="font-size:12px;color:#6B7280">How the amount works</label>
        <select id="obl-rule" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px">
          <option value="fixed">Fixed amount</option><option value="sinking_hold">Always hold this amount</option>
          <option value="receipts_share">Share of Eden's receipts</option><option value="bas_formula">BAS formula</option></select></div>
      <div><label style="font-size:12px;color:#6B7280">Amount ($)</label>
        <input id="obl-amount" type="number" step="0.01" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px"></div>
      <div><label style="font-size:12px;color:#6B7280">How often</label>
        <select id="obl-frequency" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px">
          <option value="once">Once</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option>
          <option value="biannual">Every 6 months</option><option value="annual">Annual</option><option value="rolling">Always (no due date)</option></select></div>
      <div><label style="font-size:12px;color:#6B7280">Next due date</label>
        <input id="obl-anchor" type="date" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px"></div>
      <div><label style="font-size:12px;color:#6B7280">Warn this many days before (comma separated)</label>
        <input id="obl-lead" value="30, 7" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px"></div>
      <div><label style="font-size:12px;color:#6B7280">Paid from</label>
        <select id="obl-pay-from" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px"></select></div>
      <div><label style="font-size:12px;color:#6B7280">Money parked in</label>
        <select id="obl-reserve" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px"></select></div>
      <div><label style="font-size:12px;color:#6B7280">Budget item it replaces (optional)</label>
        <input id="obl-budget-category" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px"></div>
      <div><label style="font-size:12px;color:#6B7280">Status</label>
        <select id="obl-status" style="width:100%;padding:8px;margin-top:4px;border:1px solid #E5E7EB;border-radius:6px">
          <option value="active">Active</option><option value="pending_confirmation">Needs confirming</option></select></div>
    </div>
    <label style="display:flex;align-items:center;gap:8px;margin:12px 0;font-size:13px"><input type="checkbox" id="obl-remind" checked> Send reminders for this</label>
    <label style="font-size:12px;color:#6B7280">Notes</label>
    <textarea id="obl-notes" rows="2" style="width:100%;padding:8px;margin:4px 0 12px;border:1px solid #E5E7EB;border-radius:6px"></textarea>
    <div id="obl-error" style="color:#B91C1C;font-size:12px;min-height:16px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px">
      <button onclick="document.getElementById('obl-modal').style.display='none'" style="background:#fff;border:1px solid #E5E7EB;padding:8px 16px;border-radius:8px;cursor:pointer">Cancel</button>
      <button onclick="oblSave()" style="background:#1B4332;color:#fff;border:none;padding:8px 16px;border-radius:8px;font-weight:600;cursor:pointer">Save</button>
    </div>
  </div>
</div>
```

In `page-settings`, inside the `page-body` after the "Connected Services" card, add:

```html
    <div class="card" style="margin-bottom:20px">
      <div class="card-header"><div class="card-title">Mattermost</div>
        <button onclick="mmTest()" style="margin-left:auto;background:#1B4332;color:#fff;border:none;padding:5px 12px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Send test message</button>
      </div>
      <div id="mm-status" style="padding:0 20px 16px;font-size:13px;color:#374151">Checking…</div>
    </div>
```

and in `loadSettings()` add a call `mmStatus();` as its first line inside the function body.

- [ ] **Step 5: Add the JavaScript**

After the `loadBudget()` function block (search for `function bdgtRenderHeadline`), insert before it:

```javascript
// ── OBLIGATIONS ───────────────────────────────────────────────────────────────
let _oblData = { obligations: [], accounts: [], settings: {} };

async function loadObligations() {
  try {
    const [list, position, log] = await Promise.all([
      authFetch('/api/obligations').then(r => r.json()),
      authFetch('/api/obligations/position').then(r => r.json()),
      authFetch('/api/obligations/log?limit=30').then(r => r.json()),
    ]);
    _oblData = list;
    oblRenderTargets(position.targets || []);
    oblRenderUpcoming(position.upcoming || []);
    oblRenderList(list.obligations || []);
    oblRenderLog(log.log || []);
  } catch (e) {
    console.error('Obligations load error:', e);
  }
}

function oblAccountName(key) {
  const a = (_oblData.accounts || []).find(x => x.key === key);
  return a ? a.display : (key || '—');
}

function oblDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
}

function oblRenderTargets(targets) {
  const el = document.getElementById('obl-targets');
  if (!targets.length) { el.innerHTML = '<div class="empty-state">No reserve targets yet.</div>'; return; }
  el.innerHTML = `<table class="bdgt-table"><thead><tr><th>Account</th><th>Should hold</th><th>Last known</th><th>As at</th><th>Verdict</th><th></th></tr></thead><tbody>` +
    targets.map(t => {
      const verdict = t.balance === null ? '<span style="color:#9CA3AF">no balance yet</span>'
        : (t.shortfall > 0 ? `<span style="color:#B91C1C;font-weight:600">short ${fmt(t.shortfall)}</span>` : '<span style="color:#15803D;font-weight:600">covered</span>');
      const components = t.components.map(c => `${c.label} ${fmt(c.amount)}`).join(' · ');
      return `<tr title="${components.replace(/"/g, '&quot;')}">
        <td><strong>${t.display}</strong></td><td>${fmt(t.target)}</td>
        <td>${t.balance === null ? '—' : fmt(t.balance)}</td>
        <td style="${t.stale ? 'color:#B45309' : ''}">${t.as_of ? oblDate(t.as_of) : '—'}${t.stale && t.as_of ? ' ⚠' : ''}</td>
        <td>${verdict}</td>
        <td><button onclick="oblAddBalance('${t.account_key}')" style="background:none;border:1px solid #E5E7EB;padding:3px 8px;border-radius:6px;font-size:11px;cursor:pointer">Update balance</button></td>
      </tr>`;
    }).join('') + '</tbody></table>';
}

function oblRenderUpcoming(upcoming) {
  const el = document.getElementById('obl-upcoming');
  if (!upcoming.length) { el.innerHTML = '<div class="empty-state">Nothing due in the next 90 days.</div>'; return; }
  el.innerHTML = `<table class="bdgt-table"><thead><tr><th>Due</th><th>What</th><th>Estimate</th><th>Parked in</th><th>State</th><th></th></tr></thead><tbody>` +
    upcoming.map(u => `<tr>
      <td>${oblDate(u.due_date)}<div style="font-size:11px;color:#9CA3AF">${u.days_out === 0 ? 'today' : u.days_out + ' days'}</div></td>
      <td>${u.name}</td><td>${u.estimate === null ? '<span style="color:#9CA3AF">unknown</span>' : fmt(u.estimate)}</td>
      <td>${oblAccountName(u.reserve_account)}</td>
      <td>${u.state.replace('_', ' ')}</td>
      <td style="white-space:nowrap">
        <button onclick="oblSetState(${u.occurrence_id}, 'paid')" style="background:#1B4332;color:#fff;border:none;padding:3px 8px;border-radius:6px;font-size:11px;cursor:pointer">Paid</button>
        <button onclick="oblSetState(${u.occurrence_id}, 'skipped')" style="background:none;border:1px solid #E5E7EB;padding:3px 8px;border-radius:6px;font-size:11px;cursor:pointer">Skip</button>
      </td></tr>`).join('') + '</tbody></table>';
}

function oblRenderList(rows) {
  const el = document.getElementById('obl-list');
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No obligations yet.</div>'; return; }
  const ruleLabel = { fixed: 'fixed', sinking_hold: 'always hold', receipts_share: 'share of receipts', bas_formula: 'BAS formula' };
  el.innerHTML = `<table class="bdgt-table"><thead><tr><th>Name</th><th>Amount</th><th>Cycle</th><th>Next due</th><th>Parked in</th><th></th></tr></thead><tbody>` +
    rows.map(o => `<tr style="${o.status === 'pending_confirmation' ? 'background:#FFFBEB' : ''}">
      <td><strong>${o.name}</strong>${o.status === 'pending_confirmation' ? ' <span style="font-size:11px;color:#B45309">needs confirming</span>' : ''}${o.remind ? '' : ' <span style="font-size:11px;color:#9CA3AF">silent</span>'}
        <div style="font-size:11px;color:#9CA3AF">${o.ownership} · ${ruleLabel[o.amount_rule] || o.amount_rule}</div></td>
      <td>${o.amount === null ? '—' : fmt(o.amount)}</td><td>${o.frequency}</td>
      <td>${o.next_occurrence ? oblDate(o.next_occurrence.due_date) : '—'}</td>
      <td>${oblAccountName(o.reserve_account)}</td>
      <td style="white-space:nowrap">
        <button onclick="oblEdit(${o.id})" style="background:none;border:1px solid #E5E7EB;padding:3px 8px;border-radius:6px;font-size:11px;cursor:pointer">Edit</button>
        <button onclick="oblRetire(${o.id})" style="background:none;border:1px solid #FCA5A5;color:#B91C1C;padding:3px 8px;border-radius:6px;font-size:11px;cursor:pointer">Remove</button>
      </td></tr>`).join('') + '</tbody></table>';
}

function oblRenderLog(rows) {
  const el = document.getElementById('obl-log');
  if (!rows.length) { el.innerHTML = '<div class="empty-state">Nothing sent yet.</div>'; return; }
  el.innerHTML = rows.map(r => `<div style="padding:10px 0;border-bottom:1px solid #F3F4F6">
      <div style="font-size:11px;color:#9CA3AF">${r.sent_at.replace('T', ' ')} · ${r.kind.replace('_', ' ')}${r.mattermost_post_id ? '' : ' · webhook'}</div>
      <div style="font-size:13px;white-space:pre-wrap">${r.body.replace(/</g, '&lt;')}</div>
    </div>`).join('');
}

function oblFillAccountSelects() {
  const options = ['<option value="">—</option>'].concat((_oblData.accounts || []).map(a => `<option value="${a.key}">${a.display}</option>`)).join('');
  document.getElementById('obl-pay-from').innerHTML = options;
  document.getElementById('obl-reserve').innerHTML = options;
}

function oblEdit(id) {
  oblFillAccountSelects();
  const o = id ? (_oblData.obligations || []).find(x => x.id === id) : null;
  document.getElementById('obl-modal-title').textContent = o ? 'Edit obligation' : 'Add obligation';
  document.getElementById('obl-id').value = o ? o.id : '';
  document.getElementById('obl-name').value = o ? o.name : '';
  document.getElementById('obl-ownership').value = o ? o.ownership : 'personal';
  document.getElementById('obl-rule').value = o ? o.amount_rule : 'fixed';
  document.getElementById('obl-amount').value = o && o.amount !== null ? o.amount : '';
  document.getElementById('obl-frequency').value = o ? o.frequency : 'annual';
  document.getElementById('obl-anchor').value = o && o.next_occurrence ? o.next_occurrence.standard_date : (o ? (o.anchor_date || '') : '');
  document.getElementById('obl-lead').value = o ? o.lead_days.join(', ') : '30, 7';
  document.getElementById('obl-pay-from').value = o ? (o.pay_from_account || '') : '';
  document.getElementById('obl-reserve').value = o ? (o.reserve_account || '') : '';
  document.getElementById('obl-budget-category').value = o ? (o.budget_category || '') : '';
  document.getElementById('obl-status').value = o ? o.status : 'active';
  document.getElementById('obl-remind').checked = o ? !!o.remind : true;
  document.getElementById('obl-notes').value = o ? (o.notes || '') : '';
  document.getElementById('obl-error').textContent = '';
  document.getElementById('obl-modal').style.display = 'flex';
}

async function oblSave() {
  const lead = document.getElementById('obl-lead').value.split(',').map(s => s.trim()).filter(Boolean).map(Number);
  if (lead.some(n => !Number.isInteger(n) || n < 0)) {
    document.getElementById('obl-error').textContent = 'Warning days must be whole numbers, e.g. 30, 7';
    return;
  }
  const existing = (_oblData.obligations || []).find(x => String(x.id) === document.getElementById('obl-id').value);
  const payload = {
    id: document.getElementById('obl-id').value || undefined,
    name: document.getElementById('obl-name').value,
    ownership: document.getElementById('obl-ownership').value,
    amount_rule: document.getElementById('obl-rule').value,
    amount: document.getElementById('obl-amount').value,
    frequency: document.getElementById('obl-frequency').value,
    anchor_date: document.getElementById('obl-anchor').value || null,
    lead_days: lead,
    pay_from_account: document.getElementById('obl-pay-from').value,
    reserve_account: document.getElementById('obl-reserve').value,
    budget_category: document.getElementById('obl-budget-category').value,
    status: document.getElementById('obl-status').value,
    remind: document.getElementById('obl-remind').checked,
    notes: document.getElementById('obl-notes').value,
    extension_days: existing ? existing.extension_days : 0,
    due_rule: existing ? existing.due_rule : 'standard',
    source: existing ? existing.source : 'typed',
  };
  const r = await authFetch('/api/obligations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const d = await r.json();
  if (!r.ok) { document.getElementById('obl-error').textContent = d.error || 'Could not save'; return; }
  document.getElementById('obl-modal').style.display = 'none';
  loadObligations();
}

async function oblRetire(id) {
  const o = (_oblData.obligations || []).find(x => x.id === id);
  if (!confirm(`Remove "${o ? o.name : 'this obligation'}" from the list? Its history is kept.`)) return;
  await authFetch(`/api/obligations/${id}`, { method: 'DELETE' });
  loadObligations();
}

async function oblSetState(occId, state) {
  await authFetch(`/api/obligations/occurrences/${occId}/state`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state }) });
  loadObligations();
}

async function oblAddBalance(accountKey) {
  const value = prompt(`Current balance of ${oblAccountName(accountKey)} (dollars):`);
  if (value === null || value.trim() === '') return;
  const r = await authFetch('/api/obligations/balances', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account_key: accountKey, balance: value.replace(/[$,\s]/g, '') }) });
  if (!r.ok) { alert((await r.json()).error || 'Could not save'); return; }
  loadObligations();
}

async function oblAddReceipts() {
  const now = new Date();
  const ym = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const month = prompt('Which month? (YYYY-MM)', ym);
  if (!month) return;
  const value = prompt(`Total Eden received in ${month}, GST inclusive (dollars):`);
  if (value === null || value.trim() === '') return;
  const r = await authFetch('/api/obligations/receipts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ year_month: month, amount: value.replace(/[$,\s]/g, '') }) });
  if (!r.ok) { alert((await r.json()).error || 'Could not save'); return; }
  loadObligations();
}

async function oblRunPreview(job) {
  const r = await authFetch('/api/obligations/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job, dry_run: true }) });
  const d = await r.json();
  document.getElementById('obl-preview-body').textContent = d.body || `Nothing to send today (${d.reason || 'nothing new'}).`;
  document.getElementById('obl-preview').style.display = 'block';
}

async function mmStatus() {
  const el = document.getElementById('mm-status');
  if (!el) return;
  try {
    const s = await authFetch('/api/mattermost/status').then(r => r.json());
    if (!s.configured) { el.innerHTML = 'Not configured. Add <code>MATTERMOST_URL</code> and <code>MATTERMOST_BOT_TOKEN</code> + <code>MATTERMOST_CHANNEL_ID</code> (or <code>MATTERMOST_WEBHOOK_URL</code>) in Coolify.'; return; }
    el.innerHTML = `${s.reachable ? '🟢 Reachable' : '🔴 Not reachable'} · ${s.can_read ? 'bot account (can read replies)' : 'webhook only (send only)'}`;
  } catch (e) {
    el.textContent = 'Could not check Mattermost status.';
  }
}

async function mmTest() {
  const r = await authFetch('/api/mattermost/test', { method: 'POST' });
  const d = await r.json();
  alert(r.ok ? 'Test message sent to the Family Finance channel.' : (d.error || 'Could not send'));
}
```

Note: `oblRetire` uses `confirm()` deliberately, matching how the Budget page confirms removals; this is a normal browser dialog on a user click, not something triggered by automation.

- [ ] **Step 6: Run the tests and open the page**

Run: `.venv/bin/python -m unittest discover -s tests -q` — expected 198 pass.

Smoke test in a browser: run `.venv/bin/python app.py` (the scheduler thread starts and sleeps; with no `MATTERMOST_URL` set it never sends), log in at `http://localhost:3000` with the dev credentials, open Obligations, confirm the three cards render with the seeded rows, click **Preview today's message**, and check the Settings page shows the Mattermost block. Stop the server with Ctrl-C.

- [ ] **Step 7: Commit**

```bash
git add dashboard.html tests/test_dashboard_contract.py
git commit -m "feat: obligations page with reserve targets, upcoming bills and message preview"
```

---

### Task 10: Documentation and config notes

**Files:**
- Create: `README.md`
- Modify: `docs/cash-flow-operations.md` — new section before `## Backup and restore`.

- [ ] **Step 1: Write `README.md`**

```markdown
# Family HQ

Whitewood family command centre: birthdays, goals, property, finance imports, a six-month
cash-flow forecast, and money reminders posted to Mattermost. Flask + SQLite, deployed with
Coolify at family.edencommercial.au.

## Running locally

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py            # http://localhost:3000
    .venv/bin/python -m unittest discover -s tests -q

## Environment variables (set in Coolify)

| Variable | Required | Purpose |
|---|---|---|
| `FAMILY_HQ_USER`, `FAMILY_HQ_PASS` | yes | Login |
| `SECRET_KEY` | yes | Session signing |
| `ANTHROPIC_API_KEY` | for AI features | Chat, briefing, and (step 2) reading screenshots posted to Mattermost |
| `MATTERMOST_URL` | for reminders | e.g. `https://chat.leaseintel.ai` |
| `MATTERMOST_BOT_TOKEN` | for reminders | Personal access token of the Family HQ bot account |
| `MATTERMOST_CHANNEL_ID` | for reminders | The Family Finance channel id (Channel → View Info) |
| `MATTERMOST_WEBHOOK_URL` | optional | Incoming webhook; used to send when no bot token is set |

If `MATTERMOST_URL` is absent, or neither a bot token + channel nor a webhook is set,
reminders are computed but never sent, the Obligations page still works, and Settings shows
"Not configured". Nothing else in the app is affected.

## Obligations and reminders

Every recurring money obligation (BAS, PAYG, rates, water, insurance, rego, the PropVesting
hold, the mortgage repayment and the food buffer) is a row on the **Obligations** page. From
these Family HQ works out what each reserve account should hold today and posts to Mattermost:

- **1st of the month, 7am Brisbane:** the set-aside for the month just ended.
- **30, 7 (and 1 for BAS) days before a due date:** a warning with the estimate, what the reserve
  account should hold, the last known balance and the shortfall.
- **Sunday 5pm:** the position of every reserve account.

Messages are bundled into one post a day, never repeated, and never sent between 9pm and 7am.

### Settings (`data/config.json` → `obligations`)

| Key | Default | Valid values | Meaning / when absent |
|---|---|---|---|
| `gst_fraction` | `0.0909` (1/11) | 0–1 | Share of GST-inclusive receipts reserved for GST |
| `income_tax_reserve_rate` | `0.15` | 0–1 | Share of ex-GST receipts reserved for company income tax |
| `assumed_monthly_retainer` | `10083.34` | dollars | Receipts assumed for any month not reported |
| `gst_credit_allowance_monthly` | `636` | dollars | Expected GST credits deducted per month in the BAS estimate |
| `payg_instalment_quarterly` | `3188` | dollars | PAYG instalment added to every BAS estimate |
| `sl_trading_trust_bas` | `545` | dollars | SL Trading Trust BAS added to every BAS estimate |
| `emergency_floor` | `3000` | dollars | ING Emergency target (informational; the seed row carries the amount) |
| `mortgage_repayment` | `4810.38` | dollars | Informational; the seed row carries the amount |
| `reserve_start_month` | `"2026-09"` | `YYYY-MM` | First month the income-tax pot accrues from |
| `post_hour_local` | `7` | 0–23 | Hour of the daily run |
| `weekly_day` | `6` | 0 (Mon) – 6 (Sun) | Day of the weekly position |
| `weekly_hour_local` | `17` | 0–23 | Hour of the weekly position |
| `quiet_hours` | `[21, 7]` | two hours | No posts from the first hour to the second |
| `timezone` | `"Australia/Brisbane"` | IANA zone | All scheduling |
| `stale_balance_days` | `14` | days | A balance older than this is flagged and a screenshot requested |
| `bas_lookahead_days` | `30` | days | The trust BAS joins the EComm GST target this many days before a BAS |
| `accounts` | `[]` | list of `{key, display, bank, match, loan?}` | Reserve accounts; `match` is the digits used to recognise screenshots (step 2). An empty list disables account validation on the API. |

Any key left out falls back to the default shown. `mattermost.enabled` (default `true`) set to
`false` stops all sending without removing the environment variables; `mattermost.allowed_users`
is reserved for step 2 (two-way).

### Testing a message without waiting for 7am

On the Obligations page, **Preview today's message** shows exactly what would be posted, without
sending. **Settings → Mattermost → Send test message** posts a one-line connection test.

## Install on your phone or computer

To follow in step 3.
```

- [ ] **Step 2: Extend `docs/cash-flow-operations.md`**

Insert before `## Backup and restore`:

```markdown
## Obligations and reserve accounts

The **Obligations** page lists every bill that is not a simple monthly direct debit, plus the
holds (PropVesting money, the mortgage repayment, the food buffer). Each row says which account
pays it and which account the money is parked in beforehand.

**Reserve accounts** shows what each parking account should hold today. EComm GST is the
PropVesting hold plus the GST accrued so far this quarter plus the income-tax pot. ING Home is a
sinking fund: each half-yearly, quarterly or annual bill contributes its share of the time
elapsed since it was last due. Use **Update balance** to type a balance when you check the bank;
the age of the balance is shown and flagged when it is older than two weeks.

**Eden receipts** records what Eden actually received in a month, GST inclusive. Until a month is
recorded the engine assumes the Cheesecake Shop retainer only, and says so in its messages.

**What's coming** lists the next 90 days. Mark an item **Paid** when the money has gone; it
stops the warnings for that occurrence. **Skip** drops one occurrence without changing the
schedule.

Obligation payments appear in the six-month forecast as confirmed events. If an obligation names
a **budget item it replaces**, that budget line is suppressed so the bill is not counted twice.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/cash-flow-operations.md
git commit -m "docs: obligations and Mattermost reminder settings"
```

---

## Go-live checklist (after deploy, done with Tyson)

1. In Mattermost System Console → Integrations → Bot Accounts: enable bot account creation, add a bot `familyhq` (display name Family HQ, role Member), copy the token.
2. In the Family Finance channel: add `@familyhq` as a member; Channel name → View Info → copy the ID.
3. In Coolify → Family HQ → Environment: add `MATTERMOST_URL=https://chat.leaseintel.ai`, `MATTERMOST_BOT_TOKEN`, `MATTERMOST_CHANNEL_ID`. Redeploy.
4. Settings → Mattermost → **Send test message**. Expect the line in the channel.
5. Obligations → **Preview today's message**. Read it. Then `POST /api/obligations/run {"job":"weekly"}` from the page (or wait for Sunday 5pm).
6. Record August receipts if known (**Eden receipts**), otherwise leave the assumption.
