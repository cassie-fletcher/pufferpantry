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
    mock_message.content = [MagicMock(text=response_text)]
    return mock_message


@patch("app.services.photo_service.settings")
@patch("app.services.photo_service.Anthropic")
def test_extract_from_photo(mock_anthropic_cls, mock_settings, client, tmp_path):
    """Upload a photo and get extracted recipe data back."""
    mock_settings.anthropic_api_key = "sk-ant-test-key"

    # Mock Claude to return our sample recipe
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_claude_response(
        json.dumps(SAMPLE_CLAUDE_RESPONSE)
    )

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
    assert data["photo_filename"].endswith(".jpg")
    assert data["meal_type"] == "dinner"


@patch("app.services.photo_service.settings")
@patch("app.services.photo_service.Anthropic")
def test_extract_handles_markdown_fences(mock_anthropic_cls, mock_settings, client):
    """Claude sometimes wraps JSON in markdown code fences — we should handle that."""
    mock_settings.anthropic_api_key = "sk-ant-test-key"

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    # Wrap the JSON in markdown fences
    fenced_json = "```json\n" + json.dumps(SAMPLE_CLAUDE_RESPONSE) + "\n```"
    mock_client.messages.create.return_value = _mock_claude_response(fenced_json)

    img_buf = _make_test_image()
    response = client.post(
        "/api/recipes/extract-from-photo",
        files={"photos": ("test.jpg", img_buf, "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json()[0]["title"] == "Honey Garlic Salmon"


@patch("app.services.photo_service.settings")
@patch("app.services.photo_service.Anthropic")
def test_extract_bad_json_returns_422(mock_anthropic_cls, mock_settings, client):
    """If Claude returns unparseable text, we should get a clear error."""
    mock_settings.anthropic_api_key = "sk-ant-test-key"

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_claude_response(
        "I can see a recipe but I'm not sure about the ingredients..."
    )

    img_buf = _make_test_image()
    response = client.post(
        "/api/recipes/extract-from-photo",
        files={"photos": ("test.jpg", img_buf, "image/jpeg")},
    )

    assert response.status_code == 422
    assert "parse" in response.json()["detail"].lower()


@patch("app.services.photo_service.settings")
def test_extract_no_api_key(mock_settings, client):
    """Should return a helpful error if the API key isn't configured."""
    mock_settings.anthropic_api_key = ""

    img_buf = _make_test_image()
    response = client.post(
        "/api/recipes/extract-from-photo",
        files={"photos": ("test.jpg", img_buf, "image/jpeg")},
    )

    assert response.status_code == 500
    assert "API key" in response.json()["detail"]


@patch("app.services.photo_service.settings")
@patch("app.services.photo_service.Anthropic")
def test_full_round_trip(mock_anthropic_cls, mock_settings, client):
    """Extract from photo, then save the recipe — full workflow."""
    mock_settings.anthropic_api_key = "sk-ant-test-key"

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_claude_response(
        json.dumps(SAMPLE_CLAUDE_RESPONSE)
    )

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
