"""Tests for deduction calculation and year-end summary."""

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.categories import seed_trip_categories
from app.database import Base
from app.deductions import (
    compute_trip_deduction,
    compute_trip_deduction_cents,
    compute_year_summary,
    format_cents,
)
from app.exceptions import RateResolutionError
from app.models import Trip, TripCategory, Vehicle
from app.rates import seed_mileage_rates


class DeductionCalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        seed_trip_categories(self.db)
        seed_mileage_rates(self.db)
        self.business = self.db.query(TripCategory).filter_by(code="business").one()
        self.personal = self.db.query(TripCategory).filter_by(code="personal").one()
        self.vehicle = Vehicle(display_name="Test Car")
        self.db.add(self.vehicle)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _add_trip(
        self,
        *,
        trip_date: date,
        miles: str,
        category: TripCategory,
        created_at: datetime | None = None,
    ) -> Trip:
        trip = Trip(
            vehicle_id=self.vehicle.id,
            category_id=category.id,
            trip_date=trip_date,
            origin="A",
            destination="B",
            business_purpose="Client visit",
            miles=Decimal(miles),
        )
        if created_at is not None:
            trip.created_at = created_at
        else:
            trip.created_at = datetime.combine(trip_date, datetime.min.time(), tzinfo=timezone.utc)
        self.db.add(trip)
        self.db.commit()
        self.db.refresh(trip)
        return trip

    def test_exact_deduction_for_whole_miles(self) -> None:
        cents = compute_trip_deduction_cents(Decimal("10"), 725)
        self.assertEqual(cents, 725)
        self.assertEqual(format_cents(cents), "$7.25")

    def test_exact_deduction_for_fractional_miles(self) -> None:
        cents = compute_trip_deduction_cents(Decimal("10.55"), 725)
        self.assertEqual(cents, 765)

    def test_personal_trip_has_zero_deduction(self) -> None:
        trip = self._add_trip(
            trip_date=date(2026, 3, 1),
            miles="25.00",
            category=self.personal,
        )
        deduction = compute_trip_deduction(self.db, trip)
        self.assertFalse(deduction.is_deductible)
        self.assertEqual(deduction.deduction_cents, 0)
        self.assertIsNone(deduction.rate_cents_per_mile)

    def test_business_trip_uses_resolved_rate(self) -> None:
        trip = self._add_trip(
            trip_date=date(2026, 3, 1),
            miles="100.00",
            category=self.business,
        )
        deduction = compute_trip_deduction(self.db, trip)
        self.assertTrue(deduction.is_deductible)
        self.assertEqual(deduction.rate_cents_per_mile, 725)
        self.assertEqual(deduction.deduction_cents, 7250)

    def test_year_summary_aggregates_by_category(self) -> None:
        self._add_trip(trip_date=date(2026, 1, 10), miles="10.00", category=self.business)
        self._add_trip(trip_date=date(2026, 2, 10), miles="5.50", category=self.business)
        self._add_trip(trip_date=date(2026, 3, 10), miles="20.00", category=self.personal)
        late_created = datetime(2026, 3, 20, tzinfo=timezone.utc)
        self._add_trip(
            trip_date=date(2026, 3, 1),
            miles="2.00",
            category=self.business,
            created_at=late_created,
        )

        summary = compute_year_summary(self.db, 2026)
        self.assertEqual(summary.total_miles, Decimal("37.50"))
        self.assertEqual(summary.deductible_miles, Decimal("17.50"))
        self.assertEqual(summary.personal_miles, Decimal("20.00"))
        self.assertEqual(summary.late_entered_count, 1)
        self.assertEqual(summary.business_use_percentage, Decimal("46.67"))

        business_row = next(row for row in summary.by_category if row.category_code == "business")
        personal_row = next(row for row in summary.by_category if row.category_code == "personal")
        self.assertEqual(business_row.total_miles, Decimal("17.50"))
        self.assertGreater(business_row.total_deduction_cents, 0)
        self.assertEqual(personal_row.total_deduction_cents, 0)

    def test_missing_rate_blocks_calculation(self) -> None:
        trip = self._add_trip(
            trip_date=date(2024, 1, 1),
            miles="10.00",
            category=self.business,
        )
        with self.assertRaises(RateResolutionError):
            compute_trip_deduction(self.db, trip)

    def test_empty_year_summary(self) -> None:
        summary = compute_year_summary(self.db, 2026)
        self.assertEqual(summary.total_miles, Decimal("0"))
        self.assertIsNone(summary.business_use_percentage)
        self.assertEqual(summary.late_entered_count, 0)


if __name__ == "__main__":
    unittest.main()
