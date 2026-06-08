"""Year-end deduction calculation and summary reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from .models import Trip
from .rates import RATE_SCALE, resolve_rate
from .trips import list_trips

MILES_SCALE = 100


@dataclass(frozen=True)
class TripDeduction:
    trip_id: int
    trip_date: date
    category_code: str
    category_display_name: str
    miles: Decimal
    rate_cents_per_mile: int | None
    deduction_cents: int
    is_deductible: bool
    is_late_entered: bool


@dataclass(frozen=True)
class CategorySummary:
    category_code: str
    display_name: str
    is_deductible: bool
    total_miles: Decimal
    total_deduction_cents: int


@dataclass(frozen=True)
class YearSummary:
    tax_year: int
    total_miles: Decimal
    deductible_miles: Decimal
    personal_miles: Decimal
    total_deduction_cents: int
    business_use_percentage: Decimal | None
    late_entered_count: int
    by_category: list[CategorySummary]
    trip_deductions: list[TripDeduction]


def miles_to_hundredths(miles: Decimal) -> int:
    """Convert trip miles (2 decimal places) to integer hundredths."""
    normalized = miles.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(normalized * MILES_SCALE)


def compute_trip_deduction_cents(miles: Decimal, rate_cents_per_mile: int) -> int:
    """Compute per-trip deduction in whole cents without floating-point drift."""
    miles_hundredths = miles_to_hundredths(miles)
    product = miles_hundredths * rate_cents_per_mile
    denominator = MILES_SCALE * RATE_SCALE
    return (product + denominator // 2) // denominator


def format_cents(cents: int) -> str:
    """Format integer cents as a dollar string."""
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    dollars, remainder = divmod(absolute, 100)
    return f"{sign}${dollars}.{remainder:02d}"


def compute_trip_deduction(db: Session, trip: Trip) -> TripDeduction:
    """Resolve rate and compute deductible amount for a single trip."""
    category = trip.category
    is_deductible = category.is_deductible
    if not is_deductible:
        return TripDeduction(
            trip_id=trip.id,
            trip_date=trip.trip_date,
            category_code=category.code,
            category_display_name=category.display_name,
            miles=trip.miles,
            rate_cents_per_mile=None,
            deduction_cents=0,
            is_deductible=False,
            is_late_entered=trip.is_late_entered(),
        )

    rate = resolve_rate(db, category.code, trip.trip_date)
    deduction_cents = compute_trip_deduction_cents(trip.miles, rate.rate_cents_per_mile)
    return TripDeduction(
        trip_id=trip.id,
        trip_date=trip.trip_date,
        category_code=category.code,
        category_display_name=category.display_name,
        miles=trip.miles,
        rate_cents_per_mile=rate.rate_cents_per_mile,
        deduction_cents=deduction_cents,
        is_deductible=True,
        is_late_entered=trip.is_late_entered(),
    )


def compute_year_summary(db: Session, tax_year: int) -> YearSummary:
    """Aggregate deductions and mileage totals for a tax year."""
    year_start = date(tax_year, 1, 1)
    year_end = date(tax_year, 12, 31)
    trips = list_trips(db, date_from=year_start, date_to=year_end)

    trip_deductions: list[TripDeduction] = []
    category_totals: dict[str, CategorySummary] = {}
    total_miles = Decimal("0")
    deductible_miles = Decimal("0")
    personal_miles = Decimal("0")
    total_deduction_cents = 0
    late_entered_count = 0

    for trip in trips:
        deduction = compute_trip_deduction(db, trip)
        trip_deductions.append(deduction)
        total_miles += trip.miles
        if deduction.is_late_entered:
            late_entered_count += 1

        if deduction.is_deductible:
            deductible_miles += trip.miles
            total_deduction_cents += deduction.deduction_cents
        else:
            personal_miles += trip.miles

        existing = category_totals.get(deduction.category_code)
        if existing is None:
            category_totals[deduction.category_code] = CategorySummary(
                category_code=deduction.category_code,
                display_name=deduction.category_display_name,
                is_deductible=deduction.is_deductible,
                total_miles=trip.miles,
                total_deduction_cents=deduction.deduction_cents,
            )
        else:
            category_totals[deduction.category_code] = CategorySummary(
                category_code=existing.category_code,
                display_name=existing.display_name,
                is_deductible=existing.is_deductible,
                total_miles=existing.total_miles + trip.miles,
                total_deduction_cents=existing.total_deduction_cents + deduction.deduction_cents,
            )

    if total_miles > 0:
        business_use_percentage = (deductible_miles / total_miles * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        business_use_percentage = None

    by_category = sorted(
        category_totals.values(),
        key=lambda row: (not row.is_deductible, row.display_name.lower()),
    )

    return YearSummary(
        tax_year=tax_year,
        total_miles=total_miles,
        deductible_miles=deductible_miles,
        personal_miles=personal_miles,
        total_deduction_cents=total_deduction_cents,
        business_use_percentage=business_use_percentage,
        late_entered_count=late_entered_count,
        by_category=by_category,
        trip_deductions=trip_deductions,
    )
