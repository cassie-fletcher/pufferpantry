/**
 * Pantry list component — displays inventory items grouped by storage location.
 * Supports two modes:
 * - Flat mode: grouped by storage_location string (when no storage areas exist)
 * - Zone mode: two-level grouping by storage area → zone (when areas are configured)
 */
function createPantryList({ items, storageAreas, onEdit, onDelete, onUpdateQuantity, onScanZone }) {
  const container = document.createElement("div");
  container.className = "pantry-list";

  if (items.length === 0 && (!storageAreas || storageAreas.length === 0)) {
    container.innerHTML = `
      <div class="empty-state">
        Your pantry is empty. Add items manually or scan a photo of your fridge to get started!
      </div>
    `;
    return container;
  }

  // Use zone mode if storage areas exist
  if (storageAreas && storageAreas.length > 0) {
    renderZoneGrouped(container, items, storageAreas, { onEdit, onDelete, onUpdateQuantity, onScanZone });
  } else {
    renderFlatGrouped(container, items, { onEdit, onDelete, onUpdateQuantity });
  }

  return container;
}

function renderZoneGrouped(container, items, storageAreas, handlers) {
  // Build a map of zone_id → items
  const itemsByZone = {};
  const unsortedItems = [];
  for (const item of items) {
    if (item.zone_id) {
      if (!itemsByZone[item.zone_id]) itemsByZone[item.zone_id] = [];
      itemsByZone[item.zone_id].push(item);
    } else {
      unsortedItems.push(item);
    }
  }

  for (const area of storageAreas) {
    const areaSection = document.createElement("div");
    areaSection.className = "pantry-area-group";

    const areaHeader = document.createElement("div");
    areaHeader.className = "pantry-area-header";
    areaHeader.textContent = area.name;
    areaSection.appendChild(areaHeader);

    for (const zone of area.zones) {
      const zoneGroup = document.createElement("div");
      zoneGroup.className = "pantry-zone-group";

      const zoneHeader = document.createElement("div");
      zoneHeader.className = "pantry-zone-header";

      const zoneInfo = document.createElement("span");
      zoneInfo.innerHTML = `
        <strong>${zone.name}</strong>
        <span class="text-muted zone-meta">${zone.item_count} item${zone.item_count !== 1 ? "s" : ""}${zone.last_scanned_at ? " · scanned " + formatTimeAgo(zone.last_scanned_at) : ""}</span>
      `;

      const scanBtn = document.createElement("button");
      scanBtn.className = "btn btn-secondary btn-small";
      scanBtn.textContent = "Scan";
      scanBtn.addEventListener("click", () => handlers.onScanZone(zone.id, area.id, zone.name));

      zoneHeader.appendChild(zoneInfo);
      zoneHeader.appendChild(scanBtn);
      zoneGroup.appendChild(zoneHeader);

      const zoneItems = itemsByZone[zone.id] || [];
      if (zoneItems.length > 0) {
        const itemsContainer = document.createElement("div");
        itemsContainer.className = "pantry-items";
        for (const item of zoneItems) {
          itemsContainer.appendChild(createPantryRow(item, handlers));
        }
        zoneGroup.appendChild(itemsContainer);
      } else {
        const empty = document.createElement("div");
        empty.className = "text-muted";
        empty.style.padding = "0.3rem 0";
        empty.style.fontSize = "0.85rem";
        empty.textContent = "No items tracked yet";
        zoneGroup.appendChild(empty);
      }

      areaSection.appendChild(zoneGroup);
    }

    container.appendChild(areaSection);
  }

  // Unsorted items
  if (unsortedItems.length > 0) {
    const unsortedGroup = document.createElement("div");
    unsortedGroup.className = "pantry-location-group";
    const header = document.createElement("div");
    header.className = "pantry-location-header";
    header.textContent = "Unsorted";
    unsortedGroup.appendChild(header);

    const itemsContainer = document.createElement("div");
    itemsContainer.className = "pantry-items";
    for (const item of unsortedItems) {
      itemsContainer.appendChild(createPantryRow(item, handlers));
    }
    unsortedGroup.appendChild(itemsContainer);
    container.appendChild(unsortedGroup);
  }
}

function renderFlatGrouped(container, items, handlers) {
  // Group items by storage location
  const groups = {};
  for (const item of items) {
    const loc = item.storage_location || "Other";
    if (!groups[loc]) groups[loc] = [];
    groups[loc].push(item);
  }

  const locationOrder = ["Fridge", "Freezer", "Pantry", "Spice Rack", "Counter", "Other"];
  const sortedLocations = Object.keys(groups).sort(
    (a, b) => (locationOrder.indexOf(a) === -1 ? 99 : locationOrder.indexOf(a)) -
              (locationOrder.indexOf(b) === -1 ? 99 : locationOrder.indexOf(b))
  );

  for (const location of sortedLocations) {
    const group = document.createElement("div");
    group.className = "pantry-location-group";

    const header = document.createElement("div");
    header.className = "pantry-location-header";
    header.textContent = location;
    group.appendChild(header);

    const itemsContainer = document.createElement("div");
    itemsContainer.className = "pantry-items";

    for (const item of groups[location]) {
      itemsContainer.appendChild(createPantryRow(item, handlers));
    }

    group.appendChild(itemsContainer);
    container.appendChild(group);
  }
}

const QUANTITY_LEVELS = ["Full", "Most", "Half", "Low", "Almost Empty"];
const QUANTITY_COLORS = {
  "Full": "#27ae60",
  "Most": "#2e86ab",
  "Half": "#f39c12",
  "Low": "#e67e22",
  "Almost Empty": "#c0392b",
};

function createPantryRow(item, { onEdit, onDelete, onUpdateQuantity }) {
  const row = document.createElement("div");
  row.className = "pantry-item-row";

  // Name
  const name = document.createElement("span");
  name.className = "pantry-item-name";
  name.textContent = item.name;

  // Quantity display — prefer exact quantity, fall back to level
  const quantityEl = document.createElement("div");
  quantityEl.className = "pantry-item-quantity";

  if (item.quantity && item.unit) {
    const exact = document.createElement("span");
    exact.className = "pantry-exact-qty";
    exact.textContent = `${item.quantity} ${item.unit}`;
    quantityEl.appendChild(exact);
  }

  if (item.quantity_level) {
    const bar = createQuantityBar(item.quantity_level);
    quantityEl.appendChild(bar);

    bar.addEventListener("click", (e) => {
      e.stopPropagation();
      showLevelPicker(quantityEl, item, onUpdateQuantity);
    });
  }

  // Category badge
  const category = document.createElement("span");
  category.className = "pantry-item-category";
  category.textContent = item.category || "";

  // Expiry badge
  const expiryEl = document.createElement("span");
  expiryEl.className = "pantry-item-expiry";
  if (item.expiration_date) {
    const expiry = new Date(item.expiration_date);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const daysUntil = Math.ceil((expiry - today) / (1000 * 60 * 60 * 24));

    if (daysUntil <= 0) {
      expiryEl.innerHTML = `<span class="expiry-badge expiry-danger">Expired</span>`;
    } else if (daysUntil <= 3) {
      expiryEl.innerHTML = `<span class="expiry-badge expiry-warning">Exp ${daysUntil}d</span>`;
    } else {
      expiryEl.textContent = `Exp ${expiry.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
    }
  }

  // Actions
  const actions = document.createElement("span");
  actions.className = "pantry-item-actions";
  actions.innerHTML = `
    <button class="btn btn-secondary btn-small pantry-edit-btn" title="Edit">Edit</button>
    <button class="btn btn-danger btn-small pantry-delete-btn" title="Delete">&times;</button>
  `;

  actions.querySelector(".pantry-edit-btn").addEventListener("click", () => onEdit(item.id));
  actions.querySelector(".pantry-delete-btn").addEventListener("click", () => onDelete(item.id));

  row.appendChild(name);
  row.appendChild(quantityEl);
  row.appendChild(category);
  row.appendChild(expiryEl);
  row.appendChild(actions);

  return row;
}

function createQuantityBar(level) {
  const wrapper = document.createElement("div");
  wrapper.className = "quantity-bar";
  wrapper.title = level;

  const filledCount = {
    "Full": 5, "Most": 4, "Half": 3, "Low": 2, "Almost Empty": 1,
  }[level] || 0;

  const color = QUANTITY_COLORS[level] || "#999";

  for (let i = 0; i < 5; i++) {
    const seg = document.createElement("div");
    seg.className = "quantity-bar-segment";
    if (i < filledCount) {
      seg.style.background = color;
    }
    wrapper.appendChild(seg);
  }

  const label = document.createElement("span");
  label.className = "quantity-bar-label";
  label.textContent = level;
  wrapper.appendChild(label);

  return wrapper;
}

function showLevelPicker(parentEl, item, onUpdateQuantity) {
  const existing = parentEl.querySelector(".level-picker");
  if (existing) { existing.remove(); return; }

  const picker = document.createElement("div");
  picker.className = "level-picker";

  for (const level of QUANTITY_LEVELS) {
    const btn = document.createElement("button");
    btn.className = "level-picker-btn";
    if (level === item.quantity_level) btn.classList.add("active");
    btn.textContent = level;
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      picker.remove();
      onUpdateQuantity(item.id, level);
    });
    picker.appendChild(btn);
  }

  parentEl.appendChild(picker);

  const closeHandler = (e) => {
    if (!picker.contains(e.target)) {
      picker.remove();
      document.removeEventListener("click", closeHandler);
    }
  };
  setTimeout(() => document.addEventListener("click", closeHandler), 0);
}

function formatTimeAgo(isoString) {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
