from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .audit import record_audit_event
from .exceptions import OdometerOrderingError, ValidationError, VehicleHasTripsError
from .models import Trip, Vehicle, VehicleOdometerReading


def _validate_odometer_pair(start: Optional[int], end: Optional[int]) -> None:
    if start is not None and start < 0:
        raise ValidationError("Start-of-year odometer cannot be negative.")
    if end is not None and end < 0:
        raise ValidationError("End-of-year odometer cannot be negative.")
    if start is not None and end is not None and end < start:
        raise OdometerOrderingError(
            "End-of-year odometer must be greater than or equal to start-of-year odometer."
        )


def list_vehicles(db: Session, *, include_archived: bool = False) -> list[Vehicle]:
    query = db.query(Vehicle).order_by(Vehicle.display_name.asc())
    if not include_archived:
        query = query.filter(Vehicle.is_archived.is_(False))
    return query.all()


def get_vehicle(db: Session, vehicle_id: int) -> Optional[Vehicle]:
    return db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()


def vehicle_has_trips(db: Session, vehicle_id: int) -> bool:
    return db.query(Trip.id).filter(Trip.vehicle_id == vehicle_id).first() is not None


def create_vehicle(db: Session, *, display_name: str, description: str = "") -> Vehicle:
    name = display_name.strip()
    if not name:
        raise ValidationError("Display name is required.")
    vehicle = Vehicle(display_name=name, description=description.strip())
    db.add(vehicle)
    db.flush()
    record_audit_event(
        db,
        entity_type="vehicle",
        entity_id=vehicle.id,
        action="create",
        field_changes={
            "display_name": name,
            "description": description.strip(),
        },
    )
    db.commit()
    db.refresh(vehicle)
    return vehicle


def update_vehicle(
    db: Session,
    vehicle: Vehicle,
    *,
    display_name: str,
    description: str,
) -> Vehicle:
    name = display_name.strip()
    if not name:
        raise ValidationError("Display name is required.")

    changes: dict[str, dict[str, str]] = {}
    if vehicle.display_name != name:
        changes["display_name"] = {"old": vehicle.display_name, "new": name}
        vehicle.display_name = name
    new_description = description.strip()
    if vehicle.description != new_description:
        changes["description"] = {"old": vehicle.description, "new": new_description}
        vehicle.description = new_description

    vehicle.updated_at = datetime.now(timezone.utc)
    if changes:
        record_audit_event(
            db,
            entity_type="vehicle",
            entity_id=vehicle.id,
            action="update",
            field_changes=changes,
        )
    db.commit()
    db.refresh(vehicle)
    return vehicle


def archive_vehicle(db: Session, vehicle: Vehicle) -> Vehicle:
    if vehicle.is_archived:
        return vehicle
    vehicle.is_archived = True
    vehicle.updated_at = datetime.now(timezone.utc)
    record_audit_event(
        db,
        entity_type="vehicle",
        entity_id=vehicle.id,
        action="archive",
        field_changes={"is_archived": {"old": False, "new": True}},
    )
    db.commit()
    db.refresh(vehicle)
    return vehicle


def delete_vehicle(db: Session, vehicle: Vehicle) -> None:
    if vehicle_has_trips(db, vehicle.id):
        raise VehicleHasTripsError(
            "This vehicle has associated trips and cannot be deleted. Archive it instead."
        )
    vehicle_id = vehicle.id
    db.delete(vehicle)
    record_audit_event(
        db,
        entity_type="vehicle",
        entity_id=vehicle_id,
        action="delete",
        field_changes={"display_name": vehicle.display_name},
    )
    db.commit()


def get_odometer_reading(
    db: Session, vehicle_id: int, tax_year: int
) -> Optional[VehicleOdometerReading]:
    return (
        db.query(VehicleOdometerReading)
        .filter(
            VehicleOdometerReading.vehicle_id == vehicle_id,
            VehicleOdometerReading.tax_year == tax_year,
        )
        .first()
    )


def list_odometer_readings(db: Session, vehicle_id: int) -> list[VehicleOdometerReading]:
    return (
        db.query(VehicleOdometerReading)
        .filter(VehicleOdometerReading.vehicle_id == vehicle_id)
        .order_by(VehicleOdometerReading.tax_year.desc())
        .all()
    )


def _parse_odometer_value(raw: Optional[str], label: str) -> Optional[int]:
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValidationError(f"{label} must be a whole number.") from exc
    return value


def upsert_odometer_reading(
    db: Session,
    vehicle: Vehicle,
    *,
    tax_year: int,
    odometer_year_start: Optional[str],
    odometer_year_end: Optional[str],
) -> VehicleOdometerReading:
    if tax_year < 2000 or tax_year > 2100:
        raise ValidationError("Tax year must be between 2000 and 2100.")

    start = _parse_odometer_value(odometer_year_start, "Start-of-year odometer")
    end = _parse_odometer_value(odometer_year_end, "End-of-year odometer")
    _validate_odometer_pair(start, end)

    reading = get_odometer_reading(db, vehicle.id, tax_year)
    changes: dict[str, dict[str, Optional[int]]] = {}

    if reading is None:
        reading = VehicleOdometerReading(
            vehicle_id=vehicle.id,
            tax_year=tax_year,
            odometer_year_start=start,
            odometer_year_end=end,
        )
        db.add(reading)
        db.flush()
        record_audit_event(
            db,
            entity_type="vehicle_odometer",
            entity_id=reading.id,
            action="create",
            field_changes={
                "vehicle_id": vehicle.id,
                "tax_year": tax_year,
                "odometer_year_start": start,
                "odometer_year_end": end,
            },
        )
    else:
        if reading.odometer_year_start != start:
            changes["odometer_year_start"] = {
                "old": reading.odometer_year_start,
                "new": start,
            }
            reading.odometer_year_start = start
        if reading.odometer_year_end != end:
            changes["odometer_year_end"] = {"old": reading.odometer_year_end, "new": end}
            reading.odometer_year_end = end
        reading.updated_at = datetime.now(timezone.utc)
        if changes:
            record_audit_event(
                db,
                entity_type="vehicle_odometer",
                entity_id=reading.id,
                action="update",
                field_changes={"tax_year": tax_year, **changes},
            )

    db.commit()
    db.refresh(reading)
    return reading
