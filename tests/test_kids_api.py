import json
import unittest
import warnings
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore", message="Using the in-memory storage for tracking rate limits.*")
warnings.filterwarnings("ignore", message="datetime.datetime.utcnow\\(\\) is deprecated.*")

with patch("threading.Thread.start"):
    import app as family_app

import reminders

BRISBANE = ZoneInfo("Australia/Brisbane")


class KidsApiCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({
            "mattermost": {"enabled": True, "allowed_users": ["tawhai", "mum"]},
            "kids": {"children": [
                {"key": "maia", "mattermost_channel_id": "chan-maia", "mattermost_username": "maia"},
                {"key": "tj", "mattermost_channel_id": "chan-tj", "mattermost_username": "tj"},
            ]}
        }))
        family_app.init_db()
        family_app.app.config.update(TESTING=True)
        self.adult = family_app.app.test_client()
        with self.adult.session_transaction() as session:
            session["_user_id"] = family_app.USERNAME
            session["_fresh"] = True

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    def kid_client(self, key="maia"):
        client = family_app.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = f"kid:{key}"
            session["_fresh"] = True
        return client


class IsolationTests(KidsApiCase):
    def test_kid_cannot_open_adult_pages(self):
        kid = self.kid_client()
        home = kid.get("/", follow_redirects=False)
        self.assertEqual(home.status_code, 302)
        self.assertTrue(home.headers["Location"].endswith("/kids"))
        api = kid.get("/api/obligations")
        self.assertEqual(api.status_code, 403)

    def test_anonymous_kids_me_is_401(self):
        anon = family_app.app.test_client()
        self.assertEqual(anon.get("/api/kids/me").status_code, 401)
        self.assertEqual(anon.get("/kids", follow_redirects=False).status_code, 302)

    def test_kid_cannot_set_a_pin(self):
        kid = self.kid_client()
        r = kid.post("/api/kids/admin/pin", json={"child": "maia", "pin": "2468"})
        self.assertEqual(r.status_code, 401)


class PinAndJarsTests(KidsApiCase):
    def test_adult_sets_pin_then_kid_logs_in(self):
        r = self.adult.post("/api/kids/admin/pin", json={"child": "maia", "pin": "2468"})
        self.assertTrue(r.get_json()["ok"])
        anon = family_app.app.test_client()
        bad = anon.post("/kids/login", data={"child": "maia", "pin": "0000"})
        self.assertEqual(bad.status_code, 200)
        self.assertIn(b"did not match", bad.data)
        ok = anon.post("/kids/login", data={"child": "maia", "pin": "2468"}, follow_redirects=False)
        self.assertEqual(ok.status_code, 302)
        self.assertTrue(ok.headers["Location"].endswith("/kids"))

    def test_seed_and_jars_and_first_principle_pay(self):
        seed = self.adult.post("/api/kids/admin/seed", json={"child": "maia"})
        self.assertEqual(seed.get_json()["amount"], 100)
        self.assertEqual(seed.get_json()["jar"], "grow")
        again = self.adult.post("/api/kids/admin/seed", json={"child": "maia"})
        self.assertEqual(again.status_code, 409)
        kid = self.kid_client()
        me = kid.get("/api/kids/me").get_json()
        self.assertEqual(me["jars"]["grow"], 100)
        self.assertEqual(me["next_principle"]["id"], "save-first")
        wrong = kid.post("/api/kids/principles/save-first/complete", json={"answer": 0})
        self.assertFalse(wrong.get_json()["correct"])
        right = kid.post("/api/kids/principles/save-first/complete", json={"answer": 1})
        self.assertTrue(right.get_json()["correct"])
        self.assertEqual(right.get_json()["earned"], 3)
        again = kid.post("/api/kids/principles/save-first/complete", json={"answer": 1})
        self.assertEqual(again.get_json()["earned"], 0)
        tj = self.kid_client("tj")
        blocked = tj.post("/api/kids/principles/save-first/complete", json={"answer": 1})
        self.assertEqual(blocked.status_code, 400)

    def test_goal_and_sleep_on_it(self):
        kid = self.kid_client()
        g = kid.post("/api/kids/goals", json={"title": "Lego", "target_amount": 45})
        self.assertTrue(g.get_json()["ok"])
        w = kid.post("/api/kids/wants", json={"want": "A new game"})
        self.assertIn("Sleep on it", w.get_json()["message"])
        me = kid.get("/api/kids/me").get_json()
        self.assertEqual(me["goal"]["title"], "Lego")
        self.assertEqual(me["wants"][0]["status"], "sleeping")


class MoneyMealTests(KidsApiCase):
    def test_sunday_meal_posts_to_child_channel_not_family_finance(self):
        self.adult.post("/api/kids/admin/jars", json={"child": "maia", "splurge": 10, "smile": 40, "give": 5, "grow": 100})
        fake = type("C", (), {})()
        fake.posts = []
        fake.channels = []
        fake.can_post = True
        fake.can_read = False
        fake.channel_id = "family-finance"

        def post(message, channel_id=None):
            fake.posts.append(message)
            fake.channels.append(channel_id)
            return f"p{len(fake.posts)}"
        fake.post = post

        settings = family_app.obligation_settings()
        svc = reminders.ReminderService(
            family_app.get_db, fake, settings,
            now_fn=lambda: datetime(2026, 9, 20, 16, 0, tzinfo=BRISBANE),
            kids_settings=family_app.kids_config(),
        )
        result = svc.run_kids_money_meal()
        self.assertTrue(any(k.startswith("kids_meal:maia") for k in result["sent"]))
        self.assertIn("chan-maia", fake.channels)
        self.assertNotIn("family-finance", [c for c in fake.channels if c])
        self.assertTrue(any("Hi Maia." in p for p in fake.posts))
        self.assertTrue(any("Pay into Kit" in p or "pay into Kit" in p for p in fake.posts))
        with family_app.get_db() as db:
            grow = db.execute("SELECT grow FROM kid_balances WHERE child_key='maia' ORDER BY id DESC LIMIT 1").fetchone()["grow"]
        self.assertGreater(grow, 100)

    def test_refuses_when_child_channel_is_family_finance(self):
        cfg = json.loads(family_app.CONFIG_PATH.read_text())
        cfg["kids"]["children"][0]["mattermost_channel_id"] = "family-finance"
        family_app.CONFIG_PATH.write_text(json.dumps(cfg))
        fake = type("C", (), {})()
        fake.posts = []
        fake.can_post = True
        fake.channel_id = "family-finance"
        fake.post = lambda message, channel_id=None: fake.posts.append((channel_id, message)) or "x"
        svc = reminders.ReminderService(
            family_app.get_db, fake, family_app.obligation_settings(),
            now_fn=lambda: datetime(2026, 9, 20, 16, 0, tzinfo=BRISBANE),
            kids_settings=family_app.kids_config() | {"family_finance_channel_id": "family-finance"},
        )
        result = svc.run_kids_money_meal()
        child_posts = [p for p in fake.posts if p[0] == "family-finance" and "Hi Maia." in str(p[1])]
        self.assertEqual(child_posts, [])
        self.assertTrue(any("channel" in k for k in result["skipped"]))

    def test_scheduler_runs_kids_meal_sunday_4pm_not_saturday(self):
        fake = type("C", (), {})()
        fake.can_post = True
        fake.can_read = False
        fake.channel_id = "family-finance"
        fake.post = lambda message, channel_id=None: "p"
        svc = reminders.ReminderService(
            family_app.get_db, fake, family_app.obligation_settings(),
            now_fn=lambda: datetime(2026, 9, 19, 16, 0, tzinfo=BRISBANE),
            kids_settings=family_app.kids_config(),
        )
        self.assertNotIn("kids_meal", reminders.scheduler_tick(svc, svc.now()))
        svc = reminders.ReminderService(
            family_app.get_db, fake, family_app.obligation_settings(),
            now_fn=lambda: datetime(2026, 9, 20, 16, 0, tzinfo=BRISBANE),
            kids_settings=family_app.kids_config(),
        )
        self.assertIn("kids_meal", reminders.scheduler_tick(svc, svc.now()))


class DashboardKidsTests(unittest.TestCase):
    def test_dashboard_has_kids_page(self):
        html = Path("dashboard.html").read_text()
        self.assertIn('id="page-kids"', html)
        self.assertIn("loadKids()", html)
        self.assertIn("Kids HQ", html)


if __name__ == "__main__":
    unittest.main()
