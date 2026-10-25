"""Tests for Mini App API: initData HMAC verification + order end-to-end (no network)."""
import asyncio, hashlib, hmac, json, os, tempfile, time, sys
from urllib.parse import urlencode
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aiohttp.test_utils import TestClient, TestServer
import webserver, db as dbm

TOKEN = "123456:TEST_TOKEN_abc"


def make_init(user, token=TOKEN, auth_date=None, tamper=False):
    f = {"query_id": "AAH", "user": json.dumps(user, separators=(",", ":")), "auth_date": str(auth_date or int(time.time()))}
    chk = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(secret, chk.encode(), hashlib.sha256).hexdigest()
    if tamper:
        f["user"] = f["user"].replace("Ali", "Vali")
    return urlencode(f)


def test_verify():
    u = {"id": 42, "first_name": "Ali"}
    assert webserver.verify_init_data(make_init(u), TOKEN)["user"]["id"] == 42
    assert webserver.verify_init_data(make_init(u, tamper=True), TOKEN) is None
    assert webserver.verify_init_data(make_init(u, token="999:other"), TOKEN) is None
    assert webserver.verify_init_data(make_init(u, auth_date=int(time.time()) - 3 * 86400), TOKEN) is None
    assert webserver.verify_init_data("", TOKEN) is None
    assert webserver.verify_init_data("hash=abc", TOKEN) is None
    print("verify OK")


async def e2e():
    d = dbm.DB(os.path.join(tempfile.mkdtemp(), "t.db"))
    d.set("open_time", "00:00"); d.set("close_time", "23:59")
    sent = []

    class Ctx: pass
    c = Ctx(); c.db, c.token, c.bot = d, TOKEN, None
    c.is_open, c.hours = (lambda: True), (lambda: "00:00–23:59")
    async def hook(oid, want): sent.append((oid, want))
    c.on_webapp_order = hook
    client = TestClient(TestServer(webserver.make_app(c)))
    await client.start_server()
    try:
        r = await client.get("/api/menu"); m = await r.json()
        assert r.status == 200 and len(m["categories"]) == 7 and len(m["items"]) == 23, m.keys()
        r = await client.get("/"); assert r.status == 200 and "telegram-web-app.js" in await r.text()
        r = await client.get("/photo/1"); assert r.status == 200 and r.content_type.startswith("image")
        r = await client.get("/static/logo.svg"); assert r.status == 200
        u = {"id": 777, "first_name": "Ali", "username": "ali"}
        h = {"X-Init-Data": make_init(u)}
        r = await client.post("/api/me", json={}, headers=h); j = await r.json(); assert j["ok"] and j["name"] == "Ali"
        r = await client.post("/api/me", json={}, headers={"X-Init-Data": make_init(u, tamper=True)}); assert r.status == 401
        # sold-out item is ignored, price taken from DB not client
        d.x("UPDATE items SET available=0 WHERE id=2")
        body = {"name": "Ali", "phone": "90 123 45 67", "otype": "delivery", "payment": "cash", "address": "",
                "send_location": True, "items": [{"id": 1, "qty": 2, "price": 1}, {"id": 2, "qty": 1}], "comment": "piyozsiz"}
        r = await client.post("/api/order", json=body, headers=h); j = await r.json()
        assert j["ok"], j
        o = d.order(j["order_id"]); its = d.order_items(j["order_id"])
        assert len(its) == 1 and its[0]["qty"] == 2 and o["subtotal"] == 56000 and o["delivery_fee"] == 10000
        assert o["phone"] == "+998 90 123 45 67" and o["address"] == "📍 Lokatsiya kutilmoqda" and o["status"] == "new"
        assert sent == [(j["order_id"], True)]
        r = await client.post("/api/me", json={}, headers=h); j2 = await r.json(); assert j2["phone"] == "+998 90 123 45 67"
        # pickup with GPS coords, bad phone, unauth
        body.update(otype="pickup", payment="card", phone="123"); r = await client.post("/api/order", json=body, headers=h); assert r.status == 400
        r = await client.post("/api/order", json=body); assert r.status == 401
        body.update(phone="+998901234567", otype="delivery", send_location=False, lat=41.31, lon=69.28)
        r = await client.post("/api/order", json=body, headers=h); j = await r.json(); assert j["ok"]
        o = d.order(j["order_id"]); assert o["lat"] == 41.31 and sent[-1][1] is False
        c.is_open = lambda: False
        r = await client.post("/api/order", json=body, headers=h); assert r.status == 400 and "yopiq" in (await r.json())["error"]
        print("e2e OK, orders:", [x[0] for x in sent])
    finally:
        await client.close()


if __name__ == "__main__":
    test_verify(); asyncio.run(e2e()); print("ALL OK")
