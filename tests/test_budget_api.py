import unittest
from datetime import date
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
