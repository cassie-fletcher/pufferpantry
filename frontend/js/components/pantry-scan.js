/**
 * Pantry scan component — upload shelf photos, Claude identifies items,
 * user reviews/edits the draft, then bulk-saves to inventory.
 *
 * Supports two modes:
 * - Generic mode (no zoneId): uses /api/pantry/scan, saves via /api/pantry/bulk
 * - Zone-aware mode (zoneId provided): uses /api/storage-areas/{areaId}/zones/{zoneId}/scan,
 *   shows diff indicators, saves via /api/pantry/zone-bulk
 */
function createPantryScan({
  onItemsConfirmed,
  onCancel,
  zoneId = null,
  storageAreaId = null,
  zoneName = null,
}) {
  const isZoneMode = zoneId !== null;

  const container = document.createElement("div");
  container.className = "recipe-form pantry-scan";

  const storageLocations = ["Fridge", "Freezer", "Pantry", "Spice Rack", "Counter", "Other"];
  const categories = [
    "", "Produce", "Meat & Seafood", "Dairy", "Bakery", "Frozen",
    "Drinks", "Pantry", "Condiments", "Spices",
  ];
  const quantityLevels = ["Full", "Most", "Half", "Low", "Almost Empty"];

  const headerText = isZoneMode
    ? `Scan: ${zoneName || "Zone"}`
    : "Scan Shelf Photo";

  container.innerHTML = `
    <h2>${headerText}</h2>
    <p class="text-muted scan-tip">For best results, take close-up photos with good lighting.</p>

    ${isZoneMode ? "" : `
    <div class="form-group">
      <label for="scan-location">Where are these photos from?</label>
      <select id="scan-location">
        ${storageLocations.map(l => `<option value="${l}">${l}</option>`).join("")}
      </select>
    </div>
    `}

    <div class="drop-zone" id="scan-drop-zone">
      <input type="file" accept="image/*" multiple class="hidden" id="scan-photo-input">
      <p class="drop-zone-text">
        Drag &amp; drop photos here, or <button type="button" class="btn-link" id="scan-browse-btn">browse</button>
      </p>
      <p class="drop-zone-hint">Select multiple photos to capture different angles</p>
    </div>

    <div class="photo-thumbnails" id="scan-thumbnails"></div>

    <div class="form-actions" id="scan-upload-actions">
      <button type="button" class="btn btn-primary hidden" id="scan-btn">Scan Items</button>
      <button type="button" class="btn btn-secondary" id="scan-cancel-btn">Cancel</button>
    </div>

    <div class="loading hidden" id="scan-loading">
      <div class="loading-text">Identifying items in your photos...</div>
    </div>

    <div class="error-message hidden" id="scan-error"></div>

    <!-- Review section (shown after scan) -->
    <div class="hidden" id="scan-review">
      <div class="scan-review-header">
        <h3 id="scan-review-title">Found items</h3>
        <button type="button" class="btn btn-secondary btn-small" id="scan-add-row-btn">+ Add Missing Item</button>
      </div>

      <div class="photo-thumbnails" id="scan-review-thumbs"></div>

      <div id="scan-review-table"></div>

      <div class="form-actions">
        <button type="button" class="btn btn-primary" id="scan-save-btn">Save All</button>
        <button type="button" class="btn btn-secondary" id="scan-review-cancel-btn">Cancel</button>
      </div>
    </div>
  `;

  // --- Upload phase ---
  const dropZone = container.querySelector("#scan-drop-zone");
  const fileInput = container.querySelector("#scan-photo-input");
  const browseBtn = container.querySelector("#scan-browse-btn");
  const thumbnails = container.querySelector("#scan-thumbnails");
  const scanBtn = container.querySelector("#scan-btn");
  const uploadActions = container.querySelector("#scan-upload-actions");
  const loading = container.querySelector("#scan-loading");
  const errorMsg = container.querySelector("#scan-error");

  let files = [];

  browseBtn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    addFiles(Array.from(fileInput.files));
    fileInput.value = "";
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });

  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    addFiles(Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("image/")));
  });

  function addFiles(newFiles) {
    files.push(...newFiles);
    renderUploadThumbnails();
    scanBtn.classList.toggle("hidden", files.length === 0);
  }

  function renderUploadThumbnails() {
    thumbnails.innerHTML = "";
    files.forEach((file, i) => {
      const thumb = document.createElement("div");
      thumb.className = "photo-thumb";
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = `Photo ${i + 1}`;
      const removeBtn = document.createElement("button");
      removeBtn.className = "thumb-remove";
      removeBtn.textContent = "\u00d7";
      removeBtn.addEventListener("click", () => {
        files.splice(i, 1);
        renderUploadThumbnails();
        scanBtn.classList.toggle("hidden", files.length === 0);
      });
      thumb.appendChild(img);
      thumb.appendChild(removeBtn);
      thumbnails.appendChild(thumb);
    });
  }

  // --- Scan ---
  scanBtn.addEventListener("click", async () => {
    if (files.length === 0) return;

    scanBtn.classList.add("hidden");
    dropZone.classList.add("hidden");
    thumbnails.classList.add("hidden");
    uploadActions.classList.add("hidden");
    container.querySelector(".scan-tip").classList.add("hidden");
    const locationGroup = container.querySelector("#scan-location");
    if (locationGroup) locationGroup.closest(".form-group").classList.add("hidden");
    loading.classList.remove("hidden");
    errorMsg.classList.add("hidden");

    try {
      const formData = new FormData();
      for (const file of files) {
        formData.append("photos", file);
      }

      let url;
      if (isZoneMode) {
        url = `/api/storage-areas/${storageAreaId}/zones/${zoneId}/scan`;
      } else {
        const location = container.querySelector("#scan-location").value;
        formData.append("storage_location", location);
        url = "/api/pantry/scan";
      }

      const response = await fetch(url, { method: "POST", body: formData });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to scan photos");
      }

      const items = await response.json();
      loading.classList.add("hidden");

      if (isZoneMode) {
        showZoneReview(items);
      } else {
        const location = container.querySelector("#scan-location").value;
        showReview(items, location);
      }
    } catch (err) {
      loading.classList.add("hidden");
      dropZone.classList.remove("hidden");
      thumbnails.classList.remove("hidden");
      uploadActions.classList.remove("hidden");
      scanBtn.classList.remove("hidden");
      container.querySelector(".scan-tip").classList.remove("hidden");
      const locationGroup = container.querySelector("#scan-location");
      if (locationGroup) locationGroup.closest(".form-group").classList.remove("hidden");
      errorMsg.textContent = err.message;
      errorMsg.classList.remove("hidden");
    }
  });

  container.querySelector("#scan-cancel-btn").addEventListener("click", onCancel);

  // --- Generic review phase (non-zone mode) ---
  let reviewItems = [];

  function showReview(items, location) {
    reviewItems = items.map((item, i) => ({
      _index: i,
      name: item.name || "",
      quantity_level: item.quantity_level || "Half",
      category: item.category || "",
      confidence_note: item.confidence_note || null,
      storage_location: location,
      photo_filename: item.photo_filename || null,
    }));

    const reviewSection = container.querySelector("#scan-review");
    reviewSection.classList.remove("hidden");

    container.querySelector("#scan-review-title").textContent =
      `Found ${reviewItems.length} item${reviewItems.length !== 1 ? "s" : ""} in your ${location}`;

    renderReviewThumbs();
    renderReviewTable();
  }

  // --- Zone-aware review phase ---
  let zoneReviewItems = [];

  function showZoneReview(items) {
    zoneReviewItems = items.map((item, i) => ({
      _index: i,
      name: item.name || "",
      quantity_level: item.quantity_level || "Half",
      category: item.category || "",
      confidence_note: item.confidence_note || null,
      match_action: item.match_action || "new",
      matched_item_id: item.matched_item_id || null,
      zone_id: zoneId,
      storage_location: item.storage_location || "",
      photo_filename: item.photo_filename || null,
      // User decision for removed items: "delete" or "keep"
      user_action: item.match_action === "removed" ? "keep" : null,
    }));

    const reviewSection = container.querySelector("#scan-review");
    reviewSection.classList.remove("hidden");

    const counts = { new: 0, updated: 0, unchanged: 0, removed: 0 };
    zoneReviewItems.forEach(item => { counts[item.match_action] = (counts[item.match_action] || 0) + 1; });

    container.querySelector("#scan-review-title").textContent =
      `${zoneName}: ${zoneReviewItems.length} items`;

    renderReviewThumbs();
    renderZoneReviewTable();
  }

  function renderReviewThumbs() {
    const reviewThumbs = container.querySelector("#scan-review-thumbs");
    reviewThumbs.innerHTML = "";
    files.forEach((file) => {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.className = "scan-review-thumb-img";
      reviewThumbs.appendChild(img);
    });
  }

  function renderReviewTable() {
    const table = container.querySelector("#scan-review-table");
    table.innerHTML = "";

    reviewItems.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = "scan-review-row";

      row.innerHTML = `
        <input type="text" class="scan-review-name" value="${item.name.replace(/"/g, "&quot;")}" placeholder="Item name">
        <select class="scan-review-level">
          ${quantityLevels.map(q => `<option value="${q}" ${item.quantity_level === q ? "selected" : ""}>${q}</option>`).join("")}
        </select>
        <select class="scan-review-category">
          ${categories.map(c => `<option value="${c}" ${item.category === c ? "selected" : ""}>${c || "— Category —"}</option>`).join("")}
        </select>
        <button class="btn btn-danger btn-small scan-review-remove" title="Remove">&times;</button>
      `;

      if (item.confidence_note) {
        const note = document.createElement("div");
        note.className = "confidence-warning";
        note.textContent = item.confidence_note;
        row.appendChild(note);
      }

      row.querySelector(".scan-review-name").addEventListener("input", (e) => { reviewItems[idx].name = e.target.value; });
      row.querySelector(".scan-review-level").addEventListener("change", (e) => { reviewItems[idx].quantity_level = e.target.value; });
      row.querySelector(".scan-review-category").addEventListener("change", (e) => { reviewItems[idx].category = e.target.value; });
      row.querySelector(".scan-review-remove").addEventListener("click", () => { reviewItems.splice(idx, 1); renderReviewTable(); });

      table.appendChild(row);
    });
  }

  function renderZoneReviewTable() {
    const table = container.querySelector("#scan-review-table");
    table.innerHTML = "";

    const actionBadges = {
      "new": '<span class="scan-badge scan-new">NEW</span>',
      "updated": '<span class="scan-badge scan-updated">UPDATED</span>',
      "unchanged": '<span class="scan-badge scan-unchanged">NO CHANGE</span>',
      "removed": '<span class="scan-badge scan-removed">MISSING?</span>',
    };

    zoneReviewItems.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = `scan-review-row ${item.match_action === "removed" ? "scan-row-removed" : ""}`;

      const badge = actionBadges[item.match_action] || "";

      if (item.match_action === "removed") {
        // Removed items: show name + toggle to keep/delete
        row.innerHTML = `
          ${badge}
          <span class="scan-review-name-text scan-strikethrough">${item.name}</span>
          <span class="text-muted">${item.quantity_level || ""}</span>
          <select class="scan-review-removed-action">
            <option value="keep" ${item.user_action === "keep" ? "selected" : ""}>Keep (might be hidden)</option>
            <option value="delete" ${item.user_action === "delete" ? "selected" : ""}>Remove from inventory</option>
          </select>
        `;
        row.querySelector(".scan-review-removed-action").addEventListener("change", (e) => {
          zoneReviewItems[idx].user_action = e.target.value;
        });
      } else {
        // New, updated, unchanged items: editable
        row.innerHTML = `
          ${badge}
          <input type="text" class="scan-review-name" value="${item.name.replace(/"/g, "&quot;")}" placeholder="Item name">
          <select class="scan-review-level">
            ${quantityLevels.map(q => `<option value="${q}" ${item.quantity_level === q ? "selected" : ""}>${q}</option>`).join("")}
          </select>
          <select class="scan-review-category">
            ${categories.map(c => `<option value="${c}" ${item.category === c ? "selected" : ""}>${c || "— Category —"}</option>`).join("")}
          </select>
          <button class="btn btn-danger btn-small scan-review-remove" title="Remove">&times;</button>
        `;

        if (item.confidence_note) {
          const note = document.createElement("div");
          note.className = "confidence-warning";
          note.textContent = item.confidence_note;
          row.appendChild(note);
        }

        const nameInput = row.querySelector(".scan-review-name");
        if (nameInput) nameInput.addEventListener("input", (e) => { zoneReviewItems[idx].name = e.target.value; });
        row.querySelector(".scan-review-level").addEventListener("change", (e) => { zoneReviewItems[idx].quantity_level = e.target.value; });
        const catSelect = row.querySelector(".scan-review-category");
        if (catSelect) catSelect.addEventListener("change", (e) => { zoneReviewItems[idx].category = e.target.value; });
        const removeBtn = row.querySelector(".scan-review-remove");
        if (removeBtn) removeBtn.addEventListener("click", () => { zoneReviewItems.splice(idx, 1); renderZoneReviewTable(); });
      }

      table.appendChild(row);
    });
  }

  // Add missing item
  container.querySelector("#scan-add-row-btn").addEventListener("click", () => {
    if (isZoneMode) {
      zoneReviewItems.push({
        _index: zoneReviewItems.length,
        name: "",
        quantity_level: "Full",
        category: "",
        confidence_note: null,
        match_action: "new",
        matched_item_id: null,
        zone_id: zoneId,
        storage_location: "",
        photo_filename: null,
        user_action: null,
      });
      renderZoneReviewTable();
    } else {
      const location = container.querySelector("#scan-location")?.value || "Fridge";
      reviewItems.push({
        _index: reviewItems.length,
        name: "",
        quantity_level: "Full",
        category: "",
        confidence_note: null,
        storage_location: location,
        photo_filename: null,
      });
      renderReviewTable();
    }
    const rows = container.querySelectorAll(".scan-review-name");
    if (rows.length) rows[rows.length - 1].focus();
  });

  // Save all
  container.querySelector("#scan-save-btn").addEventListener("click", async () => {
    try {
      if (isZoneMode) {
        await saveZoneResults();
      } else {
        await saveGenericResults();
      }
      onItemsConfirmed();
    } catch (err) {
      errorMsg.textContent = err.message;
      errorMsg.classList.remove("hidden");
    }
  });

  async function saveGenericResults() {
    const toSave = reviewItems
      .filter(item => item.name.trim())
      .map(item => ({
        name: item.name.trim(),
        storage_location: item.storage_location,
        category: item.category || null,
        quantity_level: item.quantity_level || null,
        photo_filename: item.photo_filename,
      }));

    if (toSave.length === 0) return;

    const response = await fetch("/api/pantry/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(toSave),
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "Failed to save items");
    }
  }

  async function saveZoneResults() {
    const actions = [];

    for (const item of zoneReviewItems) {
      if (item.match_action === "new" && item.name.trim()) {
        actions.push({
          action: "create",
          data: {
            name: item.name.trim(),
            storage_location: item.storage_location,
            category: item.category || null,
            quantity_level: item.quantity_level || null,
            photo_filename: item.photo_filename,
            zone_id: zoneId,
          },
        });
      } else if (item.match_action === "updated" && item.matched_item_id) {
        actions.push({
          action: "update",
          item_id: item.matched_item_id,
          data: {
            quantity_level: item.quantity_level || null,
            name: item.name.trim(),
            category: item.category || null,
          },
        });
      } else if (item.match_action === "removed" && item.user_action === "delete" && item.matched_item_id) {
        actions.push({
          action: "delete",
          item_id: item.matched_item_id,
        });
      }
      // "unchanged" and "removed" with "keep" are skipped
    }

    if (actions.length === 0) return;

    const response = await fetch("/api/pantry/zone-bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(actions),
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "Failed to save items");
    }
  }

  container.querySelector("#scan-review-cancel-btn").addEventListener("click", onCancel);

  return container;
}
