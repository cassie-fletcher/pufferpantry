from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PantryItemCreate(BaseModel):
    name: str
    storage_location: str
    category: str | None = None
    quantity_level: str | None = None
    quantity: str | None = None
    unit: str | None = None
    expiration_date: str | None = None
    notes: str | None = None
    photo_filename: str | None = None
    zone_id: int | None = None


class PantryItemUpdate(BaseModel):
    name: str | None = None
    storage_location: str | None = None
    category: str | None = None
    quantity_level: str | None = None
    quantity: str | None = None
    unit: str | None = None
    expiration_date: str | None = None
    notes: str | None = None
    photo_filename: str | None = None
    zone_id: int | None = None


class PantryItemRead(BaseModel):
    id: int
    name: str
    storage_location: str
    category: str | None
    quantity_level: str | None
    quantity: str | None
    unit: str | None
    expiration_date: str | None
    notes: str | None
    photo_filename: str | None
    zone_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PantryItemList(BaseModel):
    """Lightweight schema for the list view — no notes."""

    id: int
    name: str
    storage_location: str
    category: str | None
    quantity_level: str | None
    quantity: str | None
    unit: str | None
    expiration_date: str | None
    zone_id: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PantryScanResult(BaseModel):
    """A single item extracted from a kitchen photo by Claude, before saving."""

    name: str
    quantity_level: str | None = "Half"
    category: str | None = None
    confidence_note: str | None = None
