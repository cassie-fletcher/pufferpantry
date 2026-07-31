"""Tests for recipe extraction from URLs.

Follows the same convention as test_photo_extraction.py: the Claude API is
mocked (no real API key, no network), and here the page fetch (httpx.get)
is mocked too — no real URLs are ever hit.
"""

import json
from unittest.mock import MagicMock, patch


SAMPLE_URL_RESPONSE = {
    "title": "Green Goddess Salmon",
    "servings": 4,
    "protein_type": "salmon",
    "cuisine": "American",
    "calories_per_serving": 450,
    "instructions": "1.) Season the salmon.\n\n2.) Roast at 425F for 12 minutes.",
    "notes": "Great with rice.",
    "ingredients": [
        {
            "name": "salmon fillets",
            "amount": "4",
            "unit": "6-oz",
            "order": 0,
            "group": "Main",
            "category": "Meat & Seafood",
        },
        {
            # amount deliberately numeric — the model sometimes drifts from
            # the prompt's string convention; the schema must coerce it.
            "name": "fresh herbs",
            "amount": 1,
            "unit": "cup",
            "order": 0,
            "group": "Green Goddess Dressing",
            "category": "Produce",
        },
    ],
}

TEST_URL = "https://example.com/recipes/green-goddess-salmon"

# Long enough to pass the "page appears to have no recipe content" (>100 chars)
# check after tag stripping; no og:image so no image download is attempted.
PAGE_HTML = (
    "<html><body><article>"
    + "Green Goddess Salmon. Season the salmon and roast it. " * 10
    + "</article></body></html>"
)


def _mock_message(text: str, stop_reason: str = "end_turn", content=None) -> MagicMock:
    """Build a mock Messages API response.

    The content deliberately puts a thinking block *first* — on current
    Claude models thinking is on by default, so `content[0]` is not a text
    block. The service must scan for the first text block.
    """
    thinking_block = MagicMock()
    thinking_block.type = "thinking"
    thinking_block.thinking = ""

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    message = MagicMock()
    message.content = [thinking_block, text_block] if content is None else content
    message.stop_reason = stop_reason
    message.model = "mock-model"
    message.usage.input_tokens = 1000
    message.usage.output_tokens = 200
    return message


def _mock_page_response() -> MagicMock:
    resp = MagicMock()
    resp.text = PAGE_HTML
    resp.raise_for_status = MagicMock()
    return resp


# --- Request model validation (no mocks needed: rejected before the service runs) ---


def test_missing_url_rejected(client):
    response = client.post("/api/recipes/extract-from-url", json={})
    assert response.status_code == 422


def test_blank_url_rejected(client):
    response = client.post("/api/recipes/extract-from-url", json={"url": "   "})
    assert response.status_code == 422


def test_wrong_type_url_rejected(client):
    response = client.post("/api/recipes/extract-from-url", json={"url": 12345})
    assert response.status_code == 422


# --- Response handling (mocked Claude + mocked page fetch) ---


@patch("app.services.url_service.httpx.get")
@patch("app.services.url_service.settings")
@patch("app.services.url_service.Anthropic")
def test_extract_from_url_multi_block_response(
    mock_anthropic_cls, mock_settings, mock_httpx_get, client
):
    """A response with a thinking block first still extracts the text block,
    and the model id comes from settings.claude_model_fast."""
    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_settings.claude_model_fast = "claude-test-model-from-settings"
    mock_httpx_get.return_value = _mock_page_response()

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_message(
        json.dumps(SAMPLE_URL_RESPONSE)
    )

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Green Goddess Salmon"
    assert data["servings"] == 4
    assert data["protein_type"] == "salmon"
    assert data["cuisine"] == "American"
    assert data["meal_type"] == "dinner"
    assert data["source_type"] == "website"
    assert data["source_details"] == TEST_URL
    assert len(data["ingredients"]) == 2
    # Numeric amount coerced to string by the schema
    assert data["ingredients"][1]["amount"] == "1"

    # Model id must come from settings, not a hardcoded literal
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-test-model-from-settings"
    # No assistant prefill — prefills 400 on current models
    assert all(m["role"] == "user" for m in call_kwargs["messages"])


@patch("app.services.url_service.httpx.get")
@patch("app.services.url_service.settings")
@patch("app.services.url_service.Anthropic")
def test_extract_handles_markdown_fences(
    mock_anthropic_cls, mock_settings, mock_httpx_get, client
):
    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_settings.claude_model_fast = "claude-test-model-from-settings"
    mock_httpx_get.return_value = _mock_page_response()

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    fenced = "```json\n" + json.dumps(SAMPLE_URL_RESPONSE) + "\n```"
    mock_client.messages.create.return_value = _mock_message(fenced)

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 200
    assert response.json()["title"] == "Green Goddess Salmon"


@patch("app.services.url_service.httpx.get")
@patch("app.services.url_service.settings")
@patch("app.services.url_service.Anthropic")
def test_extract_handles_preamble_text(
    mock_anthropic_cls, mock_settings, mock_httpx_get, client
):
    """Without a prefill, the model may emit prose before the JSON."""
    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_settings.claude_model_fast = "claude-test-model-from-settings"
    mock_httpx_get.return_value = _mock_page_response()

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_message(
        "Here is the extracted recipe:\n" + json.dumps(SAMPLE_URL_RESPONSE)
    )

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 200
    assert response.json()["title"] == "Green Goddess Salmon"


@patch("app.services.url_service.httpx.get")
@patch("app.services.url_service.settings")
@patch("app.services.url_service.Anthropic")
def test_refusal_stop_reason_raises_cleanly(
    mock_anthropic_cls, mock_settings, mock_httpx_get, client
):
    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_settings.claude_model_fast = "claude-test-model-from-settings"
    mock_httpx_get.return_value = _mock_page_response()

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    # Refusals can carry an empty content array — must not be indexed
    mock_client.messages.create.return_value = _mock_message(
        "", stop_reason="refusal", content=[]
    )

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 422
    assert "declined" in response.json()["detail"].lower()


@patch("app.services.url_service.httpx.get")
@patch("app.services.url_service.settings")
@patch("app.services.url_service.Anthropic")
def test_max_tokens_stop_reason_raises_cleanly(
    mock_anthropic_cls, mock_settings, mock_httpx_get, client
):
    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_settings.claude_model_fast = "claude-test-model-from-settings"
    mock_httpx_get.return_value = _mock_page_response()

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_message(
        '{"title": "Truncat', stop_reason="max_tokens"
    )

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 502
    assert "truncated" in response.json()["detail"].lower()


@patch("app.services.url_service.httpx.get")
@patch("app.services.url_service.settings")
@patch("app.services.url_service.Anthropic")
def test_bad_json_returns_422(mock_anthropic_cls, mock_settings, mock_httpx_get, client):
    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_settings.claude_model_fast = "claude-test-model-from-settings"
    mock_httpx_get.return_value = _mock_page_response()

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_message(
        "I found a recipe but couldn't read the ingredients clearly."
    )

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 422
    assert "parse" in response.json()["detail"].lower()


@patch("app.services.url_service.httpx.get")
@patch("app.services.url_service.settings")
@patch("app.services.url_service.Anthropic")
def test_schema_mismatch_returns_422(
    mock_anthropic_cls, mock_settings, mock_httpx_get, client
):
    """Valid JSON that doesn't fit the recipe schema is a clear 422, not a
    silently-forwarded arbitrary dict."""
    mock_settings.anthropic_api_key = "sk-ant-test-key"
    mock_settings.claude_model_fast = "claude-test-model-from-settings"
    mock_httpx_get.return_value = _mock_page_response()

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    # Missing required "title"
    mock_client.messages.create.return_value = _mock_message(
        json.dumps({"servings": 4, "ingredients": []})
    )

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 422
    assert "format" in response.json()["detail"].lower()


@patch("app.services.url_service.settings")
def test_no_api_key_returns_500(mock_settings, client):
    mock_settings.anthropic_api_key = ""

    response = client.post("/api/recipes/extract-from-url", json={"url": TEST_URL})

    assert response.status_code == 500
    assert "API key" in response.json()["detail"]
