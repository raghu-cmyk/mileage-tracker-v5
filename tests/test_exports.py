"""Tests for IRS-compliant CSV and PDF export."""

import csv
import io
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.categories import seed_trip_categories
from app.database import Base
from app.exports import CSV_HEADERS, build_trip_log_rows, render_trip_log_csv, render_year_summary_pdf
from app.models import Trip, TripCategory, Vehicle
from app.rates import seed_mileage_rates


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        seed_trip_categories(self.db)
        seed_mileage_rates(self.db)
        self.business = self.db.query(TripCategory).filter_by(code="business").one()
        self.personal = self.db.query(TripCategory).filter_by(code="personal").one()
        self.vehicle = Vehicle(display_name="Honda Civic")
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
        origin: str = "Office",
        destination: str = "Client site",
        purpose: str = "Client meeting",
        created_at: datetime | None = None,
    ) -> Trip:
        trip = Trip(
            vehicle_id=self.vehicle.id,
            category_id=category.id,
            trip_date=trip_date,
            origin=origin,
            destination=destination,
            business_purpose=purpose,
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

    def test_csv_includes_all_substantiation_columns(self) -> None:
        self._add_trip(trip_date=date(2026, 2, 15), miles="12.50", category=self.business)
        late_created = datetime(2026, 3, 1, tzinfo=timezone.utc)
        self._add_trip(
            trip_date=date(2026, 1, 10),
            miles="5.00",
            category=self.business,
            created_at=late_created,
        )
        self._add_trip(trip_date=date(2026, 4, 1), miles="8.00", category=self.personal)

        csv_text = render_trip_log_csv(self.db, 2026)
        reader = csv.DictReader(io.StringIO(csv_text))
        self.assertEqual(reader.fieldnames, CSV_HEADERS)
        rows = list(reader)
        self.assertEqual(len(rows), 3)

        business_row = next(row for row in rows if row["miles"] == "12.50")
        self.assertEqual(business_row["date"], "2026-02-15")
        self.assertEqual(business_row["origin"], "Office")
        self.assertEqual(business_row["destination"], "Client site")
        self.assertEqual(business_row["business_purpose"], "Client meeting")
        self.assertEqual(business_row["category"], "Business")
        self.assertEqual(business_row["vehicle"], "Honda Civic")
        self.assertEqual(business_row["applied_rate_cents_per_mile"], "725")
        self.assertEqual(business_row["computed_amount_cents"], "906")
        self.assertEqual(business_row["late_entered"], "no")

        late_row = next(row for row in rows if row["miles"] == "5.00")
        self.assertEqual(late_row["late_entered"], "yes")

        personal_row = next(row for row in rows if row["miles"] == "8.00")
        self.assertEqual(personal_row["applied_rate_cents_per_mile"], "")
        self.assertEqual(personal_row["computed_amount_cents"], "0")

    def test_csv_rows_match_stored_values_exactly(self) -> None:
        trip = self._add_trip(
            trip_date=date(2026, 6, 1),
            miles="10.55",
            category=self.business,
            origin="123 Main St",
            destination="456 Oak Ave",
            purpose="Site visit",
        )
        rows = build_trip_log_rows(self.db, 2026)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.miles, str(trip.miles))
        self.assertEqual(row.computed_amount_cents, "765")
        self.assertEqual(row.origin, trip.origin)
        self.assertEqual(row.destination, trip.destination)

    def test_pdf_generates_valid_document(self) -> None:
        self._add_trip(trip_date=date(2026, 3, 1), miles="10.00", category=self.business)
        pdf_bytes = render_year_summary_pdf(self.db, 2026)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 500)

    def test_empty_year_csv_has_header_only(self) -> None:
        csv_text = render_trip_log_csv(self.db, 2026)
        lines = csv_text.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split(","), CSV_HEADERS)


if __name__ == "__main__":
    unittest.main()
