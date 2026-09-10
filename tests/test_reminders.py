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
    def __init__(self, fail=False, can_read=False):
        self.posts = []
        self.fail = fail
        self.can_post = True
        self.can_read = can_read
        self.incoming = []
        self.usernames = {"u1": "tawhai", "u2": "mum", "u9": "stranger"}
        self.reactions = []
        self.files = {}
        self.read_fail = False

    def post(self, message):
        if self.fail:
            import mattermost
            raise mattermost.MattermostError("down")
        self.posts.append(message)
        return f"post{len(self.posts)}"

    def me(self):
        return {"id": "botid", "username": "familyhq"}

    def posts_since(self, since_ms):
        if self.read_fail:
            import mattermost
            raise mattermost.MattermostError("read failed")
        return sorted([p for p in self.incoming if p["create_at"] > since_ms], key=lambda p: p["create_at"])

    def users_by_ids(self, ids):
        return {i: self.usernames[i] for i in ids if i in self.usernames}

    def add_reaction(self, post_id, emoji_name="white_check_mark"):
        self.reactions.append((post_id, emoji_name))

    def download_file(self, file_id):
        return self.files[file_id]


class ReminderCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = family_app.DB_PATH
        self.original_config_path = family_app.CONFIG_PATH
        family_app.DB_PATH = Path(self.temp_dir.name) / "family.db"
        family_app.CONFIG_PATH = Path(self.temp_dir.name) / "config.json"
        family_app.CONFIG_PATH.write_text(json.dumps({"obligations": {"accounts": [
            {"key": "eden_operating", "display": "Eden Commercial"},
            {"key": "ecomm_gst", "display": "EComm GST", "aliases": ["gst"]},
            {"key": "ing_home", "display": "ING Home", "aliases": ["home"]},
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

    def test_weekly_position_includes_birthdays_from_the_loader(self):
        def fake_birthdays(days):
            self.assertEqual(days, 14)
            return [{"name": "Robyn Whitewood", "relationship": "Family", "birthday_this_year": "2026-09-16",
                     "days_until": 3, "age_upcoming": 38}]
        svc = reminders.ReminderService(family_app.get_db, self.client, self.settings,
                                        now_fn=lambda: self.at(2026, 9, 13, hour=17), birthdays_fn=fake_birthdays)
        svc.run_weekly()
        self.assertIn("Robyn Whitewood", self.client.posts[0])

    def test_weekly_position_still_posts_when_the_birthday_loader_fails(self):
        def broken(days):
            raise RuntimeError("spreadsheet missing")
        svc = reminders.ReminderService(family_app.get_db, self.client, self.settings,
                                        now_fn=lambda: self.at(2026, 9, 13, hour=17), birthdays_fn=broken)
        result = svc.run_weekly()
        self.assertEqual(result["sent"], ["weekly_position:2026-09-13"])
        self.assertNotIn("Birthdays", self.client.posts[0])

    def test_position_exposes_targets_and_upcoming(self):
        svc = self.service(self.at(2026, 9, 9))
        position = svc.position()
        keys = {t["account_key"] for t in position["targets"]}
        self.assertTrue({"ecomm_gst", "ing_home", "ing_emergency", "gsb_everyday"} <= keys)
        self.assertTrue(all("name" in u and "due_date" in u for u in position["upcoming"]))


def _post(pid, user, message, create_at, file_ids=None):
    return {"id": pid, "user_id": user, "message": message, "create_at": create_at, "file_ids": file_ids or [], "root_id": "", "type": ""}


class PollTests(ReminderCase):
    def setUp(self):
        super().setUp()
        self.client.can_read = True
        self.settings["allowed_users"] = ["tawhai", "mum"]

    def test_first_poll_initialises_watermark_and_replays_nothing(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        self.client.incoming = [_post("old", "u1", "gst 1", 1)]
        result = svc.poll_once()
        self.assertEqual(result["reason"], "watermark initialised")
        self.assertEqual(self.client.posts, [])
        self.assertEqual(int(svc.get_state("mm_last_post_create_at")), int(svc.now().timestamp() * 1000))

    def test_poll_handles_allowed_user_commands_and_logs_reply(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        svc.set_state("mm_last_post_create_at", "1000")
        self.client.incoming = [_post("p1", "u1", "gst 9262", 2000)]
        result = svc.poll_once()
        self.assertEqual((result["processed"], result["replied"]), (1, 1))
        self.assertIn("Got it: EComm GST $9,262", self.client.posts[0])
        self.assertEqual(self.client.reactions, [("p1", "white_check_mark")])
        with family_app.get_db() as db:
            log = db.execute("SELECT kind, dedupe_key, body FROM reminder_log WHERE dedupe_key='reply:p1'").fetchone()
            balance = db.execute("SELECT balance FROM account_balances WHERE mattermost_post_id='p1'").fetchone()
        self.assertEqual(log["kind"], "reply")
        self.assertTrue(log["body"].startswith("> gst 9262"))
        self.assertEqual(balance["balance"], 9262.0)
        self.assertEqual(svc.get_state("mm_last_post_create_at"), "2000")

    def test_poll_ignores_bot_unknown_users_and_system_posts(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        svc.set_state("mm_last_post_create_at", "1000")
        self.client.incoming = [
            _post("b1", "botid", "status", 2000),
            _post("s1", "u9", "status", 2100),
            {**_post("sys", "u1", "familyhq added to the channel", 2200), "type": "system_add_to_channel"},
        ]
        result = svc.poll_once()
        self.assertEqual((result["processed"], result["skipped"]), (0, 3))
        self.assertEqual(self.client.posts, [])
        self.assertEqual(svc.get_state("mm_last_post_create_at"), "2200")

    def test_poll_never_handles_the_same_post_twice(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        svc.set_state("mm_last_post_create_at", "1000")
        self.client.incoming = [_post("p1", "u1", "gst 9262", 2000)]
        svc.poll_once()
        svc.set_state("mm_last_post_create_at", "1000")  # pretend the watermark was lost
        result = svc.poll_once()
        self.assertEqual((result["processed"], result["skipped"]), (0, 1))
        self.assertEqual(len(self.client.posts), 1)

    def test_poll_downloads_images_and_passes_them_to_the_handler(self):
        seen = {}

        def fake_llm(messages, system="", images=None):
            seen["images"] = images
            return json.dumps({"kind": "balances", "balances": [
                {"account_key": "ing_home", "name_seen": "Home", "balance": 2142.41, "available": None, "as_of": None}]})
        svc = reminders.ReminderService(family_app.get_db, self.client, self.settings,
                                        now_fn=lambda: self.at(2026, 9, 10, hour=9), llm=fake_llm)
        svc.set_state("mm_last_post_create_at", "1000")
        self.client.files["f1"] = (b"PNG!", "image/png", "ing.png")
        self.client.incoming = [_post("p2", "u2", "", 2000, file_ids=["f1"])]
        result = svc.poll_once()
        self.assertEqual(result["replied"], 1)
        self.assertEqual(seen["images"][0]["media_type"], "image/png")
        self.assertIn("Got it: ING Home $2,142", self.client.posts[0])

    def test_poll_failure_counts_and_tick_backs_off(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        svc.set_state("mm_last_post_create_at", "1000")
        svc.set_state("last_daily_run", "2026-09-10")
        self.client.read_fail = True
        for _ in range(3):
            svc.poll_once()
        self.assertEqual(svc.get_state("mm_poll_failures"), "3")
        polled_ticks = 0
        for _ in range(10):
            before = int(svc.get_state("mm_poll_failures"))
            reminders.scheduler_tick(svc, svc.now())
            polled_ticks += int(svc.get_state("mm_poll_failures")) - before
        self.assertEqual(polled_ticks, 2)  # only every fifth tick while failing

    def test_tick_polls_when_bot_can_read(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        svc.set_state("last_daily_run", "2026-09-10")
        svc.set_state("mm_last_post_create_at", "1000")
        self.client.incoming = [_post("p1", "u1", "help", 2000)]
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["poll"])
        self.assertIn("*status*", self.client.posts[0])


    def test_reply_failure_keeps_the_post_for_the_next_poll(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        svc.set_state("mm_last_post_create_at", "1000")
        self.client.incoming = [_post("p1", "u1", "help", 2000)]
        self.client.fail = True
        result = svc.poll_once()
        self.assertIn("Mattermost error", result["reason"])
        self.assertEqual(svc.get_state("mm_last_post_create_at"), "1999")
        self.client.fail = False
        result = svc.poll_once()
        self.assertEqual(result["replied"], 1)


    def test_log_failure_does_not_cause_a_resend(self):
        svc = self.service(self.at(2026, 10, 1))
        real_db = svc._db
        state = {"fail": True}

        class Proxy:
            def __init__(self, conn): self.conn = conn
            def execute(self, sql, *args):
                if state["fail"] and "INSERT OR IGNORE INTO reminder_log" in sql:
                    import sqlite3
                    raise sqlite3.OperationalError("database is locked")
                return self.conn.execute(sql, *args)
            def __getattr__(self, name): return getattr(self.conn, name)

        from contextlib import contextmanager

        @contextmanager
        def flaky_db():
            with real_db() as conn:
                yield Proxy(conn)
        svc._db = flaky_db
        with patch.object(reminders.time, "sleep", lambda s: None):
            result = svc.run_daily()
        self.assertEqual(len(self.client.posts), 1)          # sent once
        self.assertIn("monthly_setaside:2026-09", result["sent"])
        state["fail"] = False
        svc._db = real_db
        again = svc.run_daily()                               # log was lost, so this resends once more at most
        self.assertLessEqual(len(self.client.posts), 2)

    def test_poll_saves_the_watermark_after_each_post(self):
        svc = self.service(self.at(2026, 9, 10, hour=9))
        svc.set_state("mm_last_post_create_at", "1000")
        self.client.can_read = True
        self.settings["allowed_users"] = ["tawhai"]
        calls = {"n": 0}
        original_post = self.client.post

        def post_then_fail(message):
            calls["n"] += 1
            if calls["n"] == 2:
                import mattermost
                raise mattermost.MattermostError("down")
            return original_post(message)
        self.client.post = post_then_fail
        self.client.incoming = [_post("p1", "u1", "help", 2000), _post("p2", "u1", "help", 3000)]
        svc.poll_once()
        self.assertEqual(svc.get_state("mm_last_post_create_at"), "2999")


class SetupAndOverdueTests(ReminderCase):
    def test_monday_propvesting_check_until_anchor_set(self):
        svc = self.service(self.at(2026, 9, 14))  # Monday
        result = svc.run_daily()
        self.assertIn("propvesting_check:2026-09-14", result["sent"])
        self.assertIn("PropVesting check", self.client.posts[-1])
        with family_app.get_db() as db:
            db.execute("UPDATE obligations SET anchor_date='2026-09-20' WHERE name LIKE 'PropVesting%'")
        svc = self.service(self.at(2026, 9, 21))  # next Monday
        self.assertEqual([k for k in svc.run_daily()["sent"] if "propvesting" in k], [])

    def test_one_setup_question_per_day_for_pending_items(self):
        svc = self.service(self.at(2026, 9, 10))
        first = svc.run_daily()
        setup_keys = [k for k in first["sent"] if k.startswith("setup_question:")]
        self.assertEqual(len(setup_keys), 1)
        self.assertIn("Set-up question", self.client.posts[-1])
        svc = self.service(self.at(2026, 9, 11))
        second = [k for k in svc.run_daily()["sent"] if k.startswith("setup_question:")]
        self.assertEqual(len(second), 1)
        self.assertNotEqual(second, setup_keys)

    def test_overdue_open_occurrence_appears_with_negative_days(self):
        svc = self.service(self.at(2026, 10, 10))
        position = svc.position()
        mortgage = next(u for u in position["upcoming"] if u["name"] == "Mortgage repayment" and u["due_date"] == "2026-10-05")
        self.assertEqual((mortgage["days_out"], mortgage["overdue"]), (-5, True))

    def test_auto_pay_occurrence_marked_paid_after_due(self):
        with family_app.get_db() as db:
            db.execute("UPDATE obligations SET auto_pay=1 WHERE name='Mortgage repayment'")
        svc = self.service(self.at(2026, 10, 6))
        svc.regenerate_occurrences()
        with family_app.get_db() as db:
            row = db.execute("SELECT state, state_changed_by FROM obligation_occurrences o JOIN obligations b ON b.id=o.obligation_id "
                             "WHERE b.name='Mortgage repayment' AND o.due_date='2026-10-05'").fetchone()
        self.assertEqual((row["state"], row["state_changed_by"]), ("paid", "auto_pay"))
        self.assertNotIn("2026-10-05", [u["due_date"] for u in svc.position()["upcoming"] if u["name"] == "Mortgage repayment"])


class SchedulerTickTests(ReminderCase):
    def test_tick_retries_a_run_whose_send_failed(self):
        self.client.fail = True
        svc = self.service(self.at(2026, 10, 1, hour=7))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["daily"])
        self.assertIsNone(svc.get_state("last_daily_run"))
        self.client.fail = False
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["daily"])
        self.assertEqual(svc.get_state("last_daily_run"), "2026-10-01")
        self.assertIn("September 2026 set-aside", self.client.posts[0])
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), [])

    def test_tick_marks_done_when_there_was_nothing_to_send(self):
        svc = self.service(self.at(2026, 9, 10, hour=7))
        self.assertEqual(reminders.scheduler_tick(svc, svc.now()), ["daily"])
        self.assertEqual(svc.get_state("last_daily_run"), "2026-09-10")

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
