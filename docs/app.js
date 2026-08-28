// Telegram Mini App: map + list of rental listings, with multi-select filters
// and ru/en/vi throughout. API_BASE_URL is set in index.html.
const API_BASE_URL = window.API_BASE_URL || "http://localhost:5000";

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const CITY_CENTERS = {
  da_nang: [16.0544, 108.2022],
  nha_trang: [12.2388, 109.1967],
  ho_chi_minh: [10.7769, 106.7009],
  hanoi: [21.0278, 105.8342],
  hoi_an: [15.8801, 108.338],
};

let map;
let markerLayer;
let currentListings = [];
// Multi-select state: which values are ticked in each filter group.
const selected = {
  city: new Set(),
  rooms: new Set(),
  property_type: new Set(),
  renovation_quality: new Set(),
  pets_policy: new Set(), // single-choice, but kept a Set for uniform handling
};

// --- rendering helpers --------------------------------------------------------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function priceLabel(listing) {
  if (listing.price_min_usd == null) return t("price_unknown");
  const fmt = (n) => "$" + Math.round(n).toLocaleString("en-US");
  if (listing.price_max_usd && listing.price_max_usd !== listing.price_min_usd) {
    return `${fmt(listing.price_min_usd)}–${fmt(listing.price_max_usd)}${t("per_month")}`;
  }
  return `${fmt(listing.price_min_usd)}${t("per_month")}`;
}

function factsFor(listing) {
  const facts = [];
  if (listing.rooms) facts.push(optLabel("rooms", listing.rooms) + (listing.rooms === "studio" ? "" : ` ${t("rooms").toLowerCase()}`));
  if (listing.property_type) facts.push(optLabel("property_type", listing.property_type));
  if (listing.area_sqm) facts.push(`${Math.round(listing.area_sqm)} m²`);
  if (listing.renovation_quality) facts.push(optLabel("renovation_quality", listing.renovation_quality));
  if (listing.pets_policy && listing.pets_policy !== "unknown") {
    facts.push((listing.pets_policy === "allowed" ? "🐾 " : "🚫 ") + optLabel("pets_policy", listing.pets_policy));
  }
  return facts;
}

// --- filters UI ---------------------------------------------------------------

function buildChips() {
  const groups = [
    ["f-city", "city"],
    ["f-rooms", "rooms"],
    ["f-type", "property_type"],
    ["f-renovation", "renovation_quality"],
    ["f-pets", "pets_policy"],
  ];

  groups.forEach(([elId, group]) => {
    const container = document.getElementById(elId);
    const multi = container.dataset.multi === "true";
    container.innerHTML = "";

    // "Any" clears the group — the natural way to express "no preference".
    const anyChip = document.createElement("button");
    anyChip.className = "chip" + (selected[group].size === 0 ? " active" : "");
    anyChip.textContent = t("any");
    anyChip.onclick = () => {
      selected[group].clear();
      buildChips();
    };
    container.appendChild(anyChip);

    OPTIONS[group].forEach((opt) => {
      const chip = document.createElement("button");
      chip.className = "chip" + (selected[group].has(opt.v) ? " active" : "");
      chip.textContent = opt[LANG] || opt.ru;
      chip.onclick = () => {
        if (selected[group].has(opt.v)) {
          selected[group].delete(opt.v);
        } else {
          if (!multi) selected[group].clear();
          selected[group].add(opt.v);
        }
        buildChips();
      };
      container.appendChild(chip);
    });
  });

  updateFilterBadge();
}

function updateFilterBadge() {
  const count =
    Object.values(selected).reduce((sum, set) => sum + set.size, 0) +
    (document.getElementById("f-price-min").value ? 1 : 0) +
    (document.getElementById("f-price-max").value ? 1 : 0);
  const badge = document.getElementById("activeFilterCount");
  badge.textContent = count;
  badge.classList.toggle("hidden", count === 0);
}

function currentFilters() {
  const params = new URLSearchParams();
  Object.entries(selected).forEach(([group, set]) => {
    if (set.size) params.set(group, [...set].join(","));
  });
  const min = document.getElementById("f-price-min").value;
  const max = document.getElementById("f-price-max").value;
  if (min) params.set("price_min", min);
  if (max) params.set("price_max", max);
  return params;
}

// --- data ---------------------------------------------------------------------

async function loadListings() {
  const status = document.getElementById("resultCount");
  status.textContent = t("loading");

  let listings;
  try {
    const res = await fetch(`${API_BASE_URL}/listings?${currentFilters().toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    listings = await res.json();
  } catch (err) {
    // Without this the page looks identical to "no matches", which hides real
    // outages from the user.
    status.textContent = t("load_failed");
    console.error("loadListings failed:", err);
    return;
  }

  currentListings = listings;
  renderMarkers(listings);
  renderList(listings);

  const cities = [...selected.city];
  if (cities.length === 1 && CITY_CENTERS[cities[0]]) {
    map.panTo(CITY_CENTERS[cities[0]]);
  }

  status.textContent = listings.length
    ? `${listings.length} ${t("results")}`
    : t("nothing_found");
}

function renderMarkers(listings) {
  markerLayer.clearLayers();
  listings.forEach((listing) => {
    if (listing.lat == null || listing.lng == null) return;
    const label = listing.price_min_usd == null ? "?" : "$" + Math.round(listing.price_min_usd);
    const marker = L.marker([listing.lat, listing.lng], {
      icon: L.divIcon({
        className: "price-pin",
        html: `<span>${escapeHtml(label)}</span>`,
        iconSize: null,
      }),
    });
    marker.on("click", () => openListing(listing));
    markerLayer.addLayer(marker);
  });
}

function renderList(listings) {
  const view = document.getElementById("listView");
  view.innerHTML = "";
  if (!listings.length) {
    view.innerHTML = `<p class="empty">${t("nothing_found")}</p>`;
    return;
  }

  listings.forEach((listing) => {
    const card = document.createElement("article");
    card.className = "card";
    const thumb = listing.photos?.[0]?.url;
    card.innerHTML = `
      <div class="card-thumb">${
        thumb
          ? `<img src="${escapeHtml(thumb)}" loading="lazy" alt="" />`
          : `<div class="no-photo-sm">${t("no_photo")}</div>`
      }${listing.photos?.length > 1 ? `<span class="photo-count">📷 ${listing.photos.length}</span>` : ""}</div>
      <div class="card-body">
        <div class="card-price">${escapeHtml(priceLabel(listing))}</div>
        <div class="card-facts">${factsFor(listing).map(escapeHtml).join(" · ")}</div>
        <div class="card-city">📍 ${escapeHtml(optLabel("city", listing.city))}${
          listing.address_text ? ", " + escapeHtml(listing.address_text) : ""
        }</div>
      </div>`;
    card.onclick = () => openListing(listing);
    view.appendChild(card);
  });
}

// --- listing detail -----------------------------------------------------------

function openListing(listing) {
  const gallery = document.getElementById("gallery");
  const dots = document.getElementById("galleryDots");
  gallery.innerHTML = "";
  dots.innerHTML = "";

  const photos = (listing.photos || []).slice().sort((a, b) => a.position - b.position);
  if (photos.length) {
    photos.forEach((p) => {
      const img = document.createElement("img");
      img.src = p.url;
      img.loading = "lazy";
      img.alt = "";
      gallery.appendChild(img);
    });
    if (photos.length > 1) {
      photos.forEach((_, i) => {
        const dot = document.createElement("span");
        dot.className = "dot" + (i === 0 ? " active" : "");
        dots.appendChild(dot);
      });
      // Reflect which photo is in view while swiping the strip.
      gallery.onscroll = () => {
        const idx = Math.round(gallery.scrollLeft / gallery.clientWidth);
        [...dots.children].forEach((d, i) => d.classList.toggle("active", i === idx));
      };
    }
  } else {
    gallery.innerHTML = `<div class="no-photo">${t("no_photo")}</div>`;
  }

  const facts = factsFor(listing);
  const posted = listing.posted_at
    ? new Date(listing.posted_at).toLocaleDateString(LANG === "vi" ? "vi-VN" : LANG === "en" ? "en-GB" : "ru-RU")
    : null;

  document.getElementById("listingDetails").innerHTML = `
    <div class="d-price">${escapeHtml(priceLabel(listing))}</div>
    <div class="d-city">📍 ${escapeHtml(optLabel("city", listing.city))}${
      listing.address_text ? ", " + escapeHtml(listing.address_text) : ""
    }${listing.lat != null && !listing.address_text ? ` <span class="muted">(${t("no_coords")})</span>` : ""}</div>
    ${facts.length ? `<div class="d-badges">${facts.map((f) => `<span>${escapeHtml(f)}</span>`).join("")}</div>` : ""}
    ${listing.description ? `<p class="d-desc">${escapeHtml(listing.description)}</p>` : ""}
    ${listing.contact ? `<p class="d-meta">${t("contact")}: ${escapeHtml(listing.contact)}</p>` : ""}
    ${posted ? `<p class="d-meta">${t("posted")}: ${escapeHtml(posted)}</p>` : ""}
    <p class="d-warning">⚠️ ${t("scam_warning")}</p>
    <a class="source-link" href="${escapeHtml(listing.source_url)}" target="_blank" rel="noopener">
      ${t("open_original")} ↗
    </a>`;

  document.getElementById("listingModal").classList.remove("hidden");
}

// --- language -----------------------------------------------------------------

function applyStaticTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  document.getElementById("langCode").textContent = LANG.toUpperCase();
}

function buildLangList() {
  const list = document.getElementById("langList");
  list.innerHTML = "";
  LANGS.forEach((l) => {
    const btn = document.createElement("button");
    btn.className = "lang-row" + (l.code === LANG ? " active" : "");
    btn.innerHTML = `<span class="flag">${l.flag}</span><span>${l.name}</span>${
      l.code === LANG ? '<span class="check">✓</span>' : ""
    }`;
    btn.onclick = () => {
      setLang(l.code);
      applyStaticTranslations();
      buildChips();
      buildLangList();
      renderList(currentListings);
      renderMarkers(currentListings);
      closeSheet("lang");
      document.getElementById("resultCount").textContent = currentListings.length
        ? `${currentListings.length} ${t("results")}`
        : t("nothing_found");
    };
    list.appendChild(btn);
  });
}

// --- panels -------------------------------------------------------------------

function openSheet(which) {
  document.getElementById(which === "lang" ? "langSheet" : "filtersPanel").classList.add("open");
  document.getElementById(which === "lang" ? "langScrim" : "filtersScrim").classList.remove("hidden");
}

function closeSheet(which) {
  document.getElementById(which === "lang" ? "langSheet" : "filtersPanel").classList.remove("open");
  document.getElementById(which === "lang" ? "langScrim" : "filtersScrim").classList.add("hidden");
}

// --- init ---------------------------------------------------------------------

function init() {
  map = L.map("map", { zoomControl: false }).setView(CITY_CENTERS.da_nang, 13);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 19,
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);

  setLang(LANG);
  applyStaticTranslations();
  buildChips();
  buildLangList();

  document.getElementById("filtersToggle").onclick = () => openSheet("filters");
  document.getElementById("filtersClose").onclick = () => closeSheet("filters");
  document.getElementById("filtersScrim").onclick = () => closeSheet("filters");
  document.getElementById("langToggle").onclick = () => openSheet("lang");
  document.getElementById("langClose").onclick = () => closeSheet("lang");
  document.getElementById("langScrim").onclick = () => closeSheet("lang");
  document.getElementById("closeModal").onclick = () =>
    document.getElementById("listingModal").classList.add("hidden");

  document.getElementById("applyFilters").onclick = () => {
    closeSheet("filters");
    updateFilterBadge();
    loadListings();
  };
  document.getElementById("resetFilters").onclick = () => {
    Object.values(selected).forEach((s) => s.clear());
    document.getElementById("f-price-min").value = "";
    document.getElementById("f-price-max").value = "";
    buildChips();
    loadListings();
  };
  ["f-price-min", "f-price-max"].forEach((id) => {
    document.getElementById(id).oninput = updateFilterBadge;
  });

  document.querySelectorAll("#viewToggle button").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll("#viewToggle button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const isMap = btn.dataset.view === "map";
      document.getElementById("mapWrap").classList.toggle("hidden", !isMap);
      document.getElementById("listView").classList.toggle("hidden", isMap);
      // Leaflet renders a grey area if the container was hidden while resizing.
      if (isMap) setTimeout(() => map.invalidateSize(), 50);
    };
  });

  loadListings();
}

init();
