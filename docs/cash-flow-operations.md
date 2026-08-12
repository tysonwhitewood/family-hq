# Family HQ Cash-Flow Operations

## What the forecast shows

Family HQ projects personal and Eden Commercial cash to the end of the sixth calendar month. It calculates every calendar day and groups the result into calendar months, shown as a table with one column per month (money in, money out, closing balance, lowest balance) and expandable per-month detail below it.

Personal cash is shown first. Eden Commercial and the combined position are collapsed by default. Business cash never increases the personal safe-to-spend figure unless a transfer is entered as a personal inflow and business outflow.

The four figures at the top of the Budget page come from the budget items alone: Eden income a month, Eden expenses a month, the transfer to personal a month, and personal expenses a month. Until a personal income item exists, the transfer box shows the personal expenses total as "Transfer needed".

## Budget items

Add or edit budget items with the **+ Add** button on either Budget vs Actuals card. Enter the amount the bill actually is and how often it happens — weekly, fortnightly, monthly, quarterly, every 6 months, or annually. Family HQ sets aside the monthly equivalent automatically: $3,500 of annual council rates appears as about $291 a month, matching the practice of moving a fixed amount into the future-expenses account each month rather than absorbing the bill when it lands.

Income is a budget item whose **Money** field is "Coming in". Enter Eden Commercial's expected monthly income as a business inflow, the monthly family transfer as a business outflow, and the same transfer as a personal inflow. The forecast then shows income in every month instead of six months of spending with no pay.

## Categorising transactions

Family HQ categorises transactions by merchant rules first, then built-in keywords. Anything it cannot place is **Uncategorised** and appears in the **Categorise Transactions** card on the Finance page, grouped by merchant with the most frequent first. Pick a category and save: the rule applies to every existing and future transaction of that merchant. Delete a bad rule under **Saved rules** in the same card.

## Initial statement upload

Export at least the previous three months from every account used by the family or Eden Commercial.

1. Open **Finance** in Family HQ.
2. Select **Upload CSV**.
3. Select the bank's unedited CSV export.
4. In **Account**, choose **Create a new account**.
5. Enter a clear, stable account name such as `Personal everyday` or `Eden Commercial operating`.
6. Choose **Personal** for a family account or **Business** for an Eden Commercial account.
7. Choose the account type:
   - **Cash** for transaction, savings and offset accounts whose positive balance is available cash;
   - **Credit card** for a credit-card statement; or
   - **Loan** for a mortgage or other loan account.
8. Select **Upload statement**.
9. Watch the percentage bar while the file uploads. At 100%, **Processing transactions…** means Family HQ is parsing and validating the statement.
10. Wait for the green confirmation showing the number of transactions loaded and the latest transaction date. This receipt remains visible under **Recent Transactions**.
11. Confirm the account's ownership, type, balance and latest transaction date under **Accounts**.

Currently supported inputs include the existing ING, CBA-style and Great Southern Bank formats. An unsupported row is not suitable for forecasting until its parser is added.

Credit cards and loans are recorded for history but are not available cash. Credit-card activity can inform recurring spending; loan activity does not. If an offset account is genuinely available cash, classify the offset as **Cash**, not **Loan**.

Give every statement a filename unique to its account. A statement filename is its identity. Uploading a file whose name matches a statement already held by a **different** account is refused, and Family HQ asks whether to move that statement, and its transactions, to the account now selected. Answer no unless the move is genuinely intended: accepting takes the statement away from the account that holds it today.

Date-stamped exports such as `CBA_29.05.26.csv` avoid the question because each name occurs once. A bank that exports the same default name every time, such as `Transactions.csv`, does not. Rename those to include the account and statement date before uploading, for example `Personal everyday 29.05.26.csv`. Re-uploading the same filename to the **same** account is always allowed and simply replaces that snapshot.

## Review existing statement classifications

Before relying on any forecast, review every existing statement shown under **Accounts**. In particular, check `CBA_29.05.26`, every ING statement and every Great Southern statement:

1. Select **Edit classification** on each account or unclassified statement.
2. For a registered account, correct its name, **Personal** or **Business** ownership, and **Cash**, **Credit card** or **Loan** type, then select **Save classification**.
3. For an unclassified statement, choose the registered account it belongs to and select **Save classification**. Create that account through an initial CSV upload first if it does not yet exist.
4. Confirm all snapshots from the same real bank account are linked to the same registered account.
5. Recheck the Personal, Eden Commercial and Combined cards in **Budget**.

Do not rely on the forecast while any legacy account classification remains unreviewed. A wrong ownership can move money between Personal and Eden Commercial, while a loan or credit account wrongly marked as cash can overstate available cash.

## Camps, birthdays and other planned cash events

Use **Upcoming Expenses → Add** for every known event:

1. Enter a clear description such as `School camp — deposit`.
2. Enter the amount and due date.
3. Choose `personal` or `business`.
4. Choose `outflow` for a payment or `inflow` for money expected.
5. Leave recurrence blank for a one-off event, or enter `weekly`, `fortnightly`, `monthly`, `quarterly`, `biannual` or `annual`.

Enter separate events when a camp or birthday has deposits and final payments on different dates.

## Safety buffer

Open **Personal Cash Flow → Safety buffer** and enter the minimum personal cash the family wants protected.

The safe-to-spend figure is current personal cash less this buffer. A warning appears when the daily forecast first drops below the protected amount.

## Reading a month

The month-columns table gives the whole horizon at a glance. Each expandable month below it shows:

- money expected in;
- money expected out;
- balance at the end of the month;
- the lowest balance reached during the month;
- confirmed events and their dates.

The current month opens automatically. Expand later months to inspect camps, birthdays, annual bills or other events further ahead.

Every balance is only as fresh as the newest uploaded statement: the "as of" date beside **Available now** and on each account card shows when the balance was last confirmed by real bank data.

## Weekly update

Update the forecast once a week:

1. Export a current CSV from each account. Check each week's filenames differ from the statements already held, renaming them to include the account and statement date if the bank reuses one default name.
2. In **Finance**, select **Upload CSV** and choose the new statement.
3. In **Account**, select the existing registered account for that bank account. Do not create a new account for each weekly snapshot.
4. Select **Upload statement**, watch the percentage and processing states, then wait for the green parsed-count and latest-date confirmation.
5. Check the account's latest balance and date.
6. Add or revise upcoming events.
7. Review the lowest personal balance and warnings.
8. Expand Eden Commercial if a business receipt or commitment changed.

Overlapping snapshots selected against the same registered account are deduplicated, so the account's latest balance is counted once. Identical transactions in different accounts remain separate.

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
