import json
import unittest
import warnings
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore", message="Using the in-memory storage for tracking rate limits.*")
warnings.filterwarnings("ignore", message="datetime.datetime.utcnow\\(\\) is deprecated.*")

with patch("threading.Thread.start"):
    import app as family_app

import conversation
import reminders

BRISBANE = ZoneInfo("Australia/Brisbane")
SETTINGS = {"accounts": [
    {"key": "ecomm_gst", "display": "EComm GST", "aliases": ["gst", "ecomm"]},
    {"key": "ing_home", "display": "ING Home", "aliases": ["home"]},
    {"key": "eden_operating", "display": "Eden Commercial", "aliases": ["eden ops"]},
]}
TODAY = date(2026, 9, 10)


class ParseTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(conversation.parse_money("$9,262.50"), 9262.5)
        self.assertEqual(conversation.parse_money("5k"), 5000.0)
        self.assertIsNone(conversation.parse_money("soon"))

    def test_keywords(self):
        for word, kind in [("done", "done"), ("Paid ", "paid"), ("skip", "skip"), ("yes", "yes"),
                           ("status", "status"), ("help", "help"), ("?", "help")]:
            self.assertEqual(conversation.parse_command(word, SETTINGS, TODAY)["kind"], kind, word)

    def test_balances_and_receipts(self):
        self.assertEqual(conversation.parse_command("gst 9262", SETTINGS, TODAY),
                         {"kind": "balance", "account_key": "ecomm_gst", "amount": 9262.0})
        self.assertEqual(conversation.parse_command("ING Home $2,142.41", SETTINGS, TODAY)["account_key"], "ing_home")
        self.assertEqual(conversation.parse_command("eden 5280", SETTINGS, TODAY), {"kind": "receipt", "amount": 5280.0})
        self.assertEqual(conversation.parse_command("eden total 24,500", SETTINGS, TODAY), {"kind": "receipt_total", "amount": 24500.0})
        self.assertEqual(conversation.parse_command("eden ops 6783", SETTINGS, TODAY)["account_key"], "eden_operating")

    def test_bill_phrases(self):
        cmd = conversation.parse_command("rates due 27 Feb 2027 1614", SETTINGS, TODAY)
        self.assertEqual((cmd["kind"], cmd["name"], cmd["due_date"], cmd["amount"]), ("bill", "rates", "2027-02-27", 1614.0))
        cmd = conversation.parse_command("rego 965", SETTINGS, TODAY)
        self.assertEqual((cmd["kind"], cmd["name"], cmd["due_date"], cmd["amount"]), ("bill", "rego", None, 965.0))
        cmd = conversation.parse_command("water due 25/9", SETTINGS, TODAY)
        self.assertEqual((cmd["due_date"], cmd["amount"]), ("2026-09-25", None))
        cmd = conversation.parse_command("rates paid", SETTINGS, TODAY)
        self.assertEqual((cmd["kind"], cmd["name"], cmd["state"]), ("mark", "rates", "paid"))

    def test_free_text_is_none(self):
        self.assertIsNone(conversation.parse_command("can we afford the driveway this month?", SETTINGS, TODAY))
        self.assertIsNone(conversation.parse_command("", SETTINGS, TODAY))

    def test_parse_image_result_tolerates_fences(self):
        out = conversation.parse_image_result(
            '```json\n{"kind":"balances","balances":[{"account_key":"ecomm_gst","name_seen":"EComm GST",'
            '"balance":9048.03,"available":92.03,"as_of":null}]}\n```')
        self.assertEqual(out["kind"], "balances")
        self.assertEqual(out["balances"][0]["balance"], 9048.03)
        self.assertEqual(conversation.parse_image_result("nonsense")["kind"], "other")


class HandleCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({"obligations": {"accounts": [
            {"key": "eden_operating", "display": "Eden Commercial", "aliases": ["eden ops"]},
            {"key": "ecomm_gst", "display": "EComm GST", "aliases": ["gst"]},
            {"key": "ing_home", "display": "ING Home", "aliases": ["home"]},
            {"key": "ing_emergency", "display": "ING Emergency"},
            {"key": "gsb_everyday", "display": "GSB Everyday"},
        ]}}))
        family_app.init_db()
        self.settings = family_app.obligation_settings()
        self.now = datetime(2026, 9, 10, 9, 0, tzinfo=BRISBANE)
        self.svc = reminders.ReminderService(family_app.get_db, None, self.settings, now_fn=lambda: self.now)
        self.svc.regenerate_occurrences(TODAY)

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    def handle(self, text, llm=None, images=None, post_id="p1"):
        return conversation.handle_post(self.svc, {"id": post_id, "message": text}, self.settings, llm=llm, images=images, today=TODAY)

    def test_balance_is_stored_with_post_id(self):
        out = self.handle("gst 9262")
        self.assertTrue(out["acted"])
        self.assertIn("EComm GST $9,262", out["reply"])
        with family_app.get_db() as db:
            row = db.execute("SELECT * FROM account_balances WHERE account_key='ecomm_gst' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["balance"], row["source"], row["mattermost_post_id"], row["as_of"]), (9262.0, "typed", "p1", "2026-09-10"))

    def test_paid_marks_the_nearest_open_occurrence(self):
        out = self.handle("paid")
        self.assertTrue(out["acted"])
        self.assertIn("Mortgage repayment (due 5 Oct 2026)", out["reply"])
        with family_app.get_db() as db:
            state = db.execute("SELECT o.state FROM obligation_occurrences o JOIN obligations b ON b.id=o.obligation_id "
                               "WHERE b.name='Mortgage repayment' ORDER BY o.due_date LIMIT 1").fetchone()["state"]
        self.assertEqual(state, "paid")

    def test_named_mark_and_bill_update(self):
        out = self.handle("mortgage paid")
        self.assertIn("Marked Mortgage repayment", out["reply"])
        self.assertIn("nothing open", self.handle("water paid")["reply"])
        out = self.handle("rates due 27 Feb 2027 1512")
        self.assertIn("Updated Council rates", out["reply"])
        with family_app.get_db() as db:
            row = db.execute("SELECT amount, anchor_date FROM obligations WHERE name LIKE 'Council rates%'").fetchone()
        self.assertEqual((row["amount"], row["anchor_date"]), (1512.0, "2027-02-27"))
        out = self.handle("dentist due 3 Oct 600")
        self.assertIn("Added Dentist", out["reply"])
        self.assertIn("needs confirming", out["reply"])

    def test_receipt_adds_to_month_and_quotes_setaside(self):
        out = self.handle("eden 5280")
        self.assertIn("September receipts so far: $5,280", out["reply"])
        self.assertIn("GST", out["reply"])
        out = self.handle("eden 10083")
        self.assertIn("$15,363", out["reply"])
        out = self.handle("eden total 12000")
        self.assertIn("$12,000", out["reply"])
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT amount_incl_gst FROM receipts_log WHERE year_month='2026-09'").fetchone()[0], 12000.0)

    def test_status_and_help(self):
        self.assertIn("Position as at", self.handle("status")["reply"])
        self.assertIn("*status*", self.handle("help")["reply"])

    def test_yes_dates_propvesting(self):
        out = self.handle("yes")
        self.assertTrue(out["acted"])
        with family_app.get_db() as db:
            row = db.execute("SELECT anchor_date FROM obligations WHERE name LIKE 'PropVesting%'").fetchone()
        self.assertEqual(row["anchor_date"], "2026-09-10")
        self.assertIn("Yes to what", self.handle("yes")["reply"])

    def test_image_balances_are_stored_via_llm(self):
        calls = {}

        def fake_llm(messages, system="", images=None):
            calls["images"] = images
            calls["prompt"] = messages[-1]["content"]
            return json.dumps({"kind": "balances", "balances": [
                {"account_key": "ecomm_gst", "name_seen": "EComm GST", "balance": 13129.03, "available": 92.03, "as_of": None},
                {"account_key": None, "name_seen": "CommSec Shares", "balance": 0, "available": None, "as_of": None},
            ]})
        out = self.handle("", llm=fake_llm, images=[(b"PNG!", "image/png", "shot.png")])
        self.assertTrue(out["acted"])
        self.assertIn("Got it: EComm GST $13,129", out["reply"])
        self.assertIn("Not matched", out["reply"])
        self.assertEqual(calls["images"][0]["media_type"], "image/png")
        self.assertIn("ecomm_gst = EComm GST", calls["prompt"])
        with family_app.get_db() as db:
            row = db.execute("SELECT balance, source, mattermost_post_id FROM account_balances WHERE account_key='ecomm_gst' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["balance"], row["source"], row["mattermost_post_id"]), (13129.03, "screenshot", "p1"))

    def test_image_bill_creates_pending_obligation(self):
        def fake_llm(messages, system="", images=None):
            return '{"kind":"bill","bill":{"payee":"Urban Utilities","amount":454.26,"due_date":"2026-09-25","description":"water"}}'
        out = self.handle("", llm=fake_llm, images=[(b"x", "image/jpeg", "bill.jpg")])
        self.assertIn("Urban Utilities", out["reply"])
        self.assertTrue(out["acted"])

    def test_image_without_llm_explains(self):
        out = self.handle("", llm=None, images=[(b"x", "image/png", "a.png")])
        self.assertIn("no AI is configured", out["reply"])
        self.assertFalse(out["acted"])

    def test_free_text_goes_to_llm_with_context(self):
        seen = {}

        def fake_llm(messages, system="", images=None):
            seen["system"] = system
            seen["question"] = messages[-1]["content"]
            return "You can, but only just."
        out = self.handle("can we afford $600 on the driveway this month?", llm=fake_llm)
        self.assertEqual(out["reply"], "You can, but only just.")
        self.assertFalse(out["acted"])
        self.assertIn("EComm GST", seen["system"])
        self.assertIn("Next 30 days", seen["system"])
        self.assertEqual(seen["question"], "can we afford $600 on the driveway this month?")

    def test_free_text_without_llm(self):
        self.assertIn("commands", self.handle("what now?")["reply"])


if __name__ == "__main__":
    unittest.main()
