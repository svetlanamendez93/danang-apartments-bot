// Translations for the Mini App, mirroring server/i18n.py.
// Russian is primary; the language control is always visible in the header so
// a non-Russian speaker can find it without reading any Russian.

const LANGS = [
  { code: "ru", name: "Русский", flag: "🇷🇺" },
  { code: "en", name: "English", flag: "🇬🇧" },
  { code: "vi", name: "Tiếng Việt", flag: "🇻🇳" },
];

const I18N = {
  filters:       { ru: "Фильтры",           en: "Filters",         vi: "Bộ lọc" },
  map:           { ru: "Карта",             en: "Map",             vi: "Bản đồ" },
  list:          { ru: "Список",            en: "List",            vi: "Danh sách" },
  city:          { ru: "Город",             en: "City",            vi: "Thành phố" },
  budget:        { ru: "Бюджет в месяц, $", en: "Budget per month, $", vi: "Ngân sách mỗi tháng, $" },
  rooms:         { ru: "Комнаты",           en: "Bedrooms",        vi: "Phòng ngủ" },
  property_type: { ru: "Тип жилья",         en: "Property type",   vi: "Loại nhà" },
  renovation:    { ru: "Качество ремонта",  en: "Condition",       vi: "Tình trạng" },
  pets:          { ru: "Питомцы",           en: "Pets",            vi: "Thú cưng" },
  // Deliberately identical in all three languages: whoever opens this is by
  // definition looking at a language they may not read, so the heading has to
  // contain a word each of them recognises.
  language:      { ru: "Язык · Language · Ngôn ngữ", en: "Язык · Language · Ngôn ngữ", vi: "Язык · Language · Ngôn ngữ" },
  from:          { ru: "от",                en: "from",            vi: "từ" },
  to:            { ru: "до",                en: "to",              vi: "đến" },
  reset:         { ru: "Сбросить",          en: "Reset",           vi: "Đặt lại" },
  show:          { ru: "Показать",          en: "Show",            vi: "Hiển thị" },
  any:           { ru: "Любой",             en: "Any",             vi: "Bất kỳ" },
  loading:       { ru: "Загрузка…",         en: "Loading…",        vi: "Đang tải…" },
  load_failed:   { ru: "Не удалось загрузить объявления", en: "Could not load listings", vi: "Không tải được tin đăng" },
  nothing_found: { ru: "Ничего не найдено", en: "Nothing found",   vi: "Không tìm thấy" },
  no_photo:      { ru: "Фото нет",          en: "No photos",       vi: "Không có ảnh" },
  open_original: { ru: "Открыть оригинал",  en: "Open the original", vi: "Mở bài gốc" },
  source:        { ru: "Источник",          en: "Source",          vi: "Nguồn" },
  price_unknown: { ru: "Цена не указана",   en: "Price not stated", vi: "Chưa có giá" },
  per_month:     { ru: "/мес",              en: "/mo",             vi: "/tháng" },
  no_coords:     { ru: "без точного адреса", en: "no exact address", vi: "chưa có địa chỉ chính xác" },
  results:       { ru: "объявлений",        en: "listings",        vi: "tin đăng" },
  contact:       { ru: "Контакт",           en: "Contact",         vi: "Liên hệ" },
  posted:        { ru: "Опубликовано",      en: "Posted",          vi: "Đã đăng" },
  area:          { ru: "Площадь",           en: "Area",            vi: "Diện tích" },
  description:   { ru: "Описание",          en: "Description",     vi: "Mô tả" },
  user_submitted:{ ru: "от пользователя",   en: "user submitted",  vi: "người dùng gửi" },
  approx_location:{
    ru: "⚠️ Адрес в объявлении не указан — метка показывает лишь район города",
    en: "⚠️ The post gives no address — the pin only shows the general area",
    vi: "⚠️ Bài đăng không có địa chỉ — ghim chỉ cho biết khu vực chung",
  },
  open_in_maps:  { ru: "Открыть в Google Maps", en: "Open in Google Maps", vi: "Mở trong Google Maps" },
  open_area_in_maps: {
    ru: "Посмотреть район в Google Maps",
    en: "View the area in Google Maps",
    vi: "Xem khu vực trên Google Maps",
  },
  sort_newest:     { ru: "Сначала новые",   en: "Newest first",     vi: "Mới nhất trước" },
  sort_price_asc:  { ru: "Сначала дешёвые", en: "Cheapest first",   vi: "Rẻ nhất trước" },
  sort_price_desc: { ru: "Сначала дорогие", en: "Most expensive",   vi: "Đắt nhất trước" },
  sort_area_desc:  { ru: "Больше площадь",  en: "Largest area",     vi: "Diện tích lớn nhất" },
  tab_all:       { ru: "Все",               en: "All",             vi: "Tất cả" },
  tab_saved:     { ru: "★ Избранное",       en: "★ Saved",         vi: "★ Đã lưu" },
  tab_viewed:    { ru: "Просмотренные",     en: "Viewed",          vi: "Đã xem" },
  seen:          { ru: "просмотрено",       en: "viewed",          vi: "đã xem" },
  load_more:     { ru: "Показать ещё",      en: "Load more",       vi: "Xem thêm" },
  nothing_saved: { ru: "Пока ничего не добавлено в избранное. Нажмите ☆ на карточке.",
                   en: "Nothing saved yet. Tap ☆ on a card.",
                   vi: "Chưa lưu tin nào. Chạm ☆ trên thẻ." },
  nothing_viewed:{ ru: "Пока нет просмотренных объявлений.",
                   en: "No viewed listings yet.",
                   vi: "Chưa xem tin nào." },
  today:         { ru: "сегодня",           en: "today",           vi: "hôm nay" },
  yesterday:     { ru: "вчера",             en: "yesterday",       vi: "hôm qua" },
  ago:           { ru: "назад",             en: "ago",             vi: "trước" },
  day_1:         { ru: "день",              en: "day",             vi: "ngày" },
  day_2:         { ru: "дня",               en: "days",            vi: "ngày" },
  day_5:         { ru: "дней",              en: "days",            vi: "ngày" },
  scam_warning:  {
    ru: "Проверяйте жильё лично. Не переводите депозит, не увидев квартиру.",
    en: "Always view the place in person. Never send a deposit before seeing it.",
    vi: "Hãy xem nhà trực tiếp. Đừng chuyển tiền cọc trước khi xem.",
  },
};

// Filter option labels. Values must match the API's enum values exactly.
const OPTIONS = {
  city: [
    { v: "da_nang",     ru: "Дананг",   en: "Da Nang",          vi: "Đà Nẵng" },
    { v: "nha_trang",   ru: "Нячанг",   en: "Nha Trang",        vi: "Nha Trang" },
    { v: "ho_chi_minh", ru: "Хошимин",  en: "Ho Chi Minh City", vi: "TP. Hồ Chí Minh" },
    { v: "hanoi",       ru: "Ханой",    en: "Hanoi",            vi: "Hà Nội" },
    { v: "hoi_an",      ru: "Хойан",    en: "Hoi An",           vi: "Hội An" },
  ],
  rooms: [
    { v: "studio", ru: "Студия", en: "Studio", vi: "Studio" },
    { v: "1", ru: "1", en: "1", vi: "1" },
    { v: "2", ru: "2", en: "2", vi: "2" },
    { v: "3", ru: "3", en: "3", vi: "3" },
    { v: "4", ru: "4+", en: "4+", vi: "4+" },
  ],
  property_type: [
    { v: "apartment", ru: "Квартира", en: "Apartment", vi: "Căn hộ" },
    { v: "room",      ru: "Комната",  en: "Room",      vi: "Phòng" },
    { v: "house",     ru: "Дом",      en: "House",     vi: "Nhà" },
    { v: "villa",     ru: "Вилла",    en: "Villa",     vi: "Biệt thự" },
  ],
  renovation_quality: [
    { v: "needs_repair", ru: "Требует ремонта", en: "Needs repair", vi: "Cần sửa" },
    { v: "standard",     ru: "Обычный",         en: "Standard",     vi: "Tiêu chuẩn" },
    { v: "good",         ru: "Хороший",         en: "Good",         vi: "Tốt" },
    { v: "premium",      ru: "Премиум",         en: "Premium",      vi: "Cao cấp" },
  ],
  pets_policy: [
    { v: "allowed",     ru: "Можно",  en: "Allowed",  vi: "Được phép" },
    { v: "not_allowed", ru: "Нельзя", en: "Not allowed", vi: "Không được" },
  ],
};

function detectLang() {
  try {
    const saved = localStorage.getItem("lang");
    if (saved && LANGS.some((l) => l.code === saved)) return saved;
  } catch (e) {
    /* private mode blocks storage; fall through to auto-detection */
  }
  const tgLang = window.Telegram?.WebApp?.initDataUnsafe?.user?.language_code;
  const nav = (tgLang || navigator.language || "ru").toLowerCase().split("-")[0];
  return LANGS.some((l) => l.code === nav) ? nav : "ru";
}

let LANG = detectLang();

function setLang(code) {
  LANG = code;
  try {
    localStorage.setItem("lang", code);
  } catch (e) {
    /* not fatal — the choice just won't persist */
  }
  document.documentElement.lang = code;
}

function t(key) {
  return I18N[key]?.[LANG] ?? I18N[key]?.ru ?? key;
}

function optLabel(group, value) {
  const found = OPTIONS[group]?.find((o) => o.v === value);
  return found ? found[LANG] || found.ru : value;
}
