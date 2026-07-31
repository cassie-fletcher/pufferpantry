"""Tests for the local (no-API) JSON-LD url extraction path."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.url_service import (
    _json_ld_recipes,
    _map_json_ld,
    _split_ingredient,
    extract_recipe_from_url,
)

PAGE = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","name":"Some Blog"},
 {"@type":"Recipe","name":"Chipotle Chicken Enchiladas",
  "recipeYield":["4","4 servings"],
  "description":"Cozy enchiladas.",
  "recipeCuisine":["Mexican"],
  "nutrition":{"@type":"NutritionInformation","calories":"512 kcal"},
  "recipeIngredient":[
    "1 pound boneless skinless chicken tenders",
    "2 tablespoons extra virgin olive oil",
    "salt and pepper",
    "3 ears corn"],
  "recipeInstructions":[
    {"@type":"HowToStep","text":"Preheat the oven to 425 F."},
    {"@type":"HowToStep","text":"Toss the chicken with olive oil."}]}
]}
</script></head><body>hi</body></html>"""

NO_RECIPE_PAGE = "<html><body><p>Just a blog post about knitting.</p></body></html>"


def _resp(html: str) -> MagicMock:
    r = MagicMock()
    r.text = html
    r.raise_for_status = MagicMock()
    return r


def test_json_ld_found_in_graph():
    recipes = _json_ld_recipes(PAGE)
    assert len(recipes) == 1
    assert recipes[0]["name"] == "Chipotle Chicken Enchiladas"


def test_map_fields():
    mapped = _map_json_ld(_json_ld_recipes(PAGE)[0])
    assert mapped["title"] == "Chipotle Chicken Enchiladas"
    assert mapped["servings"] == 4
    assert mapped["cuisine"] == "Mexican"
    assert mapped["calories_per_serving"] == 512
    assert "1. Preheat the oven to 425 F." in mapped["instructions"]


def test_ingredient_split_and_normalize():
    mapped = _map_json_ld(_json_ld_recipes(PAGE)[0])
    by_name = {i["name"]: i for i in mapped["ingredients"]}
    assert by_name["boneless skinless chicken tenders"]["amount"] == "1"
    assert by_name["boneless skinless chicken tenders"]["unit"] == "pound"
    # quantity-less "salt and pepper" splits via the shared conjunction rule
    assert "salt" in by_name and "pepper" in by_name


def test_split_ingredient_quantityless():
    ing = _split_ingredient("Flaky sea salt", 0)
    assert ing.amount is None and ing.name == "Flaky sea salt"


@patch("app.services.url_service.httpx.get")
def test_no_json_ld_is_clear_422_no_fallback(mock_get):
    mock_get.return_value = _resp(NO_RECIPE_PAGE)
    with pytest.raises(HTTPException) as exc:
        extract_recipe_from_url("https://example.com/knitting")
    assert exc.value.status_code == 422
    assert "does not publish readable recipe data" in exc.value.detail


@patch("app.services.url_service._download_image")
@patch("app.services.url_service.httpx.get")
def test_end_to_end_local_no_api(mock_get, mock_dl):
    mock_dl.return_value = None
    mock_get.return_value = _resp(PAGE)
    with patch("app.services.url_service.Anthropic") as mock_anthropic:
        data = extract_recipe_from_url("https://example.com/enchiladas")
        mock_anthropic.assert_not_called()  # THE point: no API client, ever
    assert data["title"] == "Chipotle Chicken Enchiladas"
    assert len(data["ingredients"]) >= 4


def test_determinism():
    a = _map_json_ld(_json_ld_recipes(PAGE)[0])
    b = _map_json_ld(_json_ld_recipes(PAGE)[0])
    assert a == b
