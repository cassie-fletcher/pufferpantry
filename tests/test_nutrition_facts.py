"""Tests for the minimal nutrition-facts storage and its derivation."""
from __future__ import annotations

from unittest.mock import patch

from app.services.nutrition_service import (
    compute_nutrition_facts,
    derive_nutrition_view,
)

PER_100G = {"calories": 200.0, "protein_g": 20.0, "fat_g": 10.0,
            "carbs_g": 0.0, "fiber_g": 0.0, "sodium_mg": 50.0}


def test_facts_store_only_independent_data():
    with patch(
        "app.services.nutrition_service.lookup_ingredient_nutrition",
        return_value={**PER_100G, "usda_description": "Salmon, raw"},
    ):
        facts = compute_nutrition_facts(
            [{"name": "salmon", "amount": "1", "unit": "pound"}]
        )
    ing = facts["ingredients"][0]
    assert set(facts) == {"ingredients"}          # nothing derived stored
    assert set(ing) == {"name", "grams", "usda_match", "per_100g"}
    assert ing["grams"] == 454
    assert ing["per_100g"]["calories"] == 200.0


def test_derivation_math():
    facts = {"ingredients": [
        {"name": "salmon", "grams": 454, "usda_match": "Salmon, raw", "per_100g": PER_100G},
        {"name": "mystery", "grams": 50, "usda_match": None, "per_100g": None},
    ]}
    view = derive_nutrition_view(facts, servings=4)
    assert view["total"]["calories"] == 908.0     # 454g * 200/100g
    assert view["per_serving"]["calories"] == 227.0
    assert view["ingredients"][1]["nutrition"] is None  # miss stays visible
    # different serving count = different division, same facts
    assert derive_nutrition_view(facts, 2)["per_serving"]["calories"] == 454.0


def test_miss_only_facts_derive_to_zero():
    facts = {"ingredients": [
        {"name": "x", "grams": 50, "usda_match": None, "per_100g": None},
    ]}
    assert derive_nutrition_view(facts, 4)["per_serving"]["calories"] == 0.0


def test_amount_parsing_sums_all_terms():
    from app.services.nutrition_service import _amount_to_number

    assert _amount_to_number("1 1/2") == 1.5     # the dropped-term bug, fixed
    assert _amount_to_number("1½") == 1.5
    assert _amount_to_number("1-2") == 1.5       # range -> midpoint
    assert _amount_to_number("1 to 2") == 1.5
    assert _amount_to_number("to taste") is None


def test_quantityless_uses_usda_serving_weight():
    from app.services.nutrition_service import compute_nutrition_facts

    with patch(
        "app.services.nutrition_service.lookup_ingredient_nutrition",
        return_value={**PER_100G, "usda_description": "Avocados, raw", "fdc_id": 111},
    ), patch(
        "app.services.nutrition_service.lookup_serving_grams",
        return_value=201.0,
    ) as serving:
        facts = compute_nutrition_facts([{"name": "avocado", "amount": "1", "unit": None}])
    serving.assert_called_once_with(111)
    assert facts["ingredients"][0]["grams"] == 201   # 1 x one USDA serving


def test_countable_multiplies_serving_weight():
    from app.services.nutrition_service import compute_nutrition_facts

    with patch(
        "app.services.nutrition_service.lookup_ingredient_nutrition",
        return_value={**PER_100G, "usda_description": "Lemons, raw", "fdc_id": 222},
    ), patch(
        "app.services.nutrition_service.lookup_serving_grams",
        return_value=58.0,
    ):
        facts = compute_nutrition_facts([{"name": "lemons", "amount": "2", "unit": None}])
    assert facts["ingredients"][0]["grams"] == 116   # 2 x one lemon


def test_unit_path_makes_no_detail_call():
    from app.services.nutrition_service import compute_nutrition_facts

    with patch(
        "app.services.nutrition_service.lookup_ingredient_nutrition",
        return_value={**PER_100G, "usda_description": "Salmon, raw", "fdc_id": 333},
    ), patch(
        "app.services.nutrition_service.lookup_serving_grams"
    ) as serving:
        compute_nutrition_facts([{"name": "salmon", "amount": "1 1/2", "unit": "pounds"}])
    serving.assert_not_called()  # detail calls ONLY for unit-less ingredients
