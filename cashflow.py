"""Deterministic cash-flow forecasting for Family HQ."""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median


FORECAST_MONTHS = 6


def _empty_day(day: date) -> dict:
    return {
        "date": day.isoformat(),
        "opening_balance": 0.0,
        "inflows": 0.0,
        "outflows": 0.0,
        "closing_balance": 0.0,
        "events": [],
    }


def _horizon_days(today: date, months: int) -> int:
    """Days from today to the last day of the month `months - 1` ahead."""
    month_index = today.year * 12 + today.month - 1 + months - 1
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return (last_day - today).days + 1


def _month_label(first_day: date, month_start: date) -> str:
    """Name the month, marking a first block that starts mid-month as partial."""
    label = f"{calendar.month_name[month_start.month]} {month_start.year}"
    if first_day > month_start:
        return f"{label} (from {first_day.day} {calendar.month_abbr[first_day.month]})"
    return label


def _group_months(days: list[dict]) -> list[dict]:
    grouped: dict[tuple[int, int], list[dict]] = {}
    for day in days:
        day_date = date.fromisoformat(day["date"])
        grouped.setdefault((day_date.year, day_date.month), []).append(day)

    months = []
    for (year, month), month_days in grouped.items():
        first_day = date.fromisoformat(month_days[0]["date"])
        months.append({
            "number": len(months) + 1,
            "label": _month_label(first_day, date(year, month, 1)),
            "start_date": month_days[0]["date"],
            "end_date": month_days[-1]["date"],
            "opening_balance": month_days[0]["opening_balance"],
            "inflows": round(sum(day["inflows"] for day in month_days), 2),
            "outflows": round(sum(day["outflows"] for day in month_days), 2),
            "closing_balance": month_days[-1]["closing_balance"],
            "lowest_balance": min(day["closing_balance"] for day in month_days),
            "days": month_days,
        })
    return months


def _add_months(value: date, months: int, preferred_day: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(preferred_day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _event_dates(event: dict, start: date, end: date) -> list[date]:
    try:
        first = date.fromisoformat(str(event.get("due_date", "")))
    except ValueError:
        return []

    recurrence = event.get("recurring") or ""
    if recurrence is True or recurrence == 1 or recurrence == "1":
        recurrence = "monthly"
    recurrence = str(recurrence).lower()
    if not recurrence:
        return [first] if start <= first <= end else []

    fixed_days = {"weekly": 7, "fortnightly": 14}
    occurrences = []
    month_step = {"monthly": 1, "quarterly": 3, "annual": 12}.get(recurrence)
    if month_step:
        occurrence_number = 0
        current = first
        while current < start:
            occurrence_number += 1
            current = _add_months(
                first,
                month_step * occurrence_number,
                first.day,
            )
        while current <= end:
            occurrences.append(current)
            occurrence_number += 1
            current = _add_months(
                first,
                month_step * occurrence_number,
                first.day,
            )
        return occurrences

    current = first
    while current <= end:
        if current >= start:
            occurrences.append(current)
        if recurrence in fixed_days:
            current += timedelta(days=fixed_days[recurrence])
        else:
            break
    return occurrences


def _normalise_description(value: str) -> str:
    without_numbers = re.sub(r"\d+", "", str(value).lower())
    return re.sub(r"\s+", " ", without_numbers).strip()


def _frequency_for_intervals(intervals: list[int]) -> str:
    candidates = [
        ("weekly", 7, 2),
        ("fortnightly", 14, 2),
        ("monthly", 30, 5),
        ("quarterly", 91, 10),
        ("annual", 365, 20),
    ]
    typical = median(intervals)
    for name, expected, tolerance in candidates:
        if (
            abs(typical - expected) <= tolerance
            and all(abs(value - expected) <= tolerance for value in intervals)
        ):
            return name
    return ""


def _next_occurrence(last: date, frequency: str, forecast_start: date) -> date:
    fixed_days = {"weekly": 7, "fortnightly": 14}
    if frequency in fixed_days:
        next_date = last + timedelta(days=fixed_days[frequency])
        while next_date < forecast_start:
            next_date += timedelta(days=fixed_days[frequency])
        return next_date

    month_step = {"monthly": 1, "quarterly": 3, "annual": 12}[frequency]
    occurrence = 1
    next_date = _add_months(last, month_step, last.day)
    while next_date < forecast_start:
        occurrence += 1
        next_date = _add_months(last, month_step * occurrence, last.day)
    return next_date


def infer_recurring_events(
    transactions: list[dict],
    account_ownership: dict[str, str],
    forecast_start: date,
) -> list[dict]:
    """Infer stable repeated cash events from at least three historical rows."""
    groups = defaultdict(list)
    for transaction in transactions:
        amount = float(transaction.get("amount", 0) or 0)
        if amount == 0:
            continue
        try:
            transaction_date = date.fromisoformat(str(transaction.get("date", "")))
        except ValueError:
            continue
        account = str(transaction.get("account", ""))
        description = str(transaction.get("description", "")).strip()
        normalised = _normalise_description(description)
        if len(normalised) < 4:
            continue
        direction = "inflow" if amount > 0 else "outflow"
        groups[(account, normalised, direction)].append({
            "date": transaction_date,
            "amount": abs(amount),
            "description": description,
        })

    events = []
    for (account, _, direction), rows in groups.items():
        rows.sort(key=lambda item: item["date"])
        unique_rows = []
        seen_dates = set()
        for row in rows:
            if row["date"] in seen_dates:
                continue
            seen_dates.add(row["date"])
            unique_rows.append(row)
        if len(unique_rows) < 3:
            continue

        intervals = [
            (later["date"] - earlier["date"]).days
            for earlier, later in zip(unique_rows, unique_rows[1:])
        ]
        frequency = _frequency_for_intervals(intervals)
        if not frequency:
            continue
        amounts = [row["amount"] for row in unique_rows]
        average_amount = sum(amounts) / len(amounts)
        if max(amounts) - min(amounts) > max(2.0, average_amount * 0.15):
            continue

        last = unique_rows[-1]
        next_date = _next_occurrence(last["date"], frequency, forecast_start)
        events.append({
            "description": last["description"],
            "amount": round(average_amount, 2),
            "due_date": next_date.isoformat(),
            "recurring": frequency,
            "ownership": account_ownership.get(account, "personal"),
            "direction": direction,
            "source": "transaction_history",
            "confidence": "expected",
        })

    return sorted(events, key=lambda event: event["description"].lower())


def _opening_balances(
    transactions: list[dict],
    account_ownership: dict[str, str],
) -> dict[str, float]:
    latest: dict[str, tuple[str, float]] = {}
    for transaction in transactions:
        balance = transaction.get("balance")
        if balance is None or balance == "":
            continue
        account = str(transaction.get("account", ""))
        transaction_date = str(transaction.get("date", ""))
        previous = latest.get(account)
        if previous is None or transaction_date > previous[0]:
            latest[account] = (transaction_date, float(balance))

    totals = {"personal": 0.0, "business": 0.0}
    for account, (_, balance) in latest.items():
        ownership = account_ownership.get(account, "personal")
        if ownership not in totals:
            ownership = "personal"
        totals[ownership] += balance
    return {key: round(value, 2) for key, value in totals.items()}


def _build_section(
    ownership: str,
    opening_balance: float,
    scheduled_events: list[dict],
    today: date,
    horizon_days: int,
    safety_buffer: float,
) -> dict:
    days = [_empty_day(today + timedelta(days=offset)) for offset in range(horizon_days)]
    day_map = {day["date"]: day for day in days}
    end = today + timedelta(days=horizon_days - 1)

    for event in scheduled_events:
        if event.get("ownership", "personal") != ownership:
            continue
        direction = event.get("direction", "outflow")
        amount = round(abs(float(event.get("amount", 0) or 0)), 2)
        for event_date in _event_dates(event, today, end):
            day = day_map[event_date.isoformat()]
            rendered = {
                "description": str(event.get("description", "Cash event")),
                "amount": amount,
                "direction": direction,
                "category": str(event.get("category", "") or ""),
                "source": str(event.get("source", "manual") or "manual"),
                "confidence": str(event.get("confidence", "confirmed") or "confirmed"),
            }
            day["events"].append(rendered)
            if direction == "inflow":
                day["inflows"] = round(day["inflows"] + amount, 2)
            else:
                day["outflows"] = round(day["outflows"] + amount, 2)

    balance = round(opening_balance, 2)
    warnings = []
    threshold = safety_buffer if ownership == "personal" else 0.0
    was_below = balance < threshold
    for day in days:
        day["opening_balance"] = balance
        balance = round(balance + day["inflows"] - day["outflows"], 2)
        day["closing_balance"] = balance
        is_below = balance < threshold
        if is_below and not was_below:
            warnings.append({
                "date": day["date"],
                "projected_balance": balance,
                "threshold": round(threshold, 2),
                "amount_required": round(threshold - balance, 2),
                "events": list(day["events"]),
            })
        was_below = is_below

    lowest_day = min(days, key=lambda item: item["closing_balance"])
    next_income_index = next(
        (index for index, day in enumerate(days) if day["inflows"] > 0),
        len(days),
    )
    committed_before_income = sum(
        day["outflows"] for day in days[:next_income_index]
    )
    safe_to_spend = max(
        0.0,
        opening_balance - safety_buffer - committed_before_income,
    )
    return {
        "opening_balance": round(opening_balance, 2),
        "closing_balance": days[-1]["closing_balance"],
        "lowest_balance": lowest_day["closing_balance"],
        "lowest_balance_date": lowest_day["date"],
        "safe_to_spend": round(safe_to_spend, 2),
        "warnings": warnings,
        "days": days,
        "months": _group_months(days),
    }


def _combine_sections(personal: dict, business: dict, today: date) -> dict:
    days = []
    for personal_day, business_day in zip(personal["days"], business["days"]):
        events = list(personal_day["events"]) + list(business_day["events"])
        days.append({
            "date": personal_day["date"],
            "opening_balance": round(
                personal_day["opening_balance"] + business_day["opening_balance"], 2
            ),
            "inflows": round(personal_day["inflows"] + business_day["inflows"], 2),
            "outflows": round(personal_day["outflows"] + business_day["outflows"], 2),
            "closing_balance": round(
                personal_day["closing_balance"] + business_day["closing_balance"], 2
            ),
            "events": events,
        })
    lowest_day = min(days, key=lambda item: item["closing_balance"])
    return {
        "opening_balance": round(
            personal["opening_balance"] + business["opening_balance"], 2
        ),
        "closing_balance": days[-1]["closing_balance"],
        "lowest_balance": lowest_day["closing_balance"],
        "lowest_balance_date": lowest_day["date"],
        "safe_to_spend": 0.0,
        "warnings": [],
        "days": days,
        "months": _group_months(days),
    }


def build_forecast(
    transactions: list[dict],
    scheduled_events: list[dict],
    account_ownership: dict[str, str],
    today: date,
    safety_buffer: float,
    horizon_months: int = FORECAST_MONTHS,
) -> dict:
    """Build separated personal, business and combined daily forecasts."""
    if horizon_months <= 0:
        raise ValueError("horizon_months must be positive")
    horizon_days = _horizon_days(today, horizon_months)
    opening = _opening_balances(transactions, account_ownership)
    personal = _build_section(
        "personal",
        opening["personal"],
        scheduled_events,
        today,
        horizon_days,
        float(safety_buffer),
    )
    business = _build_section(
        "business",
        opening["business"],
        scheduled_events,
        today,
        horizon_days,
        0.0,
    )
    combined = _combine_sections(personal, business, today)
    return {
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=horizon_days - 1)).isoformat(),
        "horizon_days": horizon_days,
        "horizon_months": horizon_months,
        "safety_buffer": float(safety_buffer),
        "personal": personal,
        "business": business,
        "combined": combined,
    }
