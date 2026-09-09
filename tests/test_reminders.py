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

import reminders

BRISBANE = ZoneInfo("Australia/Brisbane")


class FakeClient:
    def __init__(self, fail=False):
        self.posts = []
        self.fail = fail
        self.can_post = True

    def post(self, message):
        if self.fail:
            import mattermost
            raise mattermost.MattermostError("down")
        self.posts.append(message)
        return f"post{len(self.posts)}"


class ReminderCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({"obligations": {"accounts": [
            {"key": "eden_operating", "display": "Eden Commercial"},
            {"key": "ecomm_gst", "display": "EComm GST"},
            {"key": "ing_home", "display": "ING Home"},
            {"key": "ing_emergency", "display": "ING Emergency"},
            {"key": "gsb_everyday", "display": "GSB Everyday"},
        ]}}))
        family_app.init_db()
        self.client = FakeClient()
        self.settings = family_app.obligation_settings()

    def tearDown(self):
        family_app.DB_PATH = self.original_db_path
        family_app.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    def service(self, now):
        return reminders.ReminderService(family_app.get_db, self.client, self.settings, now_fn=lambda: now)

    def at(self, y, m, d, hour=7):
        return datetime(y, m, d, hour, 0, tzinfo=BRISBANE)


class OccurrenceRegenerationTests(ReminderCase):
    def test_bas_occurrences_follow_ato_dates_with_estimates(self):
        svc = self.service(self.at(2026, 9, 9))
        created = svc.regenerate_occurrences()
        self.assertGreater(created, 0)
        with family_app.get_db() as db:
            rows = db.execute(
                "SELECT o.standard_date, o.due_date, o.estimate, o.estimate_detail FROM obligation_occurrences o "
                "JOIN obligations b ON b.id = o.obligation_id WHERE b.amount_rule='bas_formula' ORDER BY o.standard_date"
            ).fetchall()
        self.assertEqual([r["standard_date"] for r in rows][:4], ["2026-10-28", "2027-02-28", "2027-04-28", "2027-07-28"])
        self.assertEqual(rows[1]["due_date"], "2027-03-01")
        self.assertGreater(rows[0]["estimate"], 3188 + 545)
        self.assertEqual(json.loads(rows[0]["estimate_detail"])["months"], ["2026-07", "2026-08", "2026-09"])

    def test_regeneration_is_idempotent_and_fixed_items_carry_their_amount(self):
        svc = self.service(self.at(2026, 9, 9))
        svc.regenerate_occurrences()
        again = svc.regenerate_occurrences()
        self.assertEqual(again, 0)
        with family_app.get_db() as db:
            water = db.execute(
                "SELECT o.due_date, o.estimate FROM obligation_occurrences o JOIN obligations b ON b.id=o.obligation_id "
                "WHERE b.name='Water (Urban Utilities)' ORDER BY o.standard_date LIMIT 1"
            ).fetchone()
            rolling = db.execute(
                "SELECT COUNT(*) FROM obligation_occurrences o JOIN obligations b ON b.id=o.obligation_id "
                "WHERE b.frequency='rolling'"
            ).fetchone()[0]
        self.assertEqual(water["due_date"], "2026-11-30")
        self.assertEqual(water["estimate"], 713.45)
        self.assertEqual(rolling, 0)


class DailyRunTests(ReminderCase):
    def test_first_of_month_sends_the_setaside_once(self):
        svc = self.service(self.at(2026, 10, 1))
        result = svc.run_daily()
        self.assertIn("monthly_setaside:2026-09", result["sent"])
        self.assertEqual(len(self.client.posts), 1)
        self.assertIn("September 2026 set-aside", self.client.posts[0])
        self.assertIn("EComm GST", self.client.posts[0])
        second = svc.run_daily()
        self.assertEqual(second["sent"], [])
        self.assertIn("monthly_setaside:2026-09", second["skipped"])
        self.assertEqual(len(self.client.posts), 1)

    def test_lead_warning_fires_at_thirty_and_seven_days_and_catches_up_missed_days(self):
        svc = self.service(self.at(2026, 9, 28))  # 30 days before 28 Oct
        result = svc.run_daily()
        self.assertTrue(any(k.startswith("lead_warning:occ:") and k.endswith(":30") for k in result["sent"]), result)
        self.assertIn("Quarterly BAS + PAYG instalment", self.client.posts[-1])
        self.assertIn("EComm GST should hold", self.client.posts[-1])

        svc = self.service(self.at(2026, 10, 3))  # 25 days out: 30-day key already sent, nothing new
        self.assertEqual([k for k in svc.run_daily()["sent"] if "lead_warning" in k], [])

        svc = self.service(self.at(2026, 10, 23))  # 5 days out: missed the 7-day mark, still sends it once
        result = svc.run_daily()
        self.assertTrue(any(k.endswith(":7") for k in result["sent"]), result)
        self.assertEqual([k for k in svc.run_daily()["sent"] if "lead_warning" in k], [])

    def test_receipts_share_obligation_does_not_double_post_on_the_first(self):
        svc = self.service(self.at(2026, 10, 1))
        svc.run_daily()
        self.assertEqual(len(self.client.posts), 1)
        self.assertNotIn("Monthly tax reserve transfer** is due", self.client.posts[0])

    def test_several_items_are_bundled_into_one_post(self):
        svc = self.service(self.at(2026, 10, 1))
        with family_app.get_db() as db:
            db.execute("UPDATE obligations SET anchor_date='2026-10-31' WHERE name='ProRisk PI/PL renewal'")
        result = svc.run_daily()
        self.assertGreaterEqual(len(result["sent"]), 2)
        self.assertEqual(len(self.client.posts), 1)
        self.assertIn("---", self.client.posts[0])
        with family_app.get_db() as db:
            post_ids = {r["mattermost_post_id"] for r in db.execute("SELECT mattermost_post_id FROM reminder_log")}
        self.assertEqual(post_ids, {"post1"})

    def test_dry_run_returns_body_without_sending_or_logging(self):
        svc = self.service(self.at(2026, 10, 1))
        result = svc.run_daily(dry_run=True)
        self.assertIn("September 2026 set-aside", result["body"])
        self.assertEqual(self.client.posts, [])
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0], 0)

    def test_quiet_hours_block_sending(self):
        svc = self.service(self.at(2026, 10, 1, hour=22))
        result = svc.run_daily()
        self.assertEqual(result["sent"], [])
        self.assertEqual(result["reason"], "quiet hours")
        self.assertEqual(self.client.posts, [])

    def test_mattermost_failure_logs_nothing_so_it_retries(self):
        self.client.fail = True
        svc = self.service(self.at(2026, 10, 1))
        result = svc.run_daily()
        self.assertEqual(result["sent"], [])
        self.assertIn("Mattermost", result["reason"])
        with family_app.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0], 0)

    def test_paid_occurrence_gets_no_more_warnings(self):
        svc = self.service(self.at(2026, 9, 28))
        svc.regenerate_occurrences()
        with family_app.get_db() as db:
            db.execute("UPDATE obligation_occurrences SET state='paid' WHERE standard_date='2026-10-28'")
            bas_id = db.execute("SELECT id FROM obligation_occurrences WHERE standard_date='2026-10-28'").fetchone()["id"]
        result = svc.run_daily()
        self.assertEqual([k for k in result["sent"] if k.startswith(f"lead_warning:occ:{bas_id}:")], [])


class WeeklyRunTests(ReminderCase):
    def test_weekly_position_posts_once_per_sunday(self):
        svc = self.service(self.at(2026, 9, 13, hour=17))
        result = svc.run_weekly()
        self.assertEqual(result["sent"], ["weekly_position:2026-09-13"])
        self.assertIn("EComm GST", self.client.posts[0])
        self.assertIn("ING Emergency", self.client.posts[0])
        self.assertEqual(svc.run_weekly()["sent"], [])

    def test_position_exposes_targets_and_upcoming(self):
        svc = self.service(self.at(2026, 9, 9))
        position = svc.position()
        keys = {t["account_key"] for t in position["targets"]}
        self.assertTrue({"ecomm_gst", "ing_home", "ing_emergency", "gsb_everyday"} <= keys)
        self.assertTrue(all("name" in u and "due_date" in u for u in position["upcoming"]))


class SchedulerTickTests(ReminderCase):
    def test_tick_runs_daily_once_after_post_hour_and_weekly_on_sunday(self):
        svc = self.service(self.at(2026, 9, 13, hour=6))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), [])
        svc = self.service(self.at(2026, 9, 13, hour=7))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["daily"])
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), [])
        svc = self.service(self.at(2026, 9, 13, hour=17))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["weekly"])
        svc = self.service(self.at(2026, 9, 14, hour=17))  # Monday: daily (new day) but no weekly
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["daily"])


if __name__ == "__main__":
    unittest.main()
