# Family HQ Six-Month Cash-Flow Forecast Design

**Date:** 3 August 2026

**Status:** Approved design, pending implementation plan

**Primary user:** Whitewood family

**Deployment:** Existing Family HQ application on Coolify

## 1. Objective

Extend Family HQ so the family can see whether it will have enough cash to meet personal and business commitments across a rolling six-month window, presented in four-week planning cycles.

The product must answer:

1. What cash is available today?
2. What money is expected to enter and leave each account over the next 182 days?
3. What is the lowest projected balance, and when will it occur?
4. Are upcoming camps, birthdays and other one-off expenses adequately funded?
5. How much is safe to spend after committed and protected amounts?
6. If a shortfall is forecast, which transactions cause it?

Personal and business finances remain separate. The personal forecast is the primary view. Eden Commercial appears in a collapsed secondary view. Transfers between the two are linked but do not make business cash automatically available for personal spending.

## 2. Current-State Decision

Retain the existing Flask, SQLite and single-page Family HQ application. Rebuild the financial data and forecasting components within that application.

Do not replace Family HQ with Actual Budget, Firefly III, Maybe Finance or Ghostfolio. Those projects provide useful design patterns, but none supplies the required combination of:

- an Australian personal and business workflow;
- an explicit rolling six-month cash forecast presented in four-week cycles;
- personal-first presentation;
- linked business-to-personal transfers;
- Xero business data;
- camps, birthdays and other one-off family commitments.

Implement the required concepts independently rather than incorporating AGPL-licensed source code.

## 3. Scope

### 3.1 Included

- Personal and business bank-account records.
- Bulk import of at least three months of bank CSV files.
- Supported parsers for the family's known bank export formats.
- Persistent transaction ledger with import history and deduplication.
- User-confirmed account ownership: personal or Eden Commercial.
- Transaction categorisation and editable categorisation rules.
- Transfer identification and matching.
- Recurring income and expense detection.
- Manually entered one-off expenses and income.
- Camps, birthdays and other savings envelopes.
- Daily cash projection for 182 days.
- Four-week cycle summaries across the complete six-month window.
- Separate personal and business forecasts.
- Linked transfers between business and personal accounts.
- Xero read-only integration for Eden Commercial.
- Forecast confidence and source labels.
- Shortfall warnings and safe-to-spend calculation.
- Import, forecast and integration automated tests.
- Security remediation required for sensitive financial data.

### 3.2 Deferred

- Direct Australian personal-bank connectivity.
- Automatic payment initiation.
- Writing transactions, invoices or bills to Xero.
- Investment and net-worth forecasting.
- Tax advice or automated tax calculations.
- Multi-family or commercial SaaS tenancy.
- Native mobile applications.

Australian Open Banking integration will be reconsidered after the CSV-based forecast is proven useful and provider pricing is known.

## 4. Information Architecture

The Budget page contains three cards.

### 4.1 Personal Cash Flow

Expanded by default and visually dominant.

It displays:

- current available cash;
- protected cash;
- safe-to-spend amount;
- lowest projected balance and its date;
- each four-week cycle across the next six months;
- the final partial cycle covering any remaining days after the six complete four-week cycles;
- upcoming camps, birthdays and other one-off commitments;
- projected shortfalls;
- data freshness and forecast confidence.

Each four-week cycle is collapsible. Cycle 1 opens by default. Later cycles are collapsed to keep the page easy to scan. A rolling 182-day window contains six complete 28-day cycles and a final partial cycle of up to 14 days.

### 4.2 Eden Commercial

Collapsed by default.

It displays:

- current business cash;
- expected TCS and other receipts;
- bills and operating commitments;
- AI, software and cloud spending;
- tax and BAS reserves;
- planned transfers to personal accounts;
- lowest projected business balance and its date;
- data freshness and Xero connection status.

### 4.3 Combined Position

Collapsed by default and informational only.

It displays:

- total cash held;
- personal protected amounts;
- business tax and BAS reserves;
- scheduled business-to-personal transfers;
- combined commitments.

The combined position must not calculate personal safe-to-spend using untransferred business cash.

## 5. Financial Data Model

### 5.1 Account

Each account stores:

- stable internal identifier;
- display name;
- institution;
- masked account reference where available;
- ownership: `personal` or `business`;
- account type;
- active status;
- opening or latest confirmed balance;
- balance date;
- import source;
- optional Xero account identifier.

Ownership is explicitly confirmed by the user. It is never inferred permanently from a CSV filename.

### 5.2 Transaction

Each transaction stores:

- internal identifier;
- account identifier;
- transaction date;
- amount in AUD;
- original description;
- normalised merchant or counterparty;
- category;
- source;
- source transaction identifier when available;
- import identifier;
- transfer status;
- linked transfer transaction identifier;
- one-off status;
- user-confirmed status;
- created and updated timestamps.

An imported transaction remains traceable to its source file or Xero sync.

### 5.3 Import

Each upload or synchronisation stores:

- import identifier;
- filename or source;
- file hash where applicable;
- imported timestamp;
- account mapping;
- rows read;
- rows accepted;
- duplicates skipped;
- rows rejected;
- rejection reasons.

Importing the same file twice must not create duplicate transactions.

### 5.4 Scheduled Cash Event

All future cash movements use one structure:

- description;
- account;
- direction: inflow or outflow;
- expected amount;
- expected date;
- recurrence rule if applicable;
- category;
- source;
- confidence;
- optional linked transfer;
- active status.

Sources include:

- manual;
- recurring detection;
- Xero invoice;
- Xero bill;
- repeating Xero item;
- savings envelope;
- forecast allowance.

Confidence values are:

- `confirmed`: manually confirmed or sourced from a committed record;
- `expected`: recurring history or an expected invoice or bill;
- `estimated`: variable allowance inferred from history.

### 5.5 Savings Envelope

An envelope stores:

- name;
- target amount;
- required date;
- current protected amount;
- priority;
- linked scheduled expense where applicable;
- notes;
- active or completed status.

Camps and birthdays use this model.

## 6. Import and Reconciliation

### 6.1 CSV Import

The first release accepts at least three months of personal and business exports.

The import flow is:

1. Upload one or more files.
2. Detect the parser format.
3. Preview file, account, date range and row count.
4. Ask the user to confirm account mapping only when it is unknown.
5. Parse and validate every row.
6. Show accepted, duplicate and rejected counts.
7. Save the import and transactions atomically.
8. Recalculate balances, recurrence candidates and forecasts.

Rejected rows are visible and never silently discarded.

### 6.2 Deduplication

Deduplication uses, in priority order:

1. source transaction identifier plus source;
2. file hash plus row fingerprint;
3. account, date, amount, normalised description and balance where available.

Transactions from different accounts are never treated as duplicates merely because their date, amount and description match.

### 6.3 Transfers

A transfer is a pair of opposing transactions with:

- different accounts;
- equal amounts within an explicit tolerance;
- compatible dates within a configurable short window;
- matching transfer evidence.

The system suggests transfer pairs but does not permanently exclude ambiguous transactions until confirmed.

A confirmed business-to-personal transfer creates or links:

- a business outflow;
- a personal inflow;
- one shared transfer record.

Transfers are excluded from income and expense totals but still affect account balances.

## 7. Recurring Cash Events

Recurring detection considers:

- normalised merchant;
- account;
- amount tolerance;
- date intervals;
- at least three occurrences when practical;
- weekly, fortnightly, monthly, quarterly and annual candidates.

The system proposes:

- recurrence frequency;
- next expected date;
- expected amount;
- confidence;
- category.

The user can confirm, edit or reject a proposal. Confirmed schedules drive the forecast. Unconfirmed low-confidence guesses do not.

Variable categories such as groceries and fuel use weekly allowances rather than being represented as exact recurring merchant payments.

## 8. Forecast Engine

### 8.1 Calculation

The engine calculates each account daily for 182 days:

`closing balance = opening balance + inflows - outflows`

The next day's opening balance equals the previous day's closing balance.

Inputs include:

- latest confirmed balance;
- confirmed scheduled income and expenses;
- expected recurring income and expenses;
- manual one-off events;
- savings-envelope funding and spending;
- planned personal allowances;
- Xero invoices and bills for business;
- linked transfers.

The engine aggregates the daily projection into weekly summaries without discarding the daily detail.

### 8.2 Personal Safe-to-Spend

Personal safe-to-spend is:

`personal available cash - protected envelopes - committed outflows before the next reliable income - minimum safety buffer`

Untransferred business cash is excluded.

The safety buffer is a user-configured amount. It is displayed separately and never hidden inside an unexplained calculation.

### 8.3 Business Reserves

Tax and BAS reserves remain protected business cash. They reduce business discretionary availability but remain visible in the bank-balance forecast.

### 8.4 Shortfalls

A shortfall is raised when:

- an account is projected below zero;
- personal cash falls below the safety buffer;
- business discretionary cash would consume a protected reserve;
- a savings envelope cannot reach its target by the required date.

Every warning includes:

- date or week;
- projected balance;
- threshold breached;
- contributing cash events;
- amount required to remove the warning.

### 8.5 Scenarios

The first implementation supports a single confirmed plan. Scenario modelling is deferred until the base forecast is reliable.

## 9. Xero Integration

Xero is read-only initially and applies only to Eden Commercial.

Use Xero's official OAuth 2.0 Python SDK and normal authorisation-code flow. Do not begin with a paid Custom Connection.

Import where available:

- business bank accounts;
- bank transactions;
- bank transfers;
- invoices and expected receipts;
- bills and expected payments;
- repeating invoices;
- Bank Summary report information.

The integration stores:

- tenant identifier;
- encrypted OAuth token material;
- granted scopes;
- last successful sync;
- last attempted sync;
- sync cursor or modified-since value;
- health and error state.

Synchronisation is incremental and idempotent. A failed sync leaves the previous confirmed data intact.

The interface always shows when Xero last synced. Xero-derived events are labelled as such.

## 10. AI Use and Cost Control

AI is optional and advisory.

AI may:

- suggest merchant normalisation;
- suggest categories;
- summarise unusual spending;
- explain forecast changes in plain language.

AI must not:

- perform cash-flow arithmetic;
- determine balances;
- silently change confirmed transactions or schedules;
- transmit full financial histories without explicit user action.

Default financial processing is deterministic and local.

Before any AI request, the interface identifies what information will leave the application. Account numbers, personal identifiers and unnecessary transaction details are redacted. AI model and token costs are logged and visible.

## 11. Security and Privacy

Before connecting Xero or loading full statements:

- remove default production credentials;
- require environment-supplied username, password and session secret;
- rotate the committed Discord webhook and remove secret values from version control;
- add CSRF protection to state-changing requests;
- escape all imported text before browser rendering;
- validate uploads by type, size and content;
- restrict stored documents and financial files to authenticated access;
- encrypt Xero refresh tokens at rest;
- avoid logging statements, tokens or financial prompts;
- retain recoverable database backups;
- document restoration and token-revocation procedures.

Passwords are stored as hashes if authentication remains application-managed.

## 12. Error Handling

User-visible errors must state:

- what failed;
- what data was or was not saved;
- whether the existing forecast remains usable;
- the next action.

CSV row errors are collected and reported rather than ignored.

Xero failures do not erase existing Xero data. The application shows a stale-data warning and retains the last successful snapshot.

A forecast with missing balances or material unresolved imports is marked incomplete instead of displaying misleading precision.

## 13. Testing and Acceptance Criteria

### 13.1 Import

- The same file imported twice creates no duplicate transactions.
- Equal transactions on different accounts remain separate.
- Known ING, CBA-style and Great Southern Bank samples parse correctly.
- Invalid rows are reported with reasons.
- Account ownership persists independently of filenames.

### 13.2 Transfers

- Confirmed transfer pairs affect both account balances.
- Confirmed transfers do not inflate income or expenses.
- Unmatched PayID, BPAY or direct-credit transactions remain ordinary transactions unless confirmed otherwise.

### 13.3 Forecast

- Each forecast contains exactly 182 sequential daily positions.
- The presentation contains six complete 28-day cycles and one final partial cycle without duplicate or missing dates.
- One-off expenses appear on the correct date.
- Recurring weekly, fortnightly, monthly and annual examples appear correctly.
- Linked business-to-personal transfers occur on both sides on the same date.
- Untransferred business cash never increases personal safe-to-spend.
- The lowest projected balance and date match the daily series.
- A shortfall identifies the events contributing to it.

### 13.4 Xero

- Repeating a sync does not duplicate Xero records.
- Expired access tokens refresh safely.
- Failed syncs preserve the last successful data.
- The interface displays accurate last-sync and stale-data status.

### 13.5 Security

- Production startup fails safely when required secrets are absent.
- State-changing requests without CSRF protection are rejected.
- Imported descriptions render as text, not executable HTML.
- Xero tokens and statement contents are absent from application logs.

### 13.6 Product Acceptance

The design is successful when Mrs Whitewood can open the Personal Cash Flow card and, without interpreting accounting reports:

- see whether the family remains above its safety buffer for six months;
- see whether every planned camp and birthday is funded;
- identify the exact week of any shortfall;
- understand how much is safe to spend;
- expand Eden Commercial only when business context is needed.

## 14. Delivery Sequence

### Phase 1: Trustworthy financial foundation

- Security remediation.
- Persistent accounts, imports and transaction ledger.
- CSV preview, validation and audit results.
- Correct deduplication and transfer handling.
- Automated parser and ledger tests.

### Phase 2: Personal six-month forecast

- Three-month history import.
- Account and category confirmation.
- Recurring-event confirmation.
- Camps, birthdays and savings envelopes.
- 182-day forecast and four-week cycle interface.
- Safe-to-spend and shortfall warnings.

### Phase 3: Eden Commercial

- Separate business forecast.
- TCS receipts, business commitments and protected tax reserves.
- AI and software cost visibility.
- Linked personal transfers.
- Collapsed business and combined-position cards.

### Phase 4: Xero

- OAuth connection.
- Read-only incremental sync.
- Business accounts, transactions, invoices, bills and reports.
- Sync health and stale-data handling.

### Phase 5: Personal-bank automation assessment

- Measure the practical burden of weekly CSV imports.
- Obtain Australian CDR provider pricing and coverage.
- Add direct connectivity only if its ongoing value exceeds its cost and compliance burden.

## 15. Explicit Assumptions

- AUD is the only required currency for the initial release.
- Eden Commercial is the only Xero organisation in scope.
- Personal accounts remain CSV-based initially.
- The latest imported or synced balance is trusted only after its date is shown to the user.
- Manual confirmed events override detected or estimated events.
- The current Family HQ deployment remains single-family and authenticated.
- No payments or accounting records are created automatically.
