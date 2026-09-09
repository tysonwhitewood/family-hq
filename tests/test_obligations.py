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

    def test_paid_once_only_hold_drops_out_of_the_target(self):
        occurrences = self.occurrences + [
            {"id": 14, "obligation_id": 2, "standard_date": "2026-09-15", "due_date": "2026-09-15", "state": "paid"}]
        rows = {t["account_key"]: t for t in ob.account_targets(
            self.today, self.obligations, occurrences, self.receipts, self.balances, SETTINGS)}
        self.assertNotIn("PropVesting BAS + final return", [c["label"] for c in rows["ecomm_gst"]["components"]])

    def test_household_setaside_lines_use_monthly_equivalents(self):
        lines = ob.household_setaside_lines(self.obligations, self.occurrences, SETTINGS)
        by_name = {l["name"]: l for l in lines}
        self.assertAlmostEqual(by_name["Council rates (Scenic Rim)"]["monthly"], 1614.19 / 6, places=2)
        self.assertAlmostEqual(by_name["Water (Urban Utilities)"]["monthly"], 713.45 / 3, places=2)
        self.assertNotIn("Home & contents (RACQ)", by_name)
        self.assertNotIn("Mortgage repayment", by_name)


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


if __name__ == "__main__":
    unittest.main()
