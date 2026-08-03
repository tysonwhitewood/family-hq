# Family HQ Cash-Flow Operations

## What the forecast shows

Family HQ projects personal and Eden Commercial cash for the next 182 days. It calculates every calendar day and groups the result into six complete four-week cycles plus a final partial cycle.

Personal cash is shown first. Eden Commercial and the combined position are collapsed by default. Business cash never increases the personal safe-to-spend figure unless a transfer is entered as a personal inflow and business outflow.

## Initial statement upload

Export at least the previous three months from every account used by the family or Eden Commercial.

1. Open **Finance** in Family HQ.
2. Select **Upload CSV**.
3. Upload each bank's unedited CSV export.
4. Use filenames that clearly identify the account. Include `Eden`, `Commercial`, `Business`, `Pty` or `Company` in Eden Commercial filenames.
5. Confirm the account balances and latest transaction dates shown after upload.
6. Open **Budget** and review the Personal, Eden Commercial and Combined cards.

Currently supported inputs include the existing ING, CBA-style and Great Southern Bank formats. An unsupported row is not suitable for forecasting until its parser is added.

## Camps, birthdays and other planned cash events

Use **Upcoming Expenses → Add** for every known event:

1. Enter a clear description such as `School camp — deposit`.
2. Enter the amount and due date.
3. Choose `personal` or `business`.
4. Choose `outflow` for a payment or `inflow` for money expected.
5. Leave recurrence blank for a one-off event, or enter `weekly`, `fortnightly`, `monthly`, `quarterly` or `annual`.

Enter separate events when a camp or birthday has deposits and final payments on different dates.

## Safety buffer

Open **Personal Cash Flow → Safety buffer** and enter the minimum personal cash the family wants protected.

The safe-to-spend figure is current personal cash less this buffer. A warning appears when the daily forecast first drops below the protected amount.

## Reading a four-week cycle

Each cycle shows:

- money expected in;
- money expected out;
- balance at the end of the cycle;
- the lowest balance reached during the cycle;
- confirmed events and their dates.

Cycle 1 opens automatically. Expand later cycles to inspect camps, birthdays, annual bills or other events further ahead.

The seventh cycle is intentionally shorter. A rolling six-calendar-month window is 182 days, while six full four-week cycles cover 168 days.

## Weekly update

Update the forecast once a week:

1. Export a current CSV from each account.
2. Upload the files through Finance.
3. Check the latest balances and dates.
4. Add or revise upcoming events.
5. Review the lowest personal balance and warnings.
6. Expand Eden Commercial if a business receipt or commitment changed.

Repeated snapshots are deduplicated per account. Identical transactions in different accounts remain separate.

## Backup and restore

The live SQLite database is `/app/data/family.db`. The container's daily job creates SQLite backups in `/app/backups` and retains fourteen days.

Before a manual restore:

1. Stop Family HQ in Coolify.
2. Preserve a copy of the current database.
3. Copy the selected backup to `/app/data/family.db`.
4. Start Family HQ.
5. Confirm Budget data, upcoming events and settings before relying on the forecast.

Never overwrite a running SQLite database with a normal file copy. Use the existing SQLite backup process for live backups.

## Current automation boundary

This release uses bank CSV uploads and local deterministic forecasting.

- Xero read-only synchronisation for Eden Commercial is a separate later stage.
- Australian Open Banking/CDR connectivity is a separate commercial assessment.
- Family HQ does not initiate payments.
- AI does not calculate balances or forecast arithmetic.
