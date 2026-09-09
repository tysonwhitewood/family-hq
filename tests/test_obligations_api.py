import json
import unittest
import warnings
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

warnings.filterwarnings("ignore", message="Using the in-memory storage for tracking rate limits.*")
warnings.filterwarnings("ignore", message="datetime.datetime.utcnow\\(\\) is deprecated.*")

with patch("threading.Thread.start"):
    import app as family_app


class ObligationsDbCase(unittest.TestCase):
    """Fresh database per test, authenticated client."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({
            "obligations": {
                "accounts": [
                    {"key": "eden_operating", "display": "Eden Commercial", "bank": "CBA", "match": "1027 8937"},
                    {"key": "ecomm_gst", "display": "EComm GST", "bank": "CBA", "match": "1027 8945"},
                    {"key": "ing_home", "display": "ING Home", "bank": "ING", "match": "48305167"},
                    {"key": "ing_emergency", "display": "ING Emergency", "bank": "ING", "match": "46789692"},
                    {"key": "gsb_everyday", "display": "GSB Everyday", "bank": "GSB", "match": "51978620"},
                ]
            }
        }))
        family_app.init_db()
        family_app.app.config.update(TESTING=True)
        self.client = family_app.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = family_app.USERNAME
            session["_fresh"] = True

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()


class SchemaAndSeedTests(ObligationsDbCase):
    def test_obligation_tables_exist_with_required_columns(self):
        with family_app.get_db() as db:
            cols = lambda t: {r["name"] for r in db.execute(f"PRAGMA table_info({t})")}
            self.assertTrue({"name", "ownership", "pay_from_account", "reserve_account", "amount_rule",
                             "amount", "frequency", "anchor_date", "due_rule", "extension_days",
                             "lead_days", "remind", "status", "source", "budget_category"} <= cols("obligations"))
            self.assertTrue({"obligation_id", "due_date", "standard_date", "estimate", "estimate_detail",
                             "actual", "state"} <= cols("obligation_occurrences"))
            self.assertTrue({"account_key", "balance", "available", "as_of", "source", "raw"} <= cols("account_balances"))
            self.assertTrue({"year_month", "amount_incl_gst", "detail"} <= cols("receipts_log"))
            self.assertTrue({"kind", "dedupe_key", "obligation_id", "occurrence_id",
                             "mattermost_post_id", "body", "sent_at"} <= cols("reminder_log"))
            self.assertTrue({"key", "value"} <= cols("reminder_state"))

    def test_seed_creates_obligations_and_opening_balances_once(self):
        with family_app.get_db() as db:
            names = {r["name"] for r in db.execute("SELECT name FROM obligations")}
            balances = db.execute("SELECT COUNT(*) FROM account_balances").fetchone()[0]
            seeded = db.execute("SELECT value FROM reminder_state WHERE key='seeded_v1'").fetchone()
        self.assertIn("Monthly tax reserve transfer", names)
        self.assertIn("Quarterly BAS + PAYG instalment", names)
        self.assertIn("Council rates (Scenic Rim)", names)
        self.assertIn("Mortgage repayment", names)
        self.assertEqual(balances, 9)
        self.assertEqual(seeded["value"], "1")

        family_app.init_db()  # second start-up must not duplicate
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM obligations").fetchone()[0], len(names))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM account_balances").fetchone()[0], 9)

    def test_seeded_bas_obligation_matches_spec(self):
        with family_app.get_db() as db:
            row = db.execute("SELECT * FROM obligations WHERE name='Quarterly BAS + PAYG instalment'").fetchone()
        self.assertEqual(row["amount_rule"], "bas_formula")
        self.assertEqual(row["frequency"], "quarterly")
        self.assertEqual(row["anchor_date"], "2026-10-28")
        self.assertEqual(row["reserve_account"], "ecomm_gst")
        self.assertEqual(json.loads(row["lead_days"]), [30, 7, 1])
        self.assertEqual(row["extension_days"], 28)

    def test_settings_merge_config_over_defaults(self):
        settings = family_app.obligation_settings()
        self.assertEqual(settings["payg_instalment_quarterly"], 3188.0)
        self.assertEqual(settings["timezone"], "Australia/Brisbane")
        self.assertEqual(settings["accounts"][1]["key"], "ecomm_gst")

    def test_settings_without_config_keys_use_defaults_and_empty_accounts(self):
        family_app.CONFIG_PATH.write_text("{}")
        settings = family_app.obligation_settings()
        self.assertEqual(settings["income_tax_reserve_rate"], 0.15)
        self.assertEqual(settings["accounts"], [])


if __name__ == "__main__":
    unittest.main()
