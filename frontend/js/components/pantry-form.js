/**
 * Pantry item form for adding/editing a single inventory item.
 * Supports both create and edit modes (determined by initialData.id).
 */
function createPantryItemForm({ onSubmit, onCancel, initialData = null }) {
  const isEdit = initialData && initialData.id;

  const container = document.createElement("div");
  container.className = "recipe-form";

  const storageLocations = ["Fridge", "Freezer", "Pantry", "Spice Rack", "Counter", "Other"];
  const categories = [
    "Produce", "Meat & Seafood", "Dairy", "Bakery", "Frozen",
    "Drinks", "Pantry", "Condiments", "Spices",
  ];
  const quantityLevels = ["Full", "Most", "Half", "Low", "Almost Empty"];

  container.innerHTML = `
    <h2>${isEdit ? "Edit Pantry Item" : "Add Pantry Item"}</h2>

    <div class="form-group">
      <label for="pantry-name">Item Name</label>
      <input type="text" id="pantry-name" value="${(initialData?.name || "").replace(/"/g, "&quot;")}" required>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label for="pantry-location">Storage Location</label>
        <select id="pantry-location">
          ${storageLocations.map(l => `<option value="${l}" ${initialData?.storage_location === l ? "selected" : ""}>${l}</option>`).join("")}
        </select>
      </div>
      <div class="form-group">
        <label for="pantry-category">Category</label>
        <select id="pantry-category">
          <option value="">— Select —</option>
          ${categories.map(c => `<option value="${c}" ${initialData?.category === c ? "selected" : ""}>${c}</option>`).join("")}
        </select>
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label for="pantry-qty-level">Quantity Level</label>
        <select id="pantry-qty-level">
          <option value="">— Select —</option>
          ${quantityLevels.map(q => `<option value="${q}" ${initialData?.quantity_level === q ? "selected" : ""}>${q}</option>`).join("")}
        </select>
      </div>
      <div class="form-group">
        <label>Exact Quantity (optional)</label>
        <div style="display:flex;gap:0.5rem;">
          <input type="text" id="pantry-qty" placeholder="e.g. 2" value="${initialData?.quantity || ""}" style="flex:1;">
          <input type="text" id="pantry-unit" placeholder="e.g. cans" value="${initialData?.unit || ""}" style="flex:1;">
        </div>
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label for="pantry-expiry">Expiration Date (optional)</label>
        <input type="date" id="pantry-expiry" value="${initialData?.expiration_date || ""}">
      </div>
      <div class="form-group"></div>
    </div>

    <div class="form-group">
      <label for="pantry-notes">Notes (optional)</label>
      <textarea id="pantry-notes" rows="2">${initialData?.notes || ""}</textarea>
    </div>

    <div class="form-actions">
      <button type="button" class="btn btn-primary" id="pantry-form-save">${isEdit ? "Update" : "Add Item"}</button>
      <button type="button" class="btn btn-secondary" id="pantry-form-cancel">Cancel</button>
    </div>
  `;

  container.querySelector("#pantry-form-save").addEventListener("click", () => {
    const name = container.querySelector("#pantry-name").value.trim();
    if (!name) {
      container.querySelector("#pantry-name").focus();
      return;
    }

    const data = {
      name,
      storage_location: container.querySelector("#pantry-location").value,
      category: container.querySelector("#pantry-category").value || null,
      quantity_level: container.querySelector("#pantry-qty-level").value || null,
      quantity: container.querySelector("#pantry-qty").value.trim() || null,
      unit: container.querySelector("#pantry-unit").value.trim() || null,
      expiration_date: container.querySelector("#pantry-expiry").value || null,
      notes: container.querySelector("#pantry-notes").value.trim() || null,
    };

    onSubmit(data);
  });

  container.querySelector("#pantry-form-cancel").addEventListener("click", onCancel);

  return container;
}
