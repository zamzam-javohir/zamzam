"""Storage for ZAM ZAM bot: Postgres when DATABASE_URL is set, SQLite otherwise."""
import os, re, sqlite3, threading, datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tashkent")
CATEGORIES = [
    ("lavash", "🌯 Lavashlar"), ("shaurma", "🥙 Shaurma"), ("burger", "🍔 Burgerlar"),
    ("hotdog", "🌭 Hot-dog"), ("kombo", "🍱 Donar / Kombo"), ("drinks", "🥤 Ichimliklar"),
    ("dessert", "🍰 Desertlar"),
]
CAT_NAME = dict(CATEGORIES)
STATUSES = {
    "new": "🆕 Yangi", "accepted": "✅ Qabul qilindi", "cooking": "👨‍🍳 Tayyorlanmoqda",
    "onway": "🚚 Yo'lda", "done": "✔️ Yetkazildi", "cancelled": "❌ Bekor qilindi",
}
DEFAULT_SETTINGS = {
    "open_time": "10:00", "close_time": "23:00",
    "delivery_fee": "10000", "free_delivery_from": "150000",
    "phone": "+998 90 000 00 00 (namuna)", "address": "Toshkent sh., ... ko'chasi (namuna)",
    "telegram": "@zamzam_admin (namuna)",
    "about": "🌯 ZAM ZAM — Toshkentdagi mazali va tezkor fast food! Yangi masalliqlar, katta porsiyalar va tez yetkazib berish.",
}
SAMPLE_MENU = [  # (cat, name, desc, price, photo)
    ("lavash", "Lavash klassik", "Mol go'shti, pomidor, bodring, chips va maxsus sous", 28000, "lavash.jpg"),
    ("lavash", "Lavash pishloqli", "Mol go'shti, ikki barobar pishloq, sabzavotlar va sous", 32000, "lavash.jpg"),
    ("lavash", "Tovuqli lavash", "Tovuq go'shti, karam, pomidor, chesnokli sous", 26000, "shaurma.jpg"),
    ("lavash", "Achchiq lavash 🌶", "Mol go'shti, xalapenyo, achchiq sous, pishloq", 30000, "lavash.jpg"),
    ("shaurma", "Shaurma klassik", "Tovuq go'shti, sabzavotlar, chesnokli sous, pita nonida", 25000, "shaurma.jpg"),
    ("shaurma", "Shaurma big", "Katta porsiya: ikki barobar go'sht va fri bilan", 33000, "shaurma.jpg"),
    ("burger", "Gamburger", "Mol go'shtli kotlet, pomidor, salat, sous", 25000, "burger1.jpg"),
    ("burger", "Chizburger", "Mol go'shtli kotlet, cheddar pishlog'i, tuzlangan bodring", 29000, "burger2.jpg"),
    ("burger", "Dabl burger", "Ikki kotlet, ikki pishloq, karamellangan piyoz", 39000, "burger3.jpg"),
    ("burger", "ZAM ZAM burger", "Firma burgeri: kotlet, bekon, tuxum, pishloq, BBQ sous", 45000, "burger4.jpg"),
    ("hotdog", "Hot-dog klassik", "Sosiska, ketchup, xantal, tuzlangan bodring", 15000, "hotdog.jpg"),
    ("hotdog", "Hot-dog big", "Katta sosiska, pishloq, qovurilgan piyoz", 20000, "hotdog.jpg"),
    ("kombo", "Donar porsiya", "Donar go'sht, fri, salat va sous bilan", 38000, "donar.jpg"),
    ("kombo", "Kombo: Lavash + fri + Cola", "Lavash klassik, kartoshka fri va Coca-Cola 0.5", 42000, "kombo.jpg"),
    ("kombo", "Kartoshka fri", "Qarsildoq kartoshka fri, ketchup bilan", 14000, "fri.jpg"),
    ("drinks", "Coca-Cola 0.5 L", "Muzdek Coca-Cola", 8000, "cola.jpg"),
    ("drinks", "Coca-Cola 1.5 L", "Butun oila uchun", 15000, "cola2.jpg"),
    ("drinks", "Apelsin sharbati", "Yangi siqilgan apelsin sharbati, 0.3 L", 18000, "juice.jpg"),
    ("drinks", "Choy", "Qora yoki ko'k choy, choynakda", 5000, "tea.jpg"),
    ("dessert", "Chizkeyk", "Qulupnayli chizkeyk bo'lagi", 22000, "cheesecake.jpg"),
    ("dessert", "Donut", "Shokoladli donut", 12000, "donut.jpg"),
    ("dessert", "Shokoladli tort bo'lagi", "Yumshoq shokoladli tort", 20000, "cake.jpg"),
    ("dessert", "Muzqaymoq", "Vanilli muzqaymoq vafli stakanchada", 10000, "icecream.jpg"),
]


def now():
    return dt.datetime.now(TZ)


PG_PK = {"settings": ["key"], "cart": ["user_id", "item_id"], "loc_wait": ["user_id"],
         "admins": ["user_id"], "users": ["id"], "items": ["id"]}
PG_COLS = {"settings": ["key", "value"], "cart": ["user_id", "item_id", "qty"], "loc_wait": ["user_id", "order_id"],
           "admins": ["user_id", "is_owner", "receives", "added"]}
SERIAL_TABLES = ("items", "orders")


def pg_sql(sql):
    """Translate the small SQLite dialect used by this app to Postgres."""
    s = sql.strip()
    s = re.sub(r"\bMIN\(", "LEAST(", s)
    s = s.replace("datetime('now')", "to_char(now(), 'YYYY-MM-DD\"T\"HH24:MI:SS')")
    m = re.match(r"INSERT\s+OR\s+(IGNORE|REPLACE)\s+INTO\s+(\w+)(\s*\(([^)]*)\))?", s, re.I)
    if m:
        kind, table = m.group(1).upper(), m.group(2)
        cols = [c.strip() for c in m.group(4).split(",")] if m.group(4) else PG_COLS[table]
        s = "INSERT INTO " + table + (f"({m.group(4)})" if m.group(4) else "") + s[m.end():]
        pk = PG_PK[table]
        if kind == "IGNORE":
            s += " ON CONFLICT DO NOTHING"
        else:
            upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in pk)
            s += f" ON CONFLICT({', '.join(pk)}) DO " + (f"UPDATE SET {upd}" if upd else "NOTHING")
    if re.match(r"INSERT\s+INTO\s+(%s)\b" % "|".join(SERIAL_TABLES), s, re.I) and "RETURNING" not in s.upper():
        s += " RETURNING id"
    return s.replace("?", "%s")


class _PGCursor:
    def __init__(self, cur, returning):
        self.cur = cur
        self.lastrowid = None
        if returning:
            r = cur.fetchone()
            self.lastrowid = r["id"] if r else None

    def fetchone(self):
        try:
            return self.cur.fetchone()
        except Exception:
            return None

    def fetchall(self):
        try:
            return self.cur.fetchall()
        except Exception:
            return []

    @property
    def rowcount(self):
        return self.cur.rowcount


class PGConn:
    """sqlite3.Connection-like wrapper over psycopg 3 (auto-reconnects; Neon drops idle connections)."""

    def __init__(self, url):
        self.url = url
        self._connect()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        self.pg = psycopg.connect(self.url, row_factory=dict_row, autocommit=False,
                                  keepalives=1, keepalives_idle=30, connect_timeout=15)

    def _ensure(self):
        if self.pg.closed:
            self._connect()

    def execute(self, sql, args=(), _retry=True):
        import psycopg
        q = pg_sql(sql)
        try:
            self._ensure()
            cur = self.pg.execute(q, tuple(args))
            return _PGCursor(cur, q.endswith("RETURNING id"))
        except psycopg.OperationalError:
            if not _retry or self.pg.info.transaction_status not in (0, 3, 4):  # idle/inerror/unknown only
                raise
            try:
                self.pg.close()
            except Exception:
                pass
            self._connect()
            return self.execute(sql, args, _retry=False)
        except Exception:
            self.pg.rollback()
            raise

    def executemany(self, sql, seq):
        for a in seq:
            self.execute(sql, a)

    def executescript(self, script):
        for st in script.split(";"):
            if st.strip():
                self.execute(st)

    def commit(self):
        self.pg.commit()

    def rollback(self):
        self.pg.rollback()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et:
            self.pg.rollback()
        else:
            self.pg.commit()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT, cat TEXT, name TEXT,
    descr TEXT, price INTEGER, photo_path TEXT, file_id TEXT, available INTEGER DEFAULT 1,
    deleted INTEGER DEFAULT 0, sample INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, full_name TEXT, username TEXT,
    name TEXT, phone TEXT, blocked INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY, is_owner INTEGER DEFAULT 0,
    receives INTEGER DEFAULT 1, added TEXT);
CREATE TABLE IF NOT EXISTS cart(user_id INTEGER, item_id INTEGER, qty INTEGER,
    PRIMARY KEY(user_id, item_id));
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
    name TEXT, phone TEXT, otype TEXT, address TEXT, lat REAL, lon REAL, payment TEXT,
    comment TEXT, subtotal INTEGER, delivery_fee INTEGER, total INTEGER, status TEXT,
    created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS order_items(order_id INTEGER, item_id INTEGER, name TEXT,
    price INTEGER, qty INTEGER);
CREATE TABLE IF NOT EXISTS admin_msgs(order_id INTEGER, chat_id INTEGER, message_id INTEGER);
CREATE TABLE IF NOT EXISTS loc_wait(user_id INTEGER PRIMARY KEY, order_id INTEGER);
"""


def pg_schema():
    s = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    s = s.replace("INTEGER", "BIGINT").replace("REAL", "DOUBLE PRECISION")  # Telegram ids exceed int32
    return s


class DB:
    def __init__(self, path=None, url=None):
        url = url if url is not None else os.environ.get("DATABASE_URL", "")
        self.pg = bool(url)
        self.lock = threading.RLock()
        if self.pg:
            self.c = PGConn(url)
        else:
            self.c = sqlite3.connect(path, check_same_thread=False)
            self.c.row_factory = sqlite3.Row
            self.c.execute("PRAGMA journal_mode=WAL")
            self.c.execute("PRAGMA foreign_keys=ON")
        self._init()

    def x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            self.c.commit()
            return cur

    def one(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchone()

    def all(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchall()

    def _init(self):
        with self.lock:
            if self.pg:
                self.c.executescript(pg_schema())
                self.c.commit()
                if not self.one("SELECT 1 FROM orders LIMIT 1") and not self.one("SELECT 1 FROM settings WHERE key='_seq_init'"):
                    self.c.execute("SELECT setval('orders_id_seq', 1000, true)")  # order numbers start from 1001
                    self.c.execute("INSERT INTO settings VALUES('_seq_init','1') ON CONFLICT DO NOTHING")
            else:
                self.c.executescript(SCHEMA)
                self.c.commit()
                if not self.one("SELECT 1 FROM sqlite_sequence WHERE name='orders'"):
                    # order numbers start from 1001
                    self.c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('orders', 1000)")
            owner = os.environ.get("OWNER_ID", "").strip()
            if owner.isdigit() and not self.one("SELECT 1 FROM admins"):
                self.c.execute("INSERT INTO admins VALUES(?,1,1,?)", (int(owner), now().isoformat()))
            for k, v in DEFAULT_SETTINGS.items():
                self.c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
            if not self.one("SELECT 1 FROM items"):
                for cat, n, d, p, ph in SAMPLE_MENU:
                    self.c.execute("INSERT INTO items(cat,name,descr,price,photo_path,sample) VALUES(?,?,?,?,?,1)",
                                   (cat, n, d, p, ph))
            self.c.commit()

    # settings
    def get(self, k):
        r = self.one("SELECT value FROM settings WHERE key=?", (k,))
        return r["value"] if r else DEFAULT_SETTINGS.get(k, "")

    def set(self, k, v):
        self.x("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))

    def geti(self, k):
        try:
            return int(self.get(k))
        except ValueError:
            return int(DEFAULT_SETTINGS[k])

    # users
    def touch_user(self, u):
        self.x("""INSERT INTO users(id, full_name, username, created) VALUES(?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username, blocked=0""",
               (u.id, u.full_name, u.username, now().isoformat()))

    def user(self, uid):
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    # admins
    def admins(self):
        return self.all("SELECT a.*, u.full_name, u.username FROM admins a LEFT JOIN users u ON u.id=a.user_id ORDER BY is_owner DESC, added")

    def is_admin(self, uid):
        return bool(self.one("SELECT 1 FROM admins WHERE user_id=?", (uid,)))

    def is_owner(self, uid):
        return bool(self.one("SELECT 1 FROM admins WHERE user_id=? AND is_owner=1", (uid,)))

    def claim_owner(self, uid):
        """Atomically make uid the owner if there are no admins yet."""
        with self.lock:
            if self.c.execute("SELECT 1 FROM admins").fetchone():
                return False
            self.c.execute("INSERT INTO admins VALUES(?,1,1,?)", (uid, now().isoformat()))
            self.c.commit()
            return True

    def add_admin(self, uid):
        self.x("INSERT OR IGNORE INTO admins VALUES(?,0,1,?)", (uid, now().isoformat()))

    def transfer_owner(self, old, new):
        with self.c:
            self.c.execute("INSERT OR IGNORE INTO admins VALUES(?,0,1,?)", (new, now().isoformat()))
            self.c.execute("UPDATE admins SET is_owner=0 WHERE user_id=?", (old,))
            self.c.execute("UPDATE admins SET is_owner=1 WHERE user_id=?", (new,))

    def recipients(self):
        return [r["user_id"] for r in self.all("SELECT user_id FROM admins WHERE receives=1")]

    # items
    def items(self, cat, include_unavail=True):
        q = "SELECT * FROM items WHERE cat=? AND deleted=0" + ("" if include_unavail else " AND available=1")
        return self.all(q + " ORDER BY id", (cat,))

    def item(self, iid):
        return self.one("SELECT * FROM items WHERE id=? AND deleted=0", (iid,))

    # cart
    def cart(self, uid):
        return self.all("""SELECT c.item_id, c.qty, i.name, i.price, i.available FROM cart c
                           JOIN items i ON i.id=c.item_id WHERE c.user_id=? AND i.deleted=0 ORDER BY i.cat, i.id""", (uid,))

    def cart_add(self, uid, iid, qty):
        self.x("""INSERT INTO cart VALUES(?,?,?) ON CONFLICT(user_id,item_id)
                  DO UPDATE SET qty=MIN(99, cart.qty+excluded.qty)""", (uid, iid, qty))

    def cart_change(self, uid, iid, delta):
        self.x("UPDATE cart SET qty=MIN(99, qty+?) WHERE user_id=? AND item_id=?", (delta, uid, iid))
        self.x("DELETE FROM cart WHERE qty<=0")

    def cart_clear(self, uid):
        self.x("DELETE FROM cart WHERE user_id=?", (uid,))

    def totals(self, uid, otype="delivery"):
        rows = [r for r in self.cart(uid) if r["available"]]
        sub = sum(r["price"] * r["qty"] for r in rows)
        fee = 0
        if otype == "delivery" and sub and sub < self.geti("free_delivery_from"):
            fee = self.geti("delivery_fee")
        return rows, sub, fee, sub + fee

    # orders
    def create_order(self, uid, d):
        rows, sub, fee, total = self.totals(uid, d["otype"])
        if not rows:
            return None
        ts = now().isoformat()
        with self.lock:
            cur = self.c.execute("""INSERT INTO orders(user_id,name,phone,otype,address,lat,lon,payment,comment,
                subtotal,delivery_fee,total,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?)""",
                (uid, d["name"], d["phone"], d["otype"], d.get("address"), d.get("lat"), d.get("lon"),
                 d["payment"], d.get("comment"), sub, fee, total, ts, ts))
            oid = cur.lastrowid
            self.c.executemany("INSERT INTO order_items VALUES(?,?,?,?,?)",
                               [(oid, r["item_id"], r["name"], r["price"], r["qty"]) for r in rows])
            self.c.execute("DELETE FROM cart WHERE user_id=?", (uid,))
            self.c.execute("UPDATE users SET name=?, phone=? WHERE id=?", (d["name"], d["phone"], uid))
            self.c.commit()
        return oid

    def order(self, oid):
        return self.one("SELECT * FROM orders WHERE id=?", (oid,))

    def order_items(self, oid):
        return self.all("SELECT * FROM order_items WHERE order_id=?", (oid,))

    def set_status(self, oid, st):
        self.x("UPDATE orders SET status=?, updated=? WHERE id=?", (st, now().isoformat(), oid))

    def stats(self, since_iso):
        r = self.one("""SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM orders
                        WHERE created>=? AND status!='cancelled'""", (since_iso,))
        c = self.one("SELECT COUNT(*) n FROM orders WHERE created>=? AND status='cancelled'", (since_iso,))
        return r["n"], r["s"], c["n"]

    def top_items(self, since_iso, n=5):
        return self.all("""SELECT oi.name, SUM(oi.qty) q, SUM(oi.qty*oi.price) s FROM order_items oi
                           JOIN orders o ON o.id=oi.order_id WHERE o.created>=? AND o.status!='cancelled'
                           GROUP BY oi.name ORDER BY q DESC LIMIT ?""", (since_iso, n))
