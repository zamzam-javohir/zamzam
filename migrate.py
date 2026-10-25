"""Copy all data from a local SQLite file into the Postgres database at DATABASE_URL.
Usage:  DATABASE_URL=postgresql://... python migrate.py [path/to/zamzam.db]
Safe to re-run: existing rows with the same primary key are overwritten; log tables are replaced."""
import os, sqlite3, sys
import db as dbm

src = sys.argv[1] if len(sys.argv) > 1 else "zamzam.db"
url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("Set DATABASE_URL first")
s = sqlite3.connect(src); s.row_factory = sqlite3.Row
d = dbm.DB(url=url)          # creates schema
c = d.c
TABLES = ["settings", "users", "admins", "items", "orders", "order_items", "cart", "admin_msgs", "loc_wait"]
have = {r[0] for r in s.execute("SELECT name FROM sqlite_master WHERE type='table'")}
c.execute("DELETE FROM admins")   # replace the auto-seeded owner with the real admin list
for t in ("order_items", "admin_msgs", "cart", "loc_wait", "orders", "items"):
    c.execute(f"DELETE FROM {t}")
for t in TABLES:
    if t not in have:
        continue
    rows = s.execute(f"SELECT * FROM {t}").fetchall()
    if not rows:
        print(t, 0); continue
    cols = rows[0].keys()
    pk = dbm.PG_PK.get(t)
    sql = f"INSERT INTO {t}({', '.join(cols)}) VALUES({', '.join(['%s'] * len(cols))})"
    if pk:
        upd = ", ".join(f"{x}=EXCLUDED.{x}" for x in cols if x not in pk)
        sql += f" ON CONFLICT({', '.join(pk)}) DO " + (f"UPDATE SET {upd}" if upd else "NOTHING")
    for r in rows:
        c.pg.execute(sql, tuple(r))
    print(t, len(rows))
for t in dbm.SERIAL_TABLES:
    c.pg.execute(f"SELECT setval('{t}_id_seq', GREATEST((SELECT COALESCE(MAX(id),0) FROM {t}), %s), true)",
                 (1000 if t == "orders" else 1,))
c.commit()
print("admins:", [dict(r) for r in c.pg.execute("SELECT user_id, is_owner FROM admins").fetchall()])
print("Migration done.")
