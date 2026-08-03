"""Deterministic cash-flow forecasting for Family HQ."""

from __future__ import annotations

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


def _empty_section(today: date, horizon_days: int) -> dict:
    days = [_empty_day(today + timedelta(days=offset)) for offset in range(horizon_days)]
    return {
        "opening_balance": 0.0,
        "closing_balance": 0.0,
        "lowest_balance": 0.0,
        "lowest_balance_date": today.isoformat(),
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
    personal = _empty_section(today, horizon_days)
    business = _empty_section(today, horizon_days)
    combined = _empty_section(today, horizon_days)
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
