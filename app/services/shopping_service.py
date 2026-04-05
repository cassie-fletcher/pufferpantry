"""Shopping list generation — consolidates ingredients from selected recipes
and categorizes them by grocery store section.
"""

import re

# Keyword → category mapping. Checked in order; first match wins.
# Each entry is (category_name, set_of_keywords).
CATEGORY_RULES = [
    ("Meat & Seafood", {
        "chicken", "beef", "pork", "steak", "ground meat", "turkey", "lamb",
        "bacon", "sausage", "ham", "salmon", "shrimp", "fish", "tuna",
        "prosciutto", "chorizo", "meatball",
    }),
    ("Dairy", {
        "milk", "cheese", "butter", "cream", "yogurt", "sour cream",
        "mozzarella", "parmesan", "cheddar", "ricotta", "feta",
        "cream cheese", "half-and-half", "whipping cream", "egg", "eggs",
    }),
    ("Produce", {
        "garlic", "onion", "tomato", "potato", "pepper", "lettuce", "spinach",
        "carrot", "celery", "broccoli", "zucchini", "squash", "mushroom",
        "avocado", "lemon", "lime", "orange", "apple", "banana", "berry",
        "basil", "cilantro", "parsley", "thyme", "rosemary", "dill", "mint",
        "chive", "scallion", "shallot", "ginger", "jalapeño", "poblano",
        "corn", "cucumber", "cabbage", "kale", "arugula", "radish",
        "sweet potato", "green bean", "asparagus", "pea", "beet",
        "herb", "fresh",
    }),
    ("Bakery", {
        "bread", "tortilla", "bun", "roll", "pita", "naan", "bagel",
        "croissant", "baguette",
    }),
    ("Frozen", {
        "frozen",
    }),
    ("Drinks", {
        "wine", "beer", "bourbon", "whiskey", "vodka", "rum", "tequila",
        "juice", "soda", "sparkling water", "seltzer", "tonic",
        "lemonade", "kombucha", "cider",
    }),
    ("Pantry", {
        "oil", "olive oil", "vinegar", "flour", "sugar",
        "salt", "pepper", "paprika", "cumin", "oregano", "cinnamon",
        "chili powder", "cayenne", "turmeric", "nutmeg",
        "pasta", "rice", "orzo", "noodle", "quinoa", "couscous",
        "broth", "stock", "sauce", "paste", "powder",
        "bean", "lentil", "chickpea",
        "honey", "maple syrup", "mustard", "ketchup", "mayo",
        "breadcrumb", "panko", "cornstarch", "baking",
        "sriracha", "worcestershire", "extract", "seasoning",
        "sesame", "peanut butter", "almond", "walnut", "pecan",
        "coconut", "vanilla", "chocolate", "cocoa",
        "tortilla", "tortillas", "wrap", "wraps", "chips",
    }),
]

# Words to strip when normalizing ingredient names for matching
STRIP_WORDS = [
    "fresh", "dried", "ground", "chopped", "minced", "diced", "sliced",
    "shredded", "grated", "crushed", "whole", "large", "small", "medium",
    "thinly", "finely", "roughly", "coarsely", "plus more for serving",
    "for serving", "optional", "to taste", "divided",
    "fine", "coarse", "pink himalayan", "kosher", "sea",
    "extra virgin", "extra-virgin", "virgin",
    "plain", "raw", "unsalted", "salted",
]

# Ingredient name aliases — map variations to a canonical name for better consolidation
NAME_ALIASES = {
    "fine pink himalayan salt": "salt",
    "fine himalayan salt": "salt",
    "kosher salt": "salt",
    "fine salt": "salt",
    "coarse salt": "salt",
    "sea salt": "salt",
    "black pepper": "pepper",
    "freshly ground pepper": "pepper",
    "freshly ground black pepper": "pepper",
    "ground black pepper": "pepper",
    "extra virgin olive oil": "olive oil",
    "extra-virgin olive oil": "olive oil",
    "feta cheese": "feta",
    "crumbled feta": "feta",
    "feta cheese, cubed or crumbled": "feta",
    "block feta cheese": "feta",
}


def _normalize_name(name: str) -> str:
    """Normalize an ingredient name for deduplication."""
    n = name.lower().strip()
    # Normalize hyphens to spaces (extra-virgin → extra virgin)
    n = n.replace("-", " ")
    # Remove parenthetical notes like "(about 2 cups)"
    n = re.sub(r"\([^)]*\)", "", n)
    # Remove common descriptor words
    for word in STRIP_WORDS:
        n = re.sub(rf"\b{re.escape(word)}\b", "", n, flags=re.IGNORECASE)
    # Collapse whitespace and strip punctuation
    n = re.sub(r"[,;]+", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    # Apply name aliases for common variations
    if n in NAME_ALIASES:
        n = NAME_ALIASES[n]
    # Also try matching after stripping trailing descriptors like "cubed or crumbled"
    stripped = re.sub(r",?\s*(cubed|crumbled|halved|patted dry|cut into .*)$", "", n).strip()
    if stripped in NAME_ALIASES:
        n = NAME_ALIASES[stripped]
    return n


# Map plural units to singular for matching
UNIT_ALIASES = {
    "tablespoons": "tbsp", "tablespoon": "tbsp", "tbsp": "tbsp",
    "teaspoons": "tsp", "teaspoon": "tsp", "tsp": "tsp",
    "cups": "cup", "cup": "cup",
    "pounds": "lb", "pound": "lb", "lbs": "lb", "lb": "lb",
    "ounces": "oz", "ounce": "oz", "oz": "oz",
    "cloves": "clove", "clove": "clove",
    "slices": "slice", "slice": "slice",
    "cans": "can", "can": "can",
    "pieces": "piece", "piece": "piece",
}


def _normalize_unit(unit: str | None) -> str:
    """Normalize a unit string for consistent matching."""
    if not unit:
        return ""
    u = unit.lower().strip().rstrip(".")
    return UNIT_ALIASES.get(u, u)


# Conversion factors to tablespoons (base volume unit for consolidation)
VOLUME_TO_TBSP = {
    "tsp": 1 / 3,
    "tbsp": 1,
    "cup": 16,
    "fl oz": 2,
}

# Conversion factors to ounces (base weight unit)
WEIGHT_TO_OZ = {
    "oz": 1,
    "lb": 16,
    "g": 1 / 28.35,
    "kg": 35.274,
}


def _convert_to_common_unit(amount: float, unit: str) -> tuple[float, str] | None:
    """Try to convert an amount+unit to a common base unit for summing.
    Returns (converted_amount, base_unit) or None if not convertible.
    """
    if unit in VOLUME_TO_TBSP:
        return (amount * VOLUME_TO_TBSP[unit], "tbsp")
    if unit in WEIGHT_TO_OZ:
        return (amount * WEIGHT_TO_OZ[unit], "oz")
    return None


def _format_amount(amount: float, base_unit: str) -> tuple[str, str]:
    """Convert a base-unit amount back to a friendly display unit."""
    if base_unit == "tbsp":
        if amount >= 16:
            cups = amount / 16
            if cups == int(cups):
                return (str(int(cups)), "cup")
            return (f"{cups:.1f}", "cups")
        if amount >= 1:
            if amount == int(amount):
                return (str(int(amount)), "tbsp")
            return (f"{amount:.1f}", "tbsp")
        tsp = amount * 3
        if tsp == int(tsp):
            return (str(int(tsp)), "tsp")
        return (f"{tsp:.1f}", "tsp")
    if base_unit == "oz":
        if amount >= 16:
            lbs = amount / 16
            if lbs == int(lbs):
                return (str(int(lbs)), "lb")
            return (f"{lbs:.1f}", "lbs")
        if amount == int(amount):
            return (str(int(amount)), "oz")
        return (f"{amount:.1f}", "oz")
    return (str(amount), base_unit)


# Flat lookup: keyword → category, built from CATEGORY_RULES for fast access
_KEYWORD_TO_CATEGORY = {}
for _cat, _keywords in CATEGORY_RULES:
    for _kw in _keywords:
        _KEYWORD_TO_CATEGORY[_kw] = _cat


def _categorize(name: str) -> str:
    """Assign a grocery category by scanning words right-to-left.

    The last meaningful noun determines the category. Earlier words are
    usually adjectives/modifiers. For example:
      "chicken broth"  → "broth" is Pantry (not Meat)
      "corn tortillas"  → "tortillas" is Bakery (not Produce)
      "chicken breast" → "breast" has no match, "chicken" is Meat
      "dried oregano"  → "oregano" is Pantry
    """
    lower = name.lower()
    # Remove parentheticals and punctuation
    lower = re.sub(r"\([^)]*\)", "", lower)
    lower = re.sub(r"[,;/]+", " ", lower)
    words = lower.split()

    # Scan right-to-left: first keyword match wins
    for word in reversed(words):
        word = word.strip(".")
        if word in _KEYWORD_TO_CATEGORY:
            return _KEYWORD_TO_CATEGORY[word]
        # Also check two-word phrases (current word + next word to the right)

    # Also try multi-word keywords against the full string (e.g., "soy sauce", "olive oil")
    for kw, cat in _KEYWORD_TO_CATEGORY.items():
        if " " in kw and kw in lower:
            return cat

    return "Misc"


def _parse_amount(amount_str: str | None) -> float | None:
    """Try to parse an amount string into a number."""
    if not amount_str:
        return None
    s = amount_str.strip()
    try:
        if "/" in s:
            # Handle "1/2" or "1 1/2"
            parts = s.split()
            if len(parts) == 2 and "/" in parts[1]:
                whole = float(parts[0])
                num, den = parts[1].split("/")
                return whole + float(num) / float(den)
            elif "/" in parts[0]:
                num, den = parts[0].split("/")
                return float(num) / float(den)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def generate_shopping_list(recipes: list) -> dict:
    """Generate a consolidated, categorized shopping list from recipes.

    Each recipe should have: title (str), ingredients (list of dicts with
    name, amount, unit).
    """
    # Consolidate ingredients by normalized name only.
    # When units differ but are convertible (e.g., tbsp vs cup), convert to a common unit.
    consolidated = {}  # key: normalized_name → merged item

    for recipe in recipes:
        recipe_title = recipe.get("title", "Unknown")
        for ing in recipe.get("ingredients", []):
            name = ing.get("name", "")
            amount = ing.get("amount")
            unit = ing.get("unit")
            category = ing.get("category")

            norm = _normalize_name(name)
            if not norm:
                continue

            norm_unit = _normalize_unit(unit)
            parsed = _parse_amount(amount)

            if norm in consolidated:
                existing = consolidated[norm]
                if recipe_title not in existing["from_recipes"]:
                    existing["from_recipes"].append(recipe_title)

                # Try to sum amounts
                if parsed is not None and existing["_base_amount"] is not None:
                    # Try converting both to a common unit
                    new_conv = _convert_to_common_unit(parsed, norm_unit) if norm_unit else None
                    ext_conv = _convert_to_common_unit(
                        existing["_base_amount"], existing["_base_unit"]
                    ) if existing["_base_unit"] else None

                    if new_conv and ext_conv and new_conv[1] == ext_conv[1]:
                        # Same base unit — sum them
                        total = new_conv[0] + ext_conv[0]
                        display_amount, display_unit = _format_amount(total, new_conv[1])
                        existing["amount"] = display_amount
                        existing["unit"] = display_unit
                        existing["_base_amount"] = total
                        existing["_base_unit"] = new_conv[1]
                    elif norm_unit == existing.get("_orig_unit", ""):
                        # Same unit, just sum directly
                        total = parsed + existing["_base_amount"]
                        existing["_base_amount"] = total
                        existing["amount"] = str(total) if total != int(total) else str(int(total))
                    elif amount:
                        existing["amount"] = f"{existing['amount']} + {amount} {norm_unit}"
                elif amount:
                    existing["amount"] = f"{existing['amount']} + {amount} {norm_unit}"
            else:
                consolidated[norm] = {
                    "name": name,
                    "amount": amount,
                    "unit": unit,
                    "category": category,
                    "from_recipes": [recipe_title],
                    "_base_amount": parsed,
                    "_base_unit": _normalize_unit(unit) if parsed else None,
                    "_orig_unit": norm_unit,
                }

    # Group by category — use Claude-assigned category if available, fall back to keywords
    categories = {}
    for item in consolidated.values():
        for k in ("_base_amount", "_base_unit", "_orig_unit"):
            item.pop(k, None)
        cat = item.pop("category", None) or _categorize(item["name"])
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    # Sort categories in a sensible grocery store order
    category_order = [
        "Produce", "Meat & Seafood", "Dairy", "Bakery",
        "Frozen", "Drinks", "Pantry", "Misc",
    ]
    result = []
    for cat_name in category_order:
        if cat_name in categories:
            items = sorted(categories[cat_name], key=lambda x: x["name"].lower())
            result.append({"name": cat_name, "items": items})

    return {"categories": result}
