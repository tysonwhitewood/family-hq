import json
import sqlite3
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

warnings.filterwarnings("ignore", message="Using the in-memory storage for tracking rate limits.*")
warnings.filterwarnings("ignore", message="datetime.datetime.utcnow\\(\\) is deprecated.*")

with patch("threading.Thread.start"):
    import app as family_app

import holdings as hold


class HoldingsCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({"obligations": {"accounts": [
            {"key": "ecomm_gst", "display": "EComm GST", "bank": "CBA", "aliases": ["gst"]},
            {"key": "super_ing", "display": "Superannuation (ING)", "bank": "ING", "match": "095236",
             "aliases": ["super"], "investment": True},
        ]}}))
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


class HoldingsLogicTests(HoldingsCase):
    def test_seed_and_summary_weights(self):
        with family_app.get_db() as db:
            summ = hold.summary(db, "super_ing", {"balance": 107723.84, "as_of": "2026-09-09"})
        self.assertEqual(len(summ["holdings"]), 6)
        self.assertAlmostEqual(summ["total"], 104712.20, places=2)
        vas = next(h for h in summ["holdings"] if h["ticker"] == "VAS.AX")
        self.assertEqual(vas["weight"], 40.5)
        self.assertAlmostEqual(summ["change_since_screenshot"], -3011.64, places=2)

    def test_upsert_matches_by_name_then_ticker_and_computes_value(self):
        with family_app.get_db() as db:
            hold.upsert_holdings(db, "super_ing", [
                {"name": "goodman group", "units": 300, "price": 27.0},
                {"name": "Vanguard Aus Shares", "ticker": "vas.ax", "units": 400, "price": 110.0},
                {"name": "New Holding", "ticker": "NEW.AX", "units": 10, "price": 2.0},
            ], "test", "2026-09-12T10:00:00")
            rows = {h["name"]: h for h in hold.list_holdings(db, "super_ing")}
        self.assertEqual(len(rows), 7)
        self.assertEqual((rows["Goodman Group"]["units"], rows["Goodman Group"]["value"]), (300.0, 8100.0))
        self.assertEqual(rows["Vanguard Australian Shares Index ETF"]["units"], 400.0)
        self.assertEqual(rows["New Holding"]["value"], 20.0)

    def test_refresh_prices_updates_values_and_reports_failures(self):
        prices = {"VAS.AX": 110.0, "GMG.AX": None}

        def fetch(ticker):
            if ticker == "DXS.AX":
                raise RuntimeError("feed down")
            return prices.get(ticker, 1.0)
        with family_app.get_db() as db:
            result = hold.refresh_prices(db, fetch, "2026-09-12T10:00:00", "super_ing")
            vas = next(h for h in hold.list_holdings(db, "super_ing") if h["ticker"] == "VAS.AX")
        self.assertIn("VAS.AX", result["updated"])
        self.assertEqual(sorted(result["failed"]), ["DXS.AX", "GMG.AX"])
        self.assertEqual((vas["price"], vas["value"], vas["price_at"]), (110.0, 42790.0, "2026-09-12"))

    def test_super_line_reads_well(self):
        with family_app.get_db() as db:
            summ = hold.summary(db, "super_ing", {"balance": 107723.84, "as_of": "2026-09-09"})
        line = hold.compose_super_line("Superannuation (ING)", summ)
        self.assertIn("holdings worth $104,712", line)
        self.assertIn("down $3,012 since the 9 Sep 2026 statement", line)
        self.assertIn("Vanguard Australian Shares Index ETF 40.5%", line)


class HoldingsApiTests(HoldingsCase):
    def test_list_refresh_save_and_delete(self):
        with patch.object(family_app, "fetch_share_price", side_effect=lambda t: 100.0):
            data = self.client.get("/api/holdings?refresh=1").get_json()
        acct = data["accounts"][0]
        self.assertEqual(acct["account_key"], "super_ing")
        self.assertEqual(acct["display"], "Superannuation (ING)")
        self.assertEqual(len(data["refresh"]["updated"]), 5)
        vas = next(h for h in acct["holdings"] if h["ticker"] == "VAS.AX")
        self.assertEqual(vas["value"], 38900.0)
        r = self.client.post("/api/holdings", json={"account_key": "super_ing", "name": "Cash Hub", "value": 9000})
        self.assertEqual(r.status_code, 200)
        bad = self.client.post("/api/holdings", json={"account_key": "nope", "name": "x"})
        self.assertEqual(bad.status_code, 400)
        data = self.client.get("/api/holdings").get_json()
        cash = next(h for h in data["accounts"][0]["holdings"] if h["name"] == "Cash Hub")
        self.assertEqual(cash["value"], 9000.0)
        self.assertEqual(self.client.delete(f"/api/holdings/{cash['id']}").status_code, 200)
        self.assertEqual(len(self.client.get("/api/holdings").get_json()["accounts"][0]["holdings"]), 5)

    def test_add_account_from_chat_writes_config(self):
        entry = family_app.add_account_from_chat({"key": "cba_savings", "display": "Cba Savings", "bank": "CBA", "match": "1234", "aliases": ["cba savings"], "investment": False})
        cfg = json.loads(family_app.CONFIG_PATH.read_text())
        self.assertIn("cba_savings", [a["key"] for a in cfg["obligations"]["accounts"]])
        with self.assertRaises(ValueError):
            family_app.add_account_from_chat(entry)


if __name__ == "__main__":
    unittest.main()
