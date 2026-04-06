from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.storage import (
    StorageAreaCreate,
    StorageAreaUpdate,
    ZoneCreate,
    ZoneUpdate,
)
from app.services import storage_service
from app.services.photo_service import (
    PHOTOS_DIR,
    extract_zone_items_from_photos,
    extract_zone_layout_from_photos,
    save_photo,
)

router = APIRouter(prefix="/storage-areas", tags=["storage"])


@router.get("")
def list_storage_areas(db: Session = Depends(get_db)):
    return storage_service.list_storage_areas(db)


@router.post("", status_code=201)
def create_storage_area(data: StorageAreaCreate, db: Session = Depends(get_db)):
    return storage_service.create_storage_area(db, data)


@router.get("/{area_id}")
def get_storage_area(area_id: int, db: Session = Depends(get_db)):
    area = storage_service.get_storage_area(db, area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Storage area not found")
    return area


@router.put("/{area_id}")
def update_storage_area(
    area_id: int, data: StorageAreaUpdate, db: Session = Depends(get_db)
):
    area = storage_service.update_storage_area(db, area_id, data)
    if not area:
        raise HTTPException(status_code=404, detail="Storage area not found")
    return area


@router.delete("/{area_id}")
def delete_storage_area(area_id: int, db: Session = Depends(get_db)):
    if not storage_service.delete_storage_area(db, area_id):
        raise HTTPException(status_code=404, detail="Storage area not found")
    return {"ok": True}


# --- Zone endpoints ---


@router.post("/{area_id}/zones", status_code=201)
def add_zone(area_id: int, data: ZoneCreate, db: Session = Depends(get_db)):
    zone = storage_service.add_zone(db, area_id, data)
    if not zone:
        raise HTTPException(status_code=404, detail="Storage area not found")
    return zone


@router.put("/{area_id}/zones/{zone_id}")
def update_zone(
    area_id: int, zone_id: int, data: ZoneUpdate, db: Session = Depends(get_db)
):
    zone = storage_service.update_zone(db, area_id, zone_id, data)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone


@router.delete("/{area_id}/zones/{zone_id}")
def delete_zone(area_id: int, zone_id: int, db: Session = Depends(get_db)):
    if not storage_service.delete_zone(db, area_id, zone_id):
        raise HTTPException(status_code=404, detail="Zone not found")
    return {"ok": True}


# --- Scan endpoints ---


@router.post("/setup-scan")
def setup_scan(
    photos: list[UploadFile] = File(...),
    area_type: str = Form("Fridge"),
):
    """Upload overview photo(s) of a storage area → Claude suggests zone layout."""
    photo_paths = []
    for photo in photos:
        filename = save_photo(photo)
        photo_paths.append(PHOTOS_DIR / filename)

    result = extract_zone_layout_from_photos(photo_paths, area_type)
    return result


@router.post("/{area_id}/zones/{zone_id}/scan")
def scan_zone(
    area_id: int,
    zone_id: int,
    photos: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Upload close-up photo(s) of a specific zone → Claude identifies items with diff."""
    result = storage_service.get_zone_with_items(db, zone_id)
    if not result:
        raise HTTPException(status_code=404, detail="Zone not found")
    zone, existing_items = result

    if zone.storage_area_id != area_id:
        raise HTTPException(status_code=404, detail="Zone not found in this area")

    filenames = []
    photo_paths = []
    for photo in photos:
        filename = save_photo(photo)
        filenames.append(filename)
        photo_paths.append(PHOTOS_DIR / filename)

    items = extract_zone_items_from_photos(
        photo_paths, zone, zone.storage_area, existing_items
    )

    # Attach photo and zone context to each item
    for item in items:
        item["photo_filename"] = filenames[0]
        item["zone_id"] = zone_id
        item["storage_location"] = zone.storage_area.area_type

    return items
