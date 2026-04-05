from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.recipe import (
    RecipeCreate,
    RecipeList,
    RecipeRead,
    RecipeUpdate,
)
from app.services import recipe_service
from app.services.nutrition_service import calculate_recipe_nutrition
from app.services.photo_service import (
    PHOTOS_DIR,
    extract_recipe_from_photos,
    save_photo,
)
from app.services.shopping_service import generate_shopping_list
from app.services.url_service import extract_recipe_from_url

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("", response_model=list[RecipeList])
def list_recipes(db: Session = Depends(get_db)):
    return recipe_service.list_recipes(db)


@router.post("/extract-from-photo")
def extract_from_photo(photos: list[UploadFile] = File(...)):
    """Upload one or more cookbook photos → Claude extracts the recipe → returns data for review.

    Accepts multiple files for recipes that span multiple pages.
    """
    filenames = []
    photo_paths = []
    for photo in photos:
        filename = save_photo(photo)
        filenames.append(filename)
        photo_paths.append(PHOTOS_DIR / filename)

    result = extract_recipe_from_photos(photo_paths)

    # Claude now returns an array of recipes (may be 1 or more)
    recipes = result if isinstance(result, list) else [result]

    for recipe in recipes:
        recipe["photo_filename"] = filenames[0]
        recipe.setdefault("meal_type", "dinner")

    # Return array — frontend handles single or multiple
    return recipes


@router.post("/extract-from-url")
def extract_from_url(body: dict):
    """Fetch a recipe URL → Claude extracts the recipe → returns data for review."""
    url = body.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    extracted = extract_recipe_from_url(url)
    extracted.setdefault("meal_type", "dinner")
    extracted["source_type"] = "website"
    extracted["source_details"] = url
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
