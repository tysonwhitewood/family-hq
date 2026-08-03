import unittest
from pathlib import Path


class DashboardContractTests(unittest.TestCase):
    def test_dashboard_contains_six_month_cash_flow_sections(self):
        html = Path("dashboard.html").read_text()

        self.assertIn('id="bdgt-personal-cashflow"', html)
        self.assertIn('id="bdgt-business-cashflow"', html)
        self.assertIn('id="bdgt-combined-cashflow"', html)
        self.assertIn("renderCashFlow", html)
        self.assertNotIn(
            "Based on last 3 months · Known upcoming expenses factored in",
            html,
        )

    def test_dashboard_exposes_forecast_controls(self):
        html = Path("dashboard.html").read_text()

        self.assertIn("bdgtEditSafetyBuffer", html)
        self.assertIn("ownership:", html)
        self.assertIn("direction:", html)
        self.assertIn("recurrence:", html)


if __name__ == "__main__":
    unittest.main()
