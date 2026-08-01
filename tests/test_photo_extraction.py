"""Tests for photo upload and recipe extraction.

These tests mock the Claude API so they don't need a real API key
or make actual HTTP requests to Anthropic.
"""

import io
import json
from unittest.mock import MagicMock, patch

from PIL import Image


SAMPLE_CLAUDE_RESPONSE = {
    "title": "Honey Garlic Salmon",
    "servings": 2,
    "calories_per_serving": 420,
    "instructions": "Step 1: Mix honey and garlic. Step 2: Glaze salmon. Step 3: Bake at 400F for 15 min.",
    "notes": "Great with rice.",
    "ingredients": [
        {"name": "salmon fillets", "amount": "2", "unit": "6-oz", "order": 0},
        {"name": "honey", "amount": "3", "unit": "tbsp", "order": 1},
        {"name": "garlic", "amount": "4", "unit": "cloves", "order": 2},
        {"name": "soy sauce", "amount": "2", "unit": "tbsp", "order": 3},
    ],
}


def _make_test_image() -> io.BytesIO:
    """Create a small valid JPEG image in memory for testing."""
    img = Image.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def _mock_claude_response(response_text: str) -> MagicMock:
    """Create a mock Anthropic messages.create() return value."""
    mock_message = MagicMock()
    mock_message.stop_reason = "end_turn"
    mock_message.content = [MagicMock(text=response_text, type="text")]
    return mock_message


def _fake_reading(method="apple_vision"):
    """A canned local-ensemble Reading matching SAMPLE_CLAUDE_RESPONSE."""
    from app.ocr.base import Reading
    from app.schemas.recipe import PhotoExtractResult

    payload = {k: v for k, v in SAMPLE_CLAUDE_RESPONSE.items()}
    payload["photo_filename"] = "placeholder.jpg"
    return Reading(
        method=method, schema=PhotoExtractResult(**payload), raw_text="raw"
    )


@patch("app.routers.recipes.read_image")
def test_extract_from_photo(mock_read, client, tmp_path):
    """Upload a photo -> the LOCAL ensemble reads it -> review data back.

    read_image is mocked at the router boundary: endpoint tests exercise the
    route contract, not the OCR engines (which need a built Vision binary
    and are covered by their own tests)."""
    mock_read.side_effect = lambda paths, method: _fake_reading(method)

    # Upload a test image
    img_buf = _make_test_image()
    response = client.post(
        "/api/recipes/extract-from-photo",
        files={"photos": ("test_recipe.jpg", img_buf, "image/jpeg")},
    )

    assert response.status_code == 200
    recipes = response.json()
    assert isinstance(recipes, list)
    data = recipes[0]
    assert data["title"] == "Honey Garlic Salmon"
    assert data["servings"] == 2
    assert data["calories_per_serving"] == 420
    assert len(data["ingredients"]) == 4
    assert data["ingredients"][0]["name"] == "salmon fillets"
    # ROVER flags ride the response: both fake voters agreed on everything
    assert data["ingredients"][0]["amount_confidence"] == "high"
    assert data["photo_filename"].endswith(".jpg")
    assert data["photo_filenames"][0].endswith(".jpg")  # all pages recorded
    assert data["meal_type"] == "dinner"


@patch("app.services.photo_service.settings")
@patch("app.services.photo_service.Anthropic")
def test_backup_claude_handles_markdown_fences(mock_anthropic_cls, mock_settings, tmp_path):
    """The DORMANT Claude backup still parses fenced JSON (kept honest even
    though the endpoint no longer calls it)."""
    from app.services.photo_service import extract_recipe_from_photos

    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    fenced_json = "```json\n" + json.dumps(SAMPLE_CLAUDE_RESPONSE) + "\n```"
    mock_client.messages.create.return_value = _mock_claude_response(fenced_json)

    img = tmp_path / "t.jpg"
    img.write_bytes(_make_test_image().getvalue())
    result = extract_recipe_from_photos([img])
    recipes = result if isinstance(result, list) else [result]
    assert recipes[0]["title"] == "Honey Garlic Salmon"


@patch("app.services.photo_service.settings")
@patch("app.services.photo_service.Anthropic")
def test_backup_claude_bad_json_raises(mock_anthropic_cls, mock_settings, tmp_path):
    """The dormant backup raises a clear 422 on unparseable text."""
    import pytest
    from fastapi import HTTPException

    from app.services.photo_service import extract_recipe_from_photos

    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_claude_response(
        "I can see a recipe but I'm not sure about the ingredients..."
    )

    img = tmp_path / "t.jpg"
    img.write_bytes(_make_test_image().getvalue())
    with pytest.raises(HTTPException) as exc:
        extract_recipe_from_photos([img])
    assert exc.value.status_code == 422


@patch("app.services.photo_service.settings")
def test_backup_claude_no_api_key(mock_settings, tmp_path):
    """The dormant backup fails loudly without a key. The ENDPOINT no longer
    needs a key at all — extraction is local."""
    import pytest
    from fastapi import HTTPException

    from app.services.photo_service import extract_recipe_from_photos

    mock_settings.anthropic_api_key = ""
    img = tmp_path / "t.jpg"
    img.write_bytes(_make_test_image().getvalue())
    with pytest.raises(HTTPException) as exc:
        extract_recipe_from_photos([img])
    assert exc.value.status_code == 500
    assert "API key" in exc.value.detail


@patch("app.routers.recipes.read_image")
def test_full_round_trip(mock_read, client):
    """Extract via the local ensemble, then save the recipe — full workflow."""
    mock_read.side_effect = lambda paths, method: _fake_reading(method)

    # Step 1: Extract from photo
    img_buf = _make_test_image()
    extract_response = client.post(
        "/api/recipes/extract-from-photo",
        files={"photos": ("test.jpg", img_buf, "image/jpeg")},
    )
    extracted = extract_response.json()[0]

    # Step 2: Save the recipe (as the frontend would after user review)
    recipe_data = {
        "title": extracted["title"],
        "meal_type": extracted["meal_type"],
        "servings": extracted["servings"],
        "calories_per_serving": extracted["calories_per_serving"],
        "instructions": extracted["instructions"],
        "notes": extracted["notes"],
        "ingredients": extracted["ingredients"],
        "photo_filename": extracted["photo_filename"],
        "source_type": "cookbook",
    }

    create_response = client.post("/api/recipes", json=recipe_data)
    assert create_response.status_code == 201

    recipe = create_response.json()
    assert recipe["title"] == "Honey Garlic Salmon"
    assert recipe["source_type"] == "cookbook"
    assert recipe["photo_filename"].endswith(".jpg")
    assert len(recipe["ingredients"]) == 4
