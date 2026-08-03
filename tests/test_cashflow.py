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


if __name__ == "__main__":
    unittest.main()
