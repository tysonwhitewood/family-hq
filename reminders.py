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
import holdings as hold
import kids
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
    def __init__(self, get_db, client, settings: dict, now_fn=None, birthdays_fn=None, llm=None,
                 price_fn=None, account_adder=None, kids_settings: dict | None = None):
        self.get_db = get_db
        self.birthdays_fn = birthdays_fn
        self.llm = llm
        self.price_fn = price_fn
        self.account_adder = account_adder
        self.client = client
        self.settings = settings
        self.kids_settings = kids_settings or kids.kids_settings({})
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
        anchor_raw = self.settings.get('statement_reminder_anchor', ob.DEFAULT_SETTINGS['statement_reminder_anchor'])
        if self.settings.get('statement_reminder_enabled', True) and anchor_raw:
            try:
                anchor = date.fromisoformat(str(anchor_raw))
            except ValueError:
                anchor = None
            if anchor and today >= anchor and (today - anchor).days % 14 == 0:
                messages.append({
                    'kind': 'statement_reminder', 'dedupe_key': f'statement_reminder:{today.isoformat()}',
                    'obligation_id': None, 'occurrence_id': None,
                    'body': ob.compose_statement_reminder(self.settings.get('accounts', [])),
                })
        if today.weekday() == 0:
            propvesting = [o for o in data['obligations'] if 'propvesting' in o['name'].lower()
                           and o['status'] != 'retired' and not o.get('anchor_date')]
            if propvesting:
                messages.append({
                    'kind': 'propvesting_check', 'dedupe_key': f'propvesting_check:{today.isoformat()}',
                    'obligation_id': propvesting[0]['id'], 'occurrence_id': None,
                    'body': ob.compose_propvesting_check(),
                })
        with self._db() as db:
            setup_already_today = bool(db.execute(
                "SELECT 1 FROM reminder_log WHERE kind='setup_question' AND sent_at LIKE ?",
                (today.isoformat() + '%',),
            ).fetchone())
        for item in sorted((o for o in data['obligations'] if o['status'] == 'pending_confirmation'), key=lambda o: o['id']):
            created = date.fromisoformat(str(item.get('created_at') or today.isoformat())[:10])
            if (today - created).days > SETUP_QUESTION_DAYS:
                continue
            key = f'setup_question:{item["id"]}'
            if key in sent or setup_already_today or any(m['dedupe_key'].startswith('setup_question:') for m in messages):
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
        body = ob.compose_weekly_position(position['targets'], upcoming, today, birthdays)
        super_lines = self.investment_lines()
        if super_lines:
            body += '\n\n' + '\n'.join(super_lines)
        message = {
            'kind': 'weekly_position', 'dedupe_key': f'weekly_position:{today.isoformat()}',
            'obligation_id': None, 'occurrence_id': None, 'body': body,
        }
        return self._deliver([message], dry_run)

    def investment_lines(self) -> list[str]:
        """One line per investment account with holdings, prices refreshed first when a price feed exists."""
        lines = []
        accounts = [a for a in self.settings.get('accounts', []) if a.get('investment')]
        if not accounts:
            return lines
        now = self.now().isoformat()[:19]
        with self._db() as db:
            if self.price_fn is not None:
                hold.refresh_prices(db, self.price_fn, now)
            for account in accounts:
                last = db.execute('SELECT balance, as_of FROM account_balances WHERE account_key=? ORDER BY as_of DESC, id DESC LIMIT 1',
                                  (account['key'],)).fetchone()
                line = hold.compose_super_line(account.get('display', account['key']),
                                               hold.summary(db, account['key'], dict(last) if last else None))
                if line:
                    lines.append(line)
        return lines

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

    def _kid_jars(self, db, child: dict) -> dict:
        row = db.execute(
            'SELECT splurge, smile, give, grow FROM kid_balances WHERE child_key=? ORDER BY id DESC LIMIT 1',
            (child['key'],),
        ).fetchone()
        return kids.jars_from_row(dict(row) if row else None, child)

    def _kid_write_jars(self, db, child: dict, jars: dict, source: str):
        now = self.now().isoformat()[:19]
        full = kids.empty_jars()
        full.update(jars)
        db.execute(
            'INSERT INTO kid_balances (child_key, as_of, splurge, smile, give, grow, source, raw, created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (child['key'], now[:10], full['splurge'], full['smile'], full['give'], full['grow'], source, None, now),
        )

    def run_kids_money_meal(self, today: date | None = None, dry_run: bool = False) -> dict:
        """Sunday Money Meal: family bonus, child recap, parent Kit digest. Never posts to Family Finance for a child."""
        today = today or self.today()
        settings = self.kids_settings or {}
        family_chan = str(
            settings.get('family_finance_channel_id')
            or (getattr(self.client, 'channel_id', None) if self.client else None)
            or ''
        )
        result = {'sent': [], 'skipped': [], 'bodies': [], 'reason': None, 'parent_digest': None}
        if not settings.get('enabled', True):
            result['reason'] = 'nothing new'
            return result
        children = list(settings.get('children') or [])
        queue_after = []
        now = self.now().isoformat()[:19]
        sent_keys = self._sent_keys()
        with self._db() as db:
            for child in children:
                key = f"kids_meal:{child['key']}:{today.isoformat()}"
                if key in sent_keys:
                    result['skipped'].append(key)
                    continue
                jars = self._kid_jars(db, child)
                bonus_key = f"kids_bonus:{child['key']}:{today.isoformat()}"
                bonus = 0.0 if bonus_key in sent_keys else kids.family_bonus(
                    jars['smile'], jars['grow'],
                    float(settings.get('bonus_rate_weekly') or 0.01),
                    float(settings.get('bonus_cap') or 2.0),
                )
                display_jars = dict(jars)
                if bonus > 0 and bonus_key not in sent_keys:
                    jar = kids.bonus_jar(child)
                    display_jars[jar] = kids.round_cents(display_jars[jar] + bonus)
                    if not dry_run:
                        self._kid_write_jars(db, child, display_jars, 'bonus')
                        db.execute(
                            'INSERT INTO kid_ledger (child_key, kind, jar, amount, note, paid_in_kit, principle_id, created_at) '
                            'VALUES (?,?,?,?,?,?,?,?)',
                            (child['key'], 'bonus', jar, bonus, 'family bonus', 0, None, now),
                        )
                        db.execute(
                            'INSERT OR IGNORE INTO reminder_log (kind, dedupe_key, obligation_id, occurrence_id, '
                            'mattermost_post_id, body, sent_at) VALUES (?,?,?,?,?,?,?)',
                            ('kids_bonus', bonus_key, None, None, None, f'{child["name"]} {kids.dollars(bonus)}', now),
                        )
                jars = display_jars
                goal = db.execute(
                    "SELECT title, target_amount FROM kid_goals WHERE child_key=? AND status='active' ORDER BY id DESC LIMIT 1",
                    (child['key'],),
                ).fetchone()
                completed = {r['principle_id'] for r in db.execute(
                    'SELECT principle_id FROM kid_principles WHERE child_key=?', (child['key'],)
                )}
                nxt = kids.next_principle(child, completed)
                queue = [dict(r) for r in db.execute(
                    "SELECT child_key, kind, amount, note FROM kid_ledger WHERE paid_in_kit=0 AND kind IN ('seed','bonus','lesson') AND child_key=?",
                    (child['key'],),
                )]
                body = kids.compose_money_meal(child, jars, bonus, dict(goal) if goal else None, nxt, queue)
                result['bodies'].append({'child': child['key'], 'body': body})
                channel = str(child.get('mattermost_channel_id') or '').strip()
                if not kids.posting_allowed(channel, child, family_chan):
                    result['skipped'].append(key + ':channel')
                    continue
                if dry_run:
                    result['sent'].append(key)
                    continue
                if self.in_quiet_hours(self.now()):
                    result['reason'] = 'quiet hours'
                    return result
                if self.client is None or not getattr(self.client, 'can_post', False):
                    result['reason'] = 'Mattermost not configured'
                    return result
                try:
                    post_id = self.client.post(body, channel_id=channel)
                except MattermostError as exc:
                    result['reason'] = f'Mattermost error: {exc}'
                    return result
                db.execute(
                    'INSERT OR IGNORE INTO reminder_log (kind, dedupe_key, obligation_id, occurrence_id, '
                    'mattermost_post_id, body, sent_at) VALUES (?,?,?,?,?,?,?)',
                    ('kids_meal', key, None, None, post_id, body, now),
                )
                result['sent'].append(key)
            queue_after = [dict(r) for r in db.execute(
                "SELECT child_key, kind, amount, note FROM kid_ledger WHERE paid_in_kit=0 AND kind IN ('seed','bonus','lesson') ORDER BY id"
            )]
        digest = kids.compose_parent_digest(queue_after, children)
        result['parent_digest'] = digest
        digest_key = f'kids_parent_digest:{today.isoformat()}'
        if digest_key not in sent_keys and not dry_run and self.client is not None and getattr(self.client, 'can_post', False):
            if not self.in_quiet_hours(self.now()):
                try:
                    post_id = self.client.post(digest)
                    with self._db() as db:
                        db.execute(
                            'INSERT OR IGNORE INTO reminder_log (kind, dedupe_key, obligation_id, occurrence_id, '
                            'mattermost_post_id, body, sent_at) VALUES (?,?,?,?,?,?,?)',
                            ('kids_parent_digest', digest_key, None, None, post_id, digest, now),
                        )
                    result['sent'].append(digest_key)
                except MattermostError as exc:
                    result['reason'] = f'Mattermost error: {exc}'
                    return result
        if not result['reason']:
            result['reason'] = 'nothing new' if not result['sent'] else None
        return result

    def poll_kids(self, today: date | None = None) -> dict:
        """Read each child's Mattermost channel. Never treats Family Finance as a kids channel."""
        today = today or self.today()
        result = {'processed': 0, 'replied': 0, 'reason': None}
        settings = self.kids_settings or {}
        if self.client is None or not getattr(self.client, 'can_read', False):
            result['reason'] = 'bot token not configured'
            return result
        family_chan = str(
            settings.get('family_finance_channel_id')
            or getattr(self.client, 'channel_id', None)
            or ''
        )
        adult_allowed = {u.lower() for u in (self.settings.get('allowed_users') or [])}
        for child in settings.get('children') or []:
            channel = str(child.get('mattermost_channel_id') or '').strip()
            if not kids.posting_allowed(channel, child, family_chan):
                continue
            state_key = f"kids_mm_{child['key']}"
            watermark = self.get_state(state_key)
            now_ms = int(self.now().timestamp() * 1000)
            if watermark is None:
                self.set_state(state_key, str(now_ms))
                continue
            try:
                posts = self.client.posts_since(int(watermark), channel_id=channel)
                bot_id = self.client.me()['id']
                names = self.client.users_by_ids({p['user_id'] for p in posts})
            except MattermostError as exc:
                result['reason'] = f'Mattermost error: {exc}'
                return result
            kid_user = str(child.get('mattermost_username') or '').strip().lower()
            newest = int(watermark)
            sent = self._sent_keys()
            for post in posts:
                newest = max(newest, int(post.get('create_at') or 0))
                key = f"kids_reply:{post['id']}"
                if post['user_id'] == bot_id or post.get('type') or key in sent:
                    continue
                username = (names.get(post['user_id']) or '').lower()
                text = str(post.get('message') or '').strip()
                reply = None
                if kid_user and username == kid_user:
                    cmd = kids.parse_kid_command(text)
                    if cmd:
                        reply = self._answer_kid(child, cmd)
                elif username in adult_allowed:
                    parsed = kids.parse_parent_jars(text, child)
                    if parsed:
                        with self._db() as db:
                            self._kid_write_jars(db, child, parsed['jars'], 'typed')
                        reply = f"Saved {child['name']}'s jars from Kit."
                if not reply:
                    continue
                result['processed'] += 1
                try:
                    post_id = self.client.post(reply, channel_id=channel)
                except MattermostError as exc:
                    result['reason'] = f'Mattermost error: {exc}'
                    return result
                self._log_sent([{'kind': 'kids_reply', 'dedupe_key': key, 'body': reply}],
                               post_id, self.now().isoformat()[:19])
                result['replied'] += 1
            self.set_state(state_key, str(newest))
        return result

    def _answer_kid(self, child: dict, cmd: dict) -> str:
        if cmd['action'] == 'help':
            return kids.compose_kid_help()
        with self._db() as db:
            jars = self._kid_jars(db, child)
            if cmd['action'] == 'jars':
                return kids.compose_jars_line(child, jars)
            goal = db.execute(
                "SELECT title, target_amount FROM kid_goals WHERE child_key=? AND status='active' ORDER BY id DESC LIMIT 1",
                (child['key'],),
            ).fetchone()
        return kids.compose_goal_line(dict(goal) if goal else None, jars.get('smile') or 0)


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
    kids_cfg = getattr(service, 'kids_settings', None) or {}
    meal_day = int(kids_cfg.get('money_meal_weekday', 6))
    meal_hour = int(kids_cfg.get('money_meal_hour', 16))
    has_kid_channel = any(str(c.get('mattermost_channel_id') or '').strip() for c in kids_cfg.get('children') or [])
    if kids_cfg.get('enabled', True) and has_kid_channel and now.weekday() == meal_day and now.hour >= meal_hour and service.get_state('last_kids_meal_run') != today:
        outcome = service.run_kids_money_meal(now.date())
        if _delivered(outcome):
            service.set_state('last_kids_meal_run', today)
        else:
            print(f'[reminders] kids meal not marked done, will retry: {outcome.get("reason")}', flush=True)
        ran.append('kids_meal')
    if service.client is not None and getattr(service.client, 'can_read', False):
        tick = int(service.get_state('mm_poll_tick') or 0) + 1
        service.set_state('mm_poll_tick', str(tick))
        failures = int(service.get_state('mm_poll_failures') or 0)
        if failures < POLL_BACKOFF_AFTER or tick % POLL_BACKOFF_EVERY == 0:
            outcome = service.poll_once(now.date())
            if outcome.get('processed') or outcome.get('reason') not in (None, 'watermark initialised'):
                ran.append('poll')
            kids_poll = service.poll_kids(now.date())
            if kids_poll.get('processed') or (kids_poll.get('reason') or '').startswith('Mattermost error'):
                ran.append('kids_poll')
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
