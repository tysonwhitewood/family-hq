import json as _json
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
