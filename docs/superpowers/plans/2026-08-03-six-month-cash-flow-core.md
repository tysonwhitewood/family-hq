# Six-Month Cash-Flow Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current three-month average cards with a trustworthy 182-day personal and business cash forecast presented in four-week cycles.

**Architecture:** Add a focused, pure-Python `cashflow.py` domain module for recurrence detection, daily projections, cycle grouping and safe-to-spend calculations. Keep Flask responsible for persistence and HTTP only, reuse the existing CSV upload workflow, and update the existing Budget page to show personal first with collapsed business and combined views.

**Tech Stack:** Python 3.12, Flask 3, SQLite, standard-library `unittest`, existing vanilla HTML/CSS/JavaScript dashboard.

## Global Constraints

- Forecast horizon is exactly 182 sequential calendar days, beginning today.
- Presentation uses six complete 28-day cycles plus one final partial cycle.
- Personal and Eden Commercial forecasts remain separate.
- Business cash does not increase personal safe-to-spend until a linked transfer occurs.
- AUD is the only currency.
- Forecast arithmetic is deterministic and local; no LLM performs calculations.
- Existing CSV formats remain supported.
- No Xero write operations or payment initiation.

---

### Task 1: Pure cash-flow forecast engine

**Files:**
- Create: `cashflow.py`
- Create: `tests/test_cashflow.py`

**Interfaces:**
- Consumes: transaction dictionaries with `account`, `date`, `amount`, `description` and optional `balance`; scheduled-event dictionaries with `description`, `amount`, `due_date`, `recurring`, `category` and `ownership`.
- Produces: `build_forecast(transactions, scheduled_events, account_ownership, today, safety_buffer, horizon_days=182) -> dict`.

- [ ] **Step 1: Write failing horizon and cycle tests**

```python
def test_forecast_has_182_days_and_seven_cycles(self):
    result = build_forecast([], [], {}, date(2026, 8, 3), 1000)
    self.assertEqual(len(result["personal"]["days"]), 182)
    self.assertEqual([len(c["days"]) for c in result["personal"]["cycles"]], [28, 28, 28, 28, 28, 28, 14])

def test_cycles_are_sequential_without_duplicate_dates(self):
    result = build_forecast([], [], {}, date(2026, 8, 3), 1000)
    dates = [day["date"] for cycle in result["personal"]["cycles"] for day in cycle["days"]]
    self.assertEqual(len(dates), len(set(dates)))
    self.assertEqual(dates[0], "2026-08-03")
    self.assertEqual(dates[-1], "2027-01-31")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_cashflow -v`

Expected: import failure because `cashflow.py` does not exist.

- [ ] **Step 3: Implement the 182-day calendar and 28-day cycle grouping**

Create immutable daily positions for personal and business. Expose cycle labels, start dates, end dates, opening balance, inflows, outflows, closing balance and lowest balance.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python3 -m unittest tests.test_cashflow -v`

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add cashflow.py tests/test_cashflow.py
git commit -m "feat: add six-month cash flow calendar"
```

### Task 2: Balance, event and separation rules

**Files:**
- Modify: `cashflow.py`
- Modify: `tests/test_cashflow.py`

**Interfaces:**
- Consumes: the Task 1 `build_forecast` interface.
- Produces: forecast sections `personal`, `business` and `combined`, plus `safe_to_spend`, `lowest_balance`, `lowest_balance_date`, `warnings` and daily `events`.

- [ ] **Step 1: Write failing financial-behaviour tests**

```python
def test_personal_and_business_cash_stay_separate(self):
    transactions = [
        {"account": "Everyday", "date": "2026-08-03", "amount": 0, "description": "balance", "balance": 2000},
        {"account": "Eden", "date": "2026-08-03", "amount": 0, "description": "balance", "balance": 10000},
    ]
    result = build_forecast(
        transactions, [], {"Everyday": "personal", "Eden": "business"},
        date(2026, 8, 3), 500,
    )
    self.assertEqual(result["personal"]["opening_balance"], 2000)
    self.assertEqual(result["business"]["opening_balance"], 10000)
    self.assertEqual(result["personal"]["safe_to_spend"], 1500)

def test_scheduled_expense_changes_balance_on_due_date(self):
    events = [{
        "description": "School camp", "amount": 800, "due_date": "2026-08-20",
        "recurring": 0, "category": "School / Kids", "ownership": "personal",
    }]
    result = build_forecast([], events, {}, date(2026, 8, 3), 0)
    day = next(d for d in result["personal"]["days"] if d["date"] == "2026-08-20")
    self.assertEqual(day["outflows"], 800)
    self.assertEqual(day["closing_balance"], -800)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_cashflow -v`

Expected: assertions fail because balances and scheduled events are not applied.

- [ ] **Step 3: Implement opening-balance selection and scheduled events**

Use the latest non-null balance per account as the opening balance. Apply personal events only to personal and business events only to business. Treat positive scheduled amounts as outflows unless the event explicitly has `direction="inflow"`.

- [ ] **Step 4: Add and verify recurring-event tests**

Add tests proving weekly, fortnightly, monthly and annual schedules occur on correct dates. A monthly event on the 31st must use the last valid day in a shorter month.

- [ ] **Step 5: Implement recurrence expansion**

Support `weekly`, `fortnightly`, `monthly`, `quarterly`, `annual` and non-recurring events. Do not infer recurrence inside the arithmetic engine.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `python3 -m unittest tests.test_cashflow -v`

Expected: all cash-flow tests pass.

- [ ] **Step 7: Commit**

```bash
git add cashflow.py tests/test_cashflow.py
git commit -m "feat: calculate separated daily cash forecasts"
```

### Task 3: Flask forecast API and corrected imports

**Files:**
- Modify: `app.py`
- Create: `tests/test_budget_api.py`

**Interfaces:**
- Consumes: `cashflow.build_forecast`.
- Produces: authenticated `GET /api/budget/forecast?start=YYYY-MM-DD`, retaining the existing `/api/budget/summary` response while adding `cash_flow`.

- [ ] **Step 1: Write failing tests for account-safe deduplication**

```python
def test_equal_transactions_on_different_accounts_are_not_duplicates(self):
    rows = [
        {"account": "Personal", "date": "2026-07-01", "amount": -25, "description": "CAFE", "balance": 100},
        {"account": "Eden", "date": "2026-07-01", "amount": -25, "description": "CAFE", "balance": 500},
    ]
    self.assertEqual(len(_deduplicate_transactions(rows)), 2)
```

- [ ] **Step 2: Run test and verify RED**

Run: `python3 -m unittest tests.test_budget_api.BudgetImportTests -v`

Expected: import failure because `_deduplicate_transactions` is not defined.

- [ ] **Step 3: Extract and fix transaction deduplication**

Create `_deduplicate_transactions(transactions)` and include account, date, normalised description, amount and balance in the fallback key. Replace the inline deduplication block in `_parse_csv_files`.

- [ ] **Step 4: Write failing API test**

Patch `_parse_csv_files` and `get_db` with controlled test data. Assert that `/api/budget/summary` returns:

```json
{
  "cash_flow": {
    "horizon_days": 182,
    "cycle_days": 28,
    "personal": {"cycles": []},
    "business": {"cycles": []},
    "combined": {"cycles": []}
  }
}
```

The test logs in through Flask's test client before requesting the route.

- [ ] **Step 5: Run API test and verify RED**

Run: `python3 -m unittest tests.test_budget_api -v`

Expected: `cash_flow` is missing.

- [ ] **Step 6: Implement API integration**

Map account ownership with the existing account rules as a temporary migration fallback. Add explicit `ownership` to upcoming expenses based on their saved category/type field when available. Call `build_forecast` with Brisbane's current date and a configurable personal safety buffer.

- [ ] **Step 7: Run API and engine tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add app.py cashflow.py tests/test_budget_api.py tests/test_cashflow.py
git commit -m "feat: expose six-month cash flow forecast API"
```

### Task 4: Personal-first four-week cycle interface

**Files:**
- Modify: `dashboard.html`
- Create: `tests/test_dashboard_contract.py`

**Interfaces:**
- Consumes: `cash_flow` from `/api/budget/summary`.
- Produces: Personal Cash Flow expanded by default; Eden Commercial and Combined Position collapsed by default; each forecast cycle individually expandable.

- [ ] **Step 1: Write failing dashboard contract test**

```python
def test_dashboard_contains_six_month_cash_flow_sections(self):
    html = Path("dashboard.html").read_text()
    self.assertIn('id="bdgt-personal-cashflow"', html)
    self.assertIn('id="bdgt-business-cashflow"', html)
    self.assertIn('id="bdgt-combined-cashflow"', html)
    self.assertIn("renderCashFlow", html)
    self.assertNotIn("Based on last 3 months · Known upcoming expenses factored in", html)
```

- [ ] **Step 2: Run test and verify RED**

Run: `python3 -m unittest tests.test_dashboard_contract -v`

Expected: required section identifiers are absent.

- [ ] **Step 3: Build the cash-flow cards**

Replace the current three-month forecast cards with:

- personal summary: available now, safe to spend, lowest balance and lowest date;
- cycle 1 open and later personal cycles collapsed;
- Eden Commercial card collapsed;
- Combined Position card collapsed and labelled informational;
- per-cycle inflow, outflow, closing balance and lowest balance;
- event rows showing date, description, amount and confidence/source where available;
- warning rows that identify projected negative or below-buffer dates.

Use `textContent` or an HTML-escaping helper for all imported descriptions.

- [ ] **Step 4: Run dashboard contract and full test suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard.html tests/test_dashboard_contract.py
git commit -m "feat: show personal-first four-week cash cycles"
```

### Task 5: Forecast controls, validation and documentation

**Files:**
- Modify: `app.py`
- Modify: `dashboard.html`
- Modify: `tests/test_budget_api.py`
- Modify: `README.md` if present; otherwise create `docs/cash-flow-operations.md`

**Interfaces:**
- Consumes: existing upcoming-expense endpoints and cash-flow API.
- Produces: validated ownership, recurrence, direction and safety-buffer inputs; operations guide for imports and interpreting the forecast.

- [ ] **Step 1: Write failing validation tests**

Test that negative or non-numeric safety buffers, invalid dates, invalid ownership values and unsupported recurrence rules receive HTTP 400 without altering stored records.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_budget_api -v`

Expected: invalid values are accepted or cause 500 responses.

- [ ] **Step 3: Implement minimal validation**

Accept only:

- ownership: `personal`, `business`;
- direction: `inflow`, `outflow`;
- recurrence: empty, `weekly`, `fortnightly`, `monthly`, `quarterly`, `annual`;
- ISO calendar dates;
- finite non-negative money values.

- [ ] **Step 4: Add UI controls**

Allow upcoming events to select personal/business, inflow/outflow and recurrence. Add a personal safety-buffer control with a plain-English explanation. Preserve the existing camps, birthdays and savings-goal workflow.

- [ ] **Step 5: Write the operations guide**

Document:

1. exporting and uploading the last three months of personal and business CSVs;
2. naming/mapping personal and Eden accounts;
3. entering camps and birthdays;
4. reading cycle balances and warnings;
5. updating statements weekly;
6. backing up and restoring `family.db`;
7. the fact that Xero and direct bank automation are separate later stages.

- [ ] **Step 6: Run complete verification**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile app.py cashflow.py
git diff --check
```

Expected: all tests pass, compilation succeeds and no whitespace errors are reported.

- [ ] **Step 7: Commit**

```bash
git add app.py dashboard.html tests docs/cash-flow-operations.md
git commit -m "feat: complete six-month cash flow core"
```

## Follow-On Plans

After this core is deployed and verified with real family statements:

1. Create a separate Xero read-only integration plan for Eden Commercial.
2. Create a separate security-hardening plan before storing Xero refresh tokens.
3. Assess Australian CDR providers only after measuring the burden of weekly CSV uploads.
