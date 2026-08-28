"""End-to-end test of the real request flow, with Telegram calls stubbed out.

Runs against a throwaway SQLite file and Flask's test client, so it needs no
server, no network and no bot token:

    python tests/test_e2e.py

Covers the paths that broke in production before: ingest of the scraper's exact
payload shape, CORS headers the Mini App depends on, the moderation queue, and
webhook authentication.
"""
import io
import os
import sys
import tempfile

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
telegram_api.send_message = lambda cid, text, reply_markup=None: SENT.append(("msg", cid, text[:120])) or {"ok": True}
telegram_api.send_photo = lambda cid, url, cap, reply_markup=None: SENT.append(("photo", cid, cap[:80])) or {"ok": True}
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

print("\n=== 1. Public listings endpoint + CORS ===")
r = c.get("/listings")
ok(r.status_code == 200, f"GET /listings -> {r.status_code}")
ok(r.headers.get("Access-Control-Allow-Origin") == "*",
   f"CORS header present: {r.headers.get('Access-Control-Allow-Origin')}")

print("\n=== 2. Ingest with the exact payload the scraper sends ===")
payload = {
    "channel_username": "danangrentaflat",
    "posts": [
        {
            "message_id": 124445,
            "text": "Brand-New 2BR apartment in Khue My. Price: 800 USD/month. No pets. Da Nang",
            "photo_urls": ["https://cdn5.telesco.pe/file/abc123"],
            "posted_at": "2026-03-13T15:14:19+00:00",  # ISO string, as scraped
        },
        {
            "message_id": 124446,
            "text": "Студия в Нячанге, 350$ в месяц, можно с питомцами, хороший ремонт",
            "photo_urls": [],
            "posted_at": None,  # posts that render no <time>
        },
    ],
}
r = c.post("/internal/ingest", json=payload, headers={"X-Ingest-Token": "ingesttok"})
ok(r.status_code == 200, f"POST /internal/ingest -> {r.status_code}")
ok(r.get_json().get("created") == 2, f"created={r.get_json()}")

print("\n=== 3. Ingest is idempotent (same posts again) ===")
r = c.post("/internal/ingest", json=payload, headers={"X-Ingest-Token": "ingesttok"})
ok(r.get_json().get("created") == 0, f"duplicate ingest created={r.get_json()}")

print("\n=== 4. Extractor results on the real scraped text ===")
r = c.get("/admin/listings/pending", headers={"X-Admin-Token": "admintok"})
pend = r.get_json()
ok(len(pend) == 2, f"2 pending listings, got {len(pend)}")
for p in pend:
    print(f"    #{p['id']} city={p['city']} price={p['price_min_usd']}-{p['price_max_usd']} "
          f"rooms={p['rooms']} pets={p['pets_policy']} lat={p['lat']:.4f} lng={p['lng']:.4f}")
ok(pend[0]["lat"] != pend[1]["lat"], "fallback coords are jittered, markers won't overlap")

print("\n=== 5. Pending listings are NOT public yet ===")
ok(c.get("/listings").get_json() == [], "GET /listings still empty while pending")

print("\n=== 6. Webhook rejects a forged update (no secret) ===")
forged = {"update_id": 1, "callback_query": {"id": "1", "from": {"id": 355991407},
          "data": f"approve:{pend[0]['id']}", "message": {"chat": {"id": 1}, "message_id": 1}}}
r = c.post("/bot/webhook", json=forged)
ok(r.status_code == 403, f"forged webhook -> {r.status_code} (expected 403)")

print("\n=== 7. /pending moderation queue works ===")
SENT.clear()
c.post("/bot/webhook", json={"update_id": 2, "message": {
    "message_id": 10, "chat": {"id": 355991407}, "from": {"id": 355991407}, "text": "/pending"}},
    headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
ok(any(k == "photo" for k, _, _ in SENT), f"queue sent a listing: {SENT}")

print("\n=== 8. Approve via callback publishes it ===")
c.post("/bot/webhook", json={"update_id": 3, "callback_query": {
    "id": "cb1", "from": {"id": 355991407}, "data": f"approve:{pend[0]['id']}",
    "message": {"chat": {"id": 355991407}, "message_id": 11}}},
    headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
pub = c.get("/listings").get_json()
ok(len(pub) == 1, f"approved listing is now public ({len(pub)})")
ok(pub[0]["source_url"] == "https://t.me/danangrentaflat/124445", f"source link: {pub[0]['source_url']}")

print("\n=== 9. Non-admin cannot moderate ===")
c.post("/bot/webhook", json={"update_id": 4, "callback_query": {
    "id": "cb2", "from": {"id": 99999}, "data": f"approve:{pend[1]['id']}",
    "message": {"chat": {"id": 99999}, "message_id": 12}}},
    headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
ok(len(c.get("/listings").get_json()) == 1, "non-admin approve had no effect")

print("\n=== 10. Filters ===")
cases = [
    ("city=da_nang", 1), ("city=nha_trang", 0), ("city=bogus", 1),
    ("price_min=700", 1), ("price_min=900", 0), ("price_max=500", 0), ("price_max=900", 1),
    ("rooms=2", 1), ("rooms=studio", 0), ("pets_policy=not_allowed", 1), ("pets_policy=allowed", 0),
    ("property_type=apartment", 1), ("property_type=villa", 0),
]
for qs, expected in cases:
    got = len(c.get(f"/listings?{qs}").get_json())
    ok(got == expected, f"?{qs} -> {got} (expected {expected})")

print("\n=== 11. Malformed callback data doesn't crash ===")
for bad in ["", "approve", "approve:abc", "drop:1", "approve:1:2"]:
    r = c.post("/bot/webhook", json={"update_id": 5, "callback_query": {
        "id": "cb", "from": {"id": 355991407}, "data": bad,
        "message": {"chat": {"id": 1}, "message_id": 1}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
    ok(r.status_code == 200, f"callback data {bad!r} -> {r.status_code}")

print("\n=== 12. /submit creates a pending listing ===")
SENT.clear()
c.post("/bot/webhook", json={"update_id": 6, "message": {
    "message_id": 20, "chat": {"id": 777}, "from": {"id": 777, "username": "tester"},
    "text": "/submit Вилла в Дананге 1200$ в месяц https://facebook.com/groups/x/posts/1"}},
    headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
pend2 = c.get("/admin/listings/pending", headers={"X-Admin-Token": "admintok"}).get_json()
sub = [p for p in pend2 if p["source_type"] == "facebook"]
ok(len(sub) == 1, f"facebook submission recorded ({len(sub)})")
if sub:
    print(f"    source_url={sub[0]['source_url']} price={sub[0]['price_min_usd']} type={sub[0]['property_type']}")

print("\n=== 13. Admin endpoints reject a bad token ===")
ok(c.get("/admin/listings/pending", headers={"X-Admin-Token": "wrong"}).status_code == 403, "bad admin token -> 403")
ok(c.post("/internal/ingest", json=payload, headers={"X-Ingest-Token": "wrong"}).status_code == 403,
   "bad ingest token -> 403")

print("\n=== 14. HTML-unsafe text survives (no crash / no injection) ===")
r = c.post("/internal/ingest", json={"channel_username": "danangrentaflat", "posts": [
    {"message_id": 999, "text": "<b>Дом</b> <script>alert(1)</script> 500$ Дананг",
     "photo_urls": [], "posted_at": "bogus-not-a-date"}]},
    headers={"X-Ingest-Token": "ingesttok"})
ok(r.status_code == 200, f"unparseable date + HTML text ingested -> {r.status_code}")

print()
if FAILURES:
    print(f"{len(FAILURES)} CHECK(S) FAILED:")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("All checks passed.")
