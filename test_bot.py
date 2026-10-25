"""Simulated end-to-end tests: feed fake updates through the dispatcher with a mocked Telegram API."""
import asyncio, os, tempfile, datetime as dt, itertools
from unittest import mock
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import (SendMessage, SendPhoto, SendLocation, EditMessageText, EditMessageReplyMarkup,
                             AnswerCallbackQuery, DeleteMessage, CopyMessage, EditMessageCaption)
from aiogram.types import Update, Message, Chat, User, PhotoSize, MessageId
import bot as B

ids = itertools.count(100)
SENT = []


class FakeSession(BaseSession):
    async def make_request(self, bot, method, timeout=None):
        SENT.append(method)
        chat = Chat(id=getattr(method, "chat_id", 1) or 1, type="private")
        mk = lambda **kw: Message(message_id=next(ids), date=dt.datetime.now(), chat=chat, **kw)
        if isinstance(method, SendPhoto):
            return mk(photo=[PhotoSize(file_id="FILEID123", file_unique_id="u", width=1, height=1)], caption=method.caption)
        if isinstance(method, (SendMessage, SendLocation)):
            return mk(text=getattr(method, "text", ""))
        if isinstance(method, (EditMessageText, EditMessageReplyMarkup, EditMessageCaption)):
            return mk(text="x")
        if isinstance(method, CopyMessage):
            return MessageId(message_id=next(ids))
        return True

    async def close(self): pass
    async def stream_content(self, *a, **k):
        yield b""


OWNER = User(id=111, is_bot=False, first_name="Javohir")
CUST = User(id=222, is_bot=False, first_name="Ali", username="ali")
uid = itertools.count(1)


def msg(u, text=None, **kw):
    return Update(update_id=next(uid), message=Message(message_id=next(ids), date=dt.datetime.now(),
                  chat=Chat(id=u.id, type="private"), from_user=u, text=text, **kw))


def cb(u, data, photo=False):
    m = Message(message_id=next(ids), date=dt.datetime.now(), chat=Chat(id=u.id, type="private"),
                from_user=User(id=999, is_bot=True, first_name="bot"),
                **({"photo": [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]} if photo else {"text": "x"}))
    return Update(update_id=next(uid), callback_query={"id": str(next(ids)), "from": u, "chat_instance": "c", "data": data, "message": m})


def texts(since):
    out = []
    for m in SENT[since:]:
        t = getattr(m, "text", None) or getattr(m, "caption", None)
        if t: out.append((type(m).__name__, getattr(m, "chat_id", None), t))
    return out


def alerts(since):
    return [m.text for m in SENT[since:] if isinstance(m, AnswerCallbackQuery) and m.text]


async def run():
    tmp = tempfile.mkdtemp()
    dp = B.setup(os.path.join(tmp, "t.db"))
    bot = Bot("123:ABC", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
    db = B.db
    feed = lambda u: dp.feed_update(bot, u)
    open_patch = mock.patch.object(B, "is_open", return_value=True); open_patch.start()

    assert B.money(28000) == "28 000 so'm" and B.money(150000) == "150 000 so'm"
    assert B.norm_phone("901234567") == "+998 90 123 45 67" and B.norm_phone("+998 90 123-45-67") == "+998 90 123 45 67"
    assert B.norm_phone("12") is None and B.parse_price("28 000") == 28000 and B.parse_price("abc") is None

    n = len(SENT); await feed(msg(OWNER, "/start"))
    assert db.is_owner(111) and any("egasi" in t for *_, t in texts(n)), texts(n)
    n = len(SENT); await feed(msg(CUST, "/start"))
    assert not db.is_admin(222) and not any("egasi" in t for *_, t in texts(n))

    await feed(msg(CUST, B.B_MENU))
    await feed(cb(CUST, "cat:lavash"))
    lav = db.items("lavash")[0]
    n = len(SENT); await feed(cb(CUST, f"it:{lav['id']}:1"))
    assert any(isinstance(m, SendPhoto) for m in SENT[n:]) and db.item(lav["id"])["file_id"] == "FILEID123"
    await feed(cb(CUST, f"it:{lav['id']}:3", photo=True))
    n = len(SENT); await feed(cb(CUST, f"add:{lav['id']}:3", photo=True))
    assert db.cart(222)[0]["qty"] == 3, "add to cart"
    rows, sub, fee, tot = db.totals(222)
    assert sub == 84000 and fee == 10000 and tot == 94000
    await feed(cb(CUST, f"cq:{lav['id']}:1"))
    assert db.cart(222)[0]["qty"] == 4
    burger = db.items("burger")[-1]
    await feed(cb(CUST, f"add:{burger['id']}:2", photo=True))
    rows, sub, fee, tot = db.totals(222)
    assert sub == 4*28000 + 2*45000 and fee == 0, (sub, fee)
    n = len(SENT); await feed(msg(CUST, B.B_CART))
    assert "Jami: 202 000 so'm" in texts(n)[0][2], texts(n)

    # checkout with validation errors
    await feed(cb(CUST, "checkout"))
    n = len(SENT); await feed(msg(CUST, "A")); assert "to'g'ri" in texts(n)[0][2]
    await feed(msg(CUST, "Ali Valiyev"))
    n = len(SENT); await feed(msg(CUST, "12345")); assert "noto'g'ri" in texts(n)[0][2]
    await feed(msg(CUST, "", contact={"phone_number": "998901234567", "first_name": "Ali", "user_id": 222}))
    await feed(msg(CUST, B.B_DELIVERY))
    await feed(msg(CUST, None, location={"latitude": 41.31, "longitude": 69.28}))
    await feed(msg(CUST, B.B_CARD))
    n = len(SENT); await feed(msg(CUST, "Piyozsiz iltimos"))
    s = texts(n)[-1][2]; assert "Jami: 202 000" in s and "+998 90 123 45 67" in s and "karta" in s, s
    n = len(SENT); await feed(cb(CUST, "oconfirm"))
    o = db.order(1001); assert o and o["status"] == "new" and o["lat"] == 41.31 and not db.cart(222)
    adm = [t for t in texts(n) if t[1] == 111]
    assert adm and "Buyurtma #1001" in adm[0][2] and any(isinstance(m, SendLocation) for m in SENT[n:])
    assert any("#1001" in t and c == 222 for _, c, t in texts(n))
    # stale confirm can't duplicate
    await feed(cb(CUST, "oconfirm")); assert db.one("SELECT COUNT(*) n FROM orders")["n"] == 1

    # status flow
    n = len(SENT); await feed(cb(CUST, "st:1001:accepted")); assert db.order(1001)["status"] == "new"
    for st in ("accepted", "cooking", "onway", "done"):
        n = len(SENT); await feed(cb(OWNER, f"st:1001:{st}"))
        assert db.order(1001)["status"] == st
        assert any(c == 222 for _, c, _t in texts(n)), st
        assert any(isinstance(m, EditMessageText) for m in SENT[n:])
    n = len(SENT); await feed(cb(OWNER, "st:1001:cancelled")); assert db.order(1001)["status"] == "done"

    n = len(SENT); await feed(msg(CUST, B.B_ORDERS)); assert "#1001" in texts(n)[0][2]
    await feed(cb(CUST, "rep:1001")); assert len(db.cart(222)) == 2

    # pickup order + main-button aborts dialog
    await feed(cb(CUST, "checkout")); await feed(msg(CUST, B.B_MENU))
    st = await dp.fsm.get_context(bot, 222, 222).get_state(); assert st is None, st
    await feed(cb(CUST, "checkout")); await feed(msg(CUST, "Ali")); await feed(msg(CUST, "+998 93 111 22 33"))
    await feed(msg(CUST, B.B_PICKUP)); await feed(msg(CUST, B.B_CASH)); await feed(msg(CUST, B.B_SKIP))
    await feed(cb(CUST, "oconfirm"))
    o = db.order(1002); assert o["otype"] == "pickup" and o["delivery_fee"] == 0 and o["payment"] == "naqd"

    # closed hours
    open_patch.stop()
    with mock.patch.object(B, "is_open", return_value=False):
        await feed(cb(CUST, f"add:{lav['id']}:1", photo=True))
        n = len(SENT); await feed(cb(CUST, "checkout")); assert any("yopiqmiz" in a for a in alerts(n))
        n = len(SENT); await feed(msg(CUST, B.B_MENU)); assert "yopiqmiz" in texts(n)[0][2]
    open_patch.start()

    # admin: non-admin blocked
    n = len(SENT); await feed(msg(CUST, "/admin")); assert "faqat adminlar" in texts(n)[0][2]
    await feed(msg(OWNER, "/admin"))
    # add item
    await feed(cb(OWNER, "ad:add")); await feed(msg(OWNER, B.B_SKIP)); await feed(msg(OWNER, "Test lavash"))
    await feed(msg(OWNER, "Tavsif")); n = len(SENT); await feed(msg(OWNER, "abc")); assert "noto'g'ri" in texts(n)[0][2]
    await feed(msg(OWNER, "31 000")); await feed(cb(OWNER, "adcat:lavash"))
    new = db.items("lavash")[-1]; assert new["name"] == "Test lavash" and new["price"] == 31000
    # price edit
    await feed(cb(OWNER, f"aact:price:{new['id']}")); await feed(msg(OWNER, "33000"))
    assert db.item(new["id"])["price"] == 33000
    # toggle
    await feed(cb(OWNER, f"aact:toggle:{new['id']}")); assert db.item(new["id"])["available"] == 0
    n = len(SENT); await feed(cb(CUST, f"add:{new['id']}:1", photo=True)); assert any("mavjud emas" in a for a in alerts(n))
    # delete
    await feed(cb(OWNER, f"adel:{new['id']}")); assert db.item(new["id"]) is None
    # admins
    await feed(cb(OWNER, "aadd")); await feed(msg(OWNER, "333")); assert db.is_admin(333)
    await feed(cb(OWNER, "aadd")); await feed(msg(OWNER, None, contact={"phone_number": "+1", "first_name": "x", "user_id": 444}))
    assert db.is_admin(444)
    await feed(cb(OWNER, "arm:444")); assert not db.is_admin(444)
    await feed(cb(OWNER, "arm:111")); assert db.is_owner(111)
    n = len(SENT); await feed(cb(User(id=333, is_bot=False, first_name="A"), "ad:admins")); assert any("egasi" in a for a in alerts(n))
    await feed(cb(OWNER, "arcv:333")); assert 333 not in db.recipients()
    await feed(cb(OWNER, "arcv:111")); assert 111 in db.recipients(), "cannot remove last recipient"
    # settings
    await feed(cb(OWNER, "aset:hours")); n = len(SENT); await feed(msg(OWNER, "25:00-99")); assert "Format" in texts(n)[0][2]
    await feed(msg(OWNER, "09:30-02:00")); assert db.get("open_time") == "09:30" and db.get("close_time") == "02:00"
    await feed(cb(OWNER, "aset:phone")); await feed(msg(OWNER, "+998 71 200 00 00")); assert db.get("phone") == "+998 71 200 00 00"
    await feed(cb(OWNER, "aset:delivery_fee")); await feed(msg(OWNER, "12 000")); assert db.geti("delivery_fee") == 12000
    n = len(SENT); await feed(msg(CUST, B.B_CONTACT)); assert "+998 71 200 00 00" in texts(n)[0][2]
    # broadcast
    await feed(cb(OWNER, "ad:bc")); await feed(msg(OWNER, "Aksiya! -20%"))
    n = len(SENT); await feed(cb(OWNER, "bcgo"))
    assert sum(isinstance(m, CopyMessage) for m in SENT[n:]) >= 2
    # stats
    n = len(SENT); await feed(msg(OWNER, "/stat")); s = texts(n)[0][2]
    assert "Bugun:</b> 2 ta" in s and "ZAM ZAM burger" in s, s
    # overnight hours logic
    open_patch.stop()
    db.set("open_time", "10:00"); db.set("close_time", "23:00")
    for h, exp in ((9, False), (10, True), (22, True), (23, False)):
        with mock.patch.object(B, "now", return_value=dt.datetime(2026, 1, 1, h, 30, tzinfo=B.db.__class__ and dt.timezone.utc)):
            assert B.is_open() == exp, (h, exp)
    db.set("open_time", "18:00"); db.set("close_time", "03:00")
    for h, exp in ((17, False), (20, True), (2, True), (4, False)):
        with mock.patch.object(B, "now", return_value=dt.datetime(2026, 1, 1, h, 0)):
            assert B.is_open() == exp, (h, exp)
    # fallback & unknown callback
    n = len(SENT); await feed(msg(CUST, "salom")); assert "Tushunmadim" in texts(n)[0][2]
    await feed(cb(CUST, "garbage:1"))
    assert not any("Xatolik" in t for *_, t in texts(0)) and not alerts(0).count("⚠️ Xatolik yuz berdi, qaytadan urinib ko'ring."), "error handler fired"
    print(f"ALL TESTS PASSED ({len(SENT)} API calls simulated)")


if __name__ == "__main__":
    asyncio.run(run())
