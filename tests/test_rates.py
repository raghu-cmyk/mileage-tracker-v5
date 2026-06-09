"""Tests for mileage rate reference data and resolution."""

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.exceptions import RateResolutionError
from app.models import MileageRate
from app.rates import RATE_SCALE, resolve_rate, seed_mileage_rates, validate_rate_table


class RateResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        seed_mileage_rates(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_2026_business_rate(self) -> None:
        rate = resolve_rate(self.db, "business", date(2026, 6, 15))
        self.assertEqual(rate.rate_cents_per_mile, 725)
        self.assertEqual(rate.cents_per_mile(), Decimal("72.5"))

    def test_2025_business_rate(self) -> None:
        rate = resolve_rate(self.db, "business", date(2025, 3, 1))
        self.assertEqual(rate.rate_cents_per_mile, 700)
        self.assertEqual(rate.cents_per_mile(), Decimal("70"))

    def test_2026_medical_rate(self) -> None:
        rate = resolve_rate(self.db, "medical", date(2026, 1, 1))
        self.assertEqual(rate.rate_cents_per_mile, 205)
        self.assertEqual(rate.cents_per_mile(), Decimal("20.5"))

    def test_2026_charitable_rate(self) -> None:
        rate = resolve_rate(self.db, "charitable", date(2026, 12, 31))
        self.assertEqual(rate.rate_cents_per_mile, 140)
        self.assertEqual(rate.cents_per_mile(), Decimal("14"))

    def test_zero_matches_raises(self) -> None:
        with self.assertRaises(RateResolutionError) as ctx:
            resolve_rate(self.db, "business", date(2024, 1, 1))
        self.assertIn("No mileage rate found", str(ctx.exception.message))

    def test_multiple_matches_raises(self) -> None:
        self.db.add(
            MileageRate(
                category_code="business",
                rate_cents_per_mile=800,
                effective_start_date=date(2026, 1, 1),
                effective_end_date=date(2026, 12, 31),
            )
        )
        self.db.commit()
        with self.assertRaises(RateResolutionError) as ctx:
            resolve_rate(self.db, "business", date(2026, 6, 1))
        self.assertIn("Ambiguous mileage rate", str(ctx.exception.message))

    def test_validate_rejects_overlapping_windows(self) -> None:
        self.db.add(
            MileageRate(
                category_code="moving",
                rate_cents_per_mile=300,
                effective_start_date=date(2026, 6, 1),
                effective_end_date=date(2026, 12, 31),
            )
        )
        self.db.commit()
        with self.assertRaises(Exception):
            validate_rate_table(self.db)

    def test_rate_scale_is_ten(self) -> None:
        self.assertEqual(RATE_SCALE, 10)


if __name__ == "__main__":
    unittest.main()
