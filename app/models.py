from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

LATE_ENTRY_THRESHOLD_DAYS = 7


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    odometer_readings: Mapped[list["VehicleOdometerReading"]] = relationship(
        back_populates="vehicle",
        cascade="all, delete-orphan",
    )
    trips: Mapped[list["Trip"]] = relationship(back_populates="vehicle")


class VehicleOdometerReading(Base):
    __tablename__ = "vehicle_odometer_readings"
    __table_args__ = (UniqueConstraint("vehicle_id", "tax_year", name="uq_vehicle_tax_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    odometer_year_start: Mapped[int] = mapped_column(Integer, nullable=True)
    odometer_year_end: Mapped[int] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    vehicle: Mapped["Vehicle"] = relationship(back_populates="odometer_readings")


class TripCategory(Base):
    __tablename__ = "trip_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deductible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    trips: Mapped[list["Trip"]] = relationship(back_populates="category")


class MileageRate(Base):
    """Time-effective IRS standard mileage rate reference data."""

    __tablename__ = "mileage_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rate_cents_per_mile: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end_date: Mapped[date] = mapped_column(Date, nullable=False)

    def cents_per_mile(self) -> Decimal:
        from .rates import RATE_SCALE

        return Decimal(self.rate_cents_per_mile) / RATE_SCALE


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("trip_categories.id"), nullable=False, index=True)
    trip_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(256), nullable=False)
    destination: Mapped[str] = mapped_column(String(256), nullable=False)
    business_purpose: Mapped[str] = mapped_column(Text, nullable=False)
    miles: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    odometer_start: Mapped[int] = mapped_column(Integer, nullable=True)
    odometer_end: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    vehicle: Mapped["Vehicle"] = relationship(back_populates="trips")
    category: Mapped["TripCategory"] = relationship(back_populates="trips")
    receipts: Mapped[list["Receipt"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
    )

    def is_late_entered(self) -> bool:
        created = self.created_at.astimezone(timezone.utc).date()
        return (created - self.trip_date).days > LATE_ENTRY_THRESHOLD_DAYS


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    trip: Mapped["Trip"] = relationship(back_populates="receipts")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    field_changes: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
