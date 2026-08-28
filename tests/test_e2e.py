"""End-to-end test of the real request flow, with Telegram calls stubbed out.

Runs against a throwaway SQLite file and Flask's test client, so it needs no
server, no network and no bot token:

    python tests/test_e2e.py

Covers the paths that have broken in production before: ingest of the scraper's
exact payload shape, CORS headers the Mini App depends on, auto-publishing and
the quality gates that replaced manual approval, multi-select filters,
subscriptions, i18n and webhook authentication.
"""
import io
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

db_path = tempfile.mktemp(suffix=".db").replace("\\", "/")
os.environ["DATABASE_URL"] = "sqlite:///" + db_path
os.environ["BOT_TOKEN"] = "test:token"
os.environ["ADMIN_TELEGRAM_IDS"] = "355991407"
os.environ["ADMIN_API_TOKEN"] = "admintok"
os.environ["INGEST_TOKEN"] = "ingesttok"
os.environ["WEBHOOK_SECRET"] = "whsecret"
os.environ["WEBAPP_URL"] = "https://example.github.io/app/"
os.environ["API_BASE_URL"] = "http://localhost:5000"

from server import telegram_api

SENT = []
telegram_api.send_message = lambda cid, text, reply_markup=None: (
    SENT.append(("msg", cid, text)) or {"ok": True}
)
telegram_api.send_photo = lambda cid, url, cap, reply_markup=None: (
    SENT.append(("photo", cid, cap)) or {"ok": True}
)
telegram_api.send_media_group = lambda cid, urls, cap: (
    SENT.append(("album", cid, f"{len(urls)} photos")) or {"ok": True}
)
telegram_api.answer_callback_query = lambda *a, **k: None
telegram_api.edit_message_reply_markup = lambda *a, **k: None
telegram_api.download_file = lambda *a, **k: None

from server.app import app

app.config["TESTING"] = True
c = app.test_client()

FAILURES = []


def ok(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILURES.append(label)


HDR_INGEST = {"X-Ingest-Token": "ingesttok"}
HDR_ADMIN = {"X-Admin-Token": "admintok"}
HDR_HOOK = {"X-Telegram-Bot-Api-Secret-Token": "whsecret"}
ADMIN = 355991407


def recent(days_ago=1):
    return (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "+00:00"


def webhook(payload, headers=HDR_HOOK):
    return c.post("/bot/webhook", json=payload, headers=headers)


def msg(text, user=ADMIN, message_id=1, **extra):
    m = {"message_id": message_id, "chat": {"id": user}, "from": {"id": user, **extra}, "text": text}
    return webhook({"update_id": message_id, "message": m})


def callback(data, user=ADMIN, cb_id="cb"):
    return webhook({"update_id": 99, "callback_query": {
        "id": cb_id, "from": {"id": user}, "data": data,
        "message": {"chat": {"id": user}, "message_id": 1}}})


print("\n=== 1. Public API + CORS ===")
r = c.get("/listings")
ok(r.status_code == 200, f"GET /listings -> {r.status_code}")
ok(r.headers.get("Access-Control-Allow-Origin") == "*", "CORS header present")

print("\n=== 2. Ingest auto-publishes good listings ===")
payload = {
    "channel_username": "danangrentaflat",
    "posts": [
        {"message_id": 1001,
         "text": "Brand-New 2BR apartment in Khue My. Rooms: 2 bedrooms. Square meters: 70 m2. "
                 "Price for 1 month: 800 USD/month (21 million VND/month). No pets. Parking, elevator.",
         "photo_urls": ["https://cdn.example/1", "https://cdn.example/2", "https://cdn.example/3"],
         "posted_at": recent()},
        {"message_id": 1002,
         "text": "Студия в Нячанге у моря. 350$ в месяц, можно с питомцами, хороший ремонт, "
                 "45 м². Балкон, кондиционер, стиральная машина. Депозит 1 месяц.",
         "photo_urls": [], "posted_at": None},
    ],
}
r = c.post("/internal/ingest", json=payload, headers=HDR_INGEST)
body = r.get_json()
ok(r.status_code == 200, f"POST /internal/ingest -> {r.status_code}")
ok(body["published"] == 2, f"published immediately: {body}")

pub = c.get("/listings").get_json()
ok(len(pub) == 2, f"visible on the map right away without moderation ({len(pub)})")
ok(all(l["source_url"].startswith("https://t.me/") for l in pub), "every listing keeps its source link")
ok(pub[0]["lat"] != pub[1]["lat"], "fallback coords jittered so markers don't overlap")

print("\n=== 3. Extractor output on those posts ===")
by_city = {l["city"]: l for l in pub}
dn = by_city.get("da_nang", {})
nt = by_city.get("nha_trang", {})
ok(dn.get("price_min_usd") == 800, f"USD preferred over VND -> {dn.get('price_min_usd')}")
ok(dn.get("rooms") == "2" and dn.get("area_sqm") == 70, f"rooms/area -> {dn.get('rooms')}/{dn.get('area_sqm')}")
ok(dn.get("pets_policy") == "not_allowed", f"pets -> {dn.get('pets_policy')}")
ok(len(dn.get("photos", [])) == 3, f"all 3 photos kept -> {len(dn.get('photos', []))}")
ok(nt.get("rooms") == "studio" and nt.get("price_min_usd") == 350, "studio in Nha Trang parsed")

print("\n=== 4. Quality gates hold back what a human would have rejected ===")
junk = {"channel_username": "danangrentaflat", "posts": [
    {"message_id": 2001, "text": "Ищу квартиру в Дананге до 500$ в месяц, 1 спальня, на длительный срок. "
                                 "Пишите в личку, рассмотрю варианты рядом с морем.",
     "photo_urls": [], "posted_at": recent()},
    {"message_id": 2002, "text": "Продам байк Honda Vision 2023, пробег 5000 км, документы в порядке, "
                                 "цена 800 USD. Дананг, торг уместен при осмотре.",
     "photo_urls": [], "posted_at": recent()},
    {"message_id": 2003, "text": "Хорошая", "photo_urls": [], "posted_at": recent()},
]}
r = c.post("/internal/ingest", json=junk, headers=HDR_INGEST).get_json()
ok(r["held"] == 3 and r["published"] == 0, f"wanted-ad / non-listing / too-short all held: {r}")
ok(len(c.get("/listings").get_json()) == 2, "held items never reached the map")

print("\n=== 5. Suspiciously cheap listing publishes but is flagged ===")
cheap = {"channel_username": "danangrentaflat", "posts": [
    {"message_id": 2100, "text": "Люкс апартаменты 2 спальни у моря в Дананге, вид на океан, "
                                 "бассейн, всего 45 USD в месяц! Успейте, только сегодня, депозит онлайн.",
     "photo_urls": [], "posted_at": recent()}]}
r = c.post("/internal/ingest", json=cheap, headers=HDR_INGEST).get_json()
ok(r["published"] == 1 and r["flagged"] == 1, f"published but flagged for review: {r}")

print("\n=== 6. Duplicate detection ===")
dupe = {"channel_username": "onewaydanang", "posts": [
    {"message_id": 3001,
     "text": "Brand-New 2BR apartment in Khue My. Rooms: 2 bedrooms. Square meters: 70 m2. "
             "Price for 1 month: 800 USD/month (21 million VND/month). No pets. Parking, elevator.",
     "photo_urls": [], "posted_at": recent()}]}
r = c.post("/internal/ingest", json=dupe, headers=HDR_INGEST).get_json()
ok(r["duplicates"] == 1 and r["published"] == 0, f"same flat cross-posted is skipped: {r}")

print("\n=== 7. Stale listings expire automatically ===")
old = {"channel_username": "danangrentaflat", "posts": [
    {"message_id": 4001, "text": "1BR apartment in Da Nang, 600 USD/month, 40 m2, good condition, "
                                 "balcony, parking available, minimum 6 months contract.",
     "photo_urls": [], "posted_at": recent(days_ago=200)}]}
c.post("/internal/ingest", json=old, headers=HDR_INGEST)
urls = [l["source_url"] for l in c.get("/listings").get_json()]
ok(not any("4001" in u for u in urls), "a 200-day-old post is not shown as current")

print("\n=== 8. Multi-select filters ===")
cases = [
    ("city=da_nang", 2), ("city=nha_trang", 1), ("city=da_nang,nha_trang", 3),
    ("rooms=2", 2), ("rooms=studio", 1), ("rooms=2,studio", 3),
    ("property_type=apartment", 3), ("property_type=villa", 0),
    ("pets_policy=allowed", 1), ("pets_policy=not_allowed", 1),
    ("price_min=700", 1), ("price_max=400", 2), ("price_min=300&price_max=900", 2),
    ("city=bogus", 3), ("rooms=99", 3),
]
for qs, expected in cases:
    got = len(c.get(f"/listings?{qs}").get_json())
    ok(got == expected, f"?{qs} -> {got} (expected {expected})")

print("\n=== 9. Security ===")
ok(c.post("/bot/webhook", json={"update_id": 1, "message": {}}).status_code == 403,
   "webhook without the secret -> 403")
ok(c.get("/admin/listings/pending", headers={"X-Admin-Token": "wrong"}).status_code == 403,
   "bad admin token -> 403")
ok(c.post("/internal/ingest", json=payload, headers={"X-Ingest-Token": "wrong"}).status_code == 403,
   "bad ingest token -> 403")

before = len(c.get("/listings").get_json())
callback("reject:1", user=99999)
ok(len(c.get("/listings").get_json()) == before, "non-admin cannot moderate")

print("\n=== 10. Bot menu, i18n and language switching ===")
SENT.clear()
msg("/start", user=500, message_id=10, language_code="en")
ok(any("Rental housing in Vietnam" in t for _, _, t in SENT),
   "English speaker gets English from their Telegram locale")

SENT.clear()
callback("lang:vi", user=500)
ok(any("Nhà cho thuê tại Việt Nam" in t for _, _, t in SENT), "switching to Vietnamese works")

SENT.clear()
msg("/help", user=500, message_id=11)
ok(any("Trợ giúp" in t or "Bot này làm gì" in t for _, _, t in SENT), "language choice persists")

SENT.clear()
msg("/start", user=501, message_id=12)
ok(any("Аренда жилья во Вьетнаме" in t for _, _, t in SENT), "default language is Russian")

print("\n=== 11. Browsing listings inside the chat ===")
SENT.clear()
msg("/latest", user=502, message_id=13)
ok(any(kind in ("album", "photo", "msg") for kind, _, _ in SENT), "/latest sends listings")
ok(any(kind == "album" for kind, _, _ in SENT), "multi-photo listing sent as an album (all photos)")

print("\n=== 12. Subscriptions ===")
SENT.clear()
callback("sub:toggle", user=600)
ok(any("подписк" in t.lower() or "alerts" in t.lower() for _, _, t in SENT), "subscription can be enabled")

SENT.clear()
fresh = {"channel_username": "danangrentaflat", "posts": [
    {"message_id": 5001, "text": "Новая вилла в Дананге, 3 спальни, бассейн, 2500 USD в месяц, "
                                 "180 м², премиум отделка, парковка на 2 машины,長期 контракт.",
     "photo_urls": [], "posted_at": recent()}]}
c.post("/internal/ingest", json=fresh, headers=HDR_INGEST)
ok(any(cid == 600 for _, cid, _ in SENT), "subscriber is pushed the new listing")

callback("sub:toggle", user=600)
SENT.clear()
c.post("/internal/ingest", json={"channel_username": "danangrentaflat", "posts": [
    {"message_id": 5002, "text": "Квартира в Дананге, 1 спальня, 700 USD в месяц, 50 м², "
                                 "хороший ремонт, балкон, парковка, рядом пляж Ми Кхе.",
     "photo_urls": [], "posted_at": recent()}]}, headers=HDR_INGEST)
ok(not any(cid == 600 for _, cid, _ in SENT), "unsubscribed user gets nothing")

print("\n=== 13. Admin tools ===")
SENT.clear()
msg("/stats", message_id=20)
stats = " ".join(t for _, _, t in SENT)
ok("Статистика" in stats and "Опубликовано" in stats, "/stats renders")
ok("Пользователей" in stats, "/stats reports audience")

SENT.clear()
msg("/spam", message_id=21)
ok(any("флуда" in t for _, _, t in SENT), "/spam renders")

SENT.clear()
msg("/review", message_id=22)
ok(any("45" in t or "низкая цена" in t for _, _, t in SENT), "/review surfaces the flagged cheap listing")

SENT.clear()
msg("/stats", user=777, message_id=23)
ok(any("администратор" in t.lower() for _, _, t in SENT), "non-admin refused")

print("\n=== 14. User submissions still require approval ===")
SENT.clear()
msg("/submit Вилла в Дананге 1200$ в месяц https://facebook.com/groups/x/posts/1",
    user=800, message_id=30, username="tester")
pending = c.get("/admin/listings/pending", headers=HDR_ADMIN).get_json()
ok(len(pending) == 1 and pending[0]["source_type"] == "facebook",
   f"user submission held for review, not auto-published ({len(pending)})")
ok(any(cid == ADMIN for _, cid, _ in SENT), "admin notified about the submission")

sub_id = pending[0]["id"]
callback(f"approve:{sub_id}")
ok(any(l["id"] == sub_id for l in c.get("/listings").get_json()), "approving publishes it")

print("\n=== 15. Robustness ===")
for bad in ["", "approve", "approve:abc", "drop:1", "approve:1:2", "lang:klingon"]:
    ok(callback(bad).status_code == 200, f"callback {bad!r} handled without a 500")

r = c.post("/internal/ingest", json={"channel_username": "danangrentaflat", "posts": [
    {"message_id": 6001, "text": "<b>Дом</b> <script>alert(1)</script> в Дананге 500$ в месяц, "
                                 "3 спальни, 120 м², хороший ремонт, парковка, тихий район.",
     "photo_urls": [], "posted_at": "not-a-date"}]}, headers=HDR_INGEST)
ok(r.status_code == 200, f"HTML in text + unparseable date -> {r.status_code}")

ok(webhook({"update_id": 1, "message": {"chat": {"id": 1}, "text": "hi"}}).status_code == 200,
   "update with no sender handled")

print("\n=== 16. Extractor regression pins ===")
from db.models import City
from parser.extractor import extract

e = extract("Price for 1 month: 37 million VND/month (1,400 USD/month)", default_city=City.DA_NANG)
ok(e.price_min_usd == 1400, f"'1,400 USD' -> {e.price_min_usd} (thousands separator, prefers USD)")

e = extract("Стоимость: 22 млн VND / месяц", default_city=City.DA_NANG)
ok(e.price_min_usd and 700 < e.price_min_usd < 1000, f"'22 млн VND' -> ${e.price_min_usd}")

e = extract("Distance to the sea: less than #700m. Contact +84919289420. 800 USD/month")
ok(e.price_min_usd == 800, f"hashtags/phones not read as price -> {e.price_min_usd}")

e = extract("Brand-New 1BR apartment in Khue My. 540 USD/month. 45 m2", default_city=City.DA_NANG)
ok(e.city == City.DA_NANG, "district-only post inherits the channel's city")

print()
if FAILURES:
    print(f"{len(FAILURES)} CHECK(S) FAILED:")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("All checks passed.")
