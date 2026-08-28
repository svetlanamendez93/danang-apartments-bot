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
let currentMapPoints = [];
let currentSort = "newest";

// Saved and already-seen listings, kept per person on the server. Identity is
// Telegram's signed initData, so one person's shortlist is never another's.
const userState = { saved: new Set(), viewed: new Set(), ready: false };
const initData = window.Telegram?.WebApp?.initData || "";

// Which subset the list is showing: everything, only saved, or only seen.
let currentTab = "all";

async function api(path, options = {}) {
  return fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json",
               "X-Telegram-Init-Data": initData, ...(options.headers || {}) },
  });
}

async function loadUserState() {
  if (!initData) return; // opened outside Telegram: no identity, no per-user state
  try {
    const res = await api("/me/listings");
    if (!res.ok) return;
    const data = await res.json();
    userState.saved = new Set(data.saved);
    userState.viewed = new Set(data.viewed);
    userState.ready = true;
  } catch (err) {
    console.error("loadUserState failed:", err);
  }
}

async function setUserState(listingId, state, on) {
  const set = userState[state === "saved" ? "saved" : "viewed"];
  if (on) set.add(listingId);
  else set.delete(listingId);
  if (!initData) return;
  try {
    await api(`/me/listings/${listingId}/${state}`, {
      method: "POST",
      body: JSON.stringify({ on }),
    });
  } catch (err) {
    console.error("setUserState failed:", err);
  }
}

const SORT_OPTIONS = ["newest", "price_asc", "price_desc", "area_desc"];
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
  if (currentSort !== "newest") params.set("sort", currentSort);
  return params;
}

// --- data ---------------------------------------------------------------------

// The feed is paginated and carries only what a card shows; the map gets a
// separate, far lighter endpoint. Sending the full record for every listing
// made the response hundreds of kilobytes and timed the server out once there
// were a few hundred of them.
const PAGE_SIZE = 60;
let listOffset = 0;
let listTotal = 0;

async function loadListings(append = false) {
  const status = document.getElementById("resultCount");
  if (!append) {
    listOffset = 0;
    status.textContent = t("loading");
  }

  const params = currentFilters();
  params.set("limit", PAGE_SIZE);
  params.set("offset", listOffset);

  let data;
  try {
    const res = await fetch(`${API_BASE_URL}/listings?${params.toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    // Without this the page looks identical to "no matches", which hides real
    // outages from the user.
    status.textContent = t("load_failed");
    console.error("loadListings failed:", err);
    return;
  }

  listTotal = data.total;
  currentListings = append ? currentListings.concat(data.items) : data.items;
  renderList(currentListings);
  status.textContent = listTotal
    ? `${listTotal} ${t("results")}`
    : t("nothing_found");

  if (!append) loadMapPoints();
}

async function loadMapPoints() {
  try {
    const res = await fetch(`${API_BASE_URL}/map/points?${currentFilters().toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const points = await res.json();
    currentMapPoints = points;
    renderMarkers(points);

    const cities = [...selected.city];
    if (cities.length === 1 && CITY_CENTERS[cities[0]]) {
      map.setView(CITY_CENTERS[cities[0]], 13);
    } else if (points.length) {
      // Listings span cities hundreds of km apart, so a fixed view on one city
      // hides most of them.
      map.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lng])), {
        padding: [40, 40],
        maxZoom: 14,
      });
    }
  } catch (err) {
    console.error("loadMapPoints failed:", err);
  }
}

function renderMarkers(points) {
  markerLayer.clearLayers();
  points.forEach((p) => {
    if (p.lat == null || p.lng == null) return;
    const label = p.price == null ? "?" : "$" + p.price;
    // A pin for a listing whose address we could not identify must not look
    // like a precise one — otherwise a guessed position reads as a real
    // address and the map looks simply wrong.
    const seen = userState.viewed.has(p.id);
    const cls = ["price-pin", p.approx ? "approx" : "", seen ? "seen" : ""].join(" ").trim();
    const marker = L.marker([p.lat, p.lng], {
      listingPrice: p.price,
      icon: L.divIcon({
        className: cls,
        html: `<span>${p.approx ? "≈" : ""}${escapeHtml(label)}</span>`,
        iconSize: null,
      }),
    });
    // The map carries only ids and positions now, so the full record is
    // fetched when a pin is actually opened.
    marker.on("click", () => openListingById(p.id));
    markerLayer.addLayer(marker);
  });
}

// Several flats share a building, and everything whose address we could not
// resolve shares one fallback point, so plain markers stack: only the topmost
// is clickable and the rest are invisible. Clustering collapses each pile into
// one badge showing how many are there and the price range inside it, which
// zooming or clicking expands.
function makeClusterLayer() {
  return L.markerClusterGroup({
    showCoverageOnHover: false,
    maxClusterRadius: 45,
    spiderfyOnMaxZoom: true,
    iconCreateFunction: (cluster) => {
      const markers = cluster.getAllChildMarkers();
      const prices = markers
        .map((m) => m.options.listingPrice)
        .filter((p) => typeof p === "number");
      const count = markers.length;
      let range = "";
      if (prices.length) {
        const lo = Math.min(...prices);
        const hi = Math.max(...prices);
        range = lo === hi ? `$${Math.round(lo)}` : `$${Math.round(lo)}–${Math.round(hi)}`;
      }
      const size = count < 10 ? "sm" : count < 50 ? "md" : "lg";
      return L.divIcon({
        className: `price-cluster ${size}`,
        html: `<div><b>${count}</b>${range ? `<span>${range}</span>` : ""}</div>`,
        iconSize: null,
      });
    },
  });
}

async function loadTab(tab) {
  currentTab = tab;
  const status = document.getElementById("resultCount");
  const view = document.getElementById("listView");

  if (tab === "all") {
    document.getElementById("sortSelect").classList.remove("hidden");
    loadListings();
    return;
  }

  // A personal set is not something the public feed can filter on, so these
  // are fetched by id.
  document.getElementById("sortSelect").classList.add("hidden");
  const ids = [...(tab === "saved" ? userState.saved : userState.viewed)];
  if (!ids.length) {
    currentListings = [];
    view.innerHTML = `<p class="empty">${tab === "saved" ? t("nothing_saved") : t("nothing_viewed")}</p>`;
    status.textContent = `0 ${t("results")}`;
    return;
  }

  status.textContent = t("loading");
  const results = await Promise.all(
    ids.map((id) =>
      detailCache.has(id)
        ? Promise.resolve(detailCache.get(id))
        : fetch(`${API_BASE_URL}/listings/${id}`)
            .then((r) => (r.ok ? r.json() : null))
            .then((l) => { if (l) detailCache.set(id, l); return l; })
            .catch(() => null)
    )
  );

  // A listing can expire or be withdrawn while it sits in someone's shortlist.
  currentListings = results.filter(Boolean).map(toSummary);
  listTotal = currentListings.length;
  renderList(currentListings);
  status.textContent = `${listTotal} ${t("results")}`;
}

// The detail endpoint returns the full record; the card renderer wants the
// summary shape, so bridge the two rather than duplicating the card markup.
function toSummary(listing) {
  const photos = listing.photos || [];
  return {
    ...listing,
    thumb: photos.length ? photos[0].url : null,
    photo_count: photos.length,
  };
}

function renderList(listings) {
  const view = document.getElementById("listView");
  view.innerHTML = "";
  if (!listings.length) {
    view.innerHTML = `<p class="empty">${t("nothing_found")}</p>`;
    return;
  }

  listings.forEach((listing) => {
    const seen = userState.viewed.has(listing.id);
    const saved = userState.saved.has(listing.id);
    const card = document.createElement("article");
    card.className = "card" + (seen ? " seen" : "");
    card.innerHTML = `
      <div class="card-thumb">${
        listing.thumb
          ? `<img src="${escapeHtml(listing.thumb)}" loading="lazy" alt="" />`
          : `<div class="no-photo-sm">${t("no_photo")}</div>`
      }${listing.photo_count > 1 ? `<span class="photo-count">📷 ${listing.photo_count}</span>` : ""}</div>
      <div class="card-body">
        <div class="card-price">${escapeHtml(priceLabel(listing))}${
          seen ? `<span class="seen-tag">${t("seen")}</span>` : ""
        }</div>
        <div class="card-facts">${factsFor(listing).map(escapeHtml).join(" · ")}</div>
        <div class="card-city">📍 ${escapeHtml(optLabel("city", listing.city))}${
          listing.address_text ? ", " + escapeHtml(listing.address_text) : ""
        }</div>
      </div>
      <button class="fav-btn${saved ? " on" : ""}" aria-label="save">${saved ? "★" : "☆"}</button>`;

    card.querySelector(".fav-btn").onclick = (e) => {
      e.stopPropagation();   // saving must not also open the card
      setUserState(listing.id, "saved", !userState.saved.has(listing.id));
      renderList(currentListings);
    };
    card.onclick = () => openListingById(listing.id);
    view.appendChild(card);
  });

  if (currentTab === "all" && currentListings.length < listTotal) {
    const more = document.createElement("button");
    more.className = "load-more";
    more.textContent = t("load_more");
    more.onclick = () => {
      listOffset += PAGE_SIZE;
      loadListings(true);
    };
    view.appendChild(more);
  }
}

// --- listing detail -----------------------------------------------------------

const detailCache = new Map();

async function openListingById(id) {
  if (detailCache.has(id)) return openListing(detailCache.get(id));
  try {
    const res = await fetch(`${API_BASE_URL}/listings/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const listing = await res.json();
    detailCache.set(id, listing);
    openListing(listing);
  } catch (err) {
    console.error("openListingById failed:", err);
  }
}

function openListing(listing) {
  // Opening a card is what "viewed" means, so mark it here rather than making
  // it a separate action the user has to remember.
  if (!userState.viewed.has(listing.id)) {
    setUserState(listing.id, "viewed", true);
    renderList(currentListings);
    if (currentMapPoints.length) renderMarkers(currentMapPoints);
  }
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

function buildSortSelect() {
  const sel = document.getElementById("sortSelect");
  sel.innerHTML = "";
  SORT_OPTIONS.forEach((key) => {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = t("sort_" + key);
    opt.selected = key === currentSort;
    sel.appendChild(opt);
  });
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
      buildSortSelect();
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
  markerLayer = makeClusterLayer().addTo(map);

  setLang(LANG);
  applyStaticTranslations();
  buildChips();
  buildLangList();
  buildSortSelect();

  document.getElementById("sortSelect").onchange = (e) => {
    currentSort = e.target.value;
    loadListings();
  };

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

  document.querySelectorAll("#tabBar button").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll("#tabBar button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      // The saved and viewed tabs are lists of specific flats, so the map view
      // would show a near-empty map; switch to the list for them.
      if (btn.dataset.tab !== "all") {
        document.querySelector('#viewToggle button[data-view="list"]').click();
      }
      loadTab(btn.dataset.tab);
    };
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

  loadUserState().then(() => loadListings());
}

init();
