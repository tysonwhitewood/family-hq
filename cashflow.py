"""Deterministic cash-flow forecasting for Family HQ."""

from __future__ import annotations

import calendar
from datetime import date, timedelta


FORECAST_DAYS = 182
CYCLE_DAYS = 28


def _empty_day(day: date) -> dict:
    return {
        "date": day.isoformat(),
        "opening_balance": 0.0,
        "inflows": 0.0,
        "outflows": 0.0,
        "closing_balance": 0.0,
        "events": [],
    }


def _group_cycles(days: list[dict]) -> list[dict]:
    cycles = []
    for offset in range(0, len(days), CYCLE_DAYS):
        cycle_days = days[offset:offset + CYCLE_DAYS]
        number = len(cycles) + 1
        cycles.append({
            "number": number,
            "label": f"Cycle {number}",
            "start_date": cycle_days[0]["date"],
            "end_date": cycle_days[-1]["date"],
            "opening_balance": cycle_days[0]["opening_balance"],
            "inflows": round(sum(day["inflows"] for day in cycle_days), 2),
            "outflows": round(sum(day["outflows"] for day in cycle_days), 2),
            "closing_balance": cycle_days[-1]["closing_balance"],
            "lowest_balance": min(day["closing_balance"] for day in cycle_days),
            "days": cycle_days,
        })
    return cycles


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
    current = first
    month_step = {"monthly": 1, "quarterly": 3, "annual": 12}.get(recurrence)
    while current <= end:
        if current >= start:
            occurrences.append(current)
        if recurrence in fixed_days:
            current += timedelta(days=fixed_days[recurrence])
        elif month_step:
            current = _add_months(first, month_step * (len(occurrences) + (
                0 if first >= start else 1
            )), first.day)
            if current <= first:
                current = _add_months(first, month_step, first.day)
            while current < start:
                elapsed_months = (
                    (current.year - first.year) * 12 + current.month - first.month
                )
                current = _add_months(first, elapsed_months + month_step, first.day)
        else:
            break
    return occurrences


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
    return {
        "opening_balance": round(opening_balance, 2),
        "closing_balance": days[-1]["closing_balance"],
        "lowest_balance": lowest_day["closing_balance"],
        "lowest_balance_date": lowest_day["date"],
        "safe_to_spend": round(max(0.0, opening_balance - safety_buffer), 2),
        "warnings": warnings,
        "days": days,
        "cycles": _group_cycles(days),
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
        "cycles": _group_cycles(days),
    }


def build_forecast(
    transactions: list[dict],
    scheduled_events: list[dict],
    account_ownership: dict[str, str],
    today: date,
    safety_buffer: float,
    horizon_days: int = FORECAST_DAYS,
) -> dict:
    """Build separated personal, business and combined daily forecasts."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
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
        "cycle_days": CYCLE_DAYS,
        "safety_buffer": float(safety_buffer),
        "personal": personal,
        "business": business,
        "combined": combined,
    }
