from sqlalchemy.orm import Session, joinedload

from app.models.recipe import Ingredient, Recipe
from app.services.nutrition_service import (
    compute_nutrition_facts,
    derive_nutrition_view,
)
from app.schemas.recipe import RecipeCreate, RecipeUpdate


def list_recipes(db: Session) -> list[Recipe]:
    return db.query(Recipe).order_by(Recipe.created_at.desc()).all()


def get_recipe(db: Session, recipe_id: int) -> Recipe | None:
    return (
        db.query(Recipe)
        .options(joinedload(Recipe.ingredients))
        .filter(Recipe.id == recipe_id)
        .first()
    )


def create_recipe(db: Session, data: RecipeCreate) -> Recipe:
    recipe = Recipe(
        title=data.title,
        meal_type=data.meal_type,
        protein_type=data.protein_type,
        cuisine=data.cuisine,
        servings=data.servings,
        servings_range=data.servings_range,
        source_type=data.source_type,
        source_details=data.source_details,
        instructions=data.instructions,
        notes=data.notes,
        calories_per_serving=data.calories_per_serving,
        photo_filename=data.photo_filename,
        photo_filenames=data.photo_filenames or None,
        sub_recipe_yields=data.sub_recipe_yields or None,
        dish_photo_filename=data.dish_photo_filename,
    )
    for ing in data.ingredients:
        recipe.ingredients.append(
            Ingredient(name=ing.name, amount=ing.amount, unit=ing.unit, order=ing.order, group=ing.group, category=ing.category)
        )
    _compute_and_store_nutrition(recipe)
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


def _compute_and_store_nutrition(recipe: Recipe) -> None:
    """Compute nutrition ONCE and store it on the recipe.

    Called at create and after ingredient edits — never from the view path.
    When the computed calories come out nonzero, our count also becomes the
    displayed calories_per_serving (Cassie: website calorie claims are
    unreliable; we show our work, so our number wins). A zero/empty result
    (e.g. the USDA quota was exhausted at save time) is stored as-is; the
    nutrition endpoint lazily recomputes when it sees stored calories of
    zero.
    """
    ingredients = [
        {"name": i.name, "amount": i.amount, "unit": i.unit}
        for i in recipe.ingredients
    ]
    facts = compute_nutrition_facts(ingredients)
    recipe.nutrition = facts
    view = derive_nutrition_view(facts, recipe.servings or 2)
    computed = round(view["per_serving"]["calories"])
    if computed > 0:
        recipe.calories_per_serving = computed


def update_recipe(db: Session, recipe_id: int, data: RecipeUpdate) -> Recipe | None:
    recipe = get_recipe(db, recipe_id)
    if not recipe:
        return None

    # Update scalar fields that were provided
    update_data = data.model_dump(exclude_unset=True)
    ingredients_data = update_data.pop("ingredients", None)

    for field, value in update_data.items():
        setattr(recipe, field, value)

    # Replace ingredients if provided
    if ingredients_data is not None:
        recipe.ingredients.clear()
        for ing in ingredients_data:
            recipe.ingredients.append(
                Ingredient(name=ing["name"], amount=ing["amount"], unit=ing["unit"], order=ing["order"], group=ing.get("group", "Main"), category=ing.get("category"))
            )
        # Ingredients changed -> silently recompute the stored nutrition
        # (Cassie's rule: once per change, never on view).
        _compute_and_store_nutrition(recipe)

    db.commit()
    db.refresh(recipe)
    return recipe


def delete_recipe(db: Session, recipe_id: int) -> bool:
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        return False
    db.delete(recipe)
    db.commit()
    return True
