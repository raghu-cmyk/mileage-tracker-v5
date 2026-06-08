from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from .audit import record_audit_event
from .categories import get_category
from .exceptions import ValidationError
from .models import Trip, Vehicle


def _parse_trip_date(raw: Optional[str]) -> date:
    if raw is None or not str(raw).strip():
        raise ValidationError("Trip date is required.")
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValidationError("Trip date must be a valid date (YYYY-MM-DD).") from exc


def _parse_miles(raw: Optional[str]) -> Decimal:
    if raw is None or str(raw).strip() == "":
        raise ValidationError("Miles is required.")
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ValidationError("Miles must be a positive number.") from exc
    if value <= 0:
        raise ValidationError("Miles must be a positive number.")
    return value


def _parse_optional_odometer(raw: Optional[str], label: str) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValidationError(f"{label} must be a whole number.") from exc
    if value < 0:
        raise ValidationError(f"{label} cannot be negative.")
    return value


def _validate_trip_fields(
    db: Session,
    *,
    trip_date_raw: Optional[str],
    origin: Optional[str],
    destination: Optional[str],
    business_purpose: Optional[str],
    miles_raw: Optional[str],
    category_id_raw: Optional[str],
    vehicle_id_raw: Optional[str],
) -> tuple[date, str, str, str, Decimal, int, int]:
    trip_date = _parse_trip_date(trip_date_raw)
    today = datetime.now(timezone.utc).date()
    if trip_date > today:
        raise ValidationError("Trip date cannot be in the future.")

    origin_text = (origin or "").strip()
    if not origin_text:
        raise ValidationError("Origin is required.")

    destination_text = (destination or "").strip()
    if not destination_text:
        raise ValidationError("Destination is required.")

    purpose_text = (business_purpose or "").strip()
    if not purpose_text:
        raise ValidationError("Business purpose is required.")

    miles = _parse_miles(miles_raw)

    if category_id_raw is None or str(category_id_raw).strip() == "":
        raise ValidationError("Category is required.")
    try:
        category_id = int(str(category_id_raw).strip())
    except ValueError as exc:
        raise ValidationError("Category is required.") from exc
    category = get_category(db, category_id)
    if category is None:
        raise ValidationError("Category is required.")

    if vehicle_id_raw is None or str(vehicle_id_raw).strip() == "":
        raise ValidationError("Vehicle is required.")
    try:
        vehicle_id = int(str(vehicle_id_raw).strip())
    except ValueError as exc:
        raise ValidationError("Vehicle is required.") from exc
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if vehicle is None or vehicle.is_archived:
        raise ValidationError("Vehicle is required.")

    return trip_date, origin_text, destination_text, purpose_text, miles, category_id, vehicle_id


def list_trips(
    db: Session,
    *,
    vehicle_id: Optional[int] = None,
    category_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[Trip]:
    query = db.query(Trip).order_by(Trip.trip_date.desc(), Trip.id.desc())
    if vehicle_id is not None:
        query = query.filter(Trip.vehicle_id == vehicle_id)
    if category_id is not None:
        query = query.filter(Trip.category_id == category_id)
    if date_from is not None:
        query = query.filter(Trip.trip_date >= date_from)
    if date_to is not None:
        query = query.filter(Trip.trip_date <= date_to)
    return query.all()


def get_trip(db: Session, trip_id: int) -> Optional[Trip]:
    return db.query(Trip).filter(Trip.id == trip_id).first()


def create_trip(
    db: Session,
    *,
    trip_date_raw: Optional[str],
    origin: Optional[str],
    destination: Optional[str],
    business_purpose: Optional[str],
    miles_raw: Optional[str],
    category_id_raw: Optional[str],
    vehicle_id_raw: Optional[str],
    odometer_start_raw: Optional[str] = None,
    odometer_end_raw: Optional[str] = None,
) -> Trip:
    trip_date, origin_text, destination_text, purpose_text, miles, category_id, vehicle_id = (
        _validate_trip_fields(
            db,
            trip_date_raw=trip_date_raw,
            origin=origin,
            destination=destination,
            business_purpose=business_purpose,
            miles_raw=miles_raw,
            category_id_raw=category_id_raw,
            vehicle_id_raw=vehicle_id_raw,
        )
    )
    odometer_start = _parse_optional_odometer(odometer_start_raw, "Odometer start")
    odometer_end = _parse_optional_odometer(odometer_end_raw, "Odometer end")

    trip = Trip(
        trip_date=trip_date,
        origin=origin_text,
        destination=destination_text,
        business_purpose=purpose_text,
        miles=miles,
        category_id=category_id,
        vehicle_id=vehicle_id,
        odometer_start=odometer_start,
        odometer_end=odometer_end,
    )
    db.add(trip)
    db.flush()
    record_audit_event(
        db,
        entity_type="trip",
        entity_id=trip.id,
        action="create",
        field_changes={
            "trip_date": trip_date.isoformat(),
            "origin": origin_text,
            "destination": destination_text,
            "business_purpose": purpose_text,
            "miles": str(miles),
            "category_id": category_id,
            "vehicle_id": vehicle_id,
            "odometer_start": odometer_start,
            "odometer_end": odometer_end,
        },
    )
    db.commit()
    db.refresh(trip)
    return trip


def update_trip(
    db: Session,
    trip: Trip,
    *,
    trip_date_raw: Optional[str],
    origin: Optional[str],
    destination: Optional[str],
    business_purpose: Optional[str],
    miles_raw: Optional[str],
    category_id_raw: Optional[str],
    vehicle_id_raw: Optional[str],
    odometer_start_raw: Optional[str] = None,
    odometer_end_raw: Optional[str] = None,
) -> Trip:
    trip_date, origin_text, destination_text, purpose_text, miles, category_id, vehicle_id = (
        _validate_trip_fields(
            db,
            trip_date_raw=trip_date_raw,
            origin=origin,
            destination=destination,
            business_purpose=business_purpose,
            miles_raw=miles_raw,
            category_id_raw=category_id_raw,
            vehicle_id_raw=vehicle_id_raw,
        )
    )
    odometer_start = _parse_optional_odometer(odometer_start_raw, "Odometer start")
    odometer_end = _parse_optional_odometer(odometer_end_raw, "Odometer end")

    changes: dict = {}
    if trip.trip_date != trip_date:
        changes["trip_date"] = {"old": trip.trip_date.isoformat(), "new": trip_date.isoformat()}
        trip.trip_date = trip_date
    if trip.origin != origin_text:
        changes["origin"] = {"old": trip.origin, "new": origin_text}
        trip.origin = origin_text
    if trip.destination != destination_text:
        changes["destination"] = {"old": trip.destination, "new": destination_text}
        trip.destination = destination_text
    if trip.business_purpose != purpose_text:
        changes["business_purpose"] = {"old": trip.business_purpose, "new": purpose_text}
        trip.business_purpose = purpose_text
    if trip.miles != miles:
        changes["miles"] = {"old": str(trip.miles), "new": str(miles)}
        trip.miles = miles
    if trip.category_id != category_id:
        changes["category_id"] = {"old": trip.category_id, "new": category_id}
        trip.category_id = category_id
    if trip.vehicle_id != vehicle_id:
        changes["vehicle_id"] = {"old": trip.vehicle_id, "new": vehicle_id}
        trip.vehicle_id = vehicle_id
    if trip.odometer_start != odometer_start:
        changes["odometer_start"] = {"old": trip.odometer_start, "new": odometer_start}
        trip.odometer_start = odometer_start
    if trip.odometer_end != odometer_end:
        changes["odometer_end"] = {"old": trip.odometer_end, "new": odometer_end}
        trip.odometer_end = odometer_end

    trip.updated_at = datetime.now(timezone.utc)
    if changes:
        record_audit_event(
            db,
            entity_type="trip",
            entity_id=trip.id,
            action="update",
            field_changes=changes,
        )
    db.commit()
    db.refresh(trip)
    return trip


def delete_trip(db: Session, trip: Trip) -> None:
    from .receipts import delete_receipts_for_trip

    delete_receipts_for_trip(db, trip)
    trip_id = trip.id
    snapshot = {
        "trip_date": trip.trip_date.isoformat(),
        "origin": trip.origin,
        "destination": trip.destination,
        "business_purpose": trip.business_purpose,
        "miles": str(trip.miles),
        "category_id": trip.category_id,
        "vehicle_id": trip.vehicle_id,
    }
    db.delete(trip)
    record_audit_event(
        db,
        entity_type="trip",
        entity_id=trip_id,
        action="delete",
        field_changes=snapshot,
    )
    db.commit()
