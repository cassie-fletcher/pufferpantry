"""Nutrition lookup via the USDA FoodData Central API.

The USDA API returns nutrition per 100g of food. We convert ingredient
amounts to grams using a rough unit-to-gram table, then calculate totals.

These are estimates — good enough for meal planning, not for medical use.
"""

import logging
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

USDA_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_API_KEY = "DEMO_KEY"  # Free, rate-limited to 30 req/hour

# Nutrients we care about and their USDA names
NUTRIENT_KEYS = {
    "Energy": "calories",
    "Protein": "protein_g",
    "Total lipid (fat)": "fat_g",
    "Carbohydrate, by difference": "carbs_g",
    "Fiber, total dietary": "fiber_g",
    "Sodium, Na": "sodium_mg",
}

# Approximate weight in grams for common cooking units.
# These vary by ingredient (1 cup flour ≠ 1 cup sugar) but are close enough
# for nutrition estimates. Source: USDA general conversions.
UNIT_TO_GRAMS = {
    # Volume
    "cup": 150,
    "cups": 150,
    "tbsp": 15,
    "tablespoon": 15,
    "tablespoons": 15,
    "tsp": 5,
    "teaspoon": 5,
    "teaspoons": 5,
    "ml": 1,
    "fl oz": 30,
    # Weight
    "g": 1,
    "gram": 1,
    "grams": 1,
    "oz": 28,
    "ounce": 28,
    "ounces": 28,
    "lb": 454,
    "lbs": 454,
    "pound": 454,
    "pounds": 454,
    "kg": 1000,
    # Countable items (rough averages)
    "clove": 5,
    "cloves": 5,
    "slice": 30,
    "slices": 30,
    "piece": 100,
    "pieces": 100,
    "can": 400,
    "bunch": 150,
}

# Default weight when we can't parse the amount/unit (e.g., "a pinch", "to taste")
DEFAULT_GRAMS = 50


@lru_cache(maxsize=200)
def lookup_ingredient_nutrition(name: str) -> dict | None:
    """Search USDA for an ingredient and return per-100g nutrition.

    Results are cached in memory (lru_cache) so repeated lookups for the
    same ingredient don't hit the API again. The cache holds up to 200 items
    and resets when the server restarts.
    """
    try:
        response = httpx.get(
            USDA_API_URL,
            params={
                "api_key": USDA_API_KEY,
                "query": name,
                "pageSize": 1,
                "dataType": "Foundation,SR Legacy",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("USDA API lookup failed for: %s", name)
        return None

    foods = data.get("foods", [])
    if not foods:
        return None

    food = foods[0]
    nutrients = {}
    for n in food.get("foodNutrients", []):
        key = NUTRIENT_KEYS.get(n.get("nutrientName"))
        if key:
            # Energy appears twice (KCAL and kJ) — we want KCAL
            if n.get("nutrientName") == "Energy" and n.get("unitName", "").lower() != "kcal":
                continue
            nutrients[key] = n.get("value", 0)

    if not nutrients:
        return None

    nutrients["usda_description"] = food.get("description", name)
    return nutrients


def _parse_amount_grams(amount: str | None, unit: str | None) -> float:
    """Convert an amount + unit to approximate grams."""
    # Parse the numeric amount
    numeric = 1.0
    if amount:
        try:
            # Handle fractions like "1/2"
            if "/" in amount:
                parts = amount.split("/")
                numeric = float(parts[0]) / float(parts[1])
            else:
                numeric = float(amount)
        except (ValueError, ZeroDivisionError):
            numeric = 1.0

    # Convert unit to grams
    if unit:
        unit_lower = unit.lower().strip().rstrip(".")
        grams_per_unit = UNIT_TO_GRAMS.get(unit_lower, DEFAULT_GRAMS)
    else:
        # No unit — assume it's a countable item (e.g., "2 eggs")
        grams_per_unit = DEFAULT_GRAMS

    return numeric * grams_per_unit


def calculate_recipe_nutrition(ingredients: list, servings: int = 2) -> dict:
    """Calculate total and per-serving nutrition for a list of ingredients.

    Each ingredient should have: name, amount (str|None), unit (str|None).
    """
    totals = {k: 0.0 for k in NUTRIENT_KEYS.values()}
    ingredient_details = []

    for ing in ingredients:
        if isinstance(ing, dict):
            name = ing.get("name", "")
            amount = ing.get("amount")
            unit = ing.get("unit")
        else:
            name = getattr(ing, "name", str(ing))
            amount = getattr(ing, "amount", None)
            unit = getattr(ing, "unit", None)

        nutrition = lookup_ingredient_nutrition(name)
        grams = _parse_amount_grams(amount, unit)

        detail = {"name": name, "grams": round(grams)}

        if nutrition:
            # USDA data is per 100g, so scale by actual grams
            scale = grams / 100.0
            ing_nutrients = {}
            for key in NUTRIENT_KEYS.values():
                val = nutrition.get(key, 0) * scale
                ing_nutrients[key] = round(val, 1)
                totals[key] += val
            detail["nutrition"] = ing_nutrients
            detail["usda_match"] = nutrition.get("usda_description", "")
        else:
            detail["nutrition"] = None
            detail["usda_match"] = None

        ingredient_details.append(detail)

    # Round totals
    for key in totals:
        totals[key] = round(totals[key], 1)

    # Per-serving
    per_serving = {key: round(val / max(servings, 1), 1) for key, val in totals.items()}

    return {
        "servings": servings,
        "total": totals,
        "per_serving": per_serving,
        "ingredients": ingredient_details,
    }
