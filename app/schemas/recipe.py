from datetime import datetime

from pydantic import BaseModel, ConfigDict


# --- Ingredient schemas ---


class IngredientCreate(BaseModel):
    name: str
    amount: str | None = None
    unit: str | None = None
    order: int = 0
    group: str = "Main"
    category: str | None = None


class IngredientRead(IngredientCreate):
    id: int

    model_config = ConfigDict(from_attributes=True)


# --- Recipe schemas ---


class RecipeCreate(BaseModel):
    title: str
    meal_type: str
    protein_type: str | None = None
    cuisine: str | None = None
    servings: int = 2
    source_type: str | None = None
    source_details: str | None = None
    instructions: str | None = None
    notes: str | None = None
    calories_per_serving: int | None = None
    photo_filename: str | None = None
    dish_photo_filename: str | None = None
    dish_photo_position: str | None = None
    rating_cassie: int | None = None
    rating_chris: int | None = None
    ingredients: list[IngredientCreate] = []


class RecipeUpdate(BaseModel):
    title: str | None = None
    meal_type: str | None = None
    protein_type: str | None = None
    cuisine: str | None = None
    servings: int | None = None
    source_type: str | None = None
    source_details: str | None = None
    instructions: str | None = None
    notes: str | None = None
    calories_per_serving: int | None = None
    photo_filename: str | None = None
    dish_photo_filename: str | None = None
    dish_photo_position: str | None = None
    rating_cassie: int | None = None
    rating_chris: int | None = None
    ingredients: list[IngredientCreate] | None = None


class RecipeRead(BaseModel):
    id: int
    title: str
    meal_type: str
    protein_type: str | None
    cuisine: str | None
    servings: int
    source_type: str | None
    source_details: str | None
    instructions: str | None
    notes: str | None
    calories_per_serving: int | None
    photo_filename: str | None
    dish_photo_filename: str | None
    dish_photo_position: str | None
    rating_cassie: int | None
    rating_chris: int | None
    created_at: datetime
    updated_at: datetime
    ingredients: list[IngredientRead]

    model_config = ConfigDict(from_attributes=True)


class RecipeList(BaseModel):
    """Lightweight schema for the list view — no ingredients or instructions."""

    id: int
    title: str
    meal_type: str
    protein_type: str | None
    cuisine: str | None
    calories_per_serving: int | None
    dish_photo_filename: str | None
    dish_photo_position: str | None
    rating_cassie: int | None
    rating_chris: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PhotoExtractResult(BaseModel):
    """Data extracted from a cookbook photo by Claude, before saving to the database."""

    title: str
    meal_type: str = "dinner"
    servings: int = 2
    calories_per_serving: int | None = None
    instructions: str | None = None
    notes: str | None = None
    ingredients: list[IngredientCreate] = []
    photo_filename: str
