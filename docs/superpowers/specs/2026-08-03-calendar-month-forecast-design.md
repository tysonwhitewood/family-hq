# Calendar Month Cash Flow Presentation

**Date:** 2026-08-03
**Status:** Approved
**Supersedes presentation decisions in:** `2026-08-03-family-six-month-cash-flow-forecast-design.md`

## Problem

The forecast covers a rolling 182 days grouped into six complete 28-day cycles plus a final
partial cycle, labelled `Cycle 1` to `Cycle 7`. The operator reads this as clutter and cannot
map it to the months in which rates, insurance and mortgage payments actually fall.

Household income is monthly, so four-week cycles carry no alignment benefit. They were chosen
for consistent block length, which matters when income is fortnightly and does not apply here.

## Goal

Present the same six-month horizon as six named calendar months, from today to the end of the
sixth month.

## Non-goals

- No change to the daily forecast calculation.
- No change to opening balances, recurring inference, safety buffer, or the personal, business
  and combined separation.
- No change to warnings or lowest-balance tracking.

## Design

### Horizon becomes calendar-anchored

The horizon runs from today to the last day of the fifth month after the current month. This
always produces exactly six blocks ending on a month boundary. The day count varies between
roughly 152 and 184 days depending on the start date; the block count does not.

The current fixed `FORECAST_DAYS = 182` is replaced by `FORECAST_MONTHS = 6`. A fixed day count
only lands on a month boundary by coincidence: from 2026-08-03 it happens to end on 2027-01-31,
but from 2026-08-15 it would end mid-February and produce a stray seventh block.

`build_forecast` keeps a numeric horizon internally so daily calculation is unchanged; the
horizon is derived from the month count rather than passed as a constant.

### Grouping by calendar month

`_group_cycles` is replaced by `_group_months`, which groups the day list by calendar month
rather than slicing it into fixed 28-day chunks. Each month block carries the same fields as a
cycle does today: start and end date, opening and closing balance, inflows, outflows, lowest
balance, and its days.

The first block is partial, running from today to month end. It is labelled to make this
explicit, for example `August 2026 (from 3 Aug)`, so a partial month is not mistaken for a full
month of spending. Remaining blocks are labelled `September 2026` and so on.

Starting the forecast at the next month boundary to obtain six complete months was rejected: it
would ignore the remainder of the current month, which is precisely the period the operator
needs to see.

### Response shape

- `cycles` becomes `months`.
- `cycle_days` is removed; it has no meaning for calendar months.
- `horizon_months: 6` is added. `horizon_days`, `start_date` and `end_date` remain.

The key is renamed rather than left as `cycles` because a calendar month is not a cycle, and a
stale name would mislead the next reader.

### Dashboard

Interaction is unchanged: the first block is expanded, later blocks collapsed. Only labels and
block boundaries change. The dashboard reads `months` instead of `cycles`.

## Testing

Existing `cashflow` tests assert cycle structure and are updated to month structure.

New cases:

- exactly six month blocks are produced;
- labels and boundaries are correct, including the partial-first-month label;
- the first block starts today and the last ends on a month end;
- no gaps or duplicate dates across block boundaries;
- a start date of the 31st behaves correctly, covering short-month arithmetic;
- a start date on the first of a month yields a full, not partial, first block.

## Success criteria

The Budget page shows six named months from the current month to the sixth, the first marked as
partial, with daily figures identical to those the previous cycle presentation produced for the
same dates.
