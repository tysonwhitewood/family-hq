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


class CategoryVocabularyTests(CategorisationTestCase):
    def test_personal_and_business_have_their_own_category_lists(self):
        personal = family_app._category_vocabulary("personal")
        business = family_app._category_vocabulary("business")

        for expected in [
            "Groceries", "Dining Out", "Education", "Kids Activities",
            "Health & Medical", "Clothing", "Home & Garden", "Home Utilities",
            "Rent / Mortgage", "Birthdays & Gifts", "Banking / Fees",
            "Subscriptions", "Internet", "Entertainment", "Travel",
            "Insurance", "General",
        ]:
            self.assertIn(expected, personal, expected)
        for expected in [
            "Accounting & Legal", "Tax / ATO", "Software & Tools",
            "Food & Coffee", "Fuel & Vehicle", "Telco / Internet",
            "Office Supplies", "Marketing", "Staff & Contractors",
            "Freight & Post", "Professional Development", "Bank Fees",
            "Travel", "Insurance", "General",
        ]:
            self.assertIn(expected, business, expected)

    def test_household_categories_stay_out_of_the_business_list(self):
        business = family_app._category_vocabulary("business")

        for household_only in [
            "Groceries", "Home & Garden", "Home Utilities", "Rent / Mortgage",
            "Dining Out", "Kids Activities", "Subscriptions",
        ]:
            self.assertNotIn(household_only, business, household_only)

    def test_business_categories_stay_out_of_the_personal_list(self):
        personal = family_app._category_vocabulary("personal")

        for business_only in [
            "Fuel & Vehicle", "Accounting & Legal", "Software & Tools",
            "Food & Coffee", "Tax / ATO", "Staff & Contractors",
        ]:
            self.assertNotIn(business_only, personal, business_only)

    def test_retired_categories_are_gone_from_both_lists(self):
        everything = family_app._category_vocabulary()

        for retired in [
            "AI & Cloud", "School / Kids", "Streaming / TV", "Business Supplies",
            "ATM / Cash", "Parking / Tolls", "Transport", "ATO / Tax",
            "POS & Payments", "ASIC / Compliance", "Other",
        ]:
            self.assertNotIn(retired, everything, retired)


class ScopedCategoriserTests(CategorisationTestCase):
    def test_fuel_is_business_only(self):
        self.assertEqual(
            family_app._categorise("SHELL BOONAH 1234", "business"), "Fuel & Vehicle"
        )
        # The same servo on a personal account is not silently filed as a
        # household expense — all fuel belongs to the business.
        self.assertEqual(
            family_app._categorise("SHELL BOONAH 1234", "personal"), "Uncategorised"
        )

    def test_groceries_are_personal_only(self):
        self.assertEqual(family_app._categorise("WOOLWORTHS 123", "personal"), "Groceries")
        self.assertEqual(
            family_app._categorise("WOOLWORTHS 123", "business"), "Uncategorised"
        )

    def test_a_cafe_is_dining_out_at_home_and_food_and_coffee_at_work(self):
        self.assertEqual(family_app._categorise("CAFE 63 BOONAH", "personal"), "Dining Out")
        self.assertEqual(
            family_app._categorise("CAFE 63 BOONAH", "business"), "Food & Coffee"
        )

    def test_ai_and_cloud_merged_into_software_and_tools(self):
        for description in ["ANTHROPIC CLAUDE AI", "AWS CLOUD BILL", "ZENROWS", "GITHUB"]:
            self.assertEqual(
                family_app._categorise(description, "business"),
                "Software & Tools",
                description,
            )

    def test_shared_categories_work_on_both_sides(self):
        self.assertEqual(family_app._categorise("RACQ INSURANCE", "personal"), "Insurance")
        self.assertEqual(family_app._categorise("RACQ INSURANCE", "business"), "Insurance")
        self.assertEqual(family_app._categorise("QANTAS FLIGHT", "business"), "Travel")
        self.assertEqual(family_app._categorise("TRANSFER FROM EDEN", "personal"), "Transfers")

    def test_household_bills_bunch_into_home_utilities(self):
        for description in [
            "GLOBIRD ENERGY", "QLD URBAN UTILITIES", "COUNCIL RATES NOTICE",
        ]:
            self.assertEqual(
                family_app._categorise(description, "personal"),
                "Home Utilities",
                description,
            )

    def test_education_replaces_school_and_kids_and_activities_are_separate(self):
        self.assertEqual(family_app._categorise("RED BEAD EDUCATION", "personal"), "Education")
        self.assertEqual(family_app._categorise("RACKLEY SWIMMING", "personal"), "Kids Activities")

    def test_accounting_legal_and_tax_are_distinct_business_lines(self):
        self.assertEqual(
            family_app._categorise("LEGALVISION PTY", "business"), "Accounting & Legal"
        )
        self.assertEqual(
            family_app._categorise("ATO BAS PAYMENT", "business"), "Tax / ATO"
        )

    def test_general_is_manual_only_and_never_auto_assigned(self):
        # 'General' exists so a stray item can be filed by hand, but nothing
        # keyword-matches into it.
        self.assertIn("General", family_app._category_vocabulary("personal"))
        self.assertEqual(family_app._categorise("GENERAL STORE", "personal"), "Uncategorised")

    def test_unknown_description_is_uncategorised_not_other(self):
        self.assertEqual(
            family_app._categorise("MYSTERY MERCHANT 42", "personal"), "Uncategorised"
        )

    def test_town_name_and_word_boundary_landmines_stay_fixed(self):
        self.assertEqual(
            family_app._categorise("SOME SHOP BOONAH", "business"), "Uncategorised"
        )
        self.assertEqual(
            family_app._categorise("BOONAH HARDWARE 12", "business"), "Office Supplies"
        )
        self.assertEqual(
            family_app._categorise("RESERVOIR NEWSAGENT", "business"), "Uncategorised"
        )
        self.assertEqual(
            family_app._categorise("SHELLHARBOUR GIFTS", "business"), "Uncategorised"
        )

    def test_seeded_rules_still_categorise_the_familys_merchants(self):
        self.assertEqual(family_app._categorise("OUR COW PTY LTD", "personal"), "Groceries")
        self.assertEqual(
            family_app._categorise("HARVEST MARKETS BOONAH", "personal"), "Groceries"
        )
        self.assertEqual(family_app._categorise("UNITED 4321 BOONAH", "business"), "Fuel & Vehicle")

    def test_a_rule_beats_the_keyword_table(self):
        with family_app.get_db() as db:
            db.execute(
                "INSERT INTO merchant_rules (pattern, category, created_at) VALUES (?,?,?)",
                ("woolworths petrol", "Fuel & Vehicle", "2026-08-13"),
            )
        family_app._invalidate_merchant_rules()

        self.assertEqual(
            family_app._categorise("WOOLWORTHS PETROL 1234", "business"), "Fuel & Vehicle"
        )
        self.assertEqual(family_app._categorise("WOOLWORTHS METRO", "personal"), "Groceries")

    def test_longest_matching_rule_wins(self):
        with family_app.get_db() as db:
            db.execute(
                "INSERT INTO merchant_rules (pattern, category, created_at) VALUES (?,?,?)",
                ("united removals", "Home & Garden", "2026-08-13"),
            )
        family_app._invalidate_merchant_rules()

        self.assertEqual(
            family_app._categorise("UNITED REMOVALS 99", "personal"), "Home & Garden"
        )
        self.assertEqual(family_app._categorise("UNITED 4321", "business"), "Fuel & Vehicle")

    def test_rules_carrying_retired_category_names_are_migrated(self):
        # A rule saved before the taxonomy split must not point at a category
        # that no longer exists.
        with family_app.get_db() as db:
            rows = dict(
                db.execute("SELECT pattern, category FROM merchant_rules").fetchall()
            )
        self.assertEqual(rows.get("united"), "Fuel & Vehicle")
        self.assertNotIn("Fuel", rows.values())


class MerchantRuleApiTests(CategorisationTestCase):
    def test_saving_a_rule_recategorises_immediately(self):
        client = self._client()
        self.assertEqual(
            family_app._categorise("SPACEX STARLINK KIT", "personal"), "Internet"
        )

        response = client.post(
            "/api/finance/merchant-rules",
            json={"pattern": "SPACEX 123", "category": "General"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(family_app._categorise("SPACEX STARLINK KIT", "personal"), "General")

    def test_rule_pattern_is_normalised_and_validated(self):
        client = self._client()

        too_short = client.post(
            "/api/finance/merchant-rules", json={"pattern": "a1", "category": "Groceries"}
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
        self.assertEqual(family_app._categorise("MYSTERY SHOP 1", "personal"), "Groceries")

        deleted = client.delete(f"/api/finance/merchant-rules/{added['id']}")
        missing = client.delete(f"/api/finance/merchant-rules/{added['id']}")

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(family_app._categorise("MYSTERY SHOP 1", "personal"), "Uncategorised")

    @patch.object(family_app, "_parse_csv_files")
    def test_uncategorised_endpoint_labels_each_merchant_with_its_side(self, parse_files):
        parse_files.return_value = [
            {"account": "ING", "ownership": "personal", "date": f"2026-08-0{n}",
             "amount": -50.0, "description": f"UNKNOWN CAFEX {n}", "balance": None}
            for n in range(1, 6)
        ] + [
            {"account": "CBA", "ownership": "business", "date": "2026-08-09",
             "amount": -20.0, "description": "ODD BUSINESS SHOP", "balance": None},
            {"account": "ING", "ownership": "personal", "date": "2026-08-09",
             "amount": -30.0, "description": "WOOLWORTHS 1", "balance": None},
        ]
        client = self._client()

        payload = client.get("/api/finance/uncategorised").get_json()

        merchants = payload["merchants"]
        self.assertEqual(merchants[0]["pattern"], "unknown cafex")
        self.assertEqual(merchants[0]["count"], 5)
        self.assertEqual(merchants[0]["total"], 250.0)
        self.assertEqual(merchants[0]["ownership"], "personal")
        self.assertEqual(merchants[1]["pattern"], "odd business shop")
        self.assertEqual(merchants[1]["ownership"], "business")
        # Categorised transactions (Woolworths -> Groceries) stay out.
        self.assertEqual(len(merchants), 2)
        self.assertIn("Groceries", payload["categories"]["personal"])
        self.assertIn("Fuel & Vehicle", payload["categories"]["business"])
        self.assertNotIn("Groceries", payload["categories"]["business"])


if __name__ == "__main__":
    unittest.main()
