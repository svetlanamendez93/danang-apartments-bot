// Telegram Mini App: map + list of rental listings, with multi-select filters
// and ru/en/vi throughout. API_BASE_URL is set in index.html.
const API_BASE_URL = window.API_BASE_URL || "http://localhost:5000";

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

// Where to centre the map for a city. These are the rental districts, not the
// geometric centres — Da Nang's centroid sits next to the airport runway.
// Kept in step with CITY_CENTERS in db/models.py.
const CITY_CENTERS = {
  da_nang: [16.04953, 108.24439],
  nha_trang: [12.24685, 109.19637],
  ho_chi_minh: [10.77539, 106.69963],
  hanoi: [21.0323, 105.85069],
  hoi_an: [15.8828, 108.34289],
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
    map.setView(CITY_CENTERS[cities[0]], 13);
  } else {
    // Listings span cities hundreds of km apart, so a fixed view on one city
    // hides most of them — without this the map looked almost empty.
    const points = listings
      .filter((l) => l.lat != null && l.lng != null)
      .map((l) => [l.lat, l.lng]);
    if (points.length) {
      map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 14 });
    }
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
    // A pin for a listing whose address we could not identify must not look
    // like a precise one — otherwise a guessed position reads as a real
    // address and the map looks simply wrong.
    const approx = listing.location_is_approximate;
    const marker = L.marker([listing.lat, listing.lng], {
      icon: L.divIcon({
        className: approx ? "price-pin approx" : "price-pin",
        html: `<span>${approx ? "≈" : ""}${escapeHtml(label)}</span>`,
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
    photos.forEach((p, i) => {
      const img = document.createElement("img");
      img.src = p.url;
      img.loading = "lazy";
      img.alt = "";
      // Photos are the main thing people judge a flat on, so any of them opens
      // full screen rather than staying a small strip.
      img.onclick = () => openLightbox(photos.map((x) => x.url), i);
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

  document.getElementById("listingDetails").innerHTML = renderDetails(listing);
  document.getElementById("listingModal").classList.remove("hidden");
}

// Structured facts get their own labelled cells rather than a run-on line, so
// the eye can find "how many rooms" without reading the whole card.
const SPEC_ICONS = {
  rooms: "🛏",
  property_type: "🏠",
  area_sqm: "📐",
  renovation_quality: "🛠",
  pets_policy: "🐾",
};

function specCells(listing) {
  const cells = [];
  if (listing.rooms) {
    cells.push([SPEC_ICONS.rooms, t("rooms"), optLabel("rooms", listing.rooms)]);
  }
  if (listing.property_type) {
    cells.push([SPEC_ICONS.property_type, t("property_type"), optLabel("property_type", listing.property_type)]);
  }
  if (listing.area_sqm) {
    cells.push([SPEC_ICONS.area_sqm, t("area"), `${Math.round(listing.area_sqm)} m²`]);
  }
  if (listing.renovation_quality) {
    cells.push([SPEC_ICONS.renovation_quality, t("renovation"), optLabel("renovation_quality", listing.renovation_quality)]);
  }
  if (listing.pets_policy && listing.pets_policy !== "unknown") {
    cells.push([SPEC_ICONS.pets_policy, t("pets"), optLabel("pets_policy", listing.pets_policy)]);
  }
  return cells;
}

const CITY_QUERY_NAME = {
  da_nang: "Da Nang",
  nha_trang: "Nha Trang",
  ho_chi_minh: "Ho Chi Minh City",
  hanoi: "Hanoi",
  hoi_an: "Hoi An",
};

function googleMapsUrl(listing) {
  // Search by the address text whenever the post gave one, even if our own pin
  // is approximate: the text is a real street or building name that Google can
  // resolve far more precisely than the district coordinate we fell back to,
  // and it lands on a place with street view rather than a bare point.
  if (listing.address_text) {
    const city = CITY_QUERY_NAME[listing.city] || "";
    const query = [listing.address_text, city, "Vietnam"].filter(Boolean).join(", ");
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
  }
  if (listing.lat != null && listing.lng != null) {
    return `https://www.google.com/maps/search/?api=1&query=${listing.lat},${listing.lng}`;
  }
  return null;
}

// Searching by address name lands on a real place; a bare coordinate from the
// fallback only shows a general area, and the label should not overclaim.
function mapsLinkIsPrecise(listing) {
  return Boolean(listing.address_text);
}

function relativeDate(iso) {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return t("today");
  if (days === 1) return t("yesterday");
  if (days < 30) return `${days} ${plural(days, t("day_1"), t("day_2"), t("day_5"))} ${t("ago")}`;
  return new Date(iso).toLocaleDateString(
    LANG === "vi" ? "vi-VN" : LANG === "en" ? "en-GB" : "ru-RU"
  );
}

function renderDetails(listing) {
  const cells = specCells(listing);
  const posted = listing.posted_at ? relativeDate(listing.posted_at) : null;
  const sourceName = { telegram: "Telegram", facebook: "Facebook", manual: t("user_submitted") }[
    listing.source_type
  ] || listing.source_type;

  return `
    <div class="d-head">
      <div class="d-price">${escapeHtml(priceLabel(listing))}</div>
      ${posted ? `<div class="d-posted">${escapeHtml(posted)}</div>` : ""}
    </div>

    <div class="d-city">
      <span class="pin">📍</span>
      <span>${escapeHtml(optLabel("city", listing.city))}${
        listing.address_text ? ", " + escapeHtml(listing.address_text) : ""
      }</span>
    </div>
    ${
      listing.location_is_approximate
        ? `<div class="d-approx">${t("approx_location")}</div>`
        : ""
    }
    ${
      googleMapsUrl(listing)
        ? `<a class="maps-link" href="${escapeHtml(googleMapsUrl(listing))}"
              target="_blank" rel="noopener">
             🗺 ${mapsLinkIsPrecise(listing) ? t("open_in_maps") : t("open_area_in_maps")}
           </a>`
        : ""
    }

    ${
      cells.length
        ? `<div class="spec-grid">${cells
            .map(
              ([icon, label, value]) => `
          <div class="spec">
            <div class="spec-icon">${icon}</div>
            <div class="spec-text">
              <div class="spec-label">${escapeHtml(label)}</div>
              <div class="spec-value">${escapeHtml(value)}</div>
            </div>
          </div>`
            )
            .join("")}</div>`
        : ""
    }

    ${
      listing.description
        ? `<div class="d-section">
             <h3>${t("description")}</h3>
             <p class="d-desc">${escapeHtml(listing.description)}</p>
           </div>`
        : ""
    }

    <div class="d-section d-source">
      <h3>${t("source")}</h3>
      <div class="source-row">
        <span class="source-name">${escapeHtml(sourceName)}</span>
        ${listing.contact ? `<span class="source-contact">${escapeHtml(listing.contact)}</span>` : ""}
      </div>
    </div>

    <div class="d-warning">
      <span class="warn-icon">⚠️</span>
      <span>${t("scam_warning")}</span>
    </div>

    <a class="source-link" href="${escapeHtml(listing.source_url)}" target="_blank" rel="noopener">
      ${t("open_original")} <span class="arrow">↗</span>
    </a>`;
}

// --- fullscreen photo viewer ---------------------------------------------------

let lbPhotos = [];
let lbIndex = 0;

function openLightbox(urls, index) {
  lbPhotos = urls;
  lbIndex = index;
  showLightboxPhoto();
  document.getElementById("lightbox").classList.remove("hidden");
}

function showLightboxPhoto() {
  document.getElementById("lightboxImg").src = lbPhotos[lbIndex];
  document.getElementById("lightboxCount").textContent = `${lbIndex + 1} / ${lbPhotos.length}`;
  const single = lbPhotos.length < 2;
  document.getElementById("lightboxPrev").classList.toggle("hidden", single);
  document.getElementById("lightboxNext").classList.toggle("hidden", single);
}

function stepLightbox(delta) {
  lbIndex = (lbIndex + delta + lbPhotos.length) % lbPhotos.length;
  showLightboxPhoto();
}

function closeLightbox() {
  document.getElementById("lightbox").classList.add("hidden");
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

  document.getElementById("lightboxClose").onclick = closeLightbox;
  document.getElementById("lightboxPrev").onclick = (e) => {
    e.stopPropagation();
    stepLightbox(-1);
  };
  document.getElementById("lightboxNext").onclick = (e) => {
    e.stopPropagation();
    stepLightbox(1);
  };
  // Tapping the backdrop closes; tapping the photo itself must not.
  document.getElementById("lightbox").onclick = (e) => {
    if (e.target.id === "lightbox") closeLightbox();
  };
  document.addEventListener("keydown", (e) => {
    if (document.getElementById("lightbox").classList.contains("hidden")) return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft") stepLightbox(-1);
    if (e.key === "ArrowRight") stepLightbox(1);
  });

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
