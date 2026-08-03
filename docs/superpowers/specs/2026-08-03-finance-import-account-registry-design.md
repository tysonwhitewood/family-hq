# Finance Import Account Registry and Upload Feedback

**Date:** 3 August 2026  
**Status:** Approved for implementation  
**Scope:** Family HQ Finance CSV imports and cash-flow account classification

## Purpose

Make bank-statement uploads trustworthy for a non-technical user. Every upload must visibly progress, confirm that transactions were parsed, and attach the statement to a stable financial account rather than treating its filename as a new account.

This corrects the current live defects:

- repeated statement snapshots can double-count the same account balance;
- filenames determine whether money is personal or business;
- mortgage and loan balances are included as available cash;
- the upload UI finishes too quickly to show whether parsing succeeded.

## Account register

Family HQ will store a permanent register of financial accounts in SQLite. Each account has:

- a stable internal ID;
- a user-facing name, such as `ING Everyday` or `CBA Eden`;
- ownership: `personal` or `business`;
- account type: `cash`, `credit` or `loan`;
- an active flag;
- created and updated timestamps.

Each uploaded file is recorded separately and linked to one registered account. The import record stores:

- original and stored filenames;
- the linked account ID;
- upload time;
- parsed transaction count;
- earliest and latest transaction dates;
- import status.

No full bank account numbers are required or stored.

## Upload workflow

Selecting **Upload CSV** opens a compact upload panel before the file is sent.

The panel shows:

1. the selected filename;
2. an **Account** selector containing existing registered accounts plus **Create new account**;
3. **Personal** or **Eden Commercial** ownership when creating an account;
4. **Cash account**, **Credit card** or **Loan / mortgage** type when creating an account;
5. an **Upload** button;
6. an upload and processing status area.

For subsequent statements, the user selects the existing account. This is the authoritative link between differently named CSV snapshots of the same bank account.

The upload status moves through:

- `Uploading filename.csv — N%`;
- `Processing transactions…`;
- `Upload successful — N transactions loaded, latest D Month YYYY`.

A saved file is not reported as successful until at least one transaction has parsed. An unreadable or unsupported CSV returns a clear error and is not added to the account history.

The browser will use an upload mechanism that exposes byte-level progress. The status remains visible after completion instead of disappearing during the Finance refresh.

## Import and deduplication rules

The CSV parser will parse one file through a reusable single-file function. The bulk Finance loader and the upload endpoint will use the same parser.

Every parsed transaction receives the registered account's stable ID and display name.

Deduplication is scoped to the stable account ID and uses:

- transaction date;
- amount;
- normalised description;
- reported balance.

This removes repeated rows across overlapping statement snapshots for the same account while preserving identical transactions that occur in different accounts.

For an account's current balance, only the balance from its latest dated transaction is used. Older snapshots contribute transaction history but never add another opening balance.

Replacing or re-uploading a file with the same stored filename updates its import record and account link. It does not create a second balance.

## Forecast rules

Account type determines how imported data affects the forecast:

| Account type | Transactions used | Balance counted as cash | Recurrence inference |
|---|---:|---:|---:|
| Cash | Yes | Yes | Yes |
| Credit card | Yes | No | Yes |
| Loan / mortgage | No | No | No |

Loan repayments remain visible through the corresponding withdrawal from a cash account. Reading the loan-side transaction as well would duplicate the same cash movement.

Ownership comes from the registered account, not from filename keywords. Business cash never increases Personal safe-to-spend.

Legacy files without a registered account continue to load through conservative fallback rules:

- names containing `loan` or `mortgage` are treated as loans and excluded from cash;
- names containing `credit card` are treated as credit;
- existing business filename keywords remain the ownership fallback;
- other accounts remain personal cash until reviewed.

This fallback immediately prevents the live mortgage balances from appearing as family cash without deleting uploaded statements.

## Existing-account review

The Finance Accounts section will expose **Edit classification** for each account. The user can change:

- display name;
- personal or Eden Commercial ownership;
- cash, credit or loan type;
- the registered account to which a legacy filename belongs.

Linking a legacy filename to an existing account consolidates its transaction history and balance with that account. It does not delete the source CSV.

This provides a safe correction path for:

- `CBA_29.05.26`, which currently needs review for Eden Commercial ownership;
- older and newer ING snapshots that may represent the same account;
- Great Southern Bank mortgage/loan files;
- Great Southern Bank transfer-account snapshots.

## API behaviour

The upload endpoint accepts:

- the CSV file;
- an existing account ID, or new-account name, ownership and type.

It validates the metadata before saving. Its success response includes:

- account ID and name;
- parsed row count;
- earliest and latest dates;
- stored filename;
- a human-readable confirmation.

Invalid metadata returns HTTP 400. A valid CSV file with no supported transaction rows returns HTTP 422. Server errors return HTTP 500 without exposing internal paths or credentials.

Account-list and account-update endpoints support the upload selector and existing-account review controls.

## Data safety and migration

The schema migration is additive. Existing CSV files and transactions are not deleted.

Account and import metadata live in the existing SQLite database and are covered by the established daily backup process.

Account reclassification changes reporting and forecasting only. It does not alter the source CSV.

No upload, classification or correction initiates a payment or writes to a bank or Xero.

## Testing

Automated regression tests will prove:

- two snapshots linked to one stable account produce one opening balance;
- equal transactions in different accounts remain separate;
- mortgage and loan balances never enter available cash;
- credit-card transactions remain in spending while their balances are excluded;
- ownership comes from the account register;
- legacy loan and mortgage filenames are excluded conservatively;
- a supported upload returns its parsed count and latest date;
- a zero-row upload returns an error rather than success;
- invalid ownership and account types are rejected;
- the dashboard contains progress, processing, success and error states.

Browser verification will upload a non-sensitive fixture through the local application and confirm visible progress and success. After deployment, read-only checks will confirm the upload controls, registered-account fields and separated forecast are live without adding test transactions to the production database.

## Out of scope

- Direct bank feeds or Australian Open Banking/CDR;
- Xero synchronisation;
- PDF, XLSX, OFX or QIF statement imports;
- automatic matching based on full bank account numbers;
- deleting or editing transactions inside source statements;
- initiating payments.
