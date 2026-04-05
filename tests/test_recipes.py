"""Tests for the /api/recipes endpoints."""


def test_list_recipes_empty(client):
    response = client.get("/api/recipes")
    assert response.status_code == 200
    assert response.json() == []


def test_create_recipe(client, sample_recipe):
    response = client.post("/api/recipes", json=sample_recipe)
    assert response.status_code == 201

    data = response.json()
    assert data["title"] == "Sheet Pan Lemon Herb Chicken"
    assert data["meal_type"] == "dinner"
    assert data["servings"] == 2
    assert data["calories_per_serving"] == 480
    assert len(data["ingredients"]) == 3
    assert data["ingredients"][0]["name"] == "chicken thighs"
    assert data["id"] is not None
    assert data["created_at"] is not None


def test_list_recipes_after_create(client, sample_recipe):
    client.post("/api/recipes", json=sample_recipe)

    response = client.get("/api/recipes")
    recipes = response.json()
    assert len(recipes) == 1
    assert recipes[0]["title"] == "Sheet Pan Lemon Herb Chicken"
    # List view should not include ingredients
    assert "ingredients" not in recipes[0]


def test_get_recipe(client, sample_recipe):
    create_response = client.post("/api/recipes", json=sample_recipe)
    recipe_id = create_response.json()["id"]

    response = client.get(f"/api/recipes/{recipe_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["title"] == "Sheet Pan Lemon Herb Chicken"
    assert len(data["ingredients"]) == 3


def test_get_recipe_not_found(client):
    response = client.get("/api/recipes/999")
    assert response.status_code == 404


def test_update_recipe(client, sample_recipe):
    create_response = client.post("/api/recipes", json=sample_recipe)
    recipe_id = create_response.json()["id"]

    response = client.put(f"/api/recipes/{recipe_id}", json={
        "title": "Updated Chicken",
        "calories_per_serving": 500,
        "ingredients": [
            {"name": "chicken breast", "amount": "1.5", "unit": "lb", "order": 0},
        ],
    })
    assert response.status_code == 200

    data = response.json()
    assert data["title"] == "Updated Chicken"
    assert data["calories_per_serving"] == 500
    assert len(data["ingredients"]) == 1
    assert data["ingredients"][0]["name"] == "chicken breast"


def test_delete_recipe(client, sample_recipe):
    create_response = client.post("/api/recipes", json=sample_recipe)
    recipe_id = create_response.json()["id"]

    response = client.delete(f"/api/recipes/{recipe_id}")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # Verify it's gone
    response = client.get(f"/api/recipes/{recipe_id}")
    assert response.status_code == 404


def test_delete_recipe_not_found(client):
    response = client.delete("/api/recipes/999")
    assert response.status_code == 404


def test_create_recipe_minimal(client):
    """Only required fields — title and meal_type."""
    response = client.post("/api/recipes", json={
        "title": "Quick Oats",
        "meal_type": "breakfast",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["servings"] == 2  # default
    assert data["ingredients"] == []
    assert data["calories_per_serving"] is None
