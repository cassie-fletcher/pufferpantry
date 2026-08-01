from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(20))
    source_details: Mapped[str | None] = mapped_column(String(500))
    meal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    protein_type: Mapped[str | None] = mapped_column(String(50))
    cuisine: Mapped[str | None] = mapped_column(String(50))
    servings: Mapped[int] = mapped_column(Integer, default=2)
    instructions: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    calories_per_serving: Mapped[int | None] = mapped_column(Integer)
    photo_filename: Mapped[str | None] = mapped_column(String(255))
    # All source-page photos, in page order (first = title page). Fixes the
    # multi-page orphan: previously only filenames[0] was referenced and any
    # further pages were unreachable loose files on disk. photo_filename stays
    # as the title-page convenience the frontend already reads.
    photo_filenames: Mapped[list | None] = mapped_column(JSON)
    # {"<group name as printed>": "<printed yield>"} for sub-recipes, e.g.
    # {"goddess sauce": "1 3/4 cups"}. Consumed by the shopping list's
    # store-bought line (quantity = yield). JSON column per Cassie 2026-07-31.
    sub_recipe_yields: Mapped[dict | None] = mapped_column(JSON)
    dish_photo_filename: Mapped[str | None] = mapped_column(String(255))
    dish_photo_position: Mapped[str | None] = mapped_column(String(50))
    rating_cassie: Mapped[int | None] = mapped_column(Integer)
    rating_chris: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    ingredients: Mapped[list["Ingredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="Ingredient.order"
    )


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[str | None] = mapped_column(String(50))
    unit: Mapped[str | None] = mapped_column(String(50))
    order: Mapped[int] = mapped_column(Integer, default=0)
    group: Mapped[str] = mapped_column(String(100), default="Main")
    category: Mapped[str | None] = mapped_column(String(50))

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredients")
