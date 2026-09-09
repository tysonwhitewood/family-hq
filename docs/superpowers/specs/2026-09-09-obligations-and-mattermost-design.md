# Obligations, Reserves and Mattermost Reminders

**Date:** 2026-09-09
**Status:** Approved by Tyson 2026-09-09
**Related:** `2026-08-03-family-six-month-cash-flow-forecast-design.md` (forecast this feeds),
`docs/cash-flow-operations.md` (operator guide, to be extended)

## Problem

The family gets into cash-flow trouble when project spending leaves too little for the bills that
were always coming: quarterly BAS and PAYG, council rates, water, insurance renewals, rego. The
existing Budget page can model these, but it is only useful if someone logs in and reads it, and
nobody does. Family HQ is currently opened for birthdays and nothing else.

The cashflow schedule Tyson prepared on 9 September 2026 lists the obligations, but two of its
figures do not survive contact with the bank statements:

- The BAS estimate of ~$2,845 a quarter is far below history. Eden Commercial paid the ATO about
  $35,800 in February 2026 and about $15,400 in June 2026. Eden received roughly $190,000 between
  January and July 2026, so GST alone runs nearer $1,600 a month than $1,500 a quarter.
- The flat "$1,500 to EComm GST on the 1st" therefore under-reserves in any month a large invoice
  is paid. The reserve must follow receipts.

The $3,188 PAYG figure equals 25% of the $12,754 FY26 taxable profit, which reads as an annual
figure. **Tyson has directed that it be treated as quarterly** ($3,188 each BAS), which is the
conservative reading. Zoie Cook at HMGC is to confirm; the setting is one number to change.

## Goal

Family HQ tells Tyson and Robyn, in their Mattermost **Family Finance** channel and without them
opening the app, exactly how much to move into which account and by when, so that every BAS,
tax, rates, water, insurance and rego bill is fully funded before it lands. The family reports
balances back by posting screenshots of their banking apps into the same channel.

## Non-goals

- No change to the existing cash-flow forecast arithmetic, budget items, categoriser or imports.
- No email reminders. Mattermost replaces the email channel in the schedule document.
- No Xero integration inside the app in this release. Receipts are reported, not fetched.
- No payments. Family HQ never moves money.
- The Discord webhook is left as it is (see Security for a recommended rotation).

## Decisions taken with Tyson on 2026-09-09

| Question | Decision |
|---|---|
| How do balances get in | Screenshots of the CBA and ING apps posted to Mattermost, read by Claude; typed amounts as fallback. No CSV uploads required. |
| Mattermost | Self-hosted at `https://chat.leaseintel.ai`, same Hetzner host as Family HQ. Tyson is system admin. Incoming webhook for channel Family Finance already exists; a bot account will be added for reading. |
| Audience | One shared channel that Tyson and Robyn both see. Either may reply. |
| Tax estimate source | Rates and known figures entered as settings, seeded from the schedule document and checked against Xero by Claude during build. Refreshed by Tyson or Claude as needed. |
| Household bill data | Pulled from the bank statements already on the server; remaining gaps asked in the channel. |
| Architecture | New Obligations module with its own tables, engine, Mattermost adapter and scheduler. |
| Reserve rule | Percentage of receipts, asked monthly: GST at 1/11 of receipts, plus an income-tax reserve of 15% of ex-GST receipts (to confirm with Zoie). |

## Architecture

Three new Python modules beside `app.py`, following the pattern of `cashflow.py` and
`finance_imports.py` (pure functions, no Flask imports, testable without a server):

- **`obligations.py`** — the engine. Generates occurrences from obligations, computes estimates,
  account targets and shortfalls, composes message text. No I/O except through arguments.
- **`mattermost.py`** — the adapter. Send a post, list posts since a watermark, download a file,
  post a reaction. Thin wrappers over the Mattermost REST API v4 using `requests`, which is
  already a dependency. Takes URL, token and channel as arguments.
- **`reminders.py`** — the scheduler and conversation handler. A daemon thread started from
  `app.py` exactly as `_start_daily_screener` is. Runs the daily 7am job, the Sunday 5pm digest,
  and a 60-second poll of the channel. Owns the message log and the watermark. Calls
  `obligations.py` for maths and `mattermost.py` for I/O, and the existing `llm_chat` (extended
  with image support) for screenshot and free-text reading.

`app.py` gains new tables in `init_db()`, a seed for the obligation list, a handful of
`/api/obligations/...` routes, and a Settings button that posts a test message. `dashboard.html`
gains an Obligations page and a mobile-nav entry.

The forecast integration is one-way and read-only: `_budget_target_events` (or the upcoming
events builder) is given the list of obligation occurrences as extra dated outflows so Budget and
Obligations show the same events. Nothing in the forecast is otherwise touched.

Gunicorn runs a single worker (`--workers 1` in the Dockerfile), so one scheduler thread per
process is safe today. The message log makes it safe anyway if that ever changes.

## Data model

All new tables live in `family.db`, created in `init_db()` with `CREATE TABLE IF NOT EXISTS`.

### `obligations`

| Column | Meaning |
|---|---|
| `id` | primary key |
| `name` | e.g. `Q BAS + PAYG instalment` |
| `ownership` | `personal` or `business` |
| `pay_from_account` | key of the account the bill is paid from (see Accounts) |
| `reserve_account` | key of the account the money should be parked in beforehand |
| `amount_rule` | `fixed`, `receipts_share`, `bas_formula`, `sinking_hold` |
| `amount` | the fixed amount, or the held amount for `sinking_hold`; null for computed rules |
| `frequency` | `once`, `monthly`, `quarterly`, `biannual`, `annual`, `rolling` |
| `anchor_date` | first due date the schedule is generated from |
| `due_rule` | `standard` (roll Sat/Sun to next Monday) or `none` |
| `extension_days` | days of agent extension shown for information (BAS only); budget to standard |
| `lead_days` | JSON list, default `[30, 7]`; BAS uses `[30, 7, 1]` |
| `remind` | 1 to send reminders, 0 to track silently (monthly direct debits) |
| `status` | `active`, `pending_confirmation`, `retired` |
| `source` | where the figure came from: `schedule-doc`, `statement:<file>:<date>`, `screenshot`, `typed` |
| `notes` | free text |
| `created_at`, `updated_at` | |

### `obligation_occurrences`

One row per real due date. Generated forward for twelve months whenever an obligation is
created or edited, and regenerated idempotently by the daily job.

| Column | Meaning |
|---|---|
| `obligation_id` | |
| `due_date` | after weekend roll |
| `estimate` | amount at generation time; recomputed daily for computed rules until paid |
| `estimate_detail` | JSON of the components (GST collected, credits, PAYG, trust) |
| `actual` | amount actually paid, if reported |
| `state` | `upcoming` → `funds_confirmed` → `paid`; or `skipped` |
| `state_changed_at`, `state_changed_by` | who replied (Mattermost user) |

### `account_balances`

Append-only snapshots. The newest per account is "last known".

| Column | Meaning |
|---|---|
| `account_key` | matches the configured account list |
| `balance` | current balance |
| `available` | available balance when the screenshot shows it (CBA shows both) |
| `as_of` | date read from the screenshot or the day posted |
| `source` | `screenshot`, `typed`, `csv` |
| `mattermost_post_id` | the post it came from |
| `raw` | what Claude read, for audit |

### `receipts_log`

What Eden Commercial received in a month, feeding the GST maths.

| Column | Meaning |
|---|---|
| `year_month` | e.g. `2026-09` |
| `amount_incl_gst` | total for the month |
| `detail` | JSON list of items: retainer assumed, plus each reported receipt with source |
| `updated_at` | |

### `reminder_log`

Every message the bot sent or handled.

| Column | Meaning |
|---|---|
| `kind` | `monthly_setaside`, `lead_warning`, `weekly_position`, `propvesting_check`, `setup_question`, `ack`, `error` |
| `dedupe_key` | e.g. `lead_warning:occ:42:7` — the daily job never sends a key twice |
| `obligation_id`, `occurrence_id` | nullable |
| `mattermost_post_id` | |
| `body` | text sent |
| `sent_at` | |

### `reminder_state`

Key/value like `budget_settings`: the poll watermark (`last_post_create_at`), the PropVesting
resolved flag, the set-up question queue position.

## Accounts

The five accounts from the schedule document, plus the GSB accounts, are configured under
`obligations.accounts` in `data/config.json`, keyed for use in the tables above:

| Key | Display | Bank | Identifier used to match screenshots |
|---|---|---|---|
| `eden_operating` | Eden Commercial | CBA | 1027 8937 |
| `ecomm_gst` | EComm GST | CBA | 1027 8945 |
| `cba_utilities` | Utilities / Vehicles | CBA | 2645 1922 |
| `ing_everyday` | ING Orange Everyday | ING | 65683967 |
| `ing_emergency` | ING Emergency | ING | 46789692 |
| `ing_home` | ING Home | ING | 48305167 |
| `ing_savings` | ING Savings | ING | 804020739 |
| `gsb_everyday` | GSB Everyday | GSB (BSB 814 282) | 51978620 |
| `gsb_mortgage` | GSB Basic Variable Inv P&I (loan) | GSB | 51991707; recorded, never a reserve target |

Matching uses the account name first, then the trailing digits. An unmatched account is reported
and ignored, never guessed.

## Reserve maths

All rates are settings under `obligations` in `data/config.json`:

| Setting | Default | Meaning |
|---|---|---|
| `gst_fraction` | `0.0909` (1/11) | share of GST-inclusive receipts reserved for GST |
| `income_tax_reserve_rate` | `0.15` | share of ex-GST receipts reserved for company income tax |
| `assumed_monthly_retainer` | `10083.34` | Cheesecake Shop retainer, GST inclusive |
| `gst_credit_allowance_monthly` | `636` | expected GST credits on ~$7,000 of GST-able expenses |
| `payg_instalment_quarterly` | `3188` | Tyson's direction: the $3,188 notice is per quarter |
| `sl_trading_trust_bas` | `545` | |
| `emergency_floor` | `3000` | ING Emergency target |
| `mortgage_repayment` | `4810.38` | must sit in GSB Everyday before the 5th |
| `post_hour_local` | `7` | scheduled posts, Brisbane time |
| `quiet_hours` | `[21, 7]` | no posts between 9pm and 7am |
| `timezone` | `Australia/Brisbane` | |

Receipts for a month = `assumed_monthly_retainer` + every reported receipt (typed or read from
a screenshot). A reported total replaces the assumption if the user says "eden total 24500".

**Monthly set-aside to EComm GST** = receipts × `gst_fraction` + (receipts − GST) ×
`income_tax_reserve_rate`. GST credits are deliberately not deducted here; the difference is the
cushion. Retainer-only month: $917 + $1,375 = **$2,292**.

**BAS estimate for a quarter** = Σ(monthly receipts) × `gst_fraction` − 3 ×
`gst_credit_allowance_monthly` + `payg_instalment_quarterly` + `sl_trading_trust_bas`. Months
without a receipts row use the assumed retainer. With PAYG at $3,188 the Q1 estimate is about $6,900. The estimate is recomputed daily until paid and
shown with its components in every warning.

**Account targets today**

- `ecomm_gst` = PropVesting hold (until paid) + GST accrued this quarter to date + income-tax pot
  to date (cumulative since the start date, less PAYG instalments paid from it) + any BAS due
  within 30 days not yet paid.
- `ing_home` = Σ over household obligations with `sinking_hold` or `fixed` non-monthly rules of
  `amount × elapsed_months_in_cycle / cycle_months`, where elapsed counts from the last paid or
  anchor date. Rates $1,614 biannual accrues $269 a month; water $713 quarterly $238; SMS
  Insurance $2,955 annual $246; RACQ roadside $310 annual $26.
- `ing_emergency` = `emergency_floor`.
- `gsb_everyday` = `mortgage_repayment` until the repayment leaves on the 5th. It is funded by the
  monthly director's drawings, Eden Commercial → ING Everyday → GSB Everyday, so the monthly
  set-aside message states that the drawings must include the mortgage amount rather than asking
  for a separate reserve transfer.

**Shortfall** = target − last known balance. Every message states the balance's age.

No AI in any of the above. Claude reads images and free text only; the arithmetic is code with
unit tests.

## Seed obligations

Seeded once on first start after deploy, guarded by a `reminder_state` flag so it never re-runs.
Every row records its source.

| Name | Owner | Rule | Anchor / next due | Reserve in | Remind | Source |
|---|---|---|---|---|---|---|
| Monthly tax reserve transfer | business | receipts_share | 2026-10-01, monthly | ecomm_gst | yes | schedule-doc |
| Q BAS + PAYG instalment | business | bas_formula | 2026-10-28 quarterly (28 Feb 2027 → Mon 1 Mar; 28 Apr; 28 Jul), extension 28 days shown except Q2 | ecomm_gst | yes, [30,7,1] | schedule-doc |
| PropVesting BAS + final return | business | fixed 7755.88 | once, no date until ASIC re-registered | ecomm_gst | weekly Monday check | schedule-doc |
| ProRisk PI/PL renewal | business | fixed 2505 | 2026-11-20 annual | eden_operating | yes | insurances table |
| ASIC annual fee | business | fixed 1798 | 2027-04-14 annual | eden_operating | yes, pending_confirmation | upcoming_expenses |
| RACQ car insurance | business | fixed 98.41 | 3rd monthly | eden_operating | no | Eden statement 2026-08-03 |
| Council rates (Scenic Rim) | personal | fixed 1614.19 | 2027-02-27 biannual | ing_home | yes, pending_confirmation | ING statement 2026-04-26 ($1,190); upcoming_expenses 2026-08-27 |
| Water (Urban Utilities) | personal | fixed 713.45 | 2026-11-28 quarterly | ing_home | yes | ING statement 2026-05-28 |
| SMS Insurance | personal | fixed 2955 | 2027-03-05 annual | ing_home | yes, pending_confirmation | Eden statement 2026-03-05 |
| RACQ roadside assistance | personal | fixed 310 | 2027-07-17 annual | ing_home | yes | Eden statement 2026-07-17 |
| Car registration (TMR) | personal | fixed, unknown | unknown | ing_home | pending_confirmation | four TMR payments Apr–Jun 2026: $964.96, $438.74, $502.45, $334.63 |
| Home & contents (RACQ) | personal | fixed 165.90 | 22nd monthly | ing_everyday | no | ING statement |
| Electricity (GloBird) | personal | fixed 230 (average of Apr–Jul 2026 bills) | monthly | ing_everyday | no | ING statement |
| Mortgage repayment | personal | sinking_hold 4810.38 | 5th monthly, next 2026-10-05 | gsb_everyday | yes, [7] | GSB statement, Tyson 2026-09-09 |
| Food / tight-month buffer | personal | sinking_hold 3000 | rolling | ing_emergency | no | schedule-doc |

Health insurance: nothing found in any statement; not seeded. The set-up questions ask once.

## Mattermost conversation

### Channel and identity

One channel, Family Finance. The bot posts as **Family HQ**. Either Tyson or Robyn may reply;
replies from anyone else in the channel are ignored (allowed usernames configured under
`mattermost.allowed_users`).

### Scheduled posts (all at `post_hour_local`, never in quiet hours, at most one a day)

1. **Monthly set-aside**, 1st of the month, covering the month just ended. States assumed
   receipts, asks for extras, gives the EComm GST transfer with its two components, and the ING
   Home transfer with its components. Ends with "Reply *done* when moved." A receipts correction
   ("eden 24500") recomputes and reposts once.
2. **Lead warning**, at each of the obligation's `lead_days` before due. States due date (and
   agent extension where one exists, budgeted to the standard date), estimate with components,
   what the reserve account should hold, last known balance and its age, and the shortfall. Asks
   for a screenshot when the balance is older than 14 days. Reply *paid* moves the occurrence to
   `paid` and stops further warnings for it.
3. **Weekly position**, Sunday 5pm. One message: each reserve account, target, last known
   balance and age, one-line verdict.
4. **PropVesting check**, Monday, until resolved. Reply *yes* flips the obligation to a dated
   one-off due immediately.
5. **Set-up questions**, one a day for the first week, for each `pending_confirmation` row. A
   typed answer or a photo of the notice resolves it.

Several items falling on the same day are bundled into one post.

### Understood replies

| Reply | Effect |
|---|---|
| a screenshot of a banking app | balances read and stored; acknowledgement lists what was read |
| a photo of a bill or notice | payee, amount, due date read; attached to the matching obligation or a new `pending_confirmation` one; acknowledgement shows what was read |
| `done` | the most recent set-aside request is marked done |
| `paid` | the nearest due occurrence is marked paid |
| `skip` | the nearest due occurrence is marked skipped |
| `yes` (to PropVesting) | resolves the ASIC check |
| `status` | posts the weekly position now |
| `help` | lists these replies |
| `<account> <amount>` e.g. `gst 9262` | typed balance snapshot |
| `eden <amount>` | adds a receipt to the current month |
| `eden total <amount>` | replaces the month's receipts total |
| `<bill> due <date> <amount>` | sets or confirms an obligation |

Short keyword replies are matched in code. Anything else is passed to Claude with a strict JSON
schema (intent, account, amount, date, confidence). Low confidence or an unparseable reply gets:
"I didn't follow that. I can take done, paid, status, an amount with an account name, or a
screenshot." Nothing is silently ignored. The bot adds a ✅ reaction to every reply it acted on.

### Screenshot reading

`llm_chat` gains an optional `images` argument (base64 with media type) for the Anthropic path;
OpenRouter fallback passes the same content blocks. The prompt gives the configured account
list and asks for JSON only: `[{account_key|null, name_seen, balance, available|null, as_of|null}]`.
The adapter downloads the file with the bot token, caps at 10 MB, accepts PNG/JPEG/HEIC-converted
images only. Every read is stored in `account_balances.raw` for audit.

### Polling

Every 60 seconds `reminders.py` calls `GET /api/v4/channels/{id}/posts?since={watermark}`,
processes posts not authored by the bot, and advances the watermark in `reminder_state`. Polling
means Mattermost never needs to reach into Family HQ and no public endpoint is added.

## Obligations page

New page in `dashboard.html`, added to both navs, and the landing page after login (birthdays
remain one tap away in the nav). Three cards:

- **What's coming** — next 90 days of occurrences: due date, name, estimate, state, reserve
  account, shortfall. Buttons: mark paid, skip, edit.
- **Reserve accounts** — each account, target, last known balance, age, shortfall.
- **Conversation** — the last 30 entries from `reminder_log` with reply state.

Plus an add/edit obligation form matching the existing modal style, and in Settings a
**Mattermost** block showing connection status and a **Send test message** button.

Routes: `GET/POST /api/obligations`, `PUT/DELETE /api/obligations/<id>`,
`POST /api/obligations/occurrences/<id>/state`, `GET /api/obligations/position`,
`GET /api/obligations/log`, `POST /api/obligations/receipts`, `POST /api/obligations/balances`,
`POST /api/mattermost/test`. All behind `login_required` like the budget routes.

## Phone and computer install

- Add `sw.js`, served from `/sw.js` (added to the public paths), registered from `dashboard.html`.
  Cache-first for the shell and icons, network-first for `/api/*`, and an offline page.
- Manifest already exists; set `start_url` to `/` (unchanged) and verify `display: standalone`.
- Flask-Login already uses `remember=True`; set `REMEMBER_COOKIE_DURATION` to 180 days explicitly
  so home-screen launches do not prompt for a password.
- README gains an **Install on your phone or computer** section: iPhone (Safari → Share → Add to
  Home Screen), Android (Chrome → ⋮ → Install app), Mac (Chrome → Install, or Safari → File →
  Add to Dock).

Mattermost's own app delivers the push notifications; nothing extra is needed.

## Configuration

Secrets are environment variables set in Coolify, never in `data/config.json` (which is tracked
in git):

| Variable | Meaning |
|---|---|
| `MATTERMOST_URL` | `https://chat.leaseintel.ai` |
| `MATTERMOST_BOT_TOKEN` | personal access token of the Family HQ bot |
| `MATTERMOST_CHANNEL_ID` | Family Finance channel id |
| `MATTERMOST_WEBHOOK_URL` | the existing incoming webhook; optional fallback for sending if the bot token is absent |
| `ANTHROPIC_API_KEY` | already required; needed for screenshot reading |

Non-secret settings live under two new keys in `data/config.json`, `obligations` (rates and
accounts, defaults above) and `mattermost` (`allowed_users`, `enabled`). Absent keys fall back to
the documented defaults; an absent bot token disables polling and logs one warning at start-up.
The README documents every key, its valid values and the behaviour when absent.

## Security

- The bot token has Member role only, in one channel. It cannot read DMs or other channels.
- Only posts from `allowed_users` are acted on; everything else is logged and ignored.
- Images are downloaded only from the configured Mattermost host, size-capped, and never stored
  on disk; only the extracted numbers are kept.
- The Discord webhook URL committed in `data/config.json` should be regenerated in Discord and
  moved to an environment variable. Recommended, out of scope for this build.

## Error handling

- Mattermost or Claude unavailable: the daily job logs and retries next cycle; the poller backs
  off to 5 minutes after three consecutive failures and recovers automatically. The web app is
  unaffected.
- Duplicate protection: every scheduled message has a `dedupe_key`; a restart or redeploy cannot
  double-post.
- Unreadable screenshot: the bot says so and asks for typed balances.
- Unknown account in a screenshot: reported by name, not stored.
- Weekend due dates roll to Monday under `due_rule = standard`.
- All times Brisbane; DST is not observed in Queensland but `zoneinfo` is used regardless.

## Testing

Tests follow the existing `unittest` style in `tests/`:

- `test_obligations.py` — occurrence generation (monthly, quarterly, biannual, annual, weekend
  roll, the 28 Feb 2027 → 1 Mar case), BAS estimate with and without reported receipts,
  monthly set-aside, sinking-fund accruals, account targets and shortfalls, message composition.
- `test_mattermost.py` — adapter against a fake HTTP server: post, list since watermark, file
  download, error propagation.
- `test_reminders.py` — scheduler dedupe across restarts, quiet hours, bundling, keyword reply
  handling, allowed-user filtering, screenshot result storage with a mocked `llm_chat`.
- `test_obligations_api.py` — routes with the Flask test client, in the style of
  `test_budget_api.py`.

Manual acceptance before go-live: Send test message from Settings; run the monthly message by
hand (`/api/obligations/run?job=monthly&dry_run=1` returns the text without posting, then without
`dry_run`); post a real screenshot and check the acknowledgement and the Reserve accounts card.

## Delivery

Each step is deployable and useful on its own.

1. **Engine and one-way messages.** Tables, seed, `obligations.py`, Obligations page (read and
   edit), scheduler with monthly, lead-warning and weekly posts via bot token or webhook. Tests.
2. **Two-way.** Polling, keyword replies, Claude free-text parsing, screenshot reading, balance
   snapshots, receipts log, PropVesting check, set-up questions, reactions.
3. **Install.** Service worker, landing page, mobile nav, remember-cookie duration, README.
4. **Later, separate spec.** Xero read-only inside the app so receipts are fetched, not asked.

## Items to confirm (asked by the bot in week one unless answered sooner)

- Car registration: which vehicles, cycle and amounts behind the four TMR payments.
- SMS Insurance $2,955 (5 Mar): what it covers and whether it is personal or business.
- Council rates: next notice date and amount (half-yearly; April and August payments seen).
- ASIC fee $1,798 in April: what it is for.
- With Zoie: the 15% income-tax reserve rate, and whether the $3,188 PAYG notice is per quarter
  (Tyson's reading, used here) or per year.

## Opening balances

Seeded as `account_balances` rows dated 2026-09-09, source `screenshot`, from the screenshots
Tyson posted during design (after his $7,756 PropVesting ring-fence, $3,000 food buffer and
$1,200 tax slice were moved):

| Account | Balance | Available |
|---|---|---|
| eden_operating | 7,324.64 | 7,295.64 |
| ecomm_gst | 9,048.03 | 92.03 (7,756 + 1,200 still clearing) |
| cba_utilities | 81.74 | 81.74 |
| ing_everyday | 7,779.02 | 7,102.16 |
| ing_emergency | 3,001.03 | 3,001.03 |
| ing_home | 0.41 | 0.41 |
| ing_savings | 3.50 | 3.50 |
| gsb_everyday | 5,246.45 | 5,246.45 |
| gsb_mortgage | -756,265.54 | 0 |

## Assumptions

- August 2026 receipts equal the retainer only; statements on the server end 1 August 2026.
- The Cheesecake Shop retainer continues at $10,083.34 a month.
- Eden's GST-able expenses run about $7,000 a month, giving the $636 credit allowance.
- The Mattermost server allows bot account creation (System Console → Integrations → Bot
  Accounts) and personal access tokens.
- `ANTHROPIC_API_KEY` is set in Coolify; this could not be verified from the build machine.

## Step 2 amendments (agreed 10 September 2026)

- **Free-form questions.** Any channel message from an allowed user that is not a command or an
  image is answered by the LLM using a money context: reserve targets, next 30 days of
  occurrences, receipts assumptions, monthly budget totals. Answers are short, Australian
  English, and never invent figures that are not in the context.
- **Allowed users are Mattermost usernames**: `tawhai` (Tyson) and `mum` (Robyn), configured in
  `mattermost.allowed_users`. Posts from anyone else, and the bot's own posts, are ignored.
- **Account aliases** come from config: each account in `obligations.accounts` may list
  `aliases` (e.g. `["gst", "ecomm"]`) used to recognise typed balances such as `gst 9262`.
- **Direct debits.** Obligations gain `auto_pay` (0/1). An auto-paid obligation's warning says
  the amount will be debited on the due date from the paying account and to make sure it holds
  the money; there is no "reply paid". Its occurrence is marked paid automatically the day after
  the due date. Council rates and water are auto-paid from 10 September 2026 (set up by Robyn).
- **Overdue occurrences** (due in the last 30 days, still open) appear in the position and the
  Obligations page with a negative days-out and an "overdue" flag. Lead warnings do not repeat.
- **Vision without an Anthropic key.** `llm_chat` accepts images. With `ANTHROPIC_API_KEY` it
  uses Claude; otherwise OpenRouter with a free vision-capable model. Reading quality on
  OpenRouter is best-effort; the bot always shows what it read and asks for correction.
- **Polling watermark** starts at the time of the first poll after deploy, so old channel
  history is never replayed.
- **Manual controls**: `POST /api/mattermost/poll` runs one poll now; `POST /api/mattermost/simulate`
  with `{text}` returns what the bot would reply without posting.
