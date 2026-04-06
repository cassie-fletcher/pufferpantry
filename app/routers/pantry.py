from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.pantry import (
    PantryItemCreate,
    PantryItemList,
    PantryItemRead,
    PantryItemUpdate,
)
from app.services import pantry_service
from app.services.photo_service import (
    PHOTOS_DIR,
    extract_pantry_items_from_photos,
    save_photo,
)

router = APIRouter(prefix="/pantry", tags=["pantry"])


@router.get("", response_model=list[PantryItemList])
def list_pantry(
    location: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
):
    return pantry_service.list_items(db, storage_location=location, category=category)


@router.post("/scan")
def scan_shelf_photos(
    photos: list[UploadFile] = File(...),
    storage_location: str = Form("Fridge"),
):
    """Upload shelf photo(s) → Claude identifies items → returns draft for review.

    Does NOT save to the database — the user reviews and edits before saving.
    """
    filenames = []
    photo_paths = []
    for photo in photos:
        filename = save_photo(photo)
        filenames.append(filename)
        photo_paths.append(PHOTOS_DIR / filename)

    items = extract_pantry_items_from_photos(photo_paths, storage_location)

    # Attach the first photo filename so the frontend can show thumbnails
    for item in items:
        item["photo_filename"] = filenames[0]
        item["storage_location"] = storage_location

    return items


@router.post("/bulk", response_model=list[PantryItemRead], status_code=201)
def create_items_bulk(items: list[PantryItemCreate], db: Session = Depends(get_db)):
    """Save multiple pantry items at once (typically after scan review)."""
    return pantry_service.create_items_bulk(db, items)


@router.post("/zone-bulk", status_code=200)
def apply_zone_bulk(actions: list[dict], db: Session = Depends(get_db)):
    """Apply a batch of create/update/delete/skip actions from a zone scan review."""
    return pantry_service.apply_zone_scan_results(db, actions)


@router.get("/{item_id}", response_model=PantryItemRead)
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = pantry_service.get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pantry item not found")
    return item


@router.post("", response_model=PantryItemRead, status_code=201)
def create_item(data: PantryItemCreate, db: Session = Depends(get_db)):
    return pantry_service.create_item(db, data)


@router.put("/{item_id}", response_model=PantryItemRead)
def update_item(item_id: int, data: PantryItemUpdate, db: Session = Depends(get_db)):
    item = pantry_service.update_item(db, item_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="Pantry item not found")
    return item


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    if not pantry_service.delete_item(db, item_id):
        raise HTTPException(status_code=404, detail="Pantry item not found")
    return {"ok": True}
