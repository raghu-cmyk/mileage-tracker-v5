"""Receipt image capture, storage, and ownership-gated access."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .audit import record_audit_event
from .database import DATA_DIR
from .exceptions import ValidationError
from .models import Receipt, Trip

MAX_RECEIPT_BYTES = 10 * 1024 * 1024


def _receipts_dir() -> Path:
    return DATA_DIR / "receipts"

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def _sanitize_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^\w.\-]", "_", base)
    return cleaned[:200] if cleaned else "receipt"


def _resolve_content_type(content_type: Optional[str], filename: str) -> str:
    if content_type and content_type.lower() in ALLOWED_CONTENT_TYPES:
        return content_type.lower()
    ext = Path(filename).suffix.lower()
    for mime, suffix in ALLOWED_CONTENT_TYPES.items():
        if suffix == ext or (ext == ".jpeg" and mime == "image/jpeg"):
            return mime
    raise ValidationError(
        "Unsupported file type. Accepted formats: JPEG, PNG, WebP, HEIC."
    )


def list_receipts_for_trip(db: Session, trip_id: int) -> list[Receipt]:
    return (
        db.query(Receipt)
        .filter(Receipt.trip_id == trip_id)
        .order_by(Receipt.uploaded_at.asc(), Receipt.id.asc())
        .all()
    )


def get_receipt(db: Session, receipt_id: int) -> Optional[Receipt]:
    return db.query(Receipt).filter(Receipt.id == receipt_id).one_or_none()


def receipt_file_path(receipt: Receipt) -> Path:
    return DATA_DIR / receipt.storage_path


def store_receipt(
    db: Session,
    trip: Trip,
    *,
    filename: str,
    content_type: Optional[str],
    data: bytes,
) -> Receipt:
    if not data:
        raise ValidationError("Receipt file is empty.")
    if len(data) > MAX_RECEIPT_BYTES:
        raise ValidationError("Receipt exceeds the 10 MB size limit.")

    resolved_type = _resolve_content_type(content_type, filename)
    safe_name = _sanitize_filename(filename)
    content_hash = hashlib.sha256(data).hexdigest()

    receipt = Receipt(
        trip_id=trip.id,
        original_filename=safe_name,
        content_type=resolved_type,
        byte_size=len(data),
        content_hash=content_hash,
        storage_path="",
        uploaded_at=datetime.now(timezone.utc),
    )
    db.add(receipt)
    db.flush()

    trip_dir = _receipts_dir() / str(trip.id)
    trip_dir.mkdir(parents=True, exist_ok=True)
    suffix = ALLOWED_CONTENT_TYPES.get(resolved_type, Path(safe_name).suffix or ".bin")
    stored_name = f"{receipt.id}_{Path(safe_name).stem}{suffix}"
    file_path = trip_dir / stored_name
    file_path.write_bytes(data)

    receipt.storage_path = str(file_path.relative_to(DATA_DIR))
    record_audit_event(
        db,
        entity_type="receipt",
        entity_id=receipt.id,
        action="create",
        field_changes={
            "trip_id": trip.id,
            "original_filename": safe_name,
            "content_type": resolved_type,
            "byte_size": len(data),
            "content_hash": content_hash,
        },
    )
    db.commit()
    db.refresh(receipt)
    return receipt


def delete_receipt(db: Session, receipt: Receipt) -> None:
    receipt_id = receipt.id
    trip_id = receipt.trip_id
    snapshot = {
        "trip_id": trip_id,
        "original_filename": receipt.original_filename,
        "content_type": receipt.content_type,
        "byte_size": receipt.byte_size,
        "content_hash": receipt.content_hash,
    }
    file_path = receipt_file_path(receipt)
    db.delete(receipt)
    record_audit_event(
        db,
        entity_type="receipt",
        entity_id=receipt_id,
        action="delete",
        field_changes=snapshot,
    )
    db.commit()
    if file_path.exists():
        file_path.unlink()


def delete_receipts_for_trip(db: Session, trip: Trip) -> None:
    for receipt in list(list_receipts_for_trip(db, trip.id)):
        delete_receipt(db, receipt)


def receipt_reference_summary(receipts: list[Receipt]) -> str:
    if not receipts:
        return ""
    parts = [f"{r.id}:{r.original_filename}" for r in receipts]
    return f"{len(receipts)} ({'; '.join(parts)})"


def read_receipt_bytes(receipt: Receipt) -> Optional[bytes]:
    path = receipt_file_path(receipt)
    if not path.is_file():
        return None
    return path.read_bytes()
