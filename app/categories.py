from sqlalchemy.orm import Session

from .models import TripCategory

DEFAULT_CATEGORIES = [
    {"code": "business", "display_name": "Business", "is_deductible": True},
    {"code": "medical", "display_name": "Medical", "is_deductible": True},
    {"code": "moving", "display_name": "Moving", "is_deductible": True},
    {"code": "charitable", "display_name": "Charitable", "is_deductible": True},
    {"code": "personal", "display_name": "Personal", "is_deductible": False},
]


def seed_trip_categories(db: Session) -> None:
    existing = {row[0] for row in db.query(TripCategory.code).all()}
    for item in DEFAULT_CATEGORIES:
        if item["code"] not in existing:
            db.add(TripCategory(**item))
    db.commit()


def list_categories(db: Session) -> list[TripCategory]:
    return db.query(TripCategory).order_by(TripCategory.display_name.asc()).all()


def get_category(db: Session, category_id: int) -> TripCategory | None:
    return db.query(TripCategory).filter(TripCategory.id == category_id).first()
