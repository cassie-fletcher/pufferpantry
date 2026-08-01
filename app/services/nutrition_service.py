"""Nutrition lookup via the USDA FoodData Central API.

The USDA API returns nutrition per 100g of food. We convert ingredient
amounts to grams using a rough unit-to-gram table, then calculate totals.

These are estimates — good enough for meal planning, not for medical use.
"""

import logging
from functools import lru_cache

import re

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
    nutrients["fdc_id"] = food.get("fdcId")
    return nutrients


USDA_FOOD_DETAIL_URL = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"


@lru_cache(maxsize=200)
def lookup_serving_grams(fdc_id: int | None) -> float | None:
    """The gram weight of ONE household serving of a food (USDA foodPortions).

    One extra API call, made only for quantity-less ingredients and only at
    recipe save (Cassie's rule: default such ingredients to one serving of
    that thing). Uses the FIRST listed portion — USDA orders portions with
    the customary household measure first. None when the food has no
    portions or the call fails; the caller falls back to DEFAULT_GRAMS.
    """
    if not fdc_id:
        return None
    try:
        response = httpx.get(
            USDA_FOOD_DETAIL_URL.format(fdc_id=fdc_id),
            params={"api_key": USDA_API_KEY},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("USDA detail lookup failed for fdc_id=%s", fdc_id)
        return None
    for portion in data.get("foodPortions", []):
        grams = portion.get("gramWeight")
        if grams:
            return float(grams)
    return None


# Range separators for amounts like "1-2", "1 to 2". A range becomes its
# MIDPOINT (Cassie 2026-08-01): a single grams number is needed and the
# midpoint is the least-wrong point estimate for a calorie count.
_AMOUNT_RANGE_SPLIT_RE = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)
_UNICODE_FRACTION_VALUES = {
    "½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25, "¾": 0.75,
    "⅕": 0.2, "⅖": 0.4, "⅗": 0.6, "⅘": 0.8, "⅙": 1 / 6, "⅚": 5 / 6,
    "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}


def _amount_to_number(amount: str | None) -> float | None:
    """Parse an amount string to a number, SUMMING every term.

    "1 1/2" -> 1.5 (the old parser dropped the fraction and used 1.0 --
    measured: the salmon undercounted by a third). Handles bare fractions,
    decimals, unicode glyphs ("1½"), and ranges via midpoint. None when the
    string has no parseable number ("to taste").
    """
    if not amount or not amount.strip():
        return None

    def one_value(text: str) -> float | None:
        total, seen = 0.0, False
        for glyph, val in _UNICODE_FRACTION_VALUES.items():
            if glyph in text:
                total += val
                text = text.replace(glyph, " ")
                seen = True
        for part in text.split():
            try:
                if "/" in part:
                    num, den = part.split("/", 1)
                    total += float(num) / float(den)
                else:
                    total += float(part)
                seen = True
            except (ValueError, ZeroDivisionError):
                continue
        return total if seen else None

    pieces = [p for p in _AMOUNT_RANGE_SPLIT_RE.split(amount.strip()) if p]
    values = [v for v in (one_value(p) for p in pieces) if v is not None]
    if not values:
        return None
    if len(values) >= 2:
        return (min(values) + max(values)) / 2.0  # range midpoint
    return values[0]


def _parse_amount_grams(
    amount: str | None, unit: str | None, fdc_id: int | None = None
) -> float:
    """Convert an amount + unit to approximate grams.

    With a unit: parsed amount x grams-per-unit (UNIT_TO_GRAMS).
    Without a unit: the item is countable ("2 lemons") or quantity-less
    ("salt") — either way, one item = one USDA household serving
    (lookup_serving_grams; one detail API call, save-time only), falling
    back to DEFAULT_GRAMS when the food has no portion data.
    """
    numeric = _amount_to_number(amount)

    if unit:
        unit_lower = unit.lower().strip().rstrip(".")
        grams_per_unit = UNIT_TO_GRAMS.get(unit_lower, DEFAULT_GRAMS)
        return (numeric if numeric is not None else 1.0) * grams_per_unit

    serving = lookup_serving_grams(fdc_id)
    grams_per_item = serving if serving else DEFAULT_GRAMS
    return (numeric if numeric is not None else 1.0) * grams_per_item


def compute_nutrition_facts(ingredients: list) -> dict:
    """The INDEPENDENT nutrition facts for a recipe, and nothing derived.

    Stored shape (one USDA API pass, done at save time):
        {"ingredients": [{"name", "grams", "usda_match", "per_100g"}]}
    per_100g is the USDA per-100g nutrient dict, or None on a miss.
    Everything else — per-ingredient contribution, recipe totals, per-serving
    at any serving count — is arithmetic on these facts (derive_nutrition_view)
    and is deliberately NOT stored, so nothing can drift.
    """
    facts = []
    for ing in ingredients:
        if isinstance(ing, dict):
            name, amount, unit = ing.get("name", ""), ing.get("amount"), ing.get("unit")
        else:
            name = getattr(ing, "name", str(ing))
            amount = getattr(ing, "amount", None)
            unit = getattr(ing, "unit", None)
        nutrition = lookup_ingredient_nutrition(name)
        per_100g = (
            {
                k: v
                for k, v in nutrition.items()
                if k not in ("usda_description", "fdc_id")
            }
            if nutrition
            else None
        )
        facts.append(
            {
                "name": name,
                "grams": round(
                    _parse_amount_grams(
                        amount, unit, nutrition.get("fdc_id") if nutrition else None
                    )
                ),
                "usda_match": nutrition.get("usda_description") if nutrition else None,
                "per_100g": per_100g,
            }
        )
    return {"ingredients": facts}


def derive_nutrition_view(facts: dict, servings: int) -> dict:
    """Expand stored facts into the response shape the frontend renders.

    Pure arithmetic — no API, no storage. Matches the historical response
    contract: {servings, total, per_serving, ingredients:[{name, grams,
    nutrition|None, usda_match}]}.
    """
    totals = {k: 0.0 for k in NUTRIENT_KEYS.values()}
    details = []
    for f in facts.get("ingredients", []):
        detail = {"name": f["name"], "grams": f["grams"], "usda_match": f["usda_match"]}
        if f.get("per_100g"):
            scale = f["grams"] / 100.0
            contribution = {}
            for key in NUTRIENT_KEYS.values():
                val = f["per_100g"].get(key, 0) * scale
                contribution[key] = round(val, 1)
                totals[key] += val
            detail["nutrition"] = contribution
        else:
            detail["nutrition"] = None
        details.append(detail)
    totals = {k: round(v, 1) for k, v in totals.items()}
    per_serving = {k: round(v / max(servings, 1), 1) for k, v in totals.items()}
    return {
        "servings": servings,
        "total": totals,
        "per_serving": per_serving,
        "ingredients": details,
    }


