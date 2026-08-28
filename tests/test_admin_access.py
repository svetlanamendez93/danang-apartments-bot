"""Audit: can anyone who is not the admin reach any admin capability?

Tries every admin surface as an outsider, including someone who knows the
command names, the listing ids and the webhook URL.
"""
import io
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.update(
    DATABASE_URL="sqlite:///" + tempfile.mktemp(suffix=".db").replace("\\", "/"),
    BOT_TOKEN="test:token",
    ADMIN_TELEGRAM_IDS="355991407",       # the real admin
    ADMIN_API_TOKEN="admintok",
    INGEST_TOKEN="ingesttok",
    WEBHOOK_SECRET="whsecret",
    WEBAPP_URL="https://example.github.io/app/",
    API_BASE_URL="http://localhost:5000",
)

from server import telegram_api

SENT = []
telegram_api.send_message = lambda cid, text, reply_markup=None: (SENT.append((cid, text, reply_markup)) or {"ok": True})
telegram_api.send_photo = lambda cid, u, cap, reply_markup=None: (SENT.append((cid, cap, reply_markup)) or {"ok": True})
telegram_api.send_media_group = lambda cid, u, cap: (SENT.append((cid, cap, None)) or {"ok": True})
telegram_api.answer_callback_query = lambda *a, **k: None
telegram_api.edit_message_reply_markup = lambda *a, **k: None

from server.app import app

app.config["TESTING"] = True
c = app.test_client()
HOOK = {"X-Telegram-Bot-Api-Secret-Token": "whsecret"}

ADMIN = 355991407
INTRUDER = 66666666

FAILS = []
def ok(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)

def send(text, user, mid=1):
    SENT.clear()
    c.post("/bot/webhook", headers=HOOK, json={"update_id": mid, "message": {
        "message_id": mid, "chat": {"id": user}, "from": {"id": user}, "text": text}})
    return " ".join(t for _, t, _ in SENT)

def cb(data, user):
    SENT.clear()
    c.post("/bot/webhook", headers=HOOK, json={"update_id": 2, "callback_query": {
        "id": "x", "from": {"id": user}, "data": data,
        "message": {"chat": {"id": user}, "message_id": 1}}})
    return " ".join(t for _, t, _ in SENT)

# Seed data so there is something worth attacking.
c.post("/internal/ingest", headers={"X-Ingest-Token": "ingesttok"}, json={
    "channel_username": "danangrentaflat", "posts": [{
        "message_id": 1, "text": "2BR apartment in Da Nang, 800 USD/month, 70 m2, good condition, "
                                 "parking, elevator, balcony, long term contract.",
        "photo_urls": [], "posted_at": None}]})
listing_id = c.get("/listings").get_json()[0]["id"]

print("=== Admin commands as an outsider ===")
for cmd in ["/stats", "/review", "/pending", "/spam"]:
    out = send(cmd, INTRUDER)
    refused = "администратор" in out.lower() or "administrator" in out.lower()
    leaked = any(w in out for w in ["Статистика", "Опубликовано", "Пользователей", "флуда", "осталось:"])
    ok(refused and not leaked, f"{cmd} refused and leaks nothing (got: {out[:60]!r})")

print("\n=== Admin callbacks as an outsider ===")
out = cb("admin:menu", INTRUDER)
ok("/stats" not in out, f"admin:menu reveals no admin commands (got: {out[:60]!r})")

before = len(c.get("/listings").get_json())
cb(f"reject:{listing_id}", INTRUDER)
ok(len(c.get("/listings").get_json()) == before, "outsider cannot reject a live listing")

# A pending (user-submitted) listing must not be publishable by an outsider.
c.post("/bot/webhook", headers=HOOK, json={"update_id": 9, "message": {
    "message_id": 9, "chat": {"id": 4242}, "from": {"id": 4242, "username": "someone"},
    "text": "/submit Вилла в Дананге 1200$ в месяц https://facebook.com/x"}})
pending = c.get("/admin/listings/pending", headers={"X-Admin-Token": "admintok"}).get_json()
pid = pending[0]["id"]
cb(f"approve:{pid}", INTRUDER)
still_pending = c.get("/admin/listings/pending", headers={"X-Admin-Token": "admintok"}).get_json()
ok(any(p["id"] == pid for p in still_pending), "outsider cannot approve a pending listing")

print("\n=== The admin themself still works ===")
out = send("/stats", ADMIN)
ok("Статистика" in out, "admin gets /stats")
cb(f"approve:{pid}", ADMIN)
ok(any(l["id"] == pid for l in c.get("/listings").get_json()), "admin can approve")

print("\n=== Admin button is not even shown to outsiders ===")
SENT.clear()
send("/start", INTRUDER, mid=30)
markup = str(SENT[-1][2]) if SENT else ""
ok("admin:menu" not in markup, "no Admin button in an outsider's menu")

SENT.clear()
send("/start", ADMIN, mid=31)
markup_admin = str(SENT[-1][2]) if SENT else ""
ok("admin:menu" in markup_admin, "Admin button present for the admin")

print("\n=== HTTP admin endpoints ===")
for path, method in [("/admin/listings/pending", "get"), ("/admin/sources", "get")]:
    r = getattr(c, method)(path)
    ok(r.status_code == 403, f"{path} with no token -> {r.status_code}")
    r = getattr(c, method)(path, headers={"X-Admin-Token": "guess", "X-Ingest-Token": "guess"})
    ok(r.status_code == 403, f"{path} with a wrong token -> {r.status_code}")

r = c.post(f"/admin/listings/{listing_id}/reject")
ok(r.status_code == 403, f"POST reject with no token -> {r.status_code}")
r = c.patch(f"/admin/listings/{listing_id}", json={"price_min_usd": 1})
ok(r.status_code == 403, f"PATCH listing with no token -> {r.status_code}")

print("\n=== Forged webhook (attacker impersonating the admin id) ===")
r = c.post("/bot/webhook", json={"update_id": 1, "callback_query": {
    "id": "x", "from": {"id": ADMIN}, "data": f"reject:{listing_id}",
    "message": {"chat": {"id": ADMIN}, "message_id": 1}}})
ok(r.status_code == 403, f"no webhook secret -> {r.status_code}")
ok(len(c.get("/listings").get_json()) >= 1, "forged admin update changed nothing")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("All admin-access checks passed.")
