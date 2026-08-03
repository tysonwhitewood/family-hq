import unittest
import warnings
from datetime import date
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
