/**
 * Main entry point. Wires up tabs, recipe list, form, photo upload,
 * size toggle, recipe selection, and shopping list.
 */

const API = "/api/recipes";

document.addEventListener("DOMContentLoaded", () => {
  const recipeList = document.getElementById("recipe-list");
  const formContainer = document.getElementById("form-container");
  const addDropdown = document.getElementById("add-dropdown");
  const addToggle = document.getElementById("add-recipe-toggle");
  const addMenu = document.getElementById("add-menu");
  const sizeToggle = document.getElementById("size-toggle");
  const shoppingContent = document.getElementById("shopping-content");
  const shoppingBadge = document.getElementById("shopping-badge");
  const filterMeal = document.getElementById("filter-meal");
  const filterProtein = document.getElementById("filter-protein");
  const filterCuisine = document.getElementById("filter-cuisine");

  // --- Tab switching ---
  const tabs = document.querySelectorAll(".tab");
  const tabPanels = {
    recipes: document.getElementById("tab-recipes"),
    shopping: document.getElementById("tab-shopping"),
  };

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.dataset.tab;
      // Update active tab
      tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === target));
      // Show/hide panels
      Object.entries(tabPanels).forEach(([name, panel]) => {
        panel.classList.toggle("hidden", name !== target);
      });
      // Generate shopping list when switching to that tab
      if (target === "shopping") {
        renderShoppingTab();
      }
    });
  });

  // --- Recipe selection for shopping list ---
  const selectedRecipes = new Set();

  function toggleRecipeSelection(id, selected) {
    if (selected) {
      selectedRecipes.add(id);
    } else {
      selectedRecipes.delete(id);
    }
    updateShoppingBadge();
  }

  function updateShoppingBadge() {
    const count = selectedRecipes.size;
    shoppingBadge.textContent = count;
    shoppingBadge.classList.toggle("hidden", count === 0);
  }

  function renderShoppingTab() {
    shoppingContent.innerHTML = "";
    const list = createShoppingList(selectedRecipes);
    shoppingContent.appendChild(list);
  }

  // --- Card size toggle ---
  const savedSize = localStorage.getItem("cardSize") || "large";
  setCardSize(savedSize);

  sizeToggle.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-size]");
    if (!btn) return;
    setCardSize(btn.dataset.size);
  });

  function setCardSize(size) {
    recipeList.className = `recipe-grid ${size}`;
    localStorage.setItem("cardSize", size);
    sizeToggle.querySelectorAll("[data-size]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.size === size);
    });
  }

  // --- Dropdown menu ---
  addToggle.addEventListener("click", () => {
    addMenu.classList.toggle("hidden");
  });

  document.addEventListener("click", (e) => {
    if (!addDropdown.contains(e.target)) {
      addMenu.classList.add("hidden");
    }
  });

  addMenu.addEventListener("click", (e) => {
    const item = e.target.closest("[data-action]");
    if (!item) return;
    addMenu.classList.add("hidden");

    const action = item.dataset.action;
    if (action === "manual") showForm();
    else if (action === "photo") showPhotoUpload();
    else if (action === "url") showUrlImport();
  });

  // --- Filters ---
  filterMeal.addEventListener("change", () => loadRecipes());
  filterProtein.addEventListener("change", () => loadRecipes());
  filterCuisine.addEventListener("change", () => loadRecipes());

  // --- Load and render ---
  loadRecipes();

  async function loadRecipes() {
    recipeList.innerHTML = "";

    const response = await fetch(API);
    const allRecipes = await response.json();

    // Populate protein and cuisine filter dropdowns from actual data
    updateFilterOptions(allRecipes);

    // Apply filters
    const mealFilter = filterMeal.value;
    const proteinFilter = filterProtein.value;
    const cuisineFilter = filterCuisine.value;

    const recipes = allRecipes.filter((r) => {
      if (mealFilter && r.meal_type !== mealFilter) return false;
      if (proteinFilter && (r.protein_type || "") !== proteinFilter) return false;
      if (cuisineFilter && (r.cuisine || "") !== cuisineFilter) return false;
      return true;
    });

    if (recipes.length === 0) {
      recipeList.innerHTML = `
        <div class="empty-state">
          ${allRecipes.length === 0
            ? 'No recipes yet. Click "+ Add Recipe" to get started!'
            : "No recipes match the current filters."}
        </div>
      `;
      return;
    }

    for (const recipe of recipes) {
      const card = createRecipeCard(recipe, {
        onView: viewRecipe,
        onEdit: editRecipe,
        onDelete: deleteRecipe,
        onUploadDishPhoto: uploadDishPhoto,
        onToggleSelect: toggleRecipeSelection,
        isSelected: selectedRecipes.has(recipe.id),
      });
      recipeList.appendChild(card);
    }
  }

  function updateFilterOptions(recipes) {
    const proteins = new Set();
    const cuisines = new Set();
    for (const r of recipes) {
      if (r.protein_type) proteins.add(r.protein_type);
      if (r.cuisine) cuisines.add(r.cuisine);
    }

    // Preserve current selection
    const currentProtein = filterProtein.value;
    const currentCuisine = filterCuisine.value;

    filterProtein.innerHTML = '<option value="">All Proteins</option>';
    for (const p of [...proteins].sort()) {
      filterProtein.innerHTML += `<option value="${p}">${p}</option>`;
    }
    filterProtein.value = currentProtein;

    filterCuisine.innerHTML = '<option value="">All Cuisines</option>';
    for (const c of [...cuisines].sort()) {
      filterCuisine.innerHTML += `<option value="${c}">${c}</option>`;
    }
    filterCuisine.value = currentCuisine;
  }

  // --- Dish photo upload ---
  async function uploadDishPhoto(recipeId, file) {
    const formData = new FormData();
    formData.append("photo", file);

    await fetch(`${API}/${recipeId}/dish-photo`, {
      method: "POST",
      body: formData,
    });

    loadRecipes();
  }

  // --- View recipe ---
  async function viewRecipe(id) {
    const response = await fetch(`${API}/${id}`);
    const recipe = await response.json();

    formContainer.classList.remove("hidden");
    hideToolbarButtons();
    recipeList.classList.add("hidden");

    const detail = createRecipeDetail(recipe, {
      onEdit: (recipe) => editRecipe(recipe.id),
      onClose: () => {
        hideForm();
        recipeList.classList.remove("hidden");
        loadRecipes();
      },
      onUploadDishPhoto: async (recipeId, file) => {
        const formData = new FormData();
        formData.append("photo", file);
        await fetch(`${API}/${recipeId}/dish-photo`, {
          method: "POST",
          body: formData,
        });
        viewRecipe(recipeId);
      },
    });

    formContainer.innerHTML = "";
    formContainer.appendChild(detail);
  }

  // --- Edit recipe ---
  async function editRecipe(id) {
    const response = await fetch(`${API}/${id}`);
    const recipe = await response.json();

    formContainer.classList.remove("hidden");
    hideToolbarButtons();
    recipeList.classList.add("hidden");

    const form = createRecipeForm({
      initialData: recipe,
      onSubmit: async (data) => {
        await fetch(`${API}/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        hideForm();
        recipeList.classList.remove("hidden");
        loadRecipes();
      },
      onCancel: () => {
        hideForm();
        recipeList.classList.remove("hidden");
      },
    });

    formContainer.innerHTML = "";
    formContainer.appendChild(form);
  }

  // --- Create recipe form ---
  function showForm(initialData = null) {
    formContainer.classList.remove("hidden");
    hideToolbarButtons();

    const form = createRecipeForm({
      initialData,
      onSubmit: async (data) => {
        await fetch(API, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        hideForm();
        loadRecipes();
      },
      onCancel: hideForm,
    });

    formContainer.innerHTML = "";
    formContainer.appendChild(form);
  }

  function showPhotoUpload() {
    formContainer.classList.remove("hidden");
    hideToolbarButtons();

    const upload = createPhotoUpload({
      onExtracted: (data) => {
        const recipes = Array.isArray(data) ? data : [data];

        if (recipes.length === 1) {
          showForm(recipes[0]);
        } else {
          // Multiple recipes detected — show them all for review
          showMultiRecipeReview(recipes);
        }
      },
      onCancel: hideForm,
    });

    formContainer.innerHTML = "";
    formContainer.appendChild(upload);
  }

  function showUrlImport() {
    formContainer.classList.remove("hidden");
    hideToolbarButtons();

    const urlImport = createUrlImport({
      onExtracted: (data) => showForm(data),
      onCancel: hideForm,
    });

    formContainer.innerHTML = "";
    formContainer.appendChild(urlImport);
  }

  function showMultiRecipeReview(recipes) {
    formContainer.classList.remove("hidden");
    hideToolbarButtons();

    const wrapper = document.createElement("div");
    wrapper.innerHTML = `
      <div class="multi-recipe-header">
        <h2>${recipes.length} recipes detected</h2>
        <p class="text-muted">Review each recipe below, then save.</p>
      </div>
    `;

    let savedCount = 0;

    for (let i = 0; i < recipes.length; i++) {
      const recipeData = recipes[i];
      recipeData.meal_type = recipeData.meal_type || "dinner";

      // Wrap each form in a container div so we can replace just this one
      const formWrapper = document.createElement("div");
      formWrapper.className = "multi-recipe-item";

      const form = createRecipeForm({
        initialData: recipeData,
        onSubmit: async (data) => {
          data.source_type = data.source_type || "cookbook";
          await fetch(API, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
          // Replace just this form with a saved confirmation
          formWrapper.innerHTML = `<div class="recipe-form"><p class="text-muted">Saved: ${data.title}</p></div>`;
          savedCount++;
          // If all saved, go back to list
          if (savedCount === recipes.length) {
            hideForm();
            loadRecipes();
          }
        },
        onCancel: () => {},
      });

      formWrapper.appendChild(form);
      wrapper.appendChild(formWrapper);
    }

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-secondary";
    cancelBtn.textContent = "Cancel All";
    cancelBtn.style.marginTop = "1rem";
    cancelBtn.addEventListener("click", hideForm);
    wrapper.appendChild(cancelBtn);

    formContainer.innerHTML = "";
    formContainer.appendChild(wrapper);
  }

  function hideToolbarButtons() {
    addDropdown.classList.add("hidden");
  }

  function showToolbarButtons() {
    addDropdown.classList.remove("hidden");
    addMenu.classList.add("hidden");
  }

  function hideForm() {
    formContainer.classList.add("hidden");
    formContainer.innerHTML = "";
    showToolbarButtons();
  }

  // --- Delete ---
  async function deleteRecipe(id) {
    if (!confirm("Delete this recipe?")) return;
    selectedRecipes.delete(id);
    updateShoppingBadge();

    await fetch(`${API}/${id}`, { method: "DELETE" });
    loadRecipes();
  }
});
