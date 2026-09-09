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
                    {"key": "ecomm_gst", "display": "EComm GST", "bank": "CBA", "match": "1027 8945", "aliases": ["gst"]},
                    {"key": "ing_home", "display": "ING Home", "bank": "ING", "match": "48305167", "aliases": ["home"]},
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

    def test_seed_records_julys_known_receipts(self):
        with family_app.get_db() as db:
            row = db.execute("SELECT amount_incl_gst FROM receipts_log WHERE year_month='2026-07'").fetchone()
        self.assertAlmostEqual(row["amount_incl_gst"], 40587.02, places=2)

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


class ObligationRoutesTests(ObligationsDbCase):
    def test_list_returns_seed_with_next_occurrence_and_accounts(self):
        r = self.client.get("/api/obligations")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        bas = next(o for o in data["obligations"] if o["name"] == "Quarterly BAS + PAYG instalment")
        self.assertEqual(bas["next_occurrence"]["due_date"], "2026-10-28")
        self.assertEqual(bas["lead_days"], [30, 7, 1])
        self.assertEqual(data["accounts"][0]["key"], "eden_operating")
        self.assertEqual(data["settings"]["payg_instalment_quarterly"], 3188.0)

    def test_create_validates_and_generates_occurrences(self):
        bad = self.client.post("/api/obligations", json={"name": "", "amount_rule": "fixed"})
        self.assertEqual(bad.status_code, 400)
        bad_rule = self.client.post("/api/obligations", json={"name": "X", "ownership": "personal",
                                                              "amount_rule": "magic", "frequency": "annual"})
        self.assertEqual(bad_rule.status_code, 400)
        bad_account = self.client.post("/api/obligations", json={
            "name": "X", "ownership": "personal", "amount_rule": "fixed", "amount": 10,
            "frequency": "annual", "anchor_date": "2026-12-01", "reserve_account": "nope"})
        self.assertEqual(bad_account.status_code, 400)

        ok = self.client.post("/api/obligations", json={
            "name": "Car registration (Tiggo)", "ownership": "personal", "amount_rule": "fixed",
            "amount": 964.96, "frequency": "annual", "anchor_date": "2027-04-28",
            "pay_from_account": "ing_home", "reserve_account": "ing_home", "lead_days": [30, 7],
            "remind": True, "status": "active", "source": "typed"})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        oid = ok.get_json()["id"]
        with family_app.get_db() as db:
            occ = db.execute("SELECT due_date, estimate FROM obligation_occurrences WHERE obligation_id=?", (oid,)).fetchall()
        self.assertEqual([o["due_date"] for o in occ], ["2027-04-28"])
        self.assertEqual(occ[0]["estimate"], 964.96)

    def test_update_replaces_open_occurrences_and_delete_retires(self):
        with family_app.get_db() as db:
            water = db.execute("SELECT id FROM obligations WHERE name='Water (Urban Utilities)'").fetchone()["id"]
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        r = self.client.post("/api/obligations", json={
            "id": water, "name": "Water (Urban Utilities)", "ownership": "personal", "amount_rule": "fixed",
            "amount": 750.0, "frequency": "quarterly", "anchor_date": "2026-12-05",
            "pay_from_account": "ing_home", "reserve_account": "ing_home", "lead_days": [30, 7],
            "remind": True, "status": "active"})
        self.assertEqual(r.status_code, 200, r.get_json())
        with family_app.get_db() as db:
            dates = [o["due_date"] for o in db.execute(
                "SELECT due_date FROM obligation_occurrences WHERE obligation_id=? AND state='upcoming' ORDER BY due_date", (water,))]
        self.assertEqual(dates[0], "2026-12-07")  # 5 Dec 2026 is a Saturday
        self.assertNotIn("2026-11-30", dates)

        d = self.client.delete(f"/api/obligations/{water}")
        self.assertEqual(d.status_code, 200)
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM obligations WHERE id=?", (water,)).fetchone()["status"], "retired")
        names = [o["name"] for o in self.client.get("/api/obligations").get_json()["obligations"]]
        self.assertNotIn("Water (Urban Utilities)", names)

    def test_position_log_state_receipts_and_balances(self):
        position = self.client.get("/api/obligations/position?today=2026-09-09").get_json()
        keys = {t["account_key"] for t in position["targets"]}
        self.assertIn("ecomm_gst", keys)
        gst = next(t for t in position["targets"] if t["account_key"] == "ecomm_gst")
        self.assertEqual(gst["balance"], 9048.03)

        occ_id = position["upcoming"][0]["occurrence_id"]
        bad = self.client.post(f"/api/obligations/occurrences/{occ_id}/state", json={"state": "lost"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(f"/api/obligations/occurrences/{occ_id}/state", json={"state": "paid"})
        self.assertEqual(ok.status_code, 200)
        with family_app.get_db() as db:
            row = db.execute("SELECT state, state_changed_by FROM obligation_occurrences WHERE id=?", (occ_id,)).fetchone()
        self.assertEqual((row["state"], row["state_changed_by"]), ("paid", "app"))

        self.assertEqual(self.client.post("/api/obligations/receipts", json={"year_month": "2026-9", "amount": 1}).status_code, 400)
        self.assertEqual(self.client.post("/api/obligations/receipts", json={"year_month": "2026-09", "amount": 15363.34}).status_code, 200)
        self.assertEqual(self.client.post("/api/obligations/receipts", json={"year_month": "2026-09", "amount": 16000}).status_code, 200)
        with family_app.get_db() as db:
            rows = db.execute("SELECT amount_incl_gst FROM receipts_log WHERE year_month='2026-09'").fetchall()
        self.assertEqual([r["amount_incl_gst"] for r in rows], [16000.0])

        self.assertEqual(self.client.post("/api/obligations/balances", json={"account_key": "nope", "balance": 1}).status_code, 400)
        ok = self.client.post("/api/obligations/balances", json={"account_key": "ing_home", "balance": 780.0})
        self.assertEqual(ok.status_code, 200)
        position = self.client.get("/api/obligations/position?today=2026-09-09").get_json()
        home = next(t for t in position["targets"] if t["account_key"] == "ing_home")
        self.assertEqual(home["balance"], 780.0)

        log = self.client.get("/api/obligations/log").get_json()["log"]
        self.assertEqual(log, [])

    def test_run_dry_run_returns_body_without_mattermost(self):
        with patch.object(family_app, "mattermost_client", return_value=None):
            r = self.client.post("/api/obligations/run", json={"job": "daily", "dry_run": True, "today": "2026-10-01"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("September 2026 set-aside", r.get_json()["body"])
        bad = self.client.post("/api/obligations/run", json={"job": "hourly"})
        self.assertEqual(bad.status_code, 400)

    def test_run_without_dry_run_reports_missing_mattermost(self):
        with patch.object(family_app, "mattermost_client", return_value=None), \
             patch.object(family_app.reminders.ReminderService, "in_quiet_hours", return_value=False):
            r = self.client.post("/api/obligations/run", json={"job": "weekly", "today": "2026-09-13"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["reason"], "Mattermost not configured")

    def test_mattermost_status_and_test_message(self):
        with patch.object(family_app, "mattermost_client", return_value=None):
            status = self.client.get("/api/mattermost/status").get_json()
            self.assertEqual({k: status[k] for k in ("configured", "can_read", "can_post", "reachable")},
                             {"configured": False, "can_read": False, "can_post": False, "reachable": False})
            self.assertIn("vision", status)
            r = self.client.post("/api/mattermost/test")
            self.assertEqual(r.status_code, 503)

        class Fake:
            can_read, can_post = True, True
            def ping(self): return True
            def post(self, message): self.message = message; return "p1"
        fake = Fake()
        with patch.object(family_app, "mattermost_client", return_value=fake):
            status = self.client.get("/api/mattermost/status").get_json()
            self.assertTrue(status["configured"] and status["reachable"])
            r = self.client.post("/api/mattermost/test")
        self.assertEqual(r.get_json(), {"ok": True, "post_id": "p1"})
        self.assertIn("Family HQ", fake.message)

    def test_auto_pay_column_is_added_to_an_old_database(self):
        import sqlite3
        family_app.DB_PATH.unlink()
        old = sqlite3.connect(family_app.DB_PATH)
        old.execute("""CREATE TABLE obligations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            ownership TEXT NOT NULL, pay_from_account TEXT, reserve_account TEXT, amount_rule TEXT NOT NULL, amount REAL,
            frequency TEXT NOT NULL, anchor_date TEXT, due_rule TEXT NOT NULL DEFAULT 'standard', extension_days INTEGER NOT NULL DEFAULT 0,
            lead_days TEXT NOT NULL DEFAULT '[30, 7]', remind INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active',
            budget_category TEXT, source TEXT, notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        old.commit(); old.close()
        family_app.init_db()
        with family_app.get_db() as db:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(obligations)")}
            auto = db.execute("SELECT auto_pay FROM obligations LIMIT 1").fetchone()
        self.assertIn("auto_pay", cols)
        self.assertEqual(auto["auto_pay"], 0)

    def test_save_accepts_auto_pay(self):
        r = self.client.post("/api/obligations", json={
            "name": "Water (Urban Utilities)", "ownership": "personal", "amount_rule": "fixed", "amount": 454.26,
            "frequency": "quarterly", "anchor_date": "2026-09-25", "pay_from_account": "ing_home",
            "reserve_account": "ing_home", "lead_days": [7], "remind": True, "status": "active", "auto_pay": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        listing = self.client.get("/api/obligations").get_json()["obligations"]
        self.assertEqual(next(o for o in listing if o["id"] == r.get_json()["id"])["auto_pay"], 1)

    def test_reminder_service_carries_allowed_users_and_llm(self):
        cfg = json.loads(family_app.CONFIG_PATH.read_text())
        cfg["mattermost"] = {"allowed_users": ["tawhai", "mum"]}
        family_app.CONFIG_PATH.write_text(json.dumps(cfg))
        with patch.object(family_app, "llm_available", return_value=True):
            svc = family_app.reminder_service()
        self.assertEqual(svc.settings["allowed_users"], ["tawhai", "mum"])
        self.assertIs(svc.llm, family_app.llm_chat)

    def test_reminder_service_uses_the_family_birthday_loader(self):
        self.assertIs(family_app.reminder_service().birthdays_fn, family_app.load_birthdays)

    def test_discord_routes_are_gone(self):
        self.assertFalse(hasattr(family_app, "send_discord_webhook"))
        self.assertEqual(self.client.post("/api/discord/webhook-test").status_code, 404)

    def test_poll_and_simulate_routes(self):
        class Fake:
            can_read, can_post = True, True
            incoming = [{"id": "p1", "user_id": "u1", "message": "gst 9262", "create_at": 5000, "file_ids": [], "root_id": "", "type": ""}]
            posts = []
            def ping(self): return True
            def me(self): return {"id": "botid"}
            def posts_since(self, since): return [p for p in self.incoming if p["create_at"] > since]
            def users_by_ids(self, ids): return {"u1": "tawhai"}
            def add_reaction(self, post_id, emoji_name="white_check_mark"): pass
            def post(self, message): self.posts.append(message); return "r1"
        fake = Fake()
        cfg = json.loads(family_app.CONFIG_PATH.read_text())
        cfg["mattermost"] = {"allowed_users": ["tawhai"]}
        family_app.CONFIG_PATH.write_text(json.dumps(cfg))
        with patch.object(family_app, "mattermost_client", return_value=fake):
            first = self.client.post("/api/mattermost/poll").get_json()
            self.assertEqual(first["reason"], "watermark initialised")
            family_app.reminder_service().set_state("mm_last_post_create_at", "1000")
            second = self.client.post("/api/mattermost/poll").get_json()
            self.assertEqual((second["processed"], second["replied"]), (1, 1))
            self.assertIn("Got it: EComm GST", fake.posts[0])
            status = self.client.get("/api/mattermost/status").get_json()
            self.assertTrue(status["can_read"])
            self.assertIsNotNone(status["last_poll_at"])
            self.assertIn(status["vision"], ("Claude", "OpenRouter (free vision model)", "none"))
            sim = self.client.post("/api/mattermost/simulate", json={"text": "ing home 2142"}).get_json()
            self.assertEqual(sim["command"]["kind"], "balance")
            self.assertEqual(self.client.post("/api/mattermost/simulate", json={"text": ""}).status_code, 400)
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM account_balances WHERE account_key='ing_home' AND balance=2142").fetchone()[0], 0)

    def test_routes_require_login(self):
        anonymous = family_app.app.test_client()
        self.assertIn(anonymous.get("/api/obligations").status_code, (302, 401))
        self.assertIn(anonymous.post("/api/obligations/run", json={"job": "daily"}).status_code, (302, 401))


class ForecastHookTests(ObligationsDbCase):
    def test_obligation_events_include_dated_bills_but_not_monthly_or_transfers(self):
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        events = family_app._obligation_events(date(2026, 9, 9))
        names = [e["description"] for e in events]
        self.assertIn("Quarterly BAS + PAYG instalment", names)
        self.assertIn("Water (Urban Utilities)", names)
        self.assertNotIn("Monthly tax reserve transfer", names)
        self.assertNotIn("Home & contents (RACQ)", names)
        self.assertNotIn("Car registration (TMR)", names)  # no amount yet
        bas = next(e for e in events if e["description"] == "Quarterly BAS + PAYG instalment")
        self.assertEqual((bas["ownership"], bas["direction"], bas["source"], bas["category"]),
                         ("business", "outflow", "obligation", "Tax/ATO"))
        self.assertGreater(bas["amount"], 3733)
        self.assertEqual(bas["due_date"], "2026-10-28")

    def test_obligation_category_suppresses_the_matching_budget_target(self):
        with family_app.get_db() as db:
            db.execute("DELETE FROM budget_targets")
            db.execute("INSERT INTO budget_targets (category, monthly_target, type, frequency, direction, created_at, updated_at) "
                       "VALUES ('Water (Qld Urban Util)', 200, 'personal', 'monthly', 'outflow', 'x', 'x')")
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        events = family_app._obligation_events(date(2026, 9, 9))
        budgeted = family_app._budget_target_events(events, date(2026, 9, 9))
        self.assertEqual([b for b in budgeted if b["category"] == "Water (Qld Urban Util)"], [])

    def test_cash_flow_includes_obligation_events(self):
        family_app.reminder_service().regenerate_occurrences(date(2026, 9, 9))
        with patch.object(family_app, "_obligation_events", wraps=family_app._obligation_events) as hook:
            family_app._budget_cash_flow([], [], forecast_date=date(2026, 9, 9), safety_buffer=0)
        hook.assert_called_once_with(date(2026, 9, 9))


class LlmImageTests(unittest.TestCase):
    def test_anthropic_path_sends_image_blocks(self):
        captured = {}

        class FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)

                class R:
                    content = [type("T", (), {"text": "ok"})()]
                return R()

        class FakeClient:
            def __init__(self, api_key):
                self.messages = FakeMessages()

        fake_module = type("M", (), {"Anthropic": FakeClient})
        with patch.dict("sys.modules", {"anthropic": fake_module}), \
             patch.object(family_app, "_anthropic_key", return_value="k"):
            out = family_app.llm_chat([{"role": "user", "content": "read this"}], system="s",
                                      images=[{"media_type": "image/png", "data": "QUJD"}])
        self.assertEqual(out, "ok")
        blocks = captured["messages"][-1]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"], {"type": "base64", "media_type": "image/png", "data": "QUJD"})
        self.assertEqual(blocks[-1], {"type": "text", "text": "read this"})

    def test_openrouter_path_uses_vision_models_for_images(self):
        seen = []

        class FakeResp:
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "seen"}}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=30):
            seen.append(json.loads(req.data))
            return FakeResp()

        with patch.object(family_app, "_anthropic_key", return_value=""), \
             patch.object(family_app, "_openrouter_key", return_value="or"), \
             patch.object(family_app.urllib.request, "urlopen", fake_urlopen):
            out = family_app.llm_chat([{"role": "user", "content": "read"}],
                                      images=[{"media_type": "image/jpeg", "data": "QUJD"}])
        self.assertEqual(out, "seen")
        self.assertEqual(seen[0]["model"], family_app.OPENROUTER_VISION_MODELS[0])
        parts = seen[0]["messages"][-1]["content"]
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_vision_available_tracks_keys(self):
        with patch.object(family_app, "_anthropic_key", return_value=""), \
             patch.object(family_app, "_openrouter_key", return_value=""):
            self.assertFalse(family_app.llm_vision_available())
        with patch.object(family_app, "_anthropic_key", return_value=""), \
             patch.object(family_app, "_openrouter_key", return_value="x"):
            self.assertTrue(family_app.llm_vision_available())


if __name__ == "__main__":
    unittest.main()
