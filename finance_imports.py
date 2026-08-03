"""Parse bank statement CSV files into stable, registered-account transactions."""
import csv
import re
from datetime import datetime
from pathlib import Path


def _parse_date_flexible(value: str):
    """Try the date formats used by supported bank statement exports."""
    value = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%d %b %Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"date {value!r} not in any recognised format")


def _parse_amount(value: str) -> float:
    """Parse a statement amount, treating blank values as zero."""
    if not value:
        return 0.0
    cleaned = value.replace("$", "").replace(",", "").replace("+", "").strip().strip('"')
    return float(cleaned) if cleaned else 0.0


def _parse_balance(value: str) -> float:
    """Parse a balance, including debit and credit suffixes."""
    if not value:
        return 0.0
    cleaned = value.replace("$", "").replace(",", "").replace("+", "").strip().strip('"')
    if not cleaned:
        return 0.0
    sign = 1.0
    if cleaned.endswith(" DR") or cleaned.endswith("DR"):
        cleaned = cleaned.rsplit("DR", 1)[0].strip()
        sign = -1.0
    elif cleaned.endswith(" CR") or cleaned.endswith("CR"):
        cleaned = cleaned.rsplit("CR", 1)[0].strip()
    return sign * float(cleaned) if cleaned else 0.0


def _transaction(path: Path, metadata: dict, date_value, amount: float, description: str, balance: float) -> dict:
    account_key = (
        f"registered:{metadata['id']}"
        if metadata.get("id") is not None
        else f"legacy:{path.name.lower()}"
    )
    return {
        "account": metadata["name"],
        "account_key": account_key,
        "account_id": metadata.get("id"),
        "ownership": metadata["ownership"],
        "account_type": metadata["account_type"],
        "source_filename": path.name,
        "date": date_value.isoformat(),
        "amount": round(amount, 2),
        "description": description.strip(),
        "balance": round(balance, 2),
    }


def parse_csv_file(path: Path, metadata: dict) -> list[dict]:
    """Parse one header-based ING/GSB or headerless CBA statement CSV."""
    try:
        lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError):
        return []

    transactions = []
    if lines and lines[0].startswith("Date,"):
        for row in csv.DictReader(lines):
            try:
                credit = _parse_amount(row.get("Credit", ""))
                debit = _parse_amount(row.get("Debit", ""))
                if credit == 0 and debit == 0:
                    continue
                transactions.append(_transaction(
                    path,
                    metadata,
                    _parse_date_flexible(row.get("Date", "")),
                    credit if credit else -abs(debit),
                    row.get("Description", ""),
                    _parse_balance(row.get("Balance", "")),
                ))
            except (TypeError, ValueError):
                continue
    else:
        for line in lines:
            parts = next(csv.reader([line]))
            if len(parts) < 3:
                continue
            try:
                transactions.append(_transaction(
                    path,
                    metadata,
                    _parse_date_flexible(parts[0]),
                    _parse_amount(parts[1]),
                    parts[2].strip('"'),
                    _parse_balance(parts[3]) if len(parts) > 3 else 0,
                ))
            except (TypeError, ValueError):
                continue
    return transactions


def deduplicate_transactions(transactions: list[dict]) -> list[dict]:
    """Remove duplicate snapshot rows without merging different registered accounts."""
    seen = set()
    deduplicated = []
    for row in transactions:
        normalised_description = re.sub(
            r"\s+", " ", str(row.get("description", "")).strip().lower()
        )
        key = (
            row["account_key"],
            row["date"],
            round(float(row["amount"]), 2),
            normalised_description,
            row.get("balance"),
        )
        if key not in seen:
            seen.add(key)
            deduplicated.append(row)
    return deduplicated
