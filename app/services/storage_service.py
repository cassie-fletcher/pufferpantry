import json

from sqlalchemy.orm import Session, joinedload

from app.models.pantry import PantryItem
from app.models.storage import StorageArea, StorageZone
from app.schemas.storage import (
    StorageAreaCreate,
    StorageAreaUpdate,
    ZoneCreate,
    ZoneUpdate,
)


def list_storage_areas(db: Session) -> list[dict]:
    areas = (
        db.query(StorageArea)
        .options(joinedload(StorageArea.zones).joinedload(StorageZone.items))
        .order_by(StorageArea.created_at)
        .all()
    )
    return [_area_to_dict(area) for area in areas]


def get_storage_area(db: Session, area_id: int) -> dict | None:
    area = (
        db.query(StorageArea)
        .options(joinedload(StorageArea.zones).joinedload(StorageZone.items))
        .filter(StorageArea.id == area_id)
        .first()
    )
    if not area:
        return None
    return _area_to_dict(area)


def create_storage_area(db: Session, data: StorageAreaCreate) -> dict:
    area = StorageArea(
        name=data.name,
        area_type=data.area_type,
        notes=data.notes,
    )
    for i, zone_data in enumerate(data.zones):
        zone = StorageZone(
            name=zone_data.name,
            zone_type=zone_data.zone_type,
            typical_categories=json.dumps(zone_data.typical_categories),
            typical_container_types=json.dumps(zone_data.typical_container_types),
            scan_strategy=zone_data.scan_strategy,
            position_order=zone_data.position_order if zone_data.position_order else i,
            notes=zone_data.notes,
        )
        area.zones.append(zone)
    db.add(area)
    db.commit()
    db.refresh(area)
    return _area_to_dict(area)


def update_storage_area(
    db: Session, area_id: int, data: StorageAreaUpdate
) -> dict | None:
    area = db.query(StorageArea).filter(StorageArea.id == area_id).first()
    if not area:
        return None
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(area, field, value)
    db.commit()
    db.refresh(area)
    return get_storage_area(db, area_id)


def delete_storage_area(db: Session, area_id: int) -> bool:
    area = db.query(StorageArea).filter(StorageArea.id == area_id).first()
    if not area:
        return False
    db.delete(area)
    db.commit()
    return True


def add_zone(db: Session, area_id: int, data: ZoneCreate) -> dict | None:
    area = db.query(StorageArea).filter(StorageArea.id == area_id).first()
    if not area:
        return None
    zone = StorageZone(
        storage_area_id=area_id,
        name=data.name,
        zone_type=data.zone_type,
        typical_categories=json.dumps(data.typical_categories),
        typical_container_types=json.dumps(data.typical_container_types),
        scan_strategy=data.scan_strategy,
        position_order=data.position_order,
        notes=data.notes,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return _zone_to_dict(zone)


def update_zone(
    db: Session, area_id: int, zone_id: int, data: ZoneUpdate
) -> dict | None:
    zone = (
        db.query(StorageZone)
        .filter(StorageZone.id == zone_id, StorageZone.storage_area_id == area_id)
        .first()
    )
    if not zone:
        return None
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field in ("typical_categories", "typical_container_types"):
            setattr(zone, field, json.dumps(value))
        else:
            setattr(zone, field, value)
    db.commit()
    db.refresh(zone)
    return _zone_to_dict(zone)


def delete_zone(db: Session, area_id: int, zone_id: int) -> bool:
    zone = (
        db.query(StorageZone)
        .filter(StorageZone.id == zone_id, StorageZone.storage_area_id == area_id)
        .first()
    )
    if not zone:
        return False
    db.delete(zone)
    db.commit()
    return True


def get_zone_with_items(
    db: Session, zone_id: int
) -> tuple[StorageZone, list[PantryItem]] | None:
    zone = (
        db.query(StorageZone)
        .options(joinedload(StorageZone.storage_area))
        .filter(StorageZone.id == zone_id)
        .first()
    )
    if not zone:
        return None
    items = (
        db.query(PantryItem)
        .filter(PantryItem.zone_id == zone_id)
        .order_by(PantryItem.name)
        .all()
    )
    return zone, items


def _zone_to_dict(zone: StorageZone) -> dict:
    return {
        "id": zone.id,
        "storage_area_id": zone.storage_area_id,
        "name": zone.name,
        "zone_type": zone.zone_type,
        "typical_categories": json.loads(zone.typical_categories or "[]"),
        "typical_container_types": json.loads(zone.typical_container_types or "[]"),
        "scan_strategy": zone.scan_strategy,
        "position_order": zone.position_order,
        "setup_photo_filename": zone.setup_photo_filename,
        "last_scanned_at": zone.last_scanned_at.isoformat() if zone.last_scanned_at else None,
        "item_count": len(zone.items) if hasattr(zone, "items") and zone.items else 0,
        "notes": zone.notes,
    }


def _area_to_dict(area: StorageArea) -> dict:
    return {
        "id": area.id,
        "name": area.name,
        "area_type": area.area_type,
        "notes": area.notes,
        "created_at": area.created_at.isoformat() if area.created_at else None,
        "zones": [_zone_to_dict(z) for z in area.zones],
    }
