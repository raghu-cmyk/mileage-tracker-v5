import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import AuditEvent


def record_audit_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    field_changes: Optional[dict[str, Any]] = None,
) -> AuditEvent:
    event = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        field_changes=json.dumps(field_changes or {}, sort_keys=True),
    )
    db.add(event)
    return event
