"""Tests for receipt capture storage and export references."""

import csv
import io
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.categories import seed_trip_categories
from app.database import Base
from app.exceptions import ValidationError
from app.exports import CSV_HEADERS, render_trip_log_csv
from app.models import Trip, TripCategory, Vehicle
from app.rates import seed_mileage_rates
from app.receipts import (
    delete_receipt,
    list_receipts_for_trip,
    receipt_file_path,
    receipt_reference_summary,
    store_receipt,
)


class ReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_patch = patch("app.receipts.DATA_DIR", Path(self.temp_dir.name))
        self.db_data_patch = patch("app.database.DATA_DIR", Path(self.temp_dir.name))
        self.data_patch.start()
        self.db_data_patch.start()
        (Path(self.temp_dir.name) / "receipts").mkdir()

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        seed_trip_categories(self.db)
        seed_mileage_rates(self.db)
        self.business = self.db.query(TripCategory).filter_by(code="business").one()
        self.vehicle = Vehicle(display_name="Test Car")
        self.db.add(self.vehicle)
        self.db.commit()
        self.trip = Trip(
            vehicle_id=self.vehicle.id,
            category_id=self.business.id,
            trip_date=date(2026, 3, 15),
            origin="Home",
            destination="Office",
            business_purpose="Commute",
            miles=Decimal("12.5"),
            created_at=datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc),
        )
        self.db.add(self.trip)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.data_patch.stop()
        self.db_data_patch.stop()
        self.temp_dir.cleanup()

    def test_store_and_list_receipt(self) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        receipt = store_receipt(
            self.db,
            self.trip,
            filename="parking.png",
            content_type="image/png",
            data=png_bytes,
        )
        self.assertEqual(receipt.trip_id, self.trip.id)
        self.assertEqual(receipt.byte_size, len(png_bytes))
        self.assertTrue(receipt.content_hash)
        self.assertTrue(receipt_file_path(receipt).is_file())

        receipts = list_receipts_for_trip(self.db, self.trip.id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].original_filename, "parking.png")

    def test_reject_oversized_file(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            store_receipt(
                self.db,
                self.trip,
                filename="huge.jpg",
                content_type="image/jpeg",
                data=b"x" * (10 * 1024 * 1024 + 1),
            )
        self.assertIn("10 MB", str(ctx.exception))

    def test_reject_unsupported_type(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            store_receipt(
                self.db,
                self.trip,
                filename="notes.txt",
                content_type="text/plain",
                data=b"hello",
            )
        self.assertIn("Unsupported", str(ctx.exception))

    def test_delete_receipt_removes_file(self) -> None:
        receipt = store_receipt(
            self.db,
            self.trip,
            filename="toll.jpg",
            content_type="image/jpeg",
            data=b"\xff\xd8\xff" + b"\x00" * 32,
        )
        path = receipt_file_path(receipt)
        self.assertTrue(path.is_file())
        delete_receipt(self.db, receipt)
        self.assertFalse(path.exists())
        self.assertEqual(list_receipts_for_trip(self.db, self.trip.id), [])

    def test_csv_includes_receipt_reference(self) -> None:
        store_receipt(
            self.db,
            self.trip,
            filename="fuel.webp",
            content_type="image/webp",
            data=b"RIFF" + b"\x00" * 32,
        )
        csv_text = render_trip_log_csv(self.db, 2026)
        reader = csv.DictReader(io.StringIO(csv_text))
        row = next(reader)
        self.assertIn("receipt_reference", row)
        self.assertIn("fuel.webp", row["receipt_reference"])
        self.assertIn("receipt_reference", CSV_HEADERS)
