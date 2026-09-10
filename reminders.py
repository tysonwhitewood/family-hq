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

import conversation
import obligations as ob
from mattermost import MattermostError

REGENERATE_BACK_DAYS = 35
REGENERATE_FORWARD_DAYS = 400
UPCOMING_WINDOW_DAYS = 30
POSITION_WINDOW_DAYS = 90
BIRTHDAY_WINDOW_DAYS = 14
OVERDUE_WINDOW_DAYS = 30
SETUP_QUESTION_DAYS = 14
POLL_BACKOFF_AFTER = 3
POLL_BACKOFF_EVERY = 5


def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


class ReminderService:
    def __init__(self, get_db, client, settings: dict, now_fn=None, birthdays_fn=None, llm=None):
        self.get_db = get_db
        self.birthdays_fn = birthdays_fn
        self.llm = llm
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
            # direct debits settle themselves the day after they fall due
            for item in data['obligations']:
                if item.get('auto_pay'):
                    db.execute(
                        "UPDATE obligation_occurrences SET state='paid', state_changed_at=?, state_changed_by='auto_pay' "
                        "WHERE obligation_id=? AND state IN ('upcoming','funds_confirmed') AND due_date < ?",
                        (self.now().isoformat()[:19], item['id'], today.isoformat()),
                    )
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
            days_out = (due - today).days
            if not item or occ['state'] in ('paid', 'skipped') or days_out < -OVERDUE_WINDOW_DAYS or days_out > POSITION_WINDOW_DAYS:
                continue
            if item['amount_rule'] in ('receipts_share',) or not item.get('remind'):
                continue
            upcoming.append({
                'occurrence_id': occ['id'], 'obligation_id': item['id'], 'name': item['name'],
                'due_date': occ['due_date'], 'standard_date': occ['standard_date'], 'estimate': occ['estimate'],
                'state': occ['state'], 'reserve_account': item.get('reserve_account'),
                'days_out': days_out, 'overdue': days_out < 0, 'auto_pay': bool(item.get('auto_pay')),
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
        sent = self._sent_keys()
        if today.weekday() == 0:
            propvesting = [o for o in data['obligations'] if 'propvesting' in o['name'].lower()
                           and o['status'] != 'retired' and not o.get('anchor_date')]
            if propvesting:
                messages.append({
                    'kind': 'propvesting_check', 'dedupe_key': f'propvesting_check:{today.isoformat()}',
                    'obligation_id': propvesting[0]['id'], 'occurrence_id': None,
                    'body': ob.compose_propvesting_check(),
                })
        for item in sorted((o for o in data['obligations'] if o['status'] == 'pending_confirmation'), key=lambda o: o['id']):
            created = date.fromisoformat(str(item.get('created_at') or today.isoformat())[:10])
            if (today - created).days > SETUP_QUESTION_DAYS:
                continue
            key = f'setup_question:{item["id"]}'
            if key in sent or any(m['dedupe_key'].startswith('setup_question:') for m in messages):
                continue
            messages.append({'kind': 'setup_question', 'dedupe_key': key, 'obligation_id': item['id'],
                             'occurrence_id': None, 'body': ob.compose_setup_question(item)})
            break
        targets = {t['account_key']: t for t in ob.account_targets(
            today, data['obligations'], data['occurrences'], data['receipts_rows'], data['balances'], self.settings)}
        by_id = {o['id']: o for o in data['obligations']}
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
        self._log_sent(fresh, post_id, now)
        result['sent'] = [m['dedupe_key'] for m in fresh]
        result['post_id'] = post_id
        return result

    def _log_sent(self, messages: list[dict], post_id, now: str) -> bool:
        """Record delivered messages. The message is already in the channel, so a logging failure
        must never bubble up and cause a resend; it is retried once, then reported."""
        for attempt in (1, 2):
            try:
                with self._db() as db:
                    for m in messages:
                        db.execute(
                            'INSERT OR IGNORE INTO reminder_log (kind, dedupe_key, obligation_id, occurrence_id, '
                            'mattermost_post_id, body, sent_at) VALUES (?,?,?,?,?,?,?)',
                            (m['kind'], m['dedupe_key'], m.get('obligation_id'), m.get('occurrence_id'), post_id, m['body'], now),
                        )
                return True
            except Exception as exc:  # noqa: BLE001 — sqlite lock or disk problem
                print(f'[reminders] could not record sent messages (attempt {attempt}): {exc}', flush=True)
                time.sleep(0.5)
        return False

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

    # ── reading the channel ──────────────────────────────────────────────────
    def allowed_user_ids(self, ids) -> set[str]:
        """Ids among `ids` whose Mattermost username is in `allowed_users`; usernames cached in state."""
        allowed_names = {str(u).lower() for u in (self.settings.get('allowed_users') or [])}
        try:
            cache = json.loads(self.get_state('mm_user_cache') or '{}')
        except ValueError:
            cache = {}
        missing = [i for i in ids if i not in cache]
        if missing:
            try:
                cache.update(self.client.users_by_ids(missing))
            except MattermostError:
                pass
            self.set_state('mm_user_cache', json.dumps(cache))
        return {i for i in ids if str(cache.get(i, '')).lower() in allowed_names}

    def _download_images(self, post: dict) -> list[tuple[bytes, str, str]]:
        images = []
        for file_id in post.get('file_ids') or []:
            try:
                images.append(self.client.download_file(file_id))
            except MattermostError as exc:
                print(f'[reminders] skipped attachment {file_id}: {exc}', flush=True)
        return images

    def _bump_failures(self) -> int:
        failures = int(self.get_state('mm_poll_failures') or 0) + 1
        self.set_state('mm_poll_failures', str(failures))
        return failures

    def poll_once(self, today: date | None = None) -> dict:
        """Read new channel posts once, act on those from allowed users, reply, and advance the watermark."""
        today = today or self.today()
        result = {'processed': 0, 'replied': 0, 'skipped': 0, 'reason': None}
        if self.client is None or not getattr(self.client, 'can_read', False):
            result['reason'] = 'bot token not configured'
            return result
        now_ms = int(self.now().timestamp() * 1000)
        watermark = self.get_state('mm_last_post_create_at')
        if watermark is None:
            self.set_state('mm_last_post_create_at', str(now_ms))
            self.set_state('mm_last_poll_at', self.now().isoformat()[:19])
            result['reason'] = 'watermark initialised'
            return result
        try:
            posts = self.client.posts_since(int(watermark))
            bot_id = self.client.me()['id']
        except MattermostError as exc:
            self._bump_failures()
            result['reason'] = f'Mattermost error: {exc}'
            return result
        self.set_state('mm_poll_failures', '0')
        allowed = self.allowed_user_ids({p['user_id'] for p in posts})
        sent = self._sent_keys()
        newest = int(watermark)
        for post in posts:
            newest = max(newest, int(post.get('create_at') or 0))
            key = f"reply:{post['id']}"
            if post['user_id'] == bot_id or post.get('type') or key in sent or post['user_id'] not in allowed:
                result['skipped'] += 1
                continue
            images = self._download_images(post)
            try:
                outcome = conversation.handle_post(self, post, self.settings, llm=self.llm, images=images, today=today)
            except Exception as exc:  # noqa: BLE001 — one bad post must not stop the poll
                outcome = {'reply': f'Sorry, something went wrong handling that: {str(exc)[:120]}', 'acted': False}
            result['processed'] += 1
            reply = outcome.get('reply')
            if not reply:
                continue
            try:
                post_id = self.client.post(reply)
                if outcome.get('acted'):
                    try:
                        self.client.add_reaction(post['id'])
                    except MattermostError:
                        pass
            except MattermostError as exc:
                # Leave the watermark just before this post so it is retried next poll.
                self._bump_failures()
                result['reason'] = f'Mattermost error: {exc}'
                newest = max(int(watermark), int(post.get('create_at') or 0) - 1)
                break
            self._log_sent([{'kind': 'reply', 'dedupe_key': key,
                             'body': f"> {str(post.get('message') or '').strip() or '(image)'}\n\n{reply}"}],
                           post_id, self.now().isoformat()[:19])
            result['replied'] += 1
            self.set_state('mm_last_post_create_at', str(newest))
        self.set_state('mm_last_post_create_at', str(newest))
        self.set_state('mm_last_poll_at', self.now().isoformat()[:19])
        return result


RETRY_REASONS = ('Mattermost not configured', 'quiet hours')


def _delivered(outcome: dict) -> bool:
    """True when a run sent its messages or had nothing to send; False when sending failed and should be retried."""
    reason = str(outcome.get('reason') or '')
    if outcome.get('sent'):
        return True
    return not (reason in RETRY_REASONS or reason.startswith('Mattermost error'))


def scheduler_tick(service: ReminderService, now: datetime) -> list[str]:
    """Run whichever jobs are due at `now`; each job runs at most once per local day."""
    ran = []
    today = now.date().isoformat()
    settings = service.settings
    post_hour = int(settings.get('post_hour_local', ob.DEFAULT_SETTINGS['post_hour_local']))
    weekly_day = int(settings.get('weekly_day', ob.DEFAULT_SETTINGS['weekly_day']))
    weekly_hour = int(settings.get('weekly_hour_local', ob.DEFAULT_SETTINGS['weekly_hour_local']))
    if now.hour >= post_hour and service.get_state('last_daily_run') != today:
        outcome = service.run_daily(now.date())
        if _delivered(outcome):
            service.set_state('last_daily_run', today)
        else:
            print(f'[reminders] daily run not marked done, will retry: {outcome.get("reason")}', flush=True)
        ran.append('daily')
    if now.weekday() == weekly_day and now.hour >= weekly_hour and service.get_state('last_weekly_run') != today:
        outcome = service.run_weekly(now.date())
        if _delivered(outcome):
            service.set_state('last_weekly_run', today)
        else:
            print(f'[reminders] weekly run not marked done, will retry: {outcome.get("reason")}', flush=True)
        ran.append('weekly')
    if service.client is not None and getattr(service.client, 'can_read', False):
        tick = int(service.get_state('mm_poll_tick') or 0) + 1
        service.set_state('mm_poll_tick', str(tick))
        failures = int(service.get_state('mm_poll_failures') or 0)
        if failures < POLL_BACKOFF_AFTER or tick % POLL_BACKOFF_EVERY == 0:
            outcome = service.poll_once(now.date())
            if outcome.get('processed') or outcome.get('reason') not in (None, 'watermark initialised'):
                ran.append('poll')
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
