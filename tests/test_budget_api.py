import unittest
import warnings
from datetime import date
from io import BytesIO
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

warnings.filterwarnings(
    "ignore",
    message="Using the in-memory storage for tracking rate limits.*",
)
warnings.filterwarnings(
    "ignore",
    message="datetime.datetime.utcnow\\(\\) is deprecated.*",
)

with patch("threading.Thread.start"):
    import app as family_app


class BudgetImportTests(unittest.TestCase):
    def test_equal_transactions_on_different_accounts_are_not_duplicates(self):
        rows = [
            {
                "account": "Personal",
                "date": "2026-07-01",
                "amount": -25,
                "description": "CAFE",
                "balance": 100,
            },
            {
                "account": "Eden",
                "date": "2026-07-01",
                "amount": -25,
                "description": "CAFE",
                "balance": 500,
            },
        ]

        self.assertEqual(len(family_app._deduplicate_transactions(rows)), 2)

    def test_same_transaction_snapshot_is_deduplicated(self):
        row = {
            "account": "Personal",
            "date": "2026-07-01",
            "amount": -25,
            "description": "CAFE",
            "balance": 100,
        }

        self.assertEqual(
            len(family_app._deduplicate_transactions([row, dict(row)])),
            1,
        )


class FinanceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.init_db()

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_legacy_account_defaults_classify_mortgages_and_business_cash(self):
        defaults = family_app._legacy_account_defaults("GSB Main Mortgage")
        self.assertEqual(defaults["ownership"], "personal")
        self.assertEqual(defaults["account_type"], "loan")

        defaults = family_app._legacy_account_defaults("CBA Eden")
        self.assertEqual(defaults["ownership"], "business")
        self.assertEqual(defaults["account_type"], "cash")

    def test_finance_registry_tables_have_required_columns(self):
        with family_app.get_db() as db:
            account_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(finance_accounts)")
            }
            import_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(finance_imports)")
            }

        self.assertTrue({"name", "ownership", "account_type", "active"} <= account_columns)
        self.assertTrue(
            {"stored_filename", "account_id", "parsed_count", "earliest_date", "latest_date", "status"}
            <= import_columns
        )


class FinanceUploadTests(unittest.TestCase):
    """The upload contract protects the statement directory and import registry."""

    supported_csv = (
        b"29/05/2026,-42.50,Coffee shop,105.00\n"
        b"28/05/2026,1500.00,Salary,1605.00\n"
    )

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_data_dir = family_app.DATA_DIR
        family_app.DATA_DIR = Path(self.temp_dir.name)
        family_app.DB_PATH = family_app.DATA_DIR / "family.db"
        family_app.init_db()
        family_app.app.config.update(TESTING=True)
        self.client = family_app.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = family_app.USERNAME
            session["_fresh"] = True

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.DATA_DIR = self.original_data_dir
        self.temp_dir.cleanup()

    def _upload(self, csv_data, filename="statement.csv", **metadata):
        return self.client.post(
            "/api/finance/upload-csv",
            data={
                "file": (BytesIO(csv_data), filename),
                **metadata,
            },
            content_type="multipart/form-data",
        )

    def test_supported_csv_returns_parsed_confirmation(self):
        response = self._upload(
            self.supported_csv,
            "May.CSV",
            account_name="Everyday account",
            ownership="personal",
            account_type="cash",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["parsed_count"], 2)
        self.assertEqual(payload["latest_date"], "2026-05-29")
        self.assertIn("2 transactions loaded", payload["message"])

    def test_rejects_invalid_ownership(self):
        response = self._upload(
            self.supported_csv,
            account_name="Everyday account",
            ownership="family",
            account_type="cash",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "ownership must be personal or business"},
        )

    def test_rejects_invalid_account_type(self):
        response = self._upload(
            self.supported_csv,
            account_name="Everyday account",
            ownership="personal",
            account_type="investment",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "account_type must be cash, credit or loan"},
        )

    def test_rejects_csv_without_supported_rows_without_preserving_file(self):
        response = self._upload(
            b"not,a,supported,statement\n",
            account_name="Everyday account",
            ownership="personal",
            account_type="cash",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json(),
            {"error": "No supported transactions found in this CSV"},
        )
        self.assertFalse((family_app.DATA_DIR / "bank_statements" / "statement.csv").exists())

    def test_existing_account_id_overrides_new_account_fields(self):
        now = "2026-08-03T00:00:00"
        with family_app.get_db() as db:
            cursor = db.execute(
                "INSERT INTO finance_accounts "
                "(name, ownership, account_type, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("Existing account", "business", "loan", 1, now, now),
            )
            account_id = cursor.lastrowid

        response = self._upload(
            self.supported_csv,
            account_id=str(account_id),
            account_name="Attempted replacement account",
            ownership="personal",
            account_type="cash",
        )

        self.assertEqual(response.status_code, 200)
        with family_app.get_db() as db:
            import_row = db.execute(
                "SELECT account_id FROM finance_imports WHERE stored_filename = ?",
                ("statement.csv",),
            ).fetchone()
            replacement_account = db.execute(
                "SELECT id FROM finance_accounts WHERE name = ?",
                ("Attempted replacement account",),
            ).fetchone()
        self.assertEqual(import_row["account_id"], account_id)
        self.assertIsNone(replacement_account)

    def test_reuploading_stored_filename_updates_one_import_row(self):
        metadata = {
            "account_name": "Everyday account",
            "ownership": "personal",
            "account_type": "cash",
        }
        first = self._upload(self.supported_csv, "statement.csv", **metadata)
        second = self._upload(
            b"30/05/2026,-10.00,Groceries,95.00\n",
            "statement.csv",
            **metadata,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        with family_app.get_db() as db:
            rows = db.execute(
                "SELECT parsed_count, latest_date FROM finance_imports "
                "WHERE stored_filename = ?",
                ("statement.csv",),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parsed_count"], 1)
        self.assertEqual(rows[0]["latest_date"], "2026-05-30")
        self.assertEqual(
            (family_app.DATA_DIR / "bank_statements" / "statement.csv").read_bytes(),
            b"30/05/2026,-10.00,Groceries,95.00\n",
        )

    def test_failed_import_upsert_restores_existing_stored_file(self):
        metadata = {
            "account_name": "Everyday account",
            "ownership": "personal",
            "account_type": "cash",
        }
        first = self._upload(self.supported_csv, "statement.csv", **metadata)
        self.assertEqual(first.status_code, 200)
        with family_app.get_db() as db:
            db.execute(
                "CREATE TRIGGER fail_import_update BEFORE UPDATE ON finance_imports "
                "BEGIN SELECT RAISE(ABORT, 'forced import failure'); END"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self._upload(
                b"30/05/2026,-10.00,Groceries,95.00\n",
                "statement.csv",
                **metadata,
            )

        self.assertEqual(
            (family_app.DATA_DIR / "bank_statements" / "statement.csv").read_bytes(),
            self.supported_csv,
        )


class FinanceAccountRuleTests(unittest.TestCase):
    def setUp(self):
        family_app.app.config.update(TESTING=True)

    def _authenticated_client(self):
        client = family_app.app.test_client()
        response = client.post(
            "/login",
            data={
                "username": family_app.USERNAME,
                "password": family_app.PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_cash_forecast_excludes_debt_balances_but_keeps_credit_history(self):
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

        with patch.object(family_app, "infer_recurring_events", return_value=[]) as infer:
            forecast = family_app._budget_cash_flow(
                transactions,
                [],
                forecast_date=date(2026, 8, 3),
                safety_buffer=0,
            )

        self.assertEqual(forecast["personal"]["opening_balance"], 2500)
        self.assertEqual(forecast["business"]["opening_balance"], 9000)
        recurrence_transactions = infer.call_args.args[0]
        self.assertIn("registered:2", [row["account"] for row in recurrence_transactions])
        self.assertNotIn("registered:3", [row["account"] for row in recurrence_transactions])

    def test_forecast_keeps_same_display_name_account_keys_separate(self):
        transactions = [
            {
                "account": "Shared Everyday", "account_key": "registered:1",
                "date": "2026-05-07", "amount": -75, "description": "Monthly service",
                "balance": 1000, "ownership": "personal", "account_type": "cash",
            },
            {
                "account": "Shared Everyday", "account_key": "registered:2",
                "date": "2026-06-07", "amount": -75, "description": "Monthly service",
                "balance": 2000, "ownership": "business", "account_type": "cash",
            },
            {
                "account": "Shared Everyday", "account_key": "registered:3",
                "date": "2026-07-07", "amount": -75, "description": "Monthly service",
                "balance": None, "ownership": "personal", "account_type": "cash",
            },
        ]

        forecast = family_app._budget_cash_flow(
            transactions,
            [],
            forecast_date=date(2026, 8, 3),
            safety_buffer=0,
        )

        self.assertEqual(forecast["personal"]["opening_balance"], 1000)
        self.assertEqual(forecast["business"]["opening_balance"], 2000)
        historical_events = [
            event
            for owner in ("personal", "business")
            for day in forecast[owner]["days"]
            for event in day["events"]
            if event["source"] == "transaction_history"
        ]
        self.assertEqual(historical_events, [])

    @patch.object(family_app, "_parse_csv_files")
    def test_finance_summary_groups_by_account_key_and_uses_metadata_ownership(self, parse_files):
        today = date.today().isoformat()
        parse_files.return_value = [
            {
                "account": "CBA Everyday", "account_key": "registered:1",
                "date": today, "amount": -20, "description": "Woolworths",
                "balance": 100, "ownership": "business", "account_type": "cash",
            },
            {
                "account": "CBA Everyday", "account_key": "registered:2",
                "date": today, "amount": -30, "description": "Woolworths",
                "balance": 200, "ownership": "personal", "account_type": "cash",
            },
        ]

        response = self._authenticated_client().get("/api/finance/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["accounts"]), 2)
        self.assertEqual(payload["category_spend_business"]["Groceries"], 20)
        self.assertEqual(payload["category_spend_personal"]["Groceries"], 30)

    @patch.object(family_app, "_parse_csv_files")
    def test_finance_summary_excludes_loan_spending(self, parse_files):
        today = date.today().isoformat()
        parse_files.return_value = [
            {
                "account": "ING Everyday", "account_key": "registered:1",
                "date": today, "amount": -10, "description": "Woolworths",
                "balance": 1000, "ownership": "personal", "account_type": "cash",
            },
            {
                "account": "GSB Mortgage", "account_key": "registered:2",
                "date": today, "amount": -3700, "description": "Mortgage repay",
                "balance": -758770, "ownership": "personal", "account_type": "loan",
            },
            {
                "account": "CBA Credit Card", "account_key": "registered:3",
                "date": today, "amount": -20, "description": "Woolworths",
                "balance": 500, "ownership": "personal", "account_type": "credit",
            },
        ]

        response = self._authenticated_client().get("/api/finance/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["category_spend_personal"], {"Groceries": 30.0})
        self.assertEqual(payload["monthly_expenses"], {today[:7]: 30.0})


class BudgetApiTests(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings(
            "ignore",
            category=DeprecationWarning,
            module=r"flask_login.*",
        )
        family_app.app.config.update(TESTING=True)
        self.client = family_app.app.test_client()
        response = self.client.post(
            "/login",
            data={
                "username": family_app.USERNAME,
                "password": family_app.PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)

    @patch.object(family_app, "_parse_csv_files")
    def test_budget_summary_contains_six_month_cash_flow(self, parse_files):
        parse_files.return_value = [{
            "account": "ING Everyday",
            "date": date.today().isoformat(),
            "amount": 0,
            "description": "Opening balance",
            "balance": 2500,
        }]

        response = self.client.get("/api/budget/summary")

        self.assertEqual(response.status_code, 200)
        cash_flow = response.get_json()["cash_flow"]
        self.assertEqual(cash_flow["horizon_days"], 182)
        self.assertEqual(cash_flow["cycle_days"], 28)
        self.assertEqual(len(cash_flow["personal"]["cycles"]), 7)
        self.assertEqual(len(cash_flow["business"]["cycles"]), 7)
        self.assertEqual(len(cash_flow["combined"]["cycles"]), 7)

    @patch.object(family_app, "_parse_csv_files")
    def test_budget_summary_forecasts_recurring_history(self, parse_files):
        parse_files.return_value = [
            {"account": "ING Everyday", "date": "2026-05-07", "amount": 4000, "description": "EDEN SALARY 1001", "balance": 2000},
            {"account": "ING Everyday", "date": "2026-06-07", "amount": 4000, "description": "EDEN SALARY 1002", "balance": 2200},
            {"account": "ING Everyday", "date": "2026-07-07", "amount": 4000, "description": "EDEN SALARY 1003", "balance": 2500},
        ]

        response = self.client.get("/api/budget/forecast?start=2026-08-03")

        self.assertEqual(response.status_code, 200)
        days = response.get_json()["personal"]["days"]
        salary_day = next(day for day in days if day["date"] == "2026-08-07")
        self.assertEqual(salary_day["inflows"], 4000)
        self.assertEqual(
            salary_day["events"][0]["source"],
            "transaction_history",
        )

    def test_upcoming_rejects_invalid_ownership(self):
        response = self.client.post(
            "/api/budget/upcoming",
            json={
                "description": "Camp",
                "amount": 500,
                "due_date": "2026-09-10",
                "ownership": "family",
                "direction": "outflow",
                "recurrence": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("ownership", response.get_json()["error"])

    def test_upcoming_rejects_invalid_recurrence(self):
        response = self.client.post(
            "/api/budget/upcoming",
            json={
                "description": "Camp",
                "amount": 500,
                "due_date": "2026-09-10",
                "ownership": "personal",
                "direction": "outflow",
                "recurrence": "sometimes",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("recurrence", response.get_json()["error"])

    def test_upcoming_rejects_invalid_date_and_amount(self):
        invalid_date = self.client.post(
            "/api/budget/upcoming",
            json={
                "description": "Birthday",
                "amount": 300,
                "due_date": "10/09/2026",
            },
        )
        negative_amount = self.client.post(
            "/api/budget/upcoming",
            json={
                "description": "Birthday",
                "amount": -300,
                "due_date": "2026-09-10",
            },
        )

        self.assertEqual(invalid_date.status_code, 400)
        self.assertEqual(negative_amount.status_code, 400)

    def test_settings_reject_negative_safety_buffer(self):
        response = self.client.post(
            "/api/budget/settings",
            json={"safety_buffer": -1},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("safety_buffer", response.get_json()["error"])

    def test_legacy_recurring_upcoming_expense_defaults_to_annual(self):
        forecast = family_app._budget_cash_flow(
            [],
            [{
                "description": "ASIC Annual Fee",
                "amount": 500,
                "due_date": "2026-08-10",
                "recurring": 1,
                "category": "ASIC / Compliance",
            }],
            forecast_date=date(2026, 8, 3),
            safety_buffer=0,
        )

        event_dates = [
            day["date"]
            for day in forecast["personal"]["days"]
            if day["events"]
        ]
        self.assertEqual(event_dates, ["2026-08-10"])


if __name__ == "__main__":
    unittest.main()
