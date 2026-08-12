import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app as family_app


class CategorisationTestCase(unittest.TestCase):
    """Shared temp database so merchant rules never touch the live data."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_data_dir = family_app.DATA_DIR
        family_app.DATA_DIR = Path(self.temp_dir.name)
        family_app.DB_PATH = family_app.DATA_DIR / "family.db"
        family_app.init_db()
        family_app._invalidate_merchant_rules()

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.DATA_DIR = self.original_data_dir
        family_app._invalidate_merchant_rules()
        self.temp_dir.cleanup()

    def _client(self):
        client = family_app.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = family_app.USERNAME
            session["_fresh"] = True
        return client


class MerchantRuleCategoriserTests(CategorisationTestCase):
    def test_unknown_description_is_uncategorised_not_other(self):
        self.assertEqual(family_app._categorise("MYSTERY MERCHANT 42"), "Uncategorised")

    def test_seeded_rules_categorise_the_familys_merchants(self):
        self.assertEqual(family_app._categorise("OUR COW PTY LTD"), "Groceries")
        self.assertEqual(family_app._categorise("HARVEST MARKETS BOONAH"), "Groceries")
        self.assertEqual(family_app._categorise("ZENROWS"), "Software & Tools")
        self.assertEqual(family_app._categorise("RED BEAD EDUCATION"), "Education")
        self.assertEqual(family_app._categorise("UNITED 4321 BOONAH"), "Fuel")

    def test_a_town_name_no_longer_swallows_local_merchants(self):
        # 'boonah' alone must not mean Business Supplies; the hardware
        # store keeps its specific keyword.
        self.assertEqual(family_app._categorise("SOME SHOP BOONAH"), "Uncategorised")
        self.assertEqual(
            family_app._categorise("BOONAH HARDWARE 123"), "Business Supplies"
        )

    def test_servo_and_shell_keywords_need_word_boundaries(self):
        self.assertEqual(family_app._categorise("RESERVOIR NEWSAGENT"), "Uncategorised")
        self.assertEqual(family_app._categorise("SHELLHARBOUR GIFTS"), "Uncategorised")
        self.assertEqual(family_app._categorise("SHELL BOONAH 1234"), "Fuel")
        self.assertEqual(family_app._categorise("LOCAL SERVO PTY"), "Fuel")

    def test_education_keywords_exist(self):
        self.assertEqual(
            family_app._categorise("HOMESCHOOL CURRICULUM SUPPLIES"), "Education"
        )

    def test_a_rule_beats_the_keyword_table(self):
        # 'woolworth' would be Groceries by keyword; a rule wins.
        with family_app.get_db() as db:
            db.execute(
                "INSERT INTO merchant_rules (pattern, category, created_at) VALUES (?,?,?)",
                ("woolworths petrol", "Fuel", "2026-08-12"),
            )
        family_app._invalidate_merchant_rules()

        self.assertEqual(family_app._categorise("WOOLWORTHS PETROL 1234"), "Fuel")
        self.assertEqual(family_app._categorise("WOOLWORTHS METRO"), "Groceries")

    def test_longest_matching_rule_wins(self):
        with family_app.get_db() as db:
            db.execute(
                "INSERT INTO merchant_rules (pattern, category, created_at) VALUES (?,?,?)",
                ("united removals", "Home & Garden", "2026-08-12"),
            )
        family_app._invalidate_merchant_rules()

        # The short seeded 'united' Fuel rule must not shadow the longer one.
        self.assertEqual(family_app._categorise("UNITED REMOVALS 99"), "Home & Garden")
        self.assertEqual(family_app._categorise("UNITED 4321"), "Fuel")


class MerchantRuleApiTests(CategorisationTestCase):
    def test_saving_a_rule_recategorises_immediately(self):
        client = self._client()
        self.assertEqual(family_app._categorise("SPACEX STARLINK KIT"), "Telco / Internet")

        response = client.post(
            "/api/finance/merchant-rules",
            json={"pattern": "SPACEX 123", "category": "Electronics"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(family_app._categorise("SPACEX STARLINK KIT"), "Electronics")

    def test_rule_pattern_is_normalised_and_validated(self):
        client = self._client()

        too_short = client.post(
            "/api/finance/merchant-rules", json={"pattern": "a1", "category": "Fuel"}
        )
        no_category = client.post(
            "/api/finance/merchant-rules", json={"pattern": "big merchant"}
        )
        unknown_category = client.post(
            "/api/finance/merchant-rules",
            json={"pattern": "big merchant", "category": "Made Up"},
        )

        self.assertEqual(too_short.status_code, 400)
        self.assertEqual(no_category.status_code, 400)
        self.assertEqual(unknown_category.status_code, 400)

    def test_rules_can_be_listed_and_deleted(self):
        client = self._client()
        client.post(
            "/api/finance/merchant-rules",
            json={"pattern": "mystery shop", "category": "Groceries"},
        )

        listed = client.get("/api/finance/merchant-rules").get_json()["rules"]
        added = next(r for r in listed if r["pattern"] == "mystery shop")
        self.assertEqual(family_app._categorise("MYSTERY SHOP 1"), "Groceries")

        deleted = client.delete(f"/api/finance/merchant-rules/{added['id']}")
        missing = client.delete(f"/api/finance/merchant-rules/{added['id']}")

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(family_app._categorise("MYSTERY SHOP 1"), "Uncategorised")

    @patch.object(family_app, "_parse_csv_files")
    def test_uncategorised_endpoint_groups_by_merchant_most_common_first(self, parse_files):
        parse_files.return_value = [
            {"account": "A", "date": f"2026-08-0{n}", "amount": -50.0,
             "description": f"UNKNOWN CAFEX {n}", "balance": None}
            for n in range(1, 6)
        ] + [
            {"account": "A", "date": "2026-08-09", "amount": -20.0,
             "description": "ONE OFF SHOP", "balance": None},
            {"account": "A", "date": "2026-08-09", "amount": -30.0,
             "description": "WOOLWORTHS 1", "balance": None},
        ]
        client = self._client()

        payload = client.get("/api/finance/uncategorised").get_json()

        merchants = payload["merchants"]
        self.assertEqual(merchants[0]["pattern"], "unknown cafex")
        self.assertEqual(merchants[0]["count"], 5)
        self.assertEqual(merchants[0]["total"], 250.0)
        self.assertEqual(merchants[1]["pattern"], "one off shop")
        # Categorised transactions (Woolworths -> Groceries) stay out.
        self.assertEqual(len(merchants), 2)
        self.assertIn("Groceries", payload["categories"])
        self.assertNotIn("Uncategorised", payload["categories"])


if __name__ == "__main__":
    unittest.main()
