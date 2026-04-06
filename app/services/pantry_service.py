from sqlalchemy.orm import Session

from app.models.pantry import PantryItem
from app.schemas.pantry import PantryItemCreate, PantryItemUpdate


def list_items(
    db: Session,
    storage_location: str | None = None,
    category: str | None = None,
) -> list[PantryItem]:
    query = db.query(PantryItem)
    if storage_location:
        query = query.filter(PantryItem.storage_location == storage_location)
    if category:
        query = query.filter(PantryItem.category == category)
    return query.order_by(PantryItem.storage_location, PantryItem.name).all()


def get_item(db: Session, item_id: int) -> PantryItem | None:
    return db.query(PantryItem).filter(PantryItem.id == item_id).first()


def create_item(db: Session, data: PantryItemCreate) -> PantryItem:
    item = PantryItem(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def create_items_bulk(db: Session, items: list[PantryItemCreate]) -> list[PantryItem]:
    db_items = [PantryItem(**item.model_dump()) for item in items]
    db.add_all(db_items)
    db.commit()
    for item in db_items:
        db.refresh(item)
    return db_items


def update_item(db: Session, item_id: int, data: PantryItemUpdate) -> PantryItem | None:
    item = get_item(db, item_id)
    if not item:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


def delete_item(db: Session, item_id: int) -> bool:
    item = db.query(PantryItem).filter(PantryItem.id == item_id).first()
    if not item:
        return False
    db.delete(item)
    db.commit()
    return True


def list_items_by_zone(db: Session, zone_id: int) -> list[PantryItem]:
    return (
        db.query(PantryItem)
        .filter(PantryItem.zone_id == zone_id)
        .order_by(PantryItem.name)
        .all()
    )


def apply_zone_scan_results(db: Session, actions: list[dict]) -> list[dict]:
    """Process a batch of create/update/delete/skip actions from a zone scan review.

    Each action dict has: action ("create"|"update"|"delete"|"skip"),
    item_id (for update/delete), data (dict for create/update).
    """
    results = []
    for action in actions:
        act = action.get("action", "skip")
        item_id = action.get("item_id")
        data = action.get("data", {})

        if act == "create":
            item = PantryItem(**data)
            db.add(item)
            db.flush()
            results.append({"action": "created", "item_id": item.id, "name": data.get("name")})

        elif act == "update" and item_id:
            item = db.query(PantryItem).filter(PantryItem.id == item_id).first()
            if item:
                for field, value in data.items():
                    setattr(item, field, value)
                results.append({"action": "updated", "item_id": item_id, "name": item.name})

        elif act == "delete" and item_id:
            item = db.query(PantryItem).filter(PantryItem.id == item_id).first()
            if item:
                name = item.name
                db.delete(item)
                results.append({"action": "deleted", "item_id": item_id, "name": name})

        # "skip" actions are ignored

    db.commit()
    return results
