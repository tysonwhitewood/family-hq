# Family HQ

Whitewood family command centre: birthdays, goals, property, finance imports, a six-month
cash-flow forecast, and money reminders posted to Mattermost. Flask + SQLite, deployed with
Coolify at family.edencommercial.au.

## Running locally

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py            # http://localhost:3000
    .venv/bin/python -m unittest discover -s tests -q

## Environment variables (set in Coolify)

| Variable | Required | Purpose |
|---|---|---|
| `FAMILY_HQ_USER`, `FAMILY_HQ_PASS` | yes | Login |
| `SECRET_KEY` | yes | Session signing |
| `ANTHROPIC_API_KEY` | for AI features | Chat, briefing, and (step 2) reading screenshots posted to Mattermost |
| `MATTERMOST_URL` | for reminders | e.g. `https://chat.leaseintel.ai` |
| `MATTERMOST_BOT_TOKEN` | for reminders | Personal access token of the Family HQ bot account |
| `MATTERMOST_CHANNEL_ID` | for reminders | The Family Finance channel id (Channel → View Info) |
| `MATTERMOST_WEBHOOK_URL` | optional | Incoming webhook; used to send when no bot token is set |

If `MATTERMOST_URL` is absent, or neither a bot token + channel nor a webhook is set,
reminders are computed but never sent, the Obligations page still works, and Settings shows
"Not configured". Nothing else in the app is affected.

## Obligations and reminders

Every recurring money obligation (BAS, PAYG, rates, water, insurance, rego, the PropVesting
hold, the mortgage repayment and the food buffer) is a row on the **Obligations** page. From
these Family HQ works out what each reserve account should hold today and posts to Mattermost:

- **1st of the month, 7am Brisbane:** the set-aside for the month just ended.
- **30, 7 (and 1 for BAS) days before a due date:** a warning with the estimate, what the reserve
  account should hold, the last known balance and the shortfall.
- **Sunday 5pm:** the position of every reserve account.

Messages are bundled into one post a day, never repeated, and never sent between 9pm and 7am.

### Settings (`data/config.json` → `obligations`)

| Key | Default | Valid values | Meaning / when absent |
|---|---|---|---|
| `gst_fraction` | `0.0909` (1/11) | 0–1 | Share of GST-inclusive receipts reserved for GST |
| `income_tax_reserve_rate` | `0.15` | 0–1 | Share of ex-GST receipts reserved for company income tax |
| `assumed_monthly_retainer` | `10083.34` | dollars | Receipts assumed for any month not reported |
| `gst_credit_allowance_monthly` | `636` | dollars | Expected GST credits deducted per month in the BAS estimate |
| `payg_instalment_quarterly` | `3188` | dollars | PAYG instalment added to every BAS estimate |
| `sl_trading_trust_bas` | `545` | dollars | SL Trading Trust BAS added to every BAS estimate |
| `emergency_floor` | `3000` | dollars | ING Emergency target (informational; the seed row carries the amount) |
| `mortgage_repayment` | `4810.38` | dollars | Informational; the seed row carries the amount |
| `reserve_start_month` | `"2026-09"` | `YYYY-MM` | First month the income-tax pot accrues from |
| `post_hour_local` | `7` | 0–23 | Hour of the daily run |
| `weekly_day` | `6` | 0 (Mon) – 6 (Sun) | Day of the weekly position |
| `weekly_hour_local` | `17` | 0–23 | Hour of the weekly position |
| `quiet_hours` | `[21, 7]` | two hours | No posts from the first hour to the second |
| `timezone` | `"Australia/Brisbane"` | IANA zone | All scheduling |
| `stale_balance_days` | `14` | days | A balance older than this is flagged and a screenshot requested |
| `bas_lookahead_days` | `30` | days | The trust BAS joins the EComm GST target this many days before a BAS |
| `accounts` | `[]` | list of `{key, display, bank, match, loan?}` | Reserve accounts; `match` is the digits used to recognise screenshots (step 2). An empty list disables account validation on the API. |

Any key left out falls back to the default shown. `mattermost.enabled` (default `true`) set to
`false` stops all sending without removing the environment variables; `mattermost.allowed_users`
is reserved for step 2 (two-way).

### Testing a message without waiting for 7am

On the Obligations page, **Preview today's message** shows exactly what would be posted, without
sending. **Settings → Mattermost → Send test message** posts a one-line connection test.

## Install on your phone or computer

Coming in a later release (step 3 of the obligations work).
