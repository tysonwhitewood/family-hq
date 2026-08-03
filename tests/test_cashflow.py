import unittest
from datetime import date

from cashflow import build_forecast


class CashFlowCalendarTests(unittest.TestCase):
    def test_forecast_has_182_days_and_seven_cycles(self):
        result = build_forecast([], [], {}, date(2026, 8, 3), 1000)

        self.assertEqual(len(result["personal"]["days"]), 182)
        self.assertEqual(
            [len(cycle["days"]) for cycle in result["personal"]["cycles"]],
            [28, 28, 28, 28, 28, 28, 14],
        )

    def test_cycles_are_sequential_without_duplicate_dates(self):
        result = build_forecast([], [], {}, date(2026, 8, 3), 1000)

        dates = [
            day["date"]
            for cycle in result["personal"]["cycles"]
            for day in cycle["days"]
        ]
        self.assertEqual(len(dates), len(set(dates)))
        self.assertEqual(dates[0], "2026-08-03")
        self.assertEqual(dates[-1], "2027-01-31")


class CashFlowRulesTests(unittest.TestCase):
    def test_personal_and_business_cash_stay_separate(self):
        transactions = [
            {
                "account": "Everyday",
                "date": "2026-08-03",
                "amount": 0,
                "description": "balance",
                "balance": 2000,
            },
            {
                "account": "Eden",
                "date": "2026-08-03",
                "amount": 0,
                "description": "balance",
                "balance": 10000,
            },
        ]

        result = build_forecast(
            transactions,
            [],
            {"Everyday": "personal", "Eden": "business"},
            date(2026, 8, 3),
            500,
        )

        self.assertEqual(result["personal"]["opening_balance"], 2000)
        self.assertEqual(result["business"]["opening_balance"], 10000)
        self.assertEqual(result["personal"]["safe_to_spend"], 1500)

    def test_scheduled_expense_changes_balance_on_due_date(self):
        events = [{
            "description": "School camp",
            "amount": 800,
            "due_date": "2026-08-20",
            "recurring": "",
            "category": "School / Kids",
            "ownership": "personal",
        }]

        result = build_forecast([], events, {}, date(2026, 8, 3), 0)

        day = next(
            item
            for item in result["personal"]["days"]
            if item["date"] == "2026-08-20"
        )
        self.assertEqual(day["outflows"], 800)
        self.assertEqual(day["closing_balance"], -800)

    def test_business_inflow_never_increases_personal_balance(self):
        events = [{
            "description": "TCS invoice",
            "amount": 5000,
            "due_date": "2026-08-10",
            "direction": "inflow",
            "recurring": "",
            "ownership": "business",
        }]

        result = build_forecast([], events, {}, date(2026, 8, 3), 0)

        self.assertEqual(result["business"]["closing_balance"], 5000)
        self.assertEqual(result["personal"]["closing_balance"], 0)
        self.assertEqual(result["combined"]["closing_balance"], 5000)

    def test_weekly_and_fortnightly_events_repeat_on_schedule(self):
        events = [
            {
                "description": "Groceries allowance",
                "amount": 100,
                "due_date": "2026-08-03",
                "recurring": "weekly",
                "ownership": "personal",
            },
            {
                "description": "Salary",
                "amount": 1000,
                "due_date": "2026-08-07",
                "direction": "inflow",
                "recurring": "fortnightly",
                "ownership": "personal",
            },
        ]

        result = build_forecast(
            [], events, {}, date(2026, 8, 3), 0, horizon_days=35
        )

        grocery_dates = [
            day["date"]
            for day in result["personal"]["days"]
            if any(event["description"] == "Groceries allowance" for event in day["events"])
        ]
        salary_dates = [
            day["date"]
            for day in result["personal"]["days"]
            if any(event["description"] == "Salary" for event in day["events"])
        ]
        self.assertEqual(
            grocery_dates,
            ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"],
        )
        self.assertEqual(
            salary_dates,
            ["2026-08-07", "2026-08-21", "2026-09-04"],
        )

    def test_monthly_event_uses_last_valid_day(self):
        events = [{
            "description": "Month end bill",
            "amount": 50,
            "due_date": "2026-08-31",
            "recurring": "monthly",
            "ownership": "personal",
        }]

        result = build_forecast(
            [], events, {}, date(2026, 8, 1), 0, horizon_days=100
        )

        event_dates = [
            day["date"]
            for day in result["personal"]["days"]
            if day["events"]
        ]
        self.assertEqual(
            event_dates,
            ["2026-08-31", "2026-09-30", "2026-10-31"],
        )


if __name__ == "__main__":
    unittest.main()
