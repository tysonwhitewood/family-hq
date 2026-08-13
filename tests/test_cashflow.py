import unittest
from datetime import date

from cashflow import build_forecast, infer_recurring_events, match_internal_transfers


class InternalTransferMatchingTests(unittest.TestCase):
    """Eden paying the family is only visible as a matching pair of entries."""

    def _txn(self, account, ownership, day, amount, description):
        return {"account": account, "ownership": ownership, "date": day,
                "amount": amount, "description": description}

    def test_a_business_payment_is_matched_to_the_personal_credit(self):
        matched = match_internal_transfers([
            self._txn("Eden", "business", "2026-08-01", -5000,
                      "Transfer to T and R Whitewood director payment"),
            self._txn("ING", "personal", "2026-08-01", 5000,
                      "TYSON WHITEWOOD director payment - Deposit"),
        ])

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["amount"], 5000)
        self.assertEqual(matched[0]["from_side"], "business")
        self.assertEqual(matched[0]["to_side"], "personal")

    def test_a_few_days_of_settlement_lag_still_matches(self):
        matched = match_internal_transfers([
            self._txn("Eden", "business", "2026-08-01", -2000, "Transfer out"),
            self._txn("ING", "personal", "2026-08-04", 2000, "Osko payment"),
        ])

        self.assertEqual(len(matched), 1)

    def test_money_moved_between_two_personal_accounts_is_not_a_match(self):
        matched = match_internal_transfers([
            self._txn("ING", "personal", "2026-08-01", -900, "Transfer to savings"),
            self._txn("GSB", "personal", "2026-08-01", 900, "Transfer from ING"),
        ])

        self.assertEqual(matched, [])

    def test_a_credit_with_no_matching_payment_is_not_income_from_the_business(self):
        # Family lending money, or an unrelated deposit, has no Eden leg.
        matched = match_internal_transfers([
            self._txn("Eden", "business", "2026-08-01", -5000, "Transfer to family"),
            self._txn("ING", "personal", "2026-08-01", 5000, "Deposit"),
            self._txn("ING", "personal", "2026-08-02", 10000, "Sister loan deposit"),
            self._txn("ING", "personal", "2026-03-26", 74790, "Credit Transfer From"),
        ])

        self.assertEqual([m["amount"] for m in matched], [5000])

    def test_each_payment_is_only_matched_once(self):
        matched = match_internal_transfers([
            self._txn("Eden", "business", "2026-08-01", -1000, "Transfer"),
            self._txn("ING", "personal", "2026-08-01", 1000, "Deposit"),
            self._txn("ING", "personal", "2026-08-02", 1000, "Another deposit"),
        ])

        self.assertEqual(len(matched), 1)

    def test_money_injected_into_the_business_is_matched_the_other_way(self):
        matched = match_internal_transfers([
            self._txn("ING", "personal", "2026-08-01", -3000, "Transfer to Eden"),
            self._txn("Eden", "business", "2026-08-01", 3000, "Owner funds in"),
        ])

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["from_side"], "personal")
        self.assertEqual(matched[0]["to_side"], "business")


class CashFlowCalendarTests(unittest.TestCase):
    def test_forecast_covers_six_named_calendar_months(self):
        result = build_forecast([], [], {}, date(2026, 8, 3), 1000)

        self.assertEqual(result["horizon_months"], 6)
        self.assertEqual(
            [month["label"] for month in result["personal"]["months"]],
            [
                "August 2026 (from 3 Aug)",
                "September 2026",
                "October 2026",
                "November 2026",
                "December 2026",
                "January 2027",
            ],
        )

    def test_months_are_sequential_without_duplicate_dates(self):
        result = build_forecast([], [], {}, date(2026, 8, 3), 1000)

        dates = [
            day["date"]
            for month in result["personal"]["months"]
            for day in month["days"]
        ]
        self.assertEqual(len(dates), len(set(dates)))
        self.assertEqual(dates, [day["date"] for day in result["personal"]["days"]])
        self.assertEqual(dates[0], "2026-08-03")
        self.assertEqual(dates[-1], "2027-01-31")

    def test_horizon_ends_on_a_month_boundary_regardless_of_start_day(self):
        result = build_forecast([], [], {}, date(2026, 8, 15), 1000)

        months = result["personal"]["months"]
        self.assertEqual(len(months), 6)
        self.assertEqual(months[0]["start_date"], "2026-08-15")
        self.assertEqual(months[-1]["end_date"], "2027-01-31")
        self.assertEqual(result["end_date"], "2027-01-31")

    def test_first_of_month_start_is_not_labelled_partial(self):
        result = build_forecast([], [], {}, date(2026, 9, 1), 1000)

        months = result["personal"]["months"]
        self.assertEqual(months[0]["label"], "September 2026")
        self.assertEqual(len(months[0]["days"]), 30)
        self.assertEqual(months[-1]["end_date"], "2027-02-28")

    def test_month_end_start_date_spans_short_months(self):
        result = build_forecast([], [], {}, date(2026, 1, 31), 1000)

        months = result["personal"]["months"]
        self.assertEqual(
            [month["label"] for month in months],
            [
                "January 2026 (from 31 Jan)",
                "February 2026",
                "March 2026",
                "April 2026",
                "May 2026",
                "June 2026",
            ],
        )
        self.assertEqual(len(months[0]["days"]), 1)
        self.assertEqual(months[-1]["end_date"], "2026-06-30")


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

    def test_safe_to_spend_reserves_outflows_before_next_income(self):
        transactions = [{
            "account": "Everyday",
            "date": "2026-08-03",
            "amount": 0,
            "description": "balance",
            "balance": 2000,
        }]
        events = [
            {
                "description": "School camp",
                "amount": 800,
                "due_date": "2026-08-05",
                "recurring": "",
                "ownership": "personal",
            },
            {
                "description": "Salary",
                "amount": 1000,
                "due_date": "2026-08-10",
                "direction": "inflow",
                "recurring": "",
                "ownership": "personal",
            },
        ]

        result = build_forecast(
            transactions,
            events,
            {"Everyday": "personal"},
            date(2026, 8, 3),
            500,
        )

        self.assertEqual(result["personal"]["safe_to_spend"], 700)

    def test_safe_to_spend_treats_budgeted_income_as_planning_not_payday(self):
        transactions = [{
            "account": "Everyday",
            "date": "2026-08-03",
            "amount": 0,
            "description": "balance",
            "balance": 2000,
        }]
        events = [
            {
                "description": "Transfer from Eden (budgeted)",
                "amount": 5000,
                "due_date": "2026-08-03",
                "recurring": "monthly",
                "direction": "inflow",
                "ownership": "personal",
                "source": "budget_target",
                "confidence": "budgeted",
            },
            {
                "description": "School camp",
                "amount": 800,
                "due_date": "2026-08-05",
                "recurring": "",
                "ownership": "personal",
            },
        ]

        result = build_forecast(
            transactions,
            events,
            {"Everyday": "personal"},
            date(2026, 8, 3),
            500,
        )

        # No real (non-budgeted) income arrives in the horizon, so every
        # outflow is committed spending: 2000 - 500 buffer - 800 camp.
        self.assertEqual(result["personal"]["safe_to_spend"], 700)

    def test_sections_report_when_their_opening_balance_was_last_confirmed(self):
        transactions = [
            {"account": "Everyday", "date": "2026-08-03", "amount": -10,
             "description": "coffee", "balance": 2000},
            {"account": "Dormant saver", "date": "2026-06-01", "amount": 5,
             "description": "interest", "balance": 100},
            {"account": "Eden operating", "date": "2026-07-28", "amount": -50,
             "description": "software", "balance": 9000},
        ]

        result = build_forecast(
            transactions,
            [],
            {"Everyday": "personal", "Dormant saver": "personal",
             "Eden operating": "business"},
            date(2026, 8, 12),
            0,
        )

        # The freshest statement per side dates the balance, so one dormant
        # account cannot make the whole section look ancient.
        self.assertEqual(result["personal"]["balance_as_of"], "2026-08-03")
        self.assertEqual(result["business"]["balance_as_of"], "2026-07-28")
        self.assertEqual(result["combined"]["balance_as_of"], "2026-08-03")

    def test_balance_as_of_is_null_without_any_balances(self):
        result = build_forecast([], [], {}, date(2026, 8, 12), 0)

        self.assertIsNone(result["personal"]["balance_as_of"])
        self.assertIsNone(result["combined"]["balance_as_of"])

    def test_biannual_event_repeats_every_six_months(self):
        events = [{
            "description": "Council rates",
            "amount": 1700,
            "due_date": "2026-02-10",
            "recurring": "biannual",
            "ownership": "personal",
        }]

        result = build_forecast([], events, {}, date(2026, 8, 3), 0)

        event_dates = [
            day["date"]
            for day in result["personal"]["days"]
            if day["outflows"] > 0
        ]
        self.assertEqual(event_dates, ["2026-08-10"])
        self.assertEqual(result["personal"]["closing_balance"], -1700)

    def test_six_monthly_history_is_inferred_as_biannual(self):
        transactions = [
            {"account": "Everyday", "date": "2025-02-10", "amount": -1700, "description": "COUNCIL RATES 1"},
            {"account": "Everyday", "date": "2025-08-11", "amount": -1700, "description": "COUNCIL RATES 2"},
            {"account": "Everyday", "date": "2026-02-09", "amount": -1700, "description": "COUNCIL RATES 3"},
        ]

        events = infer_recurring_events(
            transactions, {"Everyday": "personal"}, date(2026, 8, 3)
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["recurring"], "biannual")
        self.assertEqual(events[0]["due_date"], "2026-08-09")

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
            [], events, {}, date(2026, 8, 3), 0, horizon_months=2
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
            [
                "2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31",
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            ],
        )
        self.assertEqual(
            salary_dates,
            ["2026-08-07", "2026-08-21", "2026-09-04", "2026-09-18"],
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
            [], events, {}, date(2026, 8, 1), 0, horizon_months=3
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

    def test_monthly_event_that_started_before_window_is_not_duplicated(self):
        events = [{
            "description": "Month end bill",
            "amount": 50,
            "due_date": "2026-01-31",
            "recurring": "monthly",
            "ownership": "personal",
        }]

        result = build_forecast(
            [], events, {}, date(2026, 8, 1), 0, horizon_months=2
        )

        event_days = [
            day
            for day in result["personal"]["days"]
            if day["events"]
        ]
        self.assertEqual(
            [(day["date"], len(day["events"])) for day in event_days],
            [("2026-08-31", 1), ("2026-09-30", 1)],
        )


class RecurrenceInferenceTests(unittest.TestCase):
    def test_monthly_income_and_bill_are_inferred_from_three_occurrences(self):
        transactions = [
            {"account": "Everyday", "date": "2026-05-07", "amount": 4000, "description": "EDEN SALARY 1001"},
            {"account": "Everyday", "date": "2026-06-07", "amount": 4000, "description": "EDEN SALARY 1002"},
            {"account": "Everyday", "date": "2026-07-07", "amount": 4000, "description": "EDEN SALARY 1003"},
            {"account": "Everyday", "date": "2026-05-15", "amount": -100, "description": "INTERNET BILL 501"},
            {"account": "Everyday", "date": "2026-06-15", "amount": -101, "description": "INTERNET BILL 502"},
            {"account": "Everyday", "date": "2026-07-15", "amount": -99, "description": "INTERNET BILL 503"},
        ]

        events = infer_recurring_events(
            transactions,
            {"Everyday": "personal"},
            date(2026, 8, 3),
        )

        self.assertEqual(
            events,
            [
                {
                    "description": "EDEN SALARY 1003",
                    "amount": 4000.0,
                    "due_date": "2026-08-07",
                    "recurring": "monthly",
                    "ownership": "personal",
                    "direction": "inflow",
                    "source": "transaction_history",
                    "confidence": "expected",
                },
                {
                    "description": "INTERNET BILL 503",
                    "amount": 100.0,
                    "due_date": "2026-08-15",
                    "recurring": "monthly",
                    "ownership": "personal",
                    "direction": "outflow",
                    "source": "transaction_history",
                    "confidence": "expected",
                },
            ],
        )

    def test_irregular_transactions_are_not_inferred(self):
        transactions = [
            {"account": "Everyday", "date": "2026-05-01", "amount": -50, "description": "SHOP"},
            {"account": "Everyday", "date": "2026-05-20", "amount": -50, "description": "SHOP"},
            {"account": "Everyday", "date": "2026-07-20", "amount": -50, "description": "SHOP"},
        ]

        events = infer_recurring_events(
            transactions,
            {"Everyday": "personal"},
            date(2026, 8, 3),
        )

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
