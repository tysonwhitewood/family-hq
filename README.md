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

### Replying in Mattermost (two-way)

With a bot token and channel id set, Family HQ reads the Family Finance channel every minute and
acts on posts from the usernames in `mattermost.allowed_users`. It never acts on its own posts or
on anyone else's. Every reply is logged on the Obligations page under Conversation.

| You post | It does |
|---|---|
| `status` or `position` | Posts the reserve position, next 30 days and birthdays |
| `done` | Marks the last monthly set-aside as moved |
| `paid` / `skip` | Marks the nearest open bill paid or skipped |
| `<bill> paid` e.g. `rates paid` | Marks that bill's open occurrence paid |
| `yes` | Confirms PropVesting is registered (switches the hold to pay-now) |
| `gst 9262`, `ing home 2142` | Records a balance (aliases from `obligations.accounts[].aliases`) |
| `eden 5280` | Adds a receipt to this month; `eden total 24500` sets the month |
| `rates due 27 Feb 2027 1614`, `rego 965` | Sets or creates a bill (new ones are flagged "needs confirming") |
| a screenshot of a bank app | Reads balances into the reserve accounts and confirms what it read |
| a photo of a bill or notice | Reads payee, amount and due date into a bill |
| anything else | Answered as a question from the current position and upcoming bills |

Screenshots are read by Claude when `ANTHROPIC_API_KEY` is set, otherwise by a free
vision-capable model on OpenRouter (`OPENROUTER_API_KEY`), which is less reliable. Set the
Anthropic key for dependable screenshot reading. The bot always says what it read so you can
correct it with a typed figure.

Direct debits: tick **Paid by direct debit** on an obligation and its warnings say when the debit
will be taken and what the account must hold; the occurrence is marked paid automatically the day
after. Overdue open items show as "overdue" in the position and on the Obligations page.

Manual controls: `POST /api/mattermost/poll` runs one read of the channel now;
`POST /api/mattermost/simulate` with `{"text": "..."}` shows how a reply would be understood (and,
for free text, the answer) without writing anything or posting.

Other settings under `mattermost`: `allowed_users` (list of Mattermost usernames, default empty,
meaning nobody's posts are acted on), `enabled` (default `true`).

### AI models (`data/config.json` → `ai`)

| Key | Default | Meaning / when absent |
|---|---|---|
| `openrouter_text_models` | three free Gemma/Nemotron ids | Models tried in order for chat, briefings and free-form channel answers when only `OPENROUTER_API_KEY` is set. Absent or empty: the defaults in `app.py`. |
| `openrouter_vision_models` | two free Gemma ids | Models tried in order for screenshots and bill photos on OpenRouter. Absent or empty: the defaults. |

OpenRouter retires free models without notice. If the bot replies "HTTP Error 404", refresh these
lists from https://openrouter.ai/api/v1/models (ids ending in `:free`; vision models list
`image` under input modalities). With `ANTHROPIC_API_KEY` set these lists are not used.

### Testing a message without waiting for 7am

On the Obligations page, **Preview today's message** shows exactly what would be posted, without
sending. **Settings → Mattermost → Send test message** posts a one-line connection test.

## Install on your phone or computer

Family HQ is a Progressive Web App: it installs from the browser, opens full-screen with its own
icon, and the Obligations page is the landing page. Reminders run on the server, so the app does
not need to be open for Mattermost messages to arrive.

- **iPhone or iPad:** open https://family.edencommercial.au in Safari, log in, tap the Share
  button, then **Add to Home Screen**, then **Add**.
- **Android:** open the address in Chrome, log in, tap the three-dot menu, then **Install app**
  (or **Add to Home screen**).
- **Mac:** open it in Chrome and click the install icon at the right of the address bar, or in
  Safari use **File → Add to Dock**.
- **Windows:** open it in Edge or Chrome and click the install icon in the address bar.

The login is remembered for six months on that device. When there is no connection the app shows
an offline screen instead of a browser error; the service worker (`/sw.js`) caches only the page
shell and icons, never bank data or API responses. To force a fresh copy after an update, close
and reopen the app twice.
