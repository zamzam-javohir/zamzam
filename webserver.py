"""HTTP API + Mini App static server for ZAM ZAM (runs inside the bot process)."""
import hashlib, hmac, json, logging, os, time, re
from urllib.parse import parse_qsl
from aiohttp import web

from db import CATEGORIES

log = logging.getLogger("zamzam.web")
BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "webapp")
PHOTOS = os.path.join(BASE, "photos")
CACHE = os.path.join(PHOTOS, "cache")
INITDATA_MAX_AGE = 24 * 3600


def verify_init_data(init_data: str, token: str, max_age=INITDATA_MAX_AGE):
    """Validate Telegram WebApp initData (HMAC-SHA256, per core.telegram.org/bots/webapps). Returns dict or None."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    recv = pairs.pop("hash", None)
    if not recv:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, recv):
        return None
    try:
        if max_age and time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        pairs["user"] = json.loads(pairs.get("user", "{}"))
    except (ValueError, TypeError):
        return None
    if not pairs["user"].get("id"):
        return None
    return pairs


def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    if len(d) == 9:
        d = "998" + d
    if len(d) == 12 and d.startswith("998"):
        return f"+{d[:3]} {d[3:5]} {d[5:8]} {d[8:10]} {d[10:]}"
    return None


async def place_order(ctx, u, b):
    """Shared order logic for /api/order (initData) and bot web_app_data (keyboard-button launch)."""
    uid = int(u["id"])
    db = ctx.db
    err = lambda m: {"ok": False, "error": m}
    if not ctx.is_open():
        return err(f"Hozir yopiqmiz. Ish vaqti: {ctx.hours()}")
    name = str(b.get("name", "")).strip()[:64]
    phone = norm_phone(str(b.get("phone", "")))
    otype = b.get("otype")
    payment = {"cash": "💵 Naqd", "card": "💳 Karta"}.get(b.get("payment"))
    address = str(b.get("address", "")).strip()[:300]
    lat = lon = None
    try:
        if b.get("lat") is not None and b.get("lon") is not None:
            lat, lon = float(b["lat"]), float(b["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                lat = lon = None
    except (TypeError, ValueError):
        lat = lon = None
    want_loc = bool(b.get("send_location")) and otype == "delivery" and lat is None
    if len(name) < 2:
        return err("Ismingizni kiriting")
    if not phone:
        return err("Telefon raqam noto'g'ri. Masalan: +998 90 123 45 67")
    if otype not in ("delivery", "pickup") or not payment:
        return err("Ma'lumotlar to'liq emas")
    if otype == "delivery" and len(address) < 5 and not want_loc and lat is None:
        return err("Manzilni yozing yoki lokatsiya yuborishni tanlang")
    items = b.get("items") or []
    if not isinstance(items, list) or not items or len(items) > 50:
        return err("Savat bo'sh")
    # rebuild cart server-side with real prices
    with db.lock:
        db.c.execute("DELETE FROM cart WHERE user_id=?", (uid,))
        for x in items:
            try:
                iid, q = int(x["id"]), int(x["qty"])
            except (KeyError, ValueError, TypeError):
                continue
            it = db.item(iid)
            if it and it["available"] and 0 < q <= 99:
                db.c.execute("INSERT OR REPLACE INTO cart VALUES(?,?,?)", (uid, iid, q))
        db.c.execute("""INSERT INTO users(id, full_name, username, created) VALUES(?,?,?,datetime('now'))
                        ON CONFLICT(id) DO NOTHING""",
                     (uid, " ".join(filter(None, [u.get("first_name"), u.get("last_name")])), u.get("username")))
        db.c.commit()
    if not db.totals(uid, otype)[0]:
        return err("Tanlangan taomlar hozir mavjud emas")
    oid = db.create_order(uid, {"name": name, "phone": phone, "otype": otype, "payment": payment,
                                "address": (address or ("📍 Lokatsiya yuborildi" if lat is not None else "📍 Lokatsiya kutilmoqda")) if otype == "delivery" else None,
                                "lat": lat if otype == "delivery" else None, "lon": lon if otype == "delivery" else None,
                                "comment": str(b.get("comment", "")).strip()[:500] or None})
    if not oid:
        return err("Savat bo'sh")
    log.info("WebApp order #%s by %s", oid, uid)
    o = db.order(oid)
    await ctx.on_webapp_order(oid, want_loc)
    return {"ok": True, "order_id": oid, "total": o["total"]}


def make_app(ctx):
    """ctx: object with .db, .token, .bot, .is_open(), .hours(), .on_webapp_order(oid, want_loc) coroutine."""
    app = web.Application(client_max_size=256 * 1024)
    os.makedirs(CACHE, exist_ok=True)

    def item_json(r):
        return {"id": r["id"], "cat": r["cat"], "name": r["name"], "descr": r["descr"] or "", "price": r["price"],
                "available": bool(r["available"]), "photo": f"/photo/{r['id']}?v={(r['file_id'] or r['photo_path'] or '')[-8:]}"}

    async def index(req):
        resp = web.FileResponse(os.path.join(STATIC, "index.html"))
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def menu(req):
        db = ctx.db
        cats = []
        items = []
        for key, title in CATEGORIES:
            rows = db.items(key)
            if not rows:
                continue
            emoji, _, name = title.partition(" ")
            cats.append({"key": key, "emoji": emoji, "name": name})
            items += [item_json(r) for r in rows]
        s = {k: db.get(k) for k in ("open_time", "close_time", "phone", "address", "telegram", "about")}
        s.update(delivery_fee=db.geti("delivery_fee"), free_delivery_from=db.geti("free_delivery_from"),
                 is_open=ctx.is_open(), hours=ctx.hours())
        return web.json_response({"categories": cats, "items": items, "settings": s},
                                 headers={"Cache-Control": "no-store"})

    async def photo(req):
        iid = int(req.match_info["iid"])
        it = ctx.db.item(iid)
        if not it:
            raise web.HTTPNotFound()
        p = os.path.join(PHOTOS, it["photo_path"] or "")
        if it["photo_path"] and os.path.isfile(p):
            return web.FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})
        if it["file_id"]:
            cp = os.path.join(CACHE, hashlib.md5(it["file_id"].encode()).hexdigest() + ".jpg")
            if not os.path.isfile(cp):
                try:
                    f = await ctx.bot.get_file(it["file_id"])
                    await ctx.bot.download_file(f.file_path, cp)
                except Exception as ex:
                    log.warning("photo download failed for %s: %s", iid, type(ex).__name__)
                    raise web.HTTPNotFound()
            return web.FileResponse(cp, headers={"Cache-Control": "public, max-age=86400"})
        raise web.HTTPNotFound()

    async def auth(req):
        try:
            body = await req.json()
        except Exception:
            body = {}
        data = verify_init_data(req.headers.get("X-Init-Data") or body.get("initData", ""), ctx.token)
        if not data:
            raise web.HTTPUnauthorized(text=json.dumps({"ok": False, "error": "auth"}), content_type="application/json")
        return data, body

    async def me(req):
        data, _ = await auth(req)
        u = data["user"]
        row = ctx.db.user(u["id"])
        return web.json_response({"ok": True, "name": (row["name"] if row and row["name"] else
                                                      " ".join(filter(None, [u.get("first_name"), u.get("last_name")]))),
                                  "phone": row["phone"] if row and row["phone"] else ""})

    async def order(req):
        data, b = await auth(req)
        res = await place_order(ctx, data["user"], b)
        return web.json_response(res, status=200 if res.get("ok") else 400)

    async def health(req):
        return web.json_response({"ok": True})

    app.router.add_get("/", index)
    app.router.add_get("/api/menu", menu)
    app.router.add_post("/api/me", me)
    app.router.add_post("/api/order", order)
    app.router.add_get("/photo/{iid:\\d+}", photo)
    app.router.add_get("/health", health)
    app.router.add_static("/static", STATIC)
    return app
