"""Scheduled money reminders for Family HQ.

Decides what to say each day, never says the same thing twice, bundles a day's items into
one Mattermost post, and records everything in `reminder_log`. Arithmetic lives in
`obligations.py`; network calls live in `mattermost.py`.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import obligations as ob
from mattermost import MattermostError

REGENERATE_BACK_DAYS = 35
REGENERATE_FORWARD_DAYS = 400
UPCOMING_WINDOW_DAYS = 30
POSITION_WINDOW_DAYS = 90
BIRTHDAY_WINDOW_DAYS = 14


def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


class ReminderService:
    def __init__(self, get_db, client, settings: dict, now_fn=None, birthdays_fn=None):
        self.get_db = get_db
        self.birthdays_fn = birthdays_fn
        self.client = client
        self.settings = settings
        self.tz = ZoneInfo(settings.get('timezone', ob.DEFAULT_SETTINGS['timezone']))
        self._now_fn = now_fn

    @contextmanager
    def _db(self):
        """A committed-and-closed connection; `get_db` alone never closes."""
        conn = self.get_db()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ── clock ────────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self._now_fn() if self._now_fn else datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def in_quiet_hours(self, now: datetime) -> bool:
        start, end = self.settings.get('quiet_hours', ob.DEFAULT_SETTINGS['quiet_hours'])
        hour = now.hour
        return hour >= start or hour < end if start > end else start <= hour < end

    # ── state ────────────────────────────────────────────────────────────────
    def get_state(self, key: str):
        with self._db() as db:
            row = db.execute('SELECT value FROM reminder_state WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None

    def set_state(self, key: str, value: str):
        with self._db() as db:
            db.execute(
                'INSERT INTO reminder_state (key, value, updated_at) VALUES (?,?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                (key, value, self.now().isoformat()[:19]),
            )

    # ── data ─────────────────────────────────────────────────────────────────
    def load(self, today: date | None = None) -> dict:
        with self._db() as db:
            obligations = _rows(db.execute("SELECT * FROM obligations WHERE status != 'retired'"))
            occurrences = _rows(db.execute('SELECT * FROM obligation_occurrences ORDER BY due_date'))
            receipts_rows = {
                r['year_month']: r['amount_incl_gst']
                for r in db.execute('SELECT year_month, amount_incl_gst FROM receipts_log')
            }
            balances = {}
            for r in db.execute(
                'SELECT account_key, balance, available, as_of FROM account_balances '
                'ORDER BY as_of ASC, id ASC'
            ):
                balances[r['account_key']] = {
                    'balance': r['balance'], 'available': r['available'], 'as_of': r['as_of'],
                }
        return {
            'obligations': obligations, 'occurrences': occurrences,
            'receipts_rows': receipts_rows, 'balances': balances,
        }

    # ── occurrences ──────────────────────────────────────────────────────────
    def regenerate_occurrences(self, today: date | None = None) -> int:
        """Insert any missing occurrences in the window and refresh open estimates."""
        today = today or self.today()
        start = today - timedelta(days=REGENERATE_BACK_DAYS)
        end = today + timedelta(days=REGENERATE_FORWARD_DAYS)
        data = self.load(today)
        created = 0
        with self._db() as db:
            for item in data['obligations']:
                if item['amount_rule'] == 'bas_formula':
                    pairs = [{'standard_date': d, 'due_date': ob.roll_weekend(d)} for d in ob.bas_standard_dates(start, end)]
                else:
                    pairs = ob.generate_occurrences(item, start, end)
                for pair in pairs:
                    cursor = db.execute(
                        'INSERT OR IGNORE INTO obligation_occurrences (obligation_id, standard_date, due_date, state) '
                        "VALUES (?,?,?,'upcoming')",
                        (item['id'], pair['standard_date'].isoformat(), pair['due_date'].isoformat()),
                    )
                    created += cursor.rowcount
            # refresh estimates on everything not yet paid or skipped
            for item in data['obligations']:
                open_rows = db.execute(
                    "SELECT id, standard_date FROM obligation_occurrences "
                    "WHERE obligation_id=? AND state IN ('upcoming','funds_confirmed')",
                    (item['id'],),
                ).fetchall()
                for row in open_rows:
                    if item['amount_rule'] == 'bas_formula':
                        detail = ob.bas_estimate(date.fromisoformat(row['standard_date']), data['receipts_rows'], self.settings)
                        db.execute('UPDATE obligation_occurrences SET estimate=?, estimate_detail=? WHERE id=?',
                                   (detail['total'], json.dumps(detail), row['id']))
                    elif item['amount_rule'] in ('fixed', 'sinking_hold') and item.get('amount') is not None:
                        db.execute('UPDATE obligation_occurrences SET estimate=? WHERE id=?',
                                   (float(item['amount']), row['id']))
        return created

    # ── what to say ──────────────────────────────────────────────────────────
    def _sent_keys(self) -> set[str]:
        with self._db() as db:
            return {r['dedupe_key'] for r in db.execute('SELECT dedupe_key FROM reminder_log')}

    def position(self, today: date | None = None) -> dict:
        today = today or self.today()
        self.regenerate_occurrences(today)
        data = self.load(today)
        targets = ob.account_targets(today, data['obligations'], data['occurrences'],
                                     data['receipts_rows'], data['balances'], self.settings)
        by_id = {o['id']: o for o in data['obligations']}
        upcoming = []
        for occ in data['occurrences']:
            item = by_id.get(occ['obligation_id'])
            due = date.fromisoformat(occ['due_date'])
            if not item or occ['state'] in ('paid', 'skipped') or due < today or (due - today).days > POSITION_WINDOW_DAYS:
                continue
            if item['amount_rule'] in ('receipts_share',) or not item.get('remind'):
                continue
            upcoming.append({
                'occurrence_id': occ['id'], 'obligation_id': item['id'], 'name': item['name'],
                'due_date': occ['due_date'], 'standard_date': occ['standard_date'], 'estimate': occ['estimate'],
                'state': occ['state'], 'reserve_account': item.get('reserve_account'),
                'days_out': (due - today).days,
            })
        return {'today': today.isoformat(), 'targets': targets, 'upcoming': upcoming}

    def pending_daily_messages(self, today: date) -> list[dict]:
        data = self.load(today)
        messages = []
        if today.day == 1:
            previous = ob.add_months(today.replace(day=1), -1)
            ym = ob.month_key(previous)
            receipts, assumed = ob.receipts_for_month(ym, data['receipts_rows'], self.settings)
            setaside = ob.monthly_setaside(receipts, self.settings)
            home_lines = ob.household_setaside_lines(data['obligations'], data['occurrences'], self.settings)
            mortgage = next((o['amount'] for o in data['obligations']
                             if o['amount_rule'] == 'sinking_hold' and o['reserve_account'] == 'gsb_everyday'
                             and o['status'] != 'retired'), None)
            messages.append({
                'kind': 'monthly_setaside', 'dedupe_key': f'monthly_setaside:{ym}',
                'obligation_id': None, 'occurrence_id': None,
                'body': ob.compose_monthly_setaside(previous.strftime('%B %Y'), setaside, assumed,
                                                    home_lines, mortgage, self.settings),
            })
        targets = {t['account_key']: t for t in ob.account_targets(
            today, data['obligations'], data['occurrences'], data['receipts_rows'], data['balances'], self.settings)}
        by_id = {o['id']: o for o in data['obligations']}
        sent = self._sent_keys()
        for occ in data['occurrences']:
            item = by_id.get(occ['obligation_id'])
            if not item or not item.get('remind') or item['amount_rule'] == 'receipts_share':
                continue
            if occ['state'] in ('paid', 'skipped'):
                continue
            lead_days = json.loads(item.get('lead_days') or '[]')
            if not lead_days:
                continue
            days_out = (date.fromisoformat(occ['due_date']) - today).days
            eligible = [lead for lead in lead_days if days_out <= lead]
            if days_out < 0 or not eligible:
                continue
            lead = min(eligible)
            key = f'lead_warning:occ:{occ["id"]}:{lead}'
            if key in sent:
                continue
            messages.append({
                'kind': 'lead_warning', 'dedupe_key': key,
                'obligation_id': item['id'], 'occurrence_id': occ['id'],
                'body': ob.compose_lead_warning(item, occ, days_out, targets.get(item.get('reserve_account')), today),
            })
        return messages

    # ── sending ──────────────────────────────────────────────────────────────
    def _deliver(self, messages: list[dict], dry_run: bool) -> dict:
        sent_keys = self._sent_keys()
        fresh = [m for m in messages if m['dedupe_key'] not in sent_keys]
        skipped = [m['dedupe_key'] for m in messages if m['dedupe_key'] in sent_keys]
        result = {'sent': [], 'skipped': skipped, 'body': None, 'post_id': None, 'reason': None}
        if not fresh:
            result['reason'] = 'nothing new'
            return result
        body = ob.compose_bundle([m['body'] for m in fresh])
        result['body'] = body
        if dry_run:
            result['reason'] = 'dry run'
            return result
        if self.in_quiet_hours(self.now()):
            result['reason'] = 'quiet hours'
            return result
        if self.client is None or not getattr(self.client, 'can_post', False):
            result['reason'] = 'Mattermost not configured'
            return result
        try:
            post_id = self.client.post(body)
        except MattermostError as exc:
            result['reason'] = f'Mattermost error: {exc}'
            return result
        now = self.now().isoformat()[:19]
        with self._db() as db:
            for m in fresh:
                db.execute(
                    'INSERT OR IGNORE INTO reminder_log (kind, dedupe_key, obligation_id, occurrence_id, '
                    'mattermost_post_id, body, sent_at) VALUES (?,?,?,?,?,?,?)',
                    (m['kind'], m['dedupe_key'], m['obligation_id'], m['occurrence_id'], post_id, m['body'], now),
                )
        result['sent'] = [m['dedupe_key'] for m in fresh]
        result['post_id'] = post_id
        return result

    def run_daily(self, today: date | None = None, dry_run: bool = False) -> dict:
        today = today or self.today()
        self.regenerate_occurrences(today)
        return self._deliver(self.pending_daily_messages(today), dry_run)

    def run_weekly(self, today: date | None = None, dry_run: bool = False) -> dict:
        today = today or self.today()
        self.regenerate_occurrences(today)
        position = self.position(today)
        upcoming = [u for u in position['upcoming'] if u['days_out'] <= UPCOMING_WINDOW_DAYS]
        birthdays = []
        if self.birthdays_fn is not None:
            try:
                birthdays = list(self.birthdays_fn(BIRTHDAY_WINDOW_DAYS))
            except Exception as exc:  # noqa: BLE001 — a bad spreadsheet must not stop the money message
                print(f'[reminders] birthdays unavailable: {exc}', flush=True)
        message = {
            'kind': 'weekly_position', 'dedupe_key': f'weekly_position:{today.isoformat()}',
            'obligation_id': None, 'occurrence_id': None,
            'body': ob.compose_weekly_position(position['targets'], upcoming, today, birthdays),
        }
        return self._deliver([message], dry_run)


def scheduler_tick(service: ReminderService, now: datetime) -> list[str]:
    """Run whichever jobs are due at `now`; each job runs at most once per local day."""
    ran = []
    today = now.date().isoformat()
    settings = service.settings
    post_hour = int(settings.get('post_hour_local', ob.DEFAULT_SETTINGS['post_hour_local']))
    weekly_day = int(settings.get('weekly_day', ob.DEFAULT_SETTINGS['weekly_day']))
    weekly_hour = int(settings.get('weekly_hour_local', ob.DEFAULT_SETTINGS['weekly_hour_local']))
    if now.hour >= post_hour and service.get_state('last_daily_run') != today:
        service.run_daily(now.date())
        service.set_state('last_daily_run', today)
        ran.append('daily')
    if now.weekday() == weekly_day and now.hour >= weekly_hour and service.get_state('last_weekly_run') != today:
        service.run_weekly(now.date())
        service.set_state('last_weekly_run', today)
        ran.append('weekly')
    return ran


def run_scheduler_loop(service_factory, sleep=time.sleep, log=print):
    """Daemon loop: build a fresh service each minute and run what is due. Never raises."""
    while True:
        try:
            service = service_factory()
            if service is not None:
                ran = scheduler_tick(service, service.now())
                if ran:
                    log(f'[reminders] ran {", ".join(ran)}', flush=True)
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            log(f'[reminders] tick failed: {exc}', flush=True)
        sleep(60)
