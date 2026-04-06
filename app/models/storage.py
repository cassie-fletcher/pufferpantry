from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class StorageArea(Base):
    __tablename__ = "storage_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    area_type: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    zones: Mapped[list["StorageZone"]] = relationship(
        back_populates="storage_area",
        cascade="all, delete-orphan",
        order_by="StorageZone.position_order",
    )


class StorageZone(Base):
    __tablename__ = "storage_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storage_area_id: Mapped[int] = mapped_column(
        ForeignKey("storage_areas.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(50), nullable=False)
    typical_categories: Mapped[str | None] = mapped_column(Text)
    typical_container_types: Mapped[str | None] = mapped_column(Text)
    scan_strategy: Mapped[str] = mapped_column(String(30), default="full_rescan")
    position_order: Mapped[int] = mapped_column(Integer, default=0)
    setup_photo_filename: Mapped[str | None] = mapped_column(String(255))
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    storage_area: Mapped["StorageArea"] = relationship(back_populates="zones")
    items: Mapped[list["PantryItem"]] = relationship(back_populates="zone")


# Avoid circular import — PantryItem imported at module level in pantry.py
# The string "PantryItem" in the relationship is resolved by SQLAlchemy at runtime
