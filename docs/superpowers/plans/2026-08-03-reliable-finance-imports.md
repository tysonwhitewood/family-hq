# Reliable Finance Imports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a registered-account CSV importer that prevents duplicate cash balances, excludes debt accounts from available cash, and visibly confirms upload progress and parsed results.

**Architecture:** Add an additive SQLite account/import registry and a focused `finance_imports.py` module for parsing, legacy classification and stable-account deduplication. The Flask API will resolve every file to registered or conservative legacy metadata before Finance and Budget consume it. The existing dashboard will gain an upload modal, byte-progress display, persistent completion message and account-classification controls.

**Tech Stack:** Python 3.12, Flask, SQLite, vanilla JavaScript, `XMLHttpRequest`, Python `unittest`

## Global Constraints

- Preserve every existing CSV and transaction; migrations are additive.
- Never store full bank account numbers.
- Ownership values are exactly `personal` and `business`.
- Account types are exactly `cash`, `credit` and `loan`.
- Cash balances alone contribute to available cash.
- Credit transactions contribute to spending and recurrence inference, but credit balances do not contribute to available cash.
- Loan transactions, balances and recurrence inference are excluded from the cash-flow forecast.
- A file is successful only when at least one supported transaction row parses.
- Inputs remain CSV-only; PDF, XLSX, OFX and QIF remain out of scope.
- No upload or account correction initiates a payment or writes to a bank or Xero.

---

## File map

- Create `finance_imports.py`: pure CSV parsing, legacy defaults and transaction deduplication.
- Create `tests/test_finance_imports.py`: parser, stable identity, debt exclusion and legacy fallback tests.
- Modify `app.py`: database migration, registry persistence, upload/account APIs, Finance summary and Budget integration.
- Modify `cashflow.py`: no interface expansion; continue receiving cash-only transactions from `app.py`.
- Modify `dashboard.html`: upload/classification modal, progress bar, persistent result and account editing.
- Modify `tests/test_budget_api.py`: registry API, upload API and cash-flow integration tests.
- Modify `tests/test_dashboard_contract.py`: dashboard control and status-state contract tests.
- Modify `docs/cash-flow-operations.md`: account selection and successful-upload operating instructions.

---

### Task 1: Add stable account and import records

**Files:**
- Modify: `app.py:230-390`
- Test: `tests/test_budget_api.py`

**Interfaces:**
- Produces: SQLite tables `finance_accounts` and `finance_imports`.
- Produces: `_legacy_account_defaults(name: str) -> dict`.
- Produces: `_registered_finance_accounts() -> list[dict]`.
- Produces: `_finance_import_map() -> dict[str, dict]`.

- [ ] **Step 1: Write failing schema and fallback tests**

Add tests that initialise a temporary database and assert:

```python
defaults = family_app._legacy_account_defaults("GSB Main Mortgage")
self.assertEqual(defaults["ownership"], "personal")
self.assertEqual(defaults["account_type"], "loan")

defaults = family_app._legacy_account_defaults("CBA Eden")
self.assertEqual(defaults["ownership"], "business")
self.assertEqual(defaults["account_type"], "cash")
```

Also assert that `PRAGMA table_info(finance_accounts)` contains `name`, `ownership`, `account_type` and `active`, and `PRAGMA table_info(finance_imports)` contains `stored_filename`, `account_id`, `parsed_count`, `earliest_date`, `latest_date` and `status`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceRegistryTests -v
```

Expected: failures because the tables and `_legacy_account_defaults` do not exist.

- [ ] **Step 3: Add the schema and helpers**

Add these tables to `init_db()`:

```sql
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
```

Implement conservative fallback rules:

```python
def _legacy_account_defaults(name):
    lower = str(name).lower()
    account_type = (
        "loan" if any(word in lower for word in ("loan", "mortgage"))
        else "credit" if "credit card" in lower
        else "cash"
    )
    ownership = "business" if _is_business_account(name) else "personal"
    return {"ownership": ownership, "account_type": account_type}
```

Registry reads must return ordinary dictionaries and never expose internal filesystem paths.

- [ ] **Step 4: Run the focused and existing tests**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceRegistryTests -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_budget_api.py
git commit -m "feat: register finance accounts and imports"
```

---

### Task 2: Extract single-file parsing and stable deduplication

**Files:**
- Create: `finance_imports.py`
- Create: `tests/test_finance_imports.py`
- Modify: `app.py:1279-1465`

**Interfaces:**
- Consumes: account metadata dictionaries with `id`, `name`, `ownership` and `account_type`.
- Produces: `parse_csv_file(path: Path, metadata: dict) -> list[dict]`.
- Produces: `deduplicate_transactions(transactions: list[dict]) -> list[dict]`.
- Each transaction includes `account`, `account_key`, `account_id`, `ownership`, `account_type`, `source_filename`, `date`, `amount`, `description` and `balance`.

- [ ] **Step 1: Write failing parser identity tests**

Create temporary CBA-style CSV snapshots and assert:

```python
metadata = {
    "id": 7,
    "name": "CBA Eden",
    "ownership": "business",
    "account_type": "cash",
}
rows_a = parse_csv_file(first_path, metadata)
rows_b = parse_csv_file(second_path, metadata)
combined = deduplicate_transactions(rows_a + rows_b)

self.assertTrue(all(row["account_key"] == "registered:7" for row in combined))
self.assertTrue(all(row["ownership"] == "business" for row in combined))
self.assertEqual(len(combined), 3)
```

Add a second test proving the same date, amount, description and balance remain separate when the stable account IDs differ.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_finance_imports -v
```

Expected: import failure because `finance_imports.py` does not exist.

- [ ] **Step 3: Implement the focused parser module**

Move the flexible date, amount and balance parsing into `finance_imports.py`. Implement `parse_csv_file()` for the existing header-based ING/Great Southern formats and headerless CBA format.

The account key must be:

```python
account_key = (
    f"registered:{metadata['id']}"
    if metadata.get("id") is not None
    else f"legacy:{path.name.lower()}"
)
```

Deduplicate with:

```python
key = (
    row["account_key"],
    row["date"],
    round(float(row["amount"]), 2),
    normalised_description,
    row.get("balance"),
)
```

Refactor `_parse_csv_files()` to resolve metadata from `finance_imports` by stored filename, fall back through `_legacy_account_defaults()`, call `parse_csv_file()` for each file, then deduplicate once.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_finance_imports -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add finance_imports.py app.py tests/test_finance_imports.py
git commit -m "refactor: parse statements by stable account"
```

---

### Task 3: Apply cash, credit and loan forecast rules

**Files:**
- Modify: `app.py:1535-1590`
- Modify: `app.py:1841-1905`
- Test: `tests/test_budget_api.py`

**Interfaces:**
- Consumes: transaction `ownership` and `account_type` from Task 2.
- Produces: `_budget_cash_flow()` with cash-only opening balances and cash/credit recurrence history.
- Produces: Finance summaries grouped by `account_key`, not filename.

- [ ] **Step 1: Write failing financial-rule tests**

Add one test with:

```python
transactions = [
    {
        "account": "ING Everyday", "account_key": "registered:1",
        "date": "2026-08-03", "amount": 0, "description": "Balance",
        "balance": 2500, "ownership": "personal", "account_type": "cash",
    },
    {
        "account": "CBA Credit Card", "account_key": "registered:2",
        "date": "2026-08-03", "amount": -80, "description": "Groceries",
        "balance": 1200, "ownership": "personal", "account_type": "credit",
    },
    {
        "account": "GSB Mortgage", "account_key": "registered:3",
        "date": "2026-08-03", "amount": -3700, "description": "Loan debit",
        "balance": -758770, "ownership": "personal", "account_type": "loan",
    },
    {
        "account": "CBA Eden", "account_key": "registered:4",
        "date": "2026-08-03", "amount": 0, "description": "Balance",
        "balance": 9000, "ownership": "business", "account_type": "cash",
    },
]
```

Assert:

```python
self.assertEqual(forecast["personal"]["opening_balance"], 2500)
self.assertEqual(forecast["business"]["opening_balance"], 9000)
```

Add recurrence input assertions proving credit-card spending remains eligible and loan-side rows are absent. Add a Finance summary test proving categories use `transaction["ownership"]` instead of `_is_business_account(transaction["account"])`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceAccountRuleTests -v
```

Expected: the loan balance enters Personal cash or the credit/ownership assertions fail.

- [ ] **Step 3: Implement the account-type filters**

In `_budget_cash_flow()`:

```python
cash_transactions = [
    row for row in transactions if row.get("account_type", "cash") == "cash"
]
recurrence_transactions = [
    row for row in transactions
    if row.get("account_type", "cash") in {"cash", "credit"}
    and _categorise(row.get("description", "")) != "Transfers"
]
ownership = {
    row["account"]: row.get("ownership", "personal")
    for row in transactions
}
```

Use `recurrence_transactions` for `infer_recurring_events()` and pass `cash_transactions` to `build_forecast()`.

In Finance summary aggregation, group by `account_key`, display the registered account name, and read ownership/type from the transaction metadata. Do not infer these values from the display name.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceAccountRuleTests -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass and the mortgage regression is covered.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_budget_api.py
git commit -m "fix: exclude debt accounts from cash"
```

---

### Task 4: Validate uploads and return parsed confirmation

**Files:**
- Modify: `app.py:1519-1533`
- Test: `tests/test_budget_api.py`

**Interfaces:**
- Consumes: `parse_csv_file()` from Task 2.
- Produces: `POST /api/finance/upload-csv`.
- Accepts multipart `file` plus either `account_id` or `account_name`, `ownership`, `account_type`.
- Returns `account`, `saved`, `parsed_count`, `earliest_date`, `latest_date`, `message`.

- [ ] **Step 1: Write failing upload API tests**

Test a supported CSV upload:

```python
self.assertEqual(response.status_code, 200)
payload = response.get_json()
self.assertEqual(payload["parsed_count"], 2)
self.assertEqual(payload["latest_date"], "2026-05-29")
self.assertIn("2 transactions loaded", payload["message"])
```

Add separate tests that assert:

- invalid ownership returns 400;
- invalid account type returns 400;
- a CSV with zero supported rows returns 422;
- an existing account ID overrides submitted new-account fields;
- re-uploading the same stored filename updates one `finance_imports` row.

- [ ] **Step 2: Run the upload tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceUploadTests -v
```

Expected: current endpoint returns only `ok` and `saved`, and invalid metadata is accepted.

- [ ] **Step 3: Implement validated parse-before-success upload**

Validate `.csv` case-insensitively and validate account metadata before writing the final file. Save to a temporary file inside `DATA_DIR / "bank_statements"`, parse that temporary file, and remove it when zero rows parse.

After a successful parse:

1. create or select the registered account;
2. replace the final stored file atomically with `os.replace`;
3. upsert `finance_imports` on `stored_filename`;
4. return the parsed count and date range.

Use exact status codes:

```python
return jsonify({"error": "ownership must be personal or business"}), 400
return jsonify({"error": "account_type must be cash, credit or loan"}), 400
return jsonify({"error": "No supported transactions found in this CSV"}), 422
```

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceUploadTests -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_budget_api.py
git commit -m "feat: confirm parsed finance uploads"
```

---

### Task 5: Add account creation, editing and legacy linking APIs

**Files:**
- Modify: `app.py`
- Test: `tests/test_budget_api.py`

**Interfaces:**
- Produces: `GET /api/finance/accounts`.
- Produces: `POST /api/finance/accounts`.
- Produces: `PUT /api/finance/accounts/<int:account_id>`.
- Produces: `POST /api/finance/accounts/link-legacy`.

- [ ] **Step 1: Write failing account API tests**

Assert the account list includes registered accounts and legacy files with:

```json
{
  "id": null,
  "name": "GSB Main Mortgage",
  "ownership": "personal",
  "account_type": "loan",
  "source_filenames": ["GSB Main Mortgage.csv"],
  "legacy": true
}
```

Test creating and updating an account, duplicate-name rejection, invalid values, nonexistent IDs and linking a known legacy stored filename to an existing account.

- [ ] **Step 2: Run the account API tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceAccountApiTests -v
```

Expected: 404 responses because the endpoints do not exist.

- [ ] **Step 3: Implement account APIs**

All writes must validate names after trimming and restrict ownership/type to the global values. The legacy-link endpoint accepts:

```json
{"stored_filename": "CBA_29.05.26.csv", "account_id": 3}
```

It must confirm the filename exists inside an allowed Finance CSV directory, parse it using the selected account metadata, then upsert its `finance_imports` link and parsed date/count metadata. It must not rename or delete the source CSV.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_budget_api.FinanceAccountApiTests -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_budget_api.py
git commit -m "feat: manage finance account classifications"
```

---

### Task 6: Build visible upload progress and classification controls

**Files:**
- Modify: `dashboard.html:810-860`
- Modify: `dashboard.html:2280-2410`
- Test: `tests/test_dashboard_contract.py`

**Interfaces:**
- Consumes: account and upload APIs from Tasks 4 and 5.
- Produces: `finSelectUploadFile(input)`, `finSubmitUpload()`, `finSetUploadStatus(state, message, percent)`, `finFormatDate(isoDate)`, `finEditAccount(accountKey)`.

- [ ] **Step 1: Write failing dashboard contract tests**

Assert the rendered source contains:

```python
for marker in [
    'id="fin-upload-modal"',
    'id="fin-upload-progress"',
    'id="fin-upload-status"',
    'function finSubmitUpload(',
    'xhr.upload.onprogress',
    'Processing transactions',
    'Upload successful',
    'Edit classification',
]:
    self.assertIn(marker, dashboard)
```

- [ ] **Step 2: Run the dashboard tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_dashboard_contract.DashboardContractTests -v
```

Expected: failures for the new modal, progress and account controls.

- [ ] **Step 3: Add the upload modal**

Add accessible fields for selected filename, existing-account selector, new-account name, ownership and type. Existing account selection hides the new-account fields. The modal must not send until a valid account selection is present.

Use a persistent status block below **Recent Transactions**. State colours:

- uploading/processing: blue;
- success: green;
- error: red.

- [ ] **Step 4: Implement byte-level progress and result handling**

Use `XMLHttpRequest`:

```javascript
xhr.upload.onprogress = event => {
  if (!event.lengthComputable) return;
  const percent = Math.round((event.loaded / event.total) * 100);
  finSetUploadStatus('uploading', `Uploading ${file.name} — ${percent}%`, percent);
};
xhr.upload.onload = () => {
  finSetUploadStatus('processing', 'Processing transactions…', 100);
};
```

On HTTP 200, retain:

```javascript
finSetUploadStatus(
  'success',
  `Upload successful — ${payload.parsed_count} transactions loaded, latest ${finFormatDate(payload.latest_date)}`,
  100
);
```

Refresh Finance without replacing the status block. On non-200 responses, display the server error and keep the modal available for correction.

- [ ] **Step 5: Add account classification controls**

Render ownership/type badges on every account. **Edit classification** opens a modal supporting registered-account edits and legacy linking. After a successful update, reload Finance and Budget data.

- [ ] **Step 6: Run dashboard, JavaScript and full tests**

Run:

```bash
python3 -m unittest tests.test_dashboard_contract.DashboardContractTests -v
script_start=$(rg -n '^<script>$' dashboard.html | tail -1 | cut -d: -f1)
script_end=$(rg -n '^</script>$' dashboard.html | tail -1 | cut -d: -f1)
sed -n "$((script_start + 1)),$((script_end - 1))p" dashboard.html | node --check
python3 -m unittest discover -s tests -v
```

Expected: all tests and JavaScript syntax checks pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard.html tests/test_dashboard_contract.py
git commit -m "feat: show finance upload progress"
```

---

### Task 7: Update operations guidance and verify end to end

**Files:**
- Modify: `docs/cash-flow-operations.md`
- Test: all test files

**Interfaces:**
- Consumes: completed account registry, parser, API and UI.
- Produces: operator guidance for initial and weekly uploads.

- [ ] **Step 1: Update the operating guide**

Document:

- selecting an existing account for later statement snapshots;
- creating Personal or Eden Commercial accounts;
- choosing cash, credit or loan;
- interpreting the progress bar and parsed confirmation;
- correcting existing `CBA_29.05.26`, ING and Great Southern account classifications;
- never relying on a forecast while legacy account classifications remain unreviewed.

- [ ] **Step 2: Run the complete verification suite**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile app.py cashflow.py finance_imports.py
script_start=$(rg -n '^<script>$' dashboard.html | tail -1 | cut -d: -f1)
script_end=$(rg -n '^</script>$' dashboard.html | tail -1 | cut -d: -f1)
sed -n "$((script_start + 1)),$((script_end - 1))p" dashboard.html | node --check
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 3: Verify locally in a browser**

With a temporary database and non-sensitive CSV fixtures:

1. select a file;
2. create a Personal cash account;
3. observe percentage progress;
4. observe `Processing transactions…`;
5. confirm the green parsed-count/date message persists;
6. upload an overlapping snapshot to the same account;
7. confirm the balance is counted once;
8. create a loan account and confirm its balance is absent from Personal cash;
9. confirm Eden and Personal remain separated;
10. confirm the browser console contains no errors.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/cash-flow-operations.md
git commit -m "docs: explain registered statement uploads"
```

- [ ] **Step 5: Finish the branch**

Use `superpowers:verification-before-completion`, then `superpowers:finishing-a-development-branch`. Present the integration options and do not push or deploy until the user chooses.
