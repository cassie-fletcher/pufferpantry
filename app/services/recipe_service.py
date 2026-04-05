from sqlalchemy.orm import Session, joinedload

from app.models.recipe import Ingredient, Recipe
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
        source_type=data.source_type,
        source_details=data.source_details,
        instructions=data.instructions,
        notes=data.notes,
        calories_per_serving=data.calories_per_serving,
        photo_filename=data.photo_filename,
        dish_photo_filename=data.dish_photo_filename,
    )
    for ing in data.ingredients:
        recipe.ingredients.append(
            Ingredient(name=ing.name, amount=ing.amount, unit=ing.unit, order=ing.order, group=ing.group, category=ing.category)
        )
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


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
