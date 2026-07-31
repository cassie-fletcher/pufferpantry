from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


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


class UrlExtractRequest(BaseModel):
    """Request body for POST /recipes/extract-from-url."""

    url: str

    @field_validator("url")
    @classmethod
    def _url_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url must not be blank")
        return v


class UrlExtractResult(BaseModel):
    """Data extracted from a recipe web page by Claude, before user review.

    Mirrors the shape promised by url_service.EXTRACTION_PROMPT. Unlike
    PhotoExtractResult it keeps `protein_type` and `cuisine` (the URL prompt
    asks for them and the review form passes them through) and has no
    `photo_filename` — a URL import only has an optional downloaded hero
    image (`dish_photo_filename`).
    """

    title: str
    servings: int = 2
    protein_type: str | None = None
    cuisine: str | None = None
    calories_per_serving: int | None = None
    instructions: str | None = None
    notes: str | None = None
    ingredients: list[IngredientCreate] = []
    dish_photo_filename: str | None = None

    @field_validator("ingredients", mode="before")
    @classmethod
    def _stringify_numeric_amounts(cls, v):
        """Coerce numeric ingredient amounts to strings.

        The prompt asks for string amounts ('"amount": "4"'), but the model
        occasionally returns a bare number. `IngredientCreate.amount` is a
        string (Pydantic v2 does not coerce int -> str), so stringify here
        rather than failing the whole extraction on that harmless drift.
        """
        if isinstance(v, list):
            coerced = []
            for ing in v:
                if (
                    isinstance(ing, dict)
                    and isinstance(ing.get("amount"), (int, float))
                    and not isinstance(ing.get("amount"), bool)
                ):
                    ing = {**ing, "amount": str(ing["amount"])}
                coerced.append(ing)
            return coerced
        return v
