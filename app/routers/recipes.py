import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.ocr import read_image
from app.ocr.combine import combine
from app.schemas.recipe import (
    RecipeCreate,
    RecipeList,
    RecipeRead,
    RecipeUpdate,
    UrlExtractRequest,
)
from app.services import recipe_service
from app.services.nutrition_service import calculate_recipe_nutrition
# extract_recipe_from_photos (the Claude photo path) is deliberately no
# longer imported: photo extraction runs the local ensemble. The Claude code
# stays in photo_service as a dormant backup, wired to nothing.
from app.services.photo_service import PHOTOS_DIR, save_photo
from app.services.shopping_service import generate_shopping_list
from app.services.url_service import extract_recipe_from_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recipes", tags=["recipes"])

# The local voters, in the order they are attempted. Adding a voter later =
# one entry here (and its priority in app/ocr/combine.py VOTER_PRIORITY).
LOCAL_METHODS: tuple[str, ...] = ("apple_vision", "tesseract")


@router.get("", response_model=list[RecipeList])
def list_recipes(db: Session = Depends(get_db)):
    return recipe_service.list_recipes(db)


@router.post("/extract-from-photo")
def extract_from_photo(photos: list[UploadFile] = File(...)):
    """Upload cookbook photos → the LOCAL ensemble reads them → data for review.

    Accepts multiple files for recipes spanning pages: upload order is page
    order, first file is the title page (the GUI's starred page). Each local
    method reads independently; the ROVER combiner merges the readings and
    attaches per-ingredient confidence flags (agreement=high, single
    voter=medium, disagreement=low) that the review form renders.

    NO API call — per the owner's rule the extraction is fully local
    (`claude` remains a deliberately dormant backup, wired to nothing).
    A method that fails (e.g. the Vision binary not built) is skipped with a
    log line; the endpoint fails only if EVERY method fails.
    """
    filenames = []
    photo_paths = []
    for photo in photos:
        filename = save_photo(photo)
        filenames.append(filename)
        photo_paths.append(PHOTOS_DIR / filename)

    readings = []
    errors: list[str] = []
    for method in LOCAL_METHODS:
        try:
            readings.append(read_image(photo_paths, method=method))
        except Exception as exc:  # noqa: BLE001 - per-voter isolation is the point
            logger.warning("photo extraction: method %s failed: %s", method, exc)
            errors.append(f"{method}: {exc}")
    if not readings:
        raise HTTPException(
            status_code=500,
            detail="All local extraction methods failed. " + " | ".join(errors),
        )

    combined = combine(readings)
    recipe = combined.schema.model_dump()
    recipe["photo_filename"] = filenames[0]     # title page (starred first)
    recipe["photo_filenames"] = filenames       # ALL pages, in order — no orphans
    recipe.setdefault("meal_type", "dinner")

    # Return array — the frontend's review flow expects one; kept as an array
    # for compatibility with the old multi-recipe Claude response shape.
    return [recipe]


@router.post("/extract-from-url")
def extract_from_url(body: UrlExtractRequest):
    """Fetch a recipe URL → Claude extracts the recipe → returns data for review.

    A missing or blank `url` is rejected by the request model with a 422
    (previously a hand-rolled 400 on a raw dict body).
    """
    extracted = extract_recipe_from_url(body.url)
    extracted.setdefault("meal_type", "dinner")
    extracted["source_type"] = "website"
    extracted["source_details"] = body.url
    return extracted


@router.post("/shopping-list")
def create_shopping_list(body: dict, db: Session = Depends(get_db)):
    """Generate a consolidated, categorized shopping list from selected recipes."""
    recipe_ids = body.get("recipe_ids", [])
    if not recipe_ids:
        return {"categories": []}

    recipes_data = []
    for rid in recipe_ids:
        recipe = recipe_service.get_recipe(db, rid)
        if recipe:
            recipes_data.append({
                "title": recipe.title,
                "ingredients": [
                    {"name": ing.name, "amount": ing.amount, "unit": ing.unit, "category": ing.category}
                    for ing in recipe.ingredients
                ],
            })

    return generate_shopping_list(recipes_data)


@router.get("/{recipe_id}/nutrition")
def get_recipe_nutrition(recipe_id: int, db: Session = Depends(get_db)):
    """Calculate nutrition facts for a recipe from USDA data."""
    recipe = recipe_service.get_recipe(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    ingredients = [
        {"name": ing.name, "amount": ing.amount, "unit": ing.unit}
        for ing in recipe.ingredients
    ]
    return calculate_recipe_nutrition(ingredients, recipe.servings)


@router.get("/{recipe_id}", response_model=RecipeRead)
def get_recipe(recipe_id: int, db: Session = Depends(get_db)):
    recipe = recipe_service.get_recipe(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return recipe


@router.post("", response_model=RecipeRead, status_code=201)
def create_recipe(data: RecipeCreate, db: Session = Depends(get_db)):
    return recipe_service.create_recipe(db, data)


@router.put("/{recipe_id}", response_model=RecipeRead)
def update_recipe(recipe_id: int, data: RecipeUpdate, db: Session = Depends(get_db)):
    recipe = recipe_service.update_recipe(db, recipe_id, data)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return recipe


@router.post("/{recipe_id}/dish-photo", response_model=RecipeRead)
def upload_dish_photo(recipe_id: int, photo: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a photo of the finished dish for a recipe."""
    recipe = recipe_service.get_recipe(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    filename = save_photo(photo)
    recipe.dish_photo_filename = filename
    db.commit()
    db.refresh(recipe)
    return recipe


@router.put("/{recipe_id}/dish-photo-position")
def update_dish_photo_position(recipe_id: int, body: dict, db: Session = Depends(get_db)):
    """Save the cropping position for a dish photo."""
    recipe = recipe_service.get_recipe(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe.dish_photo_position = body.get("position", "center")
    db.commit()
    return {"ok": True}


@router.delete("/{recipe_id}")
def delete_recipe(recipe_id: int, db: Session = Depends(get_db)):
    if not recipe_service.delete_recipe(db, recipe_id):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return {"ok": True}
