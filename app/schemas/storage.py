from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ZoneCreate(BaseModel):
    name: str
    zone_type: str
    typical_categories: list[str] = []
    typical_container_types: list[str] = []
    scan_strategy: str = "full_rescan"
    position_order: int = 0
    notes: str | None = None


class ZoneUpdate(BaseModel):
    name: str | None = None
    zone_type: str | None = None
    typical_categories: list[str] | None = None
    typical_container_types: list[str] | None = None
    scan_strategy: str | None = None
    position_order: int | None = None
    notes: str | None = None


class ZoneRead(BaseModel):
    id: int
    storage_area_id: int
    name: str
    zone_type: str
    typical_categories: list[str]
    typical_container_types: list[str]
    scan_strategy: str
    position_order: int
    setup_photo_filename: str | None
    last_scanned_at: datetime | None
    item_count: int = 0
    notes: str | None

    model_config = ConfigDict(from_attributes=True)


class StorageAreaCreate(BaseModel):
    name: str
    area_type: str
    notes: str | None = None
    zones: list[ZoneCreate] = []


class StorageAreaUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None


class StorageAreaRead(BaseModel):
    id: int
    name: str
    area_type: str
    notes: str | None
    zones: list[ZoneRead]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SetupScanResult(BaseModel):
    """Claude's suggested zone breakdown from an overview photo."""

    suggested_zones: list[ZoneCreate]
    description: str
