// Telegram Mini App: map + filters + listing detail.
// API_BASE_URL is set in index.html; the localhost value is the dev fallback.
const API_BASE_URL = window.API_BASE_URL || "http://localhost:5000";

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

let map;
let markers = [];

const CITY_CENTERS = {
  da_nang: { lat: 16.0544, lng: 108.2022 },
  nha_trang: { lat: 12.2388, lng: 109.1967 },
  ho_chi_minh: { lat: 10.7769, lng: 106.7009 },
  hanoi: { lat: 21.0278, lng: 105.8342 },
  hoi_an: { lat: 15.8801, lng: 108.338 },
};

map = L.map("map").setView([CITY_CENTERS.da_nang.lat, CITY_CENTERS.da_nang.lng], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);
loadListings();

function currentFilters() {
  const params = new URLSearchParams();
  const city = document.getElementById("f-city").value;
  const priceMin = document.getElementById("f-price-min").value;
  const priceMax = document.getElementById("f-price-max").value;
  const rooms = document.getElementById("f-rooms").value;
  const type = document.getElementById("f-type").value;
  const renovation = document.getElementById("f-renovation").value;
  const pets = document.getElementById("f-pets").value;

  if (city) params.set("city", city);
  if (priceMin) params.set("price_min", priceMin);
  if (priceMax) params.set("price_max", priceMax);
  if (rooms) params.set("rooms", rooms);
  if (type) params.set("property_type", type);
  if (renovation) params.set("renovation_quality", renovation);
  if (pets) params.set("pets_policy", pets);
  return params;
}

function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

async function loadListings() {
  const status = document.getElementById("resultCount");
  status.textContent = "Загрузка…";

  const params = currentFilters();
  let listings;
  try {
    const res = await fetch(`${API_BASE_URL}/listings?${params.toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    listings = await res.json();
  } catch (err) {
    // Without this the page just sits there looking empty, which is
    // indistinguishable from "no listings match" — say what actually happened.
    status.textContent = "Не удалось загрузить объявления";
    console.error("loadListings failed:", err);
    return;
  }

  renderMarkers(listings);

  const city = document.getElementById("f-city").value;
  if (city && CITY_CENTERS[city]) {
    map.panTo([CITY_CENTERS[city].lat, CITY_CENTERS[city].lng]);
  }

  status.textContent = listings.length
    ? `${listings.length} ${plural(listings.length, "объявление", "объявления", "объявлений")}`
    : "Ничего не найдено";
}

function renderMarkers(listings) {
  markers.forEach((m) => map.removeLayer(m));
  markers = [];

  listings.forEach((listing) => {
    if (listing.lat == null || listing.lng == null) return; // без координат на карте не показываем
    const marker = L.marker([listing.lat, listing.lng]).addTo(map).bindTooltip(priceLabel(listing));
    marker.on("click", () => openListing(listing));
    markers.push(marker);
  });
}

function priceLabel(listing) {
  if (listing.price_min_usd == null) return "цена не указана";
  if (listing.price_max_usd && listing.price_max_usd !== listing.price_min_usd) {
    return `$${listing.price_min_usd}–${listing.price_max_usd}`;
  }
  return `$${listing.price_min_usd}`;
}

const ROOMS_LABEL = { studio: "Студия" };
const TYPE_LABEL = { apartment: "Квартира", room: "Комната", house: "Дом", villa: "Вилла" };
const RENOVATION_LABEL = {
  needs_repair: "Требует ремонта",
  standard: "Стандартный ремонт",
  good: "Хороший ремонт",
  premium: "Премиум",
};
const PETS_LABEL = { allowed: "Можно с питомцами", not_allowed: "Без питомцев", unknown: "Про питомцев не указано" };

function openListing(listing) {
  const gallery = document.getElementById("gallery");
  gallery.innerHTML = "";
  if (listing.photos?.length) {
    listing.photos
      .sort((a, b) => a.position - b.position)
      .forEach((p) => {
        const img = document.createElement("img");
        img.src = p.url;
        img.loading = "lazy";
        gallery.appendChild(img);
      });
  } else {
    gallery.innerHTML = `<div class="no-photo">Фото не приложены</div>`;
  }

  const details = document.getElementById("listingDetails");
  details.innerHTML = `
    <h2>${priceLabel(listing)} / мес</h2>
    <p class="badges">
      ${listing.rooms ? `<span>${ROOMS_LABEL[listing.rooms] || listing.rooms + " комн."}</span>` : ""}
      ${listing.property_type ? `<span>${TYPE_LABEL[listing.property_type]}</span>` : ""}
      ${listing.renovation_quality ? `<span>${RENOVATION_LABEL[listing.renovation_quality]}</span>` : ""}
      <span>${PETS_LABEL[listing.pets_policy]}</span>
      ${listing.area_sqm ? `<span>${listing.area_sqm} м²</span>` : ""}
    </p>
    ${listing.address_text ? `<p class="address">📍 ${listing.address_text}</p>` : ""}
    <p class="description">${escapeHtml(listing.description || "")}</p>
    ${listing.contact ? `<p class="contact">Контакт: ${escapeHtml(listing.contact)}</p>` : ""}
    <a class="source-link" href="${listing.source_url}" target="_blank" rel="noopener">
      Открыть оригинал объявления (${sourceLabel(listing.source_type)}) ↗
    </a>
  `;

  document.getElementById("listingModal").classList.remove("hidden");
}

function sourceLabel(sourceType) {
  return { telegram: "Telegram", facebook: "Facebook", manual: "прислано вручную" }[sourceType] || sourceType;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("closeModal").addEventListener("click", () => {
  document.getElementById("listingModal").classList.add("hidden");
});

document.getElementById("filtersToggle").addEventListener("click", () => {
  document.getElementById("filtersPanel").classList.toggle("open");
});

document.getElementById("applyFilters").addEventListener("click", () => {
  loadListings();
  document.getElementById("filtersPanel").classList.remove("open");
});

document.getElementById("resetFilters").addEventListener("click", () => {
  document.querySelectorAll("#filtersPanel select, #filtersPanel input").forEach((el) => (el.value = ""));
  loadListings();
});
