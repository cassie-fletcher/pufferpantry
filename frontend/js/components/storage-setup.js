/**
 * Storage area setup wizard — multi-step flow for creating a fridge/freezer/pantry profile.
 * Step 1: Name + type
 * Step 2: Upload overview photo → Claude suggests zones
 * Step 3: Review/edit zone list
 * Step 4: Zone-by-zone initial scan
 */
function createStorageSetup({ onComplete, onCancel }) {
  const container = document.createElement("div");
  container.className = "recipe-form storage-setup";

  const areaTypes = ["Fridge", "Freezer", "Pantry", "Counter", "Spice Rack"];
  const zoneTypes = [
    "shelf", "crisper_drawer", "door_shelf", "pullout_tray",
    "drawer", "compartment", "rack", "bin",
  ];
  const categoryOptions = [
    "Produce", "Meat & Seafood", "Dairy", "Bakery", "Frozen",
    "Drinks", "Pantry", "Condiments", "Spices",
  ];

  let currentStep = 1;
  let areaName = "";
  let areaType = "Fridge";
  let suggestedZones = [];
  let savedAreaId = null;
  let savedZones = [];
  let scannedCount = 0;

  renderStep1();

  // --- Step 1: Name + Type ---
  function renderStep1() {
    container.innerHTML = `
      <h2>Set Up Storage Area</h2>
      <p class="text-muted">Tell us about the storage area you want to track.</p>

      <div class="form-row">
        <div class="form-group">
          <label for="setup-name">Name</label>
          <input type="text" id="setup-name" placeholder="e.g. Kitchen Fridge" value="${areaName}">
        </div>
        <div class="form-group">
          <label for="setup-type">Type</label>
          <select id="setup-type">
            ${areaTypes.map(t => `<option value="${t}" ${areaType === t ? "selected" : ""}>${t}</option>`).join("")}
          </select>
        </div>
      </div>

      <div class="form-actions">
        <button type="button" class="btn btn-primary" id="setup-next-1">Next</button>
        <button type="button" class="btn btn-secondary" id="setup-cancel-1">Cancel</button>
      </div>
    `;

    container.querySelector("#setup-next-1").addEventListener("click", () => {
      areaName = container.querySelector("#setup-name").value.trim();
      areaType = container.querySelector("#setup-type").value;
      if (!areaName) {
        container.querySelector("#setup-name").focus();
        return;
      }
      renderStep2();
    });
    container.querySelector("#setup-cancel-1").addEventListener("click", onCancel);
  }

  // --- Step 2: Overview Photo ---
  function renderStep2() {
    container.innerHTML = `
      <h2>Photo Your ${areaType}</h2>
      <p class="text-muted">Take a wide photo showing the full interior. We'll use this to identify the zones (shelves, drawers, etc.).</p>

      <div class="drop-zone" id="setup-drop-zone">
        <input type="file" accept="image/*" multiple class="hidden" id="setup-photo-input">
        <p class="drop-zone-text">
          Drag &amp; drop photo here, or <button type="button" class="btn-link" id="setup-browse-btn">browse</button>
        </p>
      </div>

      <div class="photo-thumbnails" id="setup-thumbnails"></div>

      <div class="form-actions">
        <button type="button" class="btn btn-primary hidden" id="setup-analyze-btn">Analyze Layout</button>
        <button type="button" class="btn btn-secondary" id="setup-skip-photo-btn">Skip — I'll add zones manually</button>
        <button type="button" class="btn btn-secondary" id="setup-back-2">Back</button>
      </div>

      <div class="loading hidden" id="setup-loading">
        <div class="loading-text">Analyzing your ${areaType} layout...</div>
      </div>

      <div class="error-message hidden" id="setup-error"></div>
    `;

    const dropZone = container.querySelector("#setup-drop-zone");
    const fileInput = container.querySelector("#setup-photo-input");
    const browseBtn = container.querySelector("#setup-browse-btn");
    const thumbnails = container.querySelector("#setup-thumbnails");
    const analyzeBtn = container.querySelector("#setup-analyze-btn");
    const loading = container.querySelector("#setup-loading");
    const errorMsg = container.querySelector("#setup-error");

    let files = [];

    browseBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      files.push(...Array.from(fileInput.files));
      fileInput.value = "";
      renderThumbs();
    });

    dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropZone.classList.remove("drag-over");
      files.push(...Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("image/")));
      renderThumbs();
    });

    function renderThumbs() {
      thumbnails.innerHTML = "";
      files.forEach((file, i) => {
        const thumb = document.createElement("div");
        thumb.className = "photo-thumb";
        const img = document.createElement("img");
        img.src = URL.createObjectURL(file);
        const removeBtn = document.createElement("button");
        removeBtn.className = "thumb-remove";
        removeBtn.textContent = "\u00d7";
        removeBtn.addEventListener("click", () => { files.splice(i, 1); renderThumbs(); });
        thumb.appendChild(img);
        thumb.appendChild(removeBtn);
        thumbnails.appendChild(thumb);
      });
      analyzeBtn.classList.toggle("hidden", files.length === 0);
    }

    analyzeBtn.addEventListener("click", async () => {
      analyzeBtn.classList.add("hidden");
      dropZone.classList.add("hidden");
      thumbnails.classList.add("hidden");
      loading.classList.remove("hidden");
      errorMsg.classList.add("hidden");

      try {
        const formData = new FormData();
        for (const file of files) formData.append("photos", file);
        formData.append("area_type", areaType);

        const response = await fetch("/api/storage-areas/setup-scan", {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          const err = await response.json();
          throw new Error(err.detail || "Failed to analyze layout");
        }

        const result = await response.json();
        suggestedZones = result.suggested_zones || [];
        loading.classList.add("hidden");
        renderStep3(result.description);
      } catch (err) {
        loading.classList.add("hidden");
        dropZone.classList.remove("hidden");
        thumbnails.classList.remove("hidden");
        analyzeBtn.classList.remove("hidden");
        errorMsg.textContent = err.message;
        errorMsg.classList.remove("hidden");
      }
    });

    container.querySelector("#setup-skip-photo-btn").addEventListener("click", () => {
      suggestedZones = [];
      renderStep3(null);
    });
    container.querySelector("#setup-back-2").addEventListener("click", renderStep1);
  }

  // --- Step 3: Review Zones ---
  function renderStep3(description) {
    container.innerHTML = `
      <h2>Review Zones for "${areaName}"</h2>
      ${description ? `<p class="text-muted">${description}</p>` : ""}

      <div id="setup-zones-list"></div>

      <div style="margin-top:1rem;">
        <button type="button" class="btn btn-secondary btn-small" id="setup-add-zone-btn">+ Add Zone</button>
      </div>

      <div class="form-actions">
        <button type="button" class="btn btn-primary" id="setup-save-profile">Save Profile</button>
        <button type="button" class="btn btn-secondary" id="setup-back-3">Back</button>
      </div>

      <div class="error-message hidden" id="setup-error"></div>
    `;

    renderZonesList();

    container.querySelector("#setup-add-zone-btn").addEventListener("click", () => {
      suggestedZones.push({
        name: "",
        zone_type: "shelf",
        typical_categories: [],
        typical_container_types: [],
        scan_strategy: "full_rescan",
      });
      renderZonesList();
    });

    container.querySelector("#setup-save-profile").addEventListener("click", saveProfile);
    container.querySelector("#setup-back-3").addEventListener("click", renderStep2);
  }

  function renderZonesList() {
    const list = container.querySelector("#setup-zones-list");
    list.innerHTML = "";

    suggestedZones.forEach((zone, idx) => {
      const card = document.createElement("div");
      card.className = "setup-zone-card";
      card.innerHTML = `
        <div class="setup-zone-row">
          <input type="text" class="setup-zone-name" value="${(zone.name || "").replace(/"/g, "&quot;")}" placeholder="Zone name">
          <select class="setup-zone-type">
            ${zoneTypes.map(t => `<option value="${t}" ${zone.zone_type === t ? "selected" : ""}>${t.replace(/_/g, " ")}</option>`).join("")}
          </select>
          <select class="setup-zone-strategy">
            <option value="full_rescan" ${zone.scan_strategy === "full_rescan" ? "selected" : ""}>Full Re-scan</option>
            <option value="spot_check" ${zone.scan_strategy === "spot_check" ? "selected" : ""}>Spot Check</option>
          </select>
          <button class="btn btn-danger btn-small setup-zone-remove" title="Remove">&times;</button>
        </div>
        <div class="setup-zone-categories">
          ${categoryOptions.map(c => `
            <label class="setup-cat-chip">
              <input type="checkbox" value="${c}" ${(zone.typical_categories || []).includes(c) ? "checked" : ""}>
              ${c}
            </label>
          `).join("")}
        </div>
      `;

      card.querySelector(".setup-zone-name").addEventListener("input", (e) => {
        suggestedZones[idx].name = e.target.value;
      });
      card.querySelector(".setup-zone-type").addEventListener("change", (e) => {
        suggestedZones[idx].zone_type = e.target.value;
      });
      card.querySelector(".setup-zone-strategy").addEventListener("change", (e) => {
        suggestedZones[idx].scan_strategy = e.target.value;
      });
      card.querySelectorAll(".setup-cat-chip input").forEach((cb) => {
        cb.addEventListener("change", () => {
          suggestedZones[idx].typical_categories = Array.from(
            card.querySelectorAll(".setup-cat-chip input:checked")
          ).map(c => c.value);
        });
      });
      card.querySelector(".setup-zone-remove").addEventListener("click", () => {
        suggestedZones.splice(idx, 1);
        renderZonesList();
      });

      list.appendChild(card);
    });
  }

  async function saveProfile() {
    const validZones = suggestedZones.filter(z => z.name && z.name.trim());
    if (validZones.length === 0) {
      const errorMsg = container.querySelector("#setup-error");
      errorMsg.textContent = "Add at least one zone.";
      errorMsg.classList.remove("hidden");
      return;
    }

    const payload = {
      name: areaName,
      area_type: areaType,
      zones: validZones.map((z, i) => ({
        name: z.name.trim(),
        zone_type: z.zone_type,
        typical_categories: z.typical_categories || [],
        typical_container_types: z.typical_container_types || [],
        scan_strategy: z.scan_strategy,
        position_order: i,
      })),
    };

    try {
      const response = await fetch("/api/storage-areas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to save profile");
      }

      const area = await response.json();
      savedAreaId = area.id;
      savedZones = area.zones;
      renderStep4();
    } catch (err) {
      const errorMsg = container.querySelector("#setup-error");
      errorMsg.textContent = err.message;
      errorMsg.classList.remove("hidden");
    }
  }

  // --- Step 4: Zone-by-zone scanning ---
  function renderStep4() {
    scannedCount = 0;
    container.innerHTML = `
      <h2>Scan Each Zone</h2>
      <p class="text-muted">Take a close-up photo of each zone to populate your inventory. You can skip zones and scan them later.</p>

      <div id="setup-zone-cards"></div>

      <div id="setup-scan-container" class="hidden"></div>

      <div class="form-actions" id="setup-step4-actions">
        <button type="button" class="btn btn-primary" id="setup-done-btn">Done</button>
      </div>
    `;

    renderZoneCards();

    container.querySelector("#setup-done-btn").addEventListener("click", () => {
      onComplete(savedAreaId);
    });
  }

  function renderZoneCards() {
    const cardsContainer = container.querySelector("#setup-zone-cards");
    cardsContainer.innerHTML = "";

    savedZones.forEach((zone) => {
      const card = document.createElement("div");
      card.className = "setup-scan-zone-card";
      card.dataset.zoneId = zone.id;

      const scanned = zone._scanned;
      card.innerHTML = `
        <div class="setup-scan-zone-info">
          <strong>${zone.name}</strong>
          <span class="text-muted">${zone.zone_type.replace(/_/g, " ")}</span>
          ${scanned ? '<span class="badge">Scanned</span>' : ""}
        </div>
        <div class="setup-scan-zone-actions">
          ${scanned
            ? ""
            : `<button class="btn btn-primary btn-small setup-scan-now-btn" data-zone-id="${zone.id}">Scan Now</button>
               <button class="btn btn-secondary btn-small setup-skip-btn" data-zone-id="${zone.id}">Skip</button>`
          }
        </div>
      `;

      card.querySelectorAll(".setup-scan-now-btn").forEach((btn) => {
        btn.addEventListener("click", () => startZoneScan(zone));
      });

      card.querySelectorAll(".setup-skip-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          zone._scanned = true;
          scannedCount++;
          renderZoneCards();
        });
      });

      cardsContainer.appendChild(card);
    });
  }

  function startZoneScan(zone) {
    const scanContainer = container.querySelector("#setup-scan-container");
    const cardsContainer = container.querySelector("#setup-zone-cards");
    const actions = container.querySelector("#setup-step4-actions");

    cardsContainer.classList.add("hidden");
    actions.classList.add("hidden");
    scanContainer.classList.remove("hidden");
    scanContainer.innerHTML = "";

    const scan = createPantryScan({
      zoneId: zone.id,
      storageAreaId: savedAreaId,
      zoneName: zone.name,
      onItemsConfirmed: () => {
        zone._scanned = true;
        scannedCount++;
        scanContainer.classList.add("hidden");
        scanContainer.innerHTML = "";
        cardsContainer.classList.remove("hidden");
        actions.classList.remove("hidden");
        renderZoneCards();
      },
      onCancel: () => {
        scanContainer.classList.add("hidden");
        scanContainer.innerHTML = "";
        cardsContainer.classList.remove("hidden");
        actions.classList.remove("hidden");
      },
    });

    scanContainer.appendChild(scan);
  }

  return container;
}
