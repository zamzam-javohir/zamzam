"""Copy all data from a local SQLite file into the Postgres database at DATABASE_URL.
Usage:  DATABASE_URL=postgresql://... python migrate.py [path/to/zamzam.db]
Safe to re-run. (The app also auto-seeds an empty Postgres from seed/seed.db on startup.)"""
import os, sys
import db as dbm

src = sys.argv[1] if len(sys.argv) > 1 else "zamzam.db"
url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("Set DATABASE_URL first")
d = dbm.DB(url=url)
dbm.import_sqlite(d.c, src, log=print)
print("admins:", [dict(r) for r in d.c.pg.execute("SELECT user_id, is_owner FROM admins").fetchall()])
print("Migration done.")
