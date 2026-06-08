"""Time-effective IRS mileage rate reference data and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from sqlalchemy.orm import Session

from .exceptions import RateResolutionError, RateTableValidationError
from .models import MileageRate

# rate_cents_per_mile stores tenths of a cent (e.g. 725 = 72.5¢/mile) so half-cent
# IRS rates are represented exactly without floating-point drift.
RATE_SCALE = 10

DEFAULT_RATES: list[dict] = [
    {
        "category_code": "business",
        "rate_cents_per_mile": 725,
        "effective_start_date": date(2026, 1, 1),
        "effective_end_date": date(2026, 12, 31),
    },
    {
        "category_code": "medical",
        "rate_cents_per_mile": 205,
        "effective_start_date": date(2026, 1, 1),
        "effective_end_date": date(2026, 12, 31),
    },
    {
        "category_code": "moving",
        "rate_cents_per_mile": 205,
        "effective_start_date": date(2026, 1, 1),
        "effective_end_date": date(2026, 12, 31),
    },
    {
        "category_code": "charitable",
        "rate_cents_per_mile": 140,
        "effective_start_date": date(2026, 1, 1),
        "effective_end_date": date(2026, 12, 31),
    },
    {
        "category_code": "business",
        "rate_cents_per_mile": 700,
        "effective_start_date": date(2025, 1, 1),
        "effective_end_date": date(2025, 12, 31),
    },
]


def rate_cents_per_mile_decimal(stored_value: int) -> Decimal:
    """Convert stored tenths-of-cent integer to cents per mile."""
    return Decimal(stored_value) / RATE_SCALE


def seed_mileage_rates(db: Session) -> None:
    """Insert IRS reference rates when missing; validate table on every startup."""
    existing = {
        (
            row.category_code,
            row.effective_start_date,
            row.effective_end_date,
        )
        for row in db.query(MileageRate).all()
    }
    added = False
    for item in DEFAULT_RATES:
        key = (item["category_code"], item["effective_start_date"], item["effective_end_date"])
        if key not in existing:
            db.add(MileageRate(**item))
            added = True
    if added:
        db.commit()
    validate_rate_table(db)


@dataclass(frozen=True)
class _RateWindow:
    category_code: str
    effective_start_date: date
    effective_end_date: date
    rate_id: int


def validate_rate_table(db: Session) -> None:
    """Ensure rate windows are well-formed and non-overlapping per category."""
    rates = db.query(MileageRate).order_by(
        MileageRate.category_code.asc(),
        MileageRate.effective_start_date.asc(),
    ).all()
    windows: list[_RateWindow] = []
    for rate in rates:
        if rate.effective_start_date > rate.effective_end_date:
            raise RateTableValidationError(
                f"Rate id={rate.id} for category '{rate.category_code}' has "
                f"effective_start_date after effective_end_date."
            )
        windows.append(
            _RateWindow(
                category_code=rate.category_code,
                effective_start_date=rate.effective_start_date,
                effective_end_date=rate.effective_end_date,
                rate_id=rate.id,
            )
        )
    by_category: dict[str, list[_RateWindow]] = {}
    for window in windows:
        by_category.setdefault(window.category_code, []).append(window)
    for category_code, category_windows in by_category.items():
        sorted_windows = sorted(category_windows, key=lambda w: w.effective_start_date)
        for idx in range(1, len(sorted_windows)):
            prev = sorted_windows[idx - 1]
            curr = sorted_windows[idx]
            if curr.effective_start_date <= prev.effective_end_date:
                raise RateTableValidationError(
                    f"Overlapping mileage rates for category '{category_code}': "
                    f"rate id={prev.rate_id} [{prev.effective_start_date}..{prev.effective_end_date}] "
                    f"overlaps rate id={curr.rate_id} [{curr.effective_start_date}..{curr.effective_end_date}]."
                )


def resolve_rate(db: Session, category_code: str, trip_date: date) -> MileageRate:
    """Return the single mileage rate whose effective window contains trip_date."""
    matches: Sequence[MileageRate] = (
        db.query(MileageRate)
        .filter(
            MileageRate.category_code == category_code,
            MileageRate.effective_start_date <= trip_date,
            MileageRate.effective_end_date >= trip_date,
        )
        .all()
    )
    if len(matches) == 0:
        raise RateResolutionError(
            f"No mileage rate found for category '{category_code}' on {trip_date.isoformat()}."
        )
    if len(matches) > 1:
        ids = ", ".join(str(rate.id) for rate in matches)
        raise RateResolutionError(
            f"Ambiguous mileage rate for category '{category_code}' on {trip_date.isoformat()}: "
            f"matched rate ids [{ids}]."
        )
    return matches[0]
