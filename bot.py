"""ZAM ZAM fast food — Telegram ordering bot (aiogram 3)."""
import webserver
import asyncio, html, logging, os, re, time, datetime as dt
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
                           InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile, BotCommand, ErrorEvent,
                           WebAppInfo, MenuButtonWebApp, MenuButtonCommands)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import DB, CATEGORIES, CAT_NAME, STATUSES, now

BASE = os.path.dirname(os.path.abspath(__file__))
if not os.environ.get("PUBLIC_URL") and os.environ.get("RENDER_EXTERNAL_URL"):
    os.environ["PUBLIC_URL"] = os.environ["RENDER_EXTERNAL_URL"]  # Render provides its https URL automatically
PHOTOS = os.path.join(BASE, "photos")
log = logging.getLogger("zamzam")
db: DB = None  # set in setup()
router = Router()
e = html.escape

# ---------- texts / keyboards ----------
B_MENU, B_CART, B_ORDERS, B_CONTACT, B_ABOUT = "🍽 Menyu", "🛒 Savat", "📦 Buyurtmalarim", "📞 Aloqa", "ℹ️ Biz haqimizda"
B_CANCEL, B_BACK = "❌ Bekor qilish", "⬅️ Orqaga"
B_DELIVERY, B_PICKUP = "🚚 Yetkazib berish", "🏃 Olib ketish"
B_CASH, B_CARD = "💵 Naqd", "💳 Karta"
B_SKIP = "⏭ O'tkazib yuborish"
MAIN_BUTTONS = {B_MENU, B_CART, B_ORDERS, B_CONTACT, B_ABOUT, "📋 Oddiy menyu"}


WEB = {"url": None}


def money(v):
    return f"{int(v):,}".replace(",", " ") + " so'm"


def kb(rows, placeholder=None):
    return ReplyKeyboardMarkup(keyboard=[[b if isinstance(b, KeyboardButton) else KeyboardButton(text=b) for b in r]
                                         for r in rows], resize_keyboard=True, input_field_placeholder=placeholder)


B_SIMPLE = "📋 Oddiy menyu"


def main_kb(uid=None):
    first = KeyboardButton(text=B_MENU, web_app=WebAppInfo(url=WEB["url"] + "/")) if WEB.get("url") else B_MENU
    rows = [[first], [B_CART, B_ORDERS], [B_CONTACT, B_ABOUT, B_SIMPLE]]
    if uid and db.is_admin(uid):
        rows.append(["⚙️ Admin panel"])
    return kb(rows, "Bo'limni tanlang 👇")


def parse_hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def is_open():
    try:
        o, c = parse_hm(db.get("open_time")), parse_hm(db.get("close_time"))
    except Exception:
        return True
    t = now().time()
    return (o <= t < c) if o < c else (t >= o or t < c)


def hours():
    return f"{db.get('open_time')} – {db.get('close_time')}"


def closed_text():
    return (f"😴 <b>Hozir yopiqmiz.</b>\nIsh vaqtimiz: <b>{hours()}</b> (Toshkent vaqti).\n"
            "Menyuni ko'rib, savatni tayyorlab qo'yishingiz mumkin — ochilganimizda buyurtma bering! 🌯")


class S(StatesGroup):
    name = State(); phone = State(); otype = State(); address = State(); payment = State(); comment = State(); confirm = State()


class A(StatesGroup):
    add_photo = State(); add_name = State(); add_desc = State(); add_price = State(); add_cat = State()
    price = State(); admin_add = State(); owner_xfer = State(); broadcast = State(); broadcast_ok = State(); setting = State()


# ---------- middleware ----------
class UserMW(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if u and not u.is_bot:
            try:
                db.touch_user(u)
            except Exception:
                log.exception("touch_user")
        msg = getattr(event, "message", None)
        st = data.get("state")
        if msg is not None and msg.text in MAIN_BUTTONS and st is not None and await st.get_state():
            await st.clear()  # main-menu button aborts any dialog
        return await handler(event, data)


# ---------- start / info ----------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    extra = ""
    if db.claim_owner(m.from_user.id):
        log.info("Owner claimed by %s", m.from_user.id)
        extra = ("\n\n👑 <b>Siz botning egasi (bosh admin) bo'ldingiz!</b>\n"
                 "Buyurtmalar sizga keladi. Boshqaruv: /admin")
    hello = (f"Assalomu alaykum, <b>{e(m.from_user.first_name or 'mehmon')}</b>! 👋\n\n"
             "🌯 <b>ZAM ZAM</b>'ga xush kelibsiz — eng mazali lavash, shaurma va burgerlar shu yerda!\n"
             f"🕙 Ish vaqti: <b>{hours()}</b>\n\nQuyidagi tugmalardan birini tanlang 👇")
    if not is_open():
        hello += "\n\n" + closed_text()
    if WEB["url"]:
        await m.answer(hello + extra, reply_markup=main_kb(m.from_user.id))
        await m.answer("📱 <b>ZAM ZAM ilovasi</b> — rasmli menyu, savat va buyurtma bir joyda.\n"
                       "Pastdagi tugmani bosing 👇", reply_markup=webapp_kb())
    else:
        await m.answer(hello + extra, reply_markup=main_kb(m.from_user.id))


URL_FILE = os.path.join(BASE, "tunnel_url.txt")


def webapp_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🍽 Menyuni ochish", web_app=WebAppInfo(url=WEB["url"] + "/?v=" + str(int(time.time()) // 3600)))]])


@router.message(Command("app"))
async def app_cmd(m: Message):
    if not WEB["url"]:
        return await m.answer("Ilova hozir vaqtincha ishlamayapti. 🍽 Menyu tugmasidan foydalaning.")
    await m.answer("📱 ZAM ZAM ilovasini oching 👇", reply_markup=webapp_kb())


@router.message(Command("help"))
async def help_cmd(m: Message):
    t = ("ℹ️ <b>Yordam</b>\n🍽 Menyu — taomlarni ko'rish va savatga qo'shish\n🛒 Savat — buyurtmani rasmiylashtirish\n"
         "📦 Buyurtmalarim — buyurtmalar tarixi va holati\n/cancel — joriy amalni bekor qilish")
    if db.is_admin(m.from_user.id):
        t += "\n\n⚙️ Admin: /admin, /stat"
    await m.answer(t, reply_markup=main_kb(m.from_user.id))


@router.message(Command("cancel"))
@router.message(F.text == B_CANCEL)
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi. Asosiy menyu 👇", reply_markup=main_kb(m.from_user.id))


@router.message(F.text == B_CONTACT)
async def contact(m: Message):
    await m.answer(f"📞 <b>Aloqa</b>\n\n☎️ Telefon: {e(db.get('phone'))}\n📍 Manzil: {e(db.get('address'))}\n"
                   f"✈️ Telegram: {e(db.get('telegram'))}\n🕙 Ish vaqti: {hours()}")


@router.message(F.text == B_ABOUT)
async def about(m: Message):
    await m.answer(f"ℹ️ <b>Biz haqimizda</b>\n\n{e(db.get('about'))}\n\n🚚 Yetkazib berish: {money(db.geti('delivery_fee'))}, "
                   f"<b>{money(db.geti('free_delivery_from'))}</b> dan ortiq buyurtmalarga — <b>bepul!</b>\n🕙 Ish vaqti: {hours()}")


# ---------- menu ----------
def cats_kb():
    b = InlineKeyboardBuilder()
    for key, name in CATEGORIES:
        b.button(text=name, callback_data=f"cat:{key}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="🛒 Savatni ko'rish", callback_data="cart"))
    return b.as_markup()


def items_kb(cat):
    b = InlineKeyboardBuilder()
    for it in db.items(cat):
        mark = "" if it["available"] else "🚫 "
        b.button(text=f"{mark}{it['name']} — {money(it['price'])}", callback_data=f"it:{it['id']}:1")
    b.adjust(1)
    b.row(InlineKeyboardButton(text=B_BACK, callback_data="cats"),
          InlineKeyboardButton(text="🛒 Savat", callback_data="cart"))
    return b.as_markup()


def item_caption(it):
    t = f"<b>{e(it['name'])}</b>\n\n{e(it['descr'] or '')}\n\n💰 Narxi: <b>{money(it['price'])}</b>"
    if not it["available"]:
        t += "\n\n🚫 <i>Hozircha mavjud emas</i>"
    return t


def item_kb(it, qty):
    b = InlineKeyboardBuilder()
    if it["available"]:
        b.row(InlineKeyboardButton(text="➖", callback_data=f"it:{it['id']}:{max(1, qty-1)}"),
              InlineKeyboardButton(text=f"{qty} ta", callback_data="noop"),
              InlineKeyboardButton(text="➕", callback_data=f"it:{it['id']}:{min(99, qty+1)}"))
        b.row(InlineKeyboardButton(text=f"🛒 Savatga qo'shish · {money(it['price']*qty)}", callback_data=f"add:{it['id']}:{qty}"))
    b.row(InlineKeyboardButton(text=B_BACK, callback_data=f"cat:{it['cat']}"),
          InlineKeyboardButton(text="🛒 Savat", callback_data="cart"))
    return b.as_markup()


async def send_item_photo(bot: Bot, chat_id, it, caption, markup):
    if it["file_id"]:
        try:
            return await bot.send_photo(chat_id, it["file_id"], caption=caption, reply_markup=markup)
        except TelegramBadRequest:
            log.warning("bad file_id for item %s, re-uploading", it["id"])
    path = os.path.join(PHOTOS, it["photo_path"] or "")
    if it["photo_path"] and os.path.isfile(path):
        msg = await bot.send_photo(chat_id, FSInputFile(path), caption=caption, reply_markup=markup)
        db.x("UPDATE items SET file_id=? WHERE id=?", (msg.photo[-1].file_id, it["id"]))
        return msg
    return await bot.send_message(chat_id, caption, reply_markup=markup)


async def replace_with_text(c: CallbackQuery, text, markup):
    """Edit the message to text; if it is a photo, delete and send new."""
    if c.message.photo:
        try:
            await c.message.delete()
        except TelegramBadRequest:
            pass
        await c.message.answer(text, reply_markup=markup)
    else:
        try:
            await c.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as ex:
            if "not modified" not in str(ex):
                await c.message.answer(text, reply_markup=markup)


@router.message(Command("menu"))
async def menu_cmd(m: Message):
    if WEB["url"]:
        return await m.answer("🍽 ZAM ZAM menyusini oching 👇", reply_markup=webapp_kb())
    await menu(m)


@router.message(F.web_app_data)
async def on_web_app_data(m: Message):
    import json as _j
    try:
        b = _j.loads(m.web_app_data.data)
    except Exception:
        return await m.answer("Ma'lumot noto'g'ri keldi, qayta urinib ko'ring.")
    u = {"id": m.from_user.id, "first_name": m.from_user.first_name, "last_name": m.from_user.last_name,
         "username": m.from_user.username}
    ctx = type("C", (), {})()
    ctx.db, ctx.is_open, ctx.hours = db, is_open, hours
    ctx.on_webapp_order = lambda oid, want: on_webapp_order(m.bot, oid, want)
    res = await webserver.place_order(ctx, u, b)
    if not res.get("ok"):
        await m.answer("❌ " + str(res.get("error")), reply_markup=main_kb(m.from_user.id))


@router.message(F.text.in_({B_MENU, B_SIMPLE}))
async def menu(m: Message):
    t = "🍽 <b>Menyu</b>\nBo'limni tanlang 👇"
    if not is_open():
        t = closed_text() + "\n\n" + t
    await m.answer(t, reply_markup=cats_kb())


@router.callback_query(F.data == "cats")
async def cb_cats(c: CallbackQuery):
    await replace_with_text(c, "🍽 <b>Menyu</b>\nBo'limni tanlang 👇", cats_kb())
    await c.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_cat(c: CallbackQuery):
    cat = c.data.split(":", 1)[1]
    if cat not in CAT_NAME:
        return await c.answer("Bo'lim topilmadi", show_alert=True)
    text = f"<b>{CAT_NAME[cat]}</b>\nTaomni tanlang 👇" if db.items(cat) else f"<b>{CAT_NAME[cat]}</b>\n\nBu bo'limda hozircha taom yo'q 🙁"
    await replace_with_text(c, text, items_kb(cat))
    await c.answer()


@router.callback_query(F.data.startswith("it:"))
async def cb_item(c: CallbackQuery):
    _, iid, qty = c.data.split(":")
    it = db.item(int(iid))
    if not it:
        return await c.answer("Bu taom endi mavjud emas", show_alert=True)
    qty = max(1, min(99, int(qty)))
    if c.message.photo:  # quantity change on the same card
        try:
            await c.message.edit_reply_markup(reply_markup=item_kb(it, qty))
        except TelegramBadRequest:
            pass
    else:
        try:
            await c.message.delete()
        except TelegramBadRequest:
            pass
        await send_item_photo(c.bot, c.message.chat.id, it, item_caption(it), item_kb(it, qty))
    await c.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data.startswith("add:"))
async def cb_add(c: CallbackQuery):
    _, iid, qty = c.data.split(":")
    it = db.item(int(iid))
    if not it or not it["available"]:
        return await c.answer("🚫 Bu taom hozircha mavjud emas", show_alert=True)
    db.cart_add(c.from_user.id, it["id"], max(1, min(99, int(qty))))
    _, sub, _, _ = db.totals(c.from_user.id)
    await c.answer(f"✅ {it['name']} × {qty} savatga qo'shildi!\nSavatda: {money(sub)}")
    try:
        await c.message.edit_reply_markup(reply_markup=item_kb(it, 1))
    except TelegramBadRequest:
        pass


# ---------- cart ----------
def cart_view(uid):
    rows = db.cart(uid)
    if not rows:
        b = InlineKeyboardBuilder()
        b.button(text="🍽 Menyuga o'tish", callback_data="cats")
        return "🛒 Savatingiz bo'sh.\nMenyudan mazali taomlarni tanlang! 🌯", b.as_markup()
    _, sub, fee, total = db.totals(uid)
    lines = ["🛒 <b>Savatingiz</b>\n"]
    b = InlineKeyboardBuilder()
    for i, r in enumerate(rows, 1):
        if r["available"]:
            lines.append(f"{i}. {e(r['name'])}\n    {r['qty']} × {money(r['price'])} = <b>{money(r['qty']*r['price'])}</b>")
        else:
            lines.append(f"{i}. <s>{e(r['name'])}</s> — 🚫 mavjud emas, hisobga olinmaydi")
        b.row(InlineKeyboardButton(text="➖", callback_data=f"cq:{r['item_id']}:-1"),
              InlineKeyboardButton(text=f"{r['name'][:22]} · {r['qty']}", callback_data="noop"),
              InlineKeyboardButton(text="➕", callback_data=f"cq:{r['item_id']}:1"),
              InlineKeyboardButton(text="🗑", callback_data=f"cq:{r['item_id']}:-99"))
    free_from = db.geti("free_delivery_from")
    lines.append(f"\n🧾 Taomlar: <b>{money(sub)}</b>")
    if fee:
        lines.append(f"🚚 Yetkazib berish: {money(fee)}\n💡 Yana <b>{money(free_from - sub)}</b> qo'shsangiz — yetkazib berish bepul!")
    elif sub:
        lines.append("🚚 Yetkazib berish: <b>bepul</b> 🎉")
    lines.append(f"💰 <b>Jami: {money(total)}</b>\n<i>(olib ketishda yetkazib berish haqi olinmaydi)</i>")
    b.row(InlineKeyboardButton(text="🧹 Tozalash", callback_data="cclear"),
          InlineKeyboardButton(text="🍽 Yana qo'shish", callback_data="cats"))
    b.row(InlineKeyboardButton(text="✅ Buyurtma berish", callback_data="checkout"))
    return "\n".join(lines), b.as_markup()


@router.message(F.text == B_CART)
@router.message(Command("cart"))
async def cart(m: Message):
    t, k = cart_view(m.from_user.id)
    await m.answer(t, reply_markup=k)


@router.callback_query(F.data == "cart")
async def cb_cart(c: CallbackQuery):
    t, k = cart_view(c.from_user.id)
    await replace_with_text(c, t, k)
    await c.answer()


@router.callback_query(F.data.startswith("cq:"))
async def cb_cq(c: CallbackQuery):
    _, iid, d = c.data.split(":")
    db.cart_change(c.from_user.id, int(iid), int(d))
    t, k = cart_view(c.from_user.id)
    await replace_with_text(c, t, k)
    await c.answer()


@router.callback_query(F.data == "cclear")
async def cb_clear(c: CallbackQuery):
    db.cart_clear(c.from_user.id)
    t, k = cart_view(c.from_user.id)
    await replace_with_text(c, t, k)
    await c.answer("🧹 Savat tozalandi")


# ---------- checkout ----------
@router.callback_query(F.data == "checkout")
async def cb_checkout(c: CallbackQuery, state: FSMContext):
    if not is_open():
        return await c.answer(f"😴 Hozir yopiqmiz. Ish vaqti: {hours()}", show_alert=True)
    rows, sub, _, _ = db.totals(c.from_user.id)
    if not rows:
        return await c.answer("Savatda mavjud taom yo'q", show_alert=True)
    await c.answer()
    await state.clear()
    await state.set_state(S.name)
    u = db.user(c.from_user.id)
    saved = (u["name"] if u and u["name"] else None) or c.from_user.first_name
    await c.message.answer("📝 Buyurtmani rasmiylashtiramiz.\n\n👤 Ismingizni kiriting:",
                           reply_markup=kb([[saved[:60]], [B_CANCEL]] if saved else [[B_CANCEL]], "Ismingiz"))


@router.message(S.name, F.text)
async def st_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if not (2 <= len(name) <= 60) or name.startswith("/"):
        return await m.answer("⚠️ Iltimos, ismingizni to'g'ri kiriting (2–60 belgi).")
    await state.update_data(name=name)
    await state.set_state(S.phone)
    u = db.user(m.from_user.id)
    rows = [[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]]
    if u and u["phone"]:
        rows.append([u["phone"]])
    rows.append([B_CANCEL])
    await m.answer("📱 Telefon raqamingizni yuboring (tugmani bosing) yoki +998 XX XXX XX XX ko'rinishida yozing:",
                   reply_markup=kb(rows, "+998 90 123 45 67"))


def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    if len(d) == 9:
        d = "998" + d
    if len(d) == 12 and d.startswith("998"):
        return f"+998 {d[3:5]} {d[5:8]} {d[8:10]} {d[10:12]}"
    if 10 <= len(d) <= 15 and (s or "").strip().startswith("+"):
        return "+" + d
    return None


@router.message(S.phone, F.contact | F.text)
async def st_phone(m: Message, state: FSMContext):
    if m.contact:
        if m.contact.user_id and m.contact.user_id != m.from_user.id:
            return await m.answer("⚠️ Iltimos, o'zingizning raqamingizni yuboring.")
        phone = norm_phone(m.contact.phone_number if m.contact.phone_number.startswith("+") else "+" + m.contact.phone_number)
    else:
        phone = norm_phone(m.text)
    if not phone:
        return await m.answer("⚠️ Raqam noto'g'ri. Masalan: +998 90 123 45 67")
    await state.update_data(phone=phone)
    await state.set_state(S.otype)
    _, sub, _, _ = db.totals(m.from_user.id)
    fee = 0 if sub >= db.geti("free_delivery_from") else db.geti("delivery_fee")
    await m.answer(f"🚚 Qanday olasiz?\n\n{B_DELIVERY} — {money(fee) if fee else 'bepul'}\n{B_PICKUP} — o'zingiz olib ketasiz",
                   reply_markup=kb([[B_DELIVERY, B_PICKUP], [B_CANCEL]]))


@router.message(S.otype, F.text.in_({B_DELIVERY, B_PICKUP}))
async def st_otype(m: Message, state: FSMContext):
    if m.text == B_DELIVERY:
        await state.update_data(otype="delivery")
        await state.set_state(S.address)
        await m.answer("📍 Lokatsiyangizni yuboring yoki manzilni yozing (tuman, ko'cha, uy, mo'ljal):",
                       reply_markup=kb([[KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)], [B_CANCEL]], "Manzil"))
    else:
        await state.update_data(otype="pickup", address=None, lat=None, lon=None)
        await ask_payment(m, state)


@router.message(S.otype)
async def st_otype_bad(m: Message):
    await m.answer("👇 Iltimos, tugmalardan birini tanlang.")


@router.message(S.address, F.location)
async def st_loc(m: Message, state: FSMContext):
    await state.update_data(lat=m.location.latitude, lon=m.location.longitude, address="📍 Lokatsiya yuborildi")
    await ask_payment(m, state)


@router.message(S.address, F.text)
async def st_addr(m: Message, state: FSMContext):
    a = m.text.strip()
    if len(a) < 5 or len(a) > 300:
        return await m.answer("⚠️ Manzil juda qisqa. To'liqroq yozing yoki lokatsiya yuboring.")
    await state.update_data(address=a, lat=None, lon=None)
    await ask_payment(m, state)


async def ask_payment(m, state):
    await state.set_state(S.payment)
    await m.answer("💳 To'lov turini tanlang:", reply_markup=kb([[B_CASH, B_CARD], [B_CANCEL]]))


@router.message(S.payment, F.text.in_({B_CASH, B_CARD}))
async def st_pay(m: Message, state: FSMContext):
    await state.update_data(payment="naqd" if m.text == B_CASH else "karta")
    await state.set_state(S.comment)
    await m.answer("💬 Izoh qoldirasizmi? (masalan: piyozsiz, achchiqroq, domofon kodi...)",
                   reply_markup=kb([[B_SKIP], [B_CANCEL]], "Izoh"))


@router.message(S.payment)
async def st_pay_bad(m: Message):
    await m.answer("👇 Iltimos, to'lov turini tugma orqali tanlang.")


def summary(uid, d):
    rows, sub, fee, total = db.totals(uid, d["otype"])
    L = ["🧾 <b>Buyurtmangizni tekshiring:</b>\n"]
    L += [f"• {e(r['name'])} — {r['qty']} × {money(r['price'])}" for r in rows]
    L.append(f"\n🧾 Taomlar: {money(sub)}")
    if d["otype"] == "delivery":
        L.append(f"🚚 Yetkazib berish: {money(fee) if fee else 'bepul'}")
    L.append(f"💰 <b>Jami: {money(total)}</b>\n")
    L.append(f"👤 {e(d['name'])}\n📱 {e(d['phone'])}")
    L.append(f"{'🚚 Yetkazib berish' if d['otype']=='delivery' else '🏃 Olib ketish'}")
    if d["otype"] == "delivery":
        L.append(f"📍 {e(d.get('address') or '')}")
    L.append(f"💳 To'lov: {d['payment']}")
    if d.get("comment"):
        L.append(f"💬 {e(d['comment'])}")
    return "\n".join(L), bool(rows)


@router.message(S.comment, F.text)
async def st_comment(m: Message, state: FSMContext):
    com = None if m.text == B_SKIP else m.text.strip()[:500]
    await state.update_data(comment=com)
    await state.set_state(S.confirm)
    d = await state.get_data()
    t, ok = summary(m.from_user.id, d)
    if not ok:
        await state.clear()
        return await m.answer("Savat bo'sh qolibdi 🙁", reply_markup=main_kb(m.from_user.id))
    await m.answer("Deyarli tayyor! ✨", reply_markup=ReplyKeyboardRemove())
    b = InlineKeyboardBuilder()
    b.button(text="✅ Tasdiqlash", callback_data="oconfirm")
    b.button(text="❌ Bekor qilish", callback_data="ocancel")
    await m.answer(t, reply_markup=b.as_markup())


@router.callback_query(F.data == "ocancel")
async def cb_ocancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await c.message.answer("Buyurtma bekor qilindi. Savatingiz saqlanib qoldi 🛒", reply_markup=main_kb(c.from_user.id))
    await c.answer()


@router.callback_query(F.data == "oconfirm", S.confirm)
async def cb_oconfirm(c: CallbackQuery, state: FSMContext):
    if not is_open():
        return await c.answer(f"😴 Hozir yopiqmiz. Ish vaqti: {hours()}", show_alert=True)
    d = await state.get_data()
    await state.clear()
    oid = db.create_order(c.from_user.id, d)
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await c.answer()
    if not oid:
        return await c.message.answer("Savat bo'sh 🙁", reply_markup=main_kb(c.from_user.id))
    log.info("Order #%s created by %s", oid, c.from_user.id)
    await c.message.answer(f"🎉 <b>Rahmat! Buyurtmangiz qabul qilindi.</b>\n\n🔖 Buyurtma raqami: <b>#{oid}</b>\n"
                           "Tez orada operatorimiz tasdiqlaydi. Holat o'zgarganda sizga xabar beramiz 🔔",
                           reply_markup=main_kb(c.from_user.id))
    await notify_admins(c.bot, oid)


@router.callback_query(F.data == "oconfirm")
async def cb_oconfirm_stale(c: CallbackQuery):
    await c.answer("Bu buyurtma eskirgan. Iltimos, savatdan qaytadan rasmiylashtiring.", show_alert=True)


# ---------- admin order notifications ----------
def order_text(oid, for_admin=True):
    o = db.order(oid)
    L = [f"🛎 <b>Buyurtma #{oid}</b> — {STATUSES[o['status']]}", f"🕒 {o['created'][:16].replace('T', ' ')}\n"]
    for it in db.order_items(oid):
        L.append(f"• {e(it['name'])} — {it['qty']} × {money(it['price'])}")
    L.append(f"\n🧾 Taomlar: {money(o['subtotal'])}")
    if o["otype"] == "delivery":
        L.append(f"🚚 Yetkazib berish: {money(o['delivery_fee']) if o['delivery_fee'] else 'bepul'}")
    L.append(f"💰 <b>Jami: {money(o['total'])}</b>\n💳 To'lov: {o['payment']}\n")
    if for_admin:
        u = db.user(o["user_id"])
        un = f" (@{u['username']})" if u and u["username"] else ""
        L.append(f"👤 {e(o['name'])}{e(un)}\n📱 {e(o['phone'])}\n🆔 <code>{o['user_id']}</code>")
    L.append("🚚 Yetkazib berish" if o["otype"] == "delivery" else "🏃 Olib ketish")
    if o["otype"] == "delivery":
        L.append(f"📍 {e(o['address'] or '')}")
        if o["lat"] is not None:
            L.append(f"🗺 <a href=\"https://maps.google.com/?q={o['lat']},{o['lon']}\">Google Maps</a> · "
                     f"<a href=\"https://yandex.uz/maps/?pt={o['lon']},{o['lat']}&z=17&l=map\">Yandex</a>")
    if o["comment"]:
        L.append(f"💬 {e(o['comment'])}")
    return "\n".join(L)


def status_kb(oid):
    b = InlineKeyboardBuilder()
    for st in ("accepted", "cooking", "onway", "done", "cancelled"):
        b.button(text=STATUSES[st].replace(" qilindi", ""), callback_data=f"st:{oid}:{st}")
    b.adjust(2, 2, 1)
    return b.as_markup()


async def notify_admins(bot: Bot, oid):
    o = db.order(oid)
    for aid in db.recipients():
        try:
            msg = await bot.send_message(aid, order_text(oid), reply_markup=status_kb(oid), disable_web_page_preview=True)
            db.x("INSERT INTO admin_msgs VALUES(?,?,?)", (oid, aid, msg.message_id))
            if o["lat"] is not None:
                await bot.send_location(aid, o["lat"], o["lon"], reply_to_message_id=msg.message_id)
        except (TelegramForbiddenError, TelegramBadRequest) as ex:
            log.warning("cannot notify admin %s: %s", aid, ex)


CUSTOMER_MSG = {
    "accepted": "✅ Buyurtmangiz <b>#{oid}</b> qabul qilindi! Tez orada tayyorlashni boshlaymiz.",
    "cooking": "👨‍🍳 Buyurtmangiz <b>#{oid}</b> tayyorlanmoqda...",
    "onway": "🚚 Buyurtmangiz <b>#{oid}</b> yo'lda! Kuryer tez orada yetib keladi.",
    "done": "✔️ Buyurtmangiz <b>#{oid}</b> yetkazildi. Yoqimli ishtaha! 😋\nZAM ZAM'ni tanlaganingiz uchun rahmat 🙏",
    "cancelled": "❌ Afsuski, buyurtmangiz <b>#{oid}</b> bekor qilindi. Savollar bo'lsa: {phone}",
}
PICKUP_MSG = {"onway": "🏃 Buyurtmangiz <b>#{oid}</b> tayyor! Olib ketishingiz mumkin.",
              "done": "✔️ Buyurtmangiz <b>#{oid}</b> topshirildi. Yoqimli ishtaha! 😋"}


@router.callback_query(F.data.startswith("st:"))
async def cb_status(c: CallbackQuery):
    if not db.is_admin(c.from_user.id):
        return await c.answer("⛔ Faqat adminlar uchun", show_alert=True)
    _, oid, st = c.data.split(":")
    oid = int(oid)
    o = db.order(oid)
    if not o or st not in STATUSES:
        return await c.answer("Buyurtma topilmadi", show_alert=True)
    if o["status"] == st:
        return await c.answer("Holat allaqachon shu")
    if o["status"] in ("done", "cancelled"):
        return await c.answer(f"Buyurtma yakunlangan: {STATUSES[o['status']]}", show_alert=True)
    db.set_status(oid, st)
    log.info("Order #%s -> %s by %s", oid, st, c.from_user.id)
    who = e(c.from_user.full_name)
    text = order_text(oid) + f"\n\n✏️ Oxirgi o'zgarish: {who}"
    markup = None if st in ("done", "cancelled") else status_kb(oid)
    for r in db.all("SELECT chat_id, message_id FROM admin_msgs WHERE order_id=?", (oid,)):
        try:
            await c.bot.edit_message_text(text, chat_id=r["chat_id"], message_id=r["message_id"],
                                          reply_markup=markup, disable_web_page_preview=True)
        except TelegramBadRequest:
            pass
    tmpl = (PICKUP_MSG.get(st) if o["otype"] == "pickup" else None) or CUSTOMER_MSG[st]
    try:
        await c.bot.send_message(o["user_id"], tmpl.format(oid=oid, phone=e(db.get("phone"))))
    except (TelegramForbiddenError, TelegramBadRequest) as ex:
        log.warning("cannot notify customer %s: %s", o["user_id"], ex)
    await c.answer(f"Holat: {STATUSES[st]}")


# ---------- Mini App orders ----------
async def on_webapp_order(bot: Bot, oid, want_loc):
    o = db.order(oid)
    uid = o["user_id"]
    txt = (f"🎉 <b>Rahmat! Buyurtmangiz qabul qilindi.</b>\n\n" + order_text(oid, for_admin=False) +
           "\n\nTez orada operatorimiz tasdiqlaydi. Holat o'zgarganda sizga xabar beramiz 🔔")
    try:
        await bot.send_message(uid, txt, disable_web_page_preview=True)
        if want_loc:
            db.x("INSERT OR REPLACE INTO loc_wait VALUES(?,?)", (uid, oid))
            await bot.send_message(uid, f"📍 Iltimos, buyurtma <b>#{oid}</b> uchun lokatsiyangizni yuboring "
                                        "(pastdagi tugma yoki 📎 → Joylashuv).",
                                   reply_markup=kb([[KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)],
                                                    ["⏭ Lokatsiyasiz davom etish"]]))
    except (TelegramForbiddenError, TelegramBadRequest) as ex:
        log.warning("cannot confirm webapp order to %s: %s", uid, ex)
    await notify_admins(bot, oid)


def _loc_waiting(m: Message):
    return bool(db.one("SELECT 1 FROM loc_wait WHERE user_id=?", (m.from_user.id,)))


@router.message(StateFilter(None), F.location, _loc_waiting)
async def webapp_loc(m: Message):
    r = db.one("SELECT order_id FROM loc_wait WHERE user_id=?", (m.from_user.id,))
    db.x("DELETE FROM loc_wait WHERE user_id=?", (m.from_user.id,))
    oid = r["order_id"]
    o = db.order(oid)
    lat, lon = m.location.latitude, m.location.longitude
    addr = o["address"] if o["address"] and "kutilmoqda" not in o["address"] else "📍 Lokatsiya yuborildi"
    db.x("UPDATE orders SET lat=?, lon=?, address=? WHERE id=?", (lat, lon, addr, oid))
    await m.answer(f"✅ Rahmat! Lokatsiya buyurtma <b>#{oid}</b> ga biriktirildi.", reply_markup=main_kb(m.from_user.id))
    for a in db.all("SELECT chat_id, message_id FROM admin_msgs WHERE order_id=?", (oid,)):
        try:
            done = db.order(oid)["status"] in ("done", "cancelled")
            await m.bot.edit_message_text(order_text(oid), chat_id=a["chat_id"], message_id=a["message_id"],
                                          reply_markup=None if done else status_kb(oid), disable_web_page_preview=True)
            await m.bot.send_location(a["chat_id"], lat, lon, reply_to_message_id=a["message_id"])
        except (TelegramForbiddenError, TelegramBadRequest) as ex:
            log.warning("loc forward failed: %s", ex)


@router.message(StateFilter(None), F.text == "⏭ Lokatsiyasiz davom etish")
async def webapp_loc_skip(m: Message):
    db.x("DELETE FROM loc_wait WHERE user_id=?", (m.from_user.id,))
    await m.answer("Mayli 👍 Operatorimiz manzilni telefon orqali aniqlaydi.", reply_markup=main_kb(m.from_user.id))


async def tunnel_watcher(bot: Bot):
    """Picks up the current public HTTPS URL (written by tunnel.sh) and points the menu button at it."""
    import aiohttp
    last = None
    while True:
        try:
            url = open(URL_FILE).read().strip() if os.path.isfile(URL_FILE) else ""
            if url and url != last:
                ok = False
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                        async with s.get(url + "/health") as r:
                            ok = r.status == 200
                except Exception:
                    ok = False
                if ok:
                    WEB["url"] = url
                    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🍽 Menyu", web_app=WebAppInfo(url=url + "/")))
                    log.info("Mini App URL set: %s", url)
                    last = url
            elif not url and WEB["url"]:
                WEB["url"] = None
                await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
                last = None
        except Exception as ex:
            log.warning("tunnel_watcher: %s", ex)
        await asyncio.sleep(10)


async def start_web(bot: Bot, token, dp=None):
    from aiohttp import web
    import webserver

    class Ctx:
        pass
    ctx = Ctx()
    ctx.db, ctx.token, ctx.bot, ctx.is_open, ctx.hours = db, token, bot, is_open, hours
    ctx.on_webapp_order = lambda oid, want: on_webapp_order(bot, oid, want)
    app = webserver.make_app(ctx)
    if dp is not None:  # webhook mode: Telegram POSTs updates to /tg/<secret path>
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=webhook_secret(token)).register(app, path=webhook_path(token))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    host = "0.0.0.0" if os.environ.get("PUBLIC_URL") else "127.0.0.1"
    port = int(os.environ.get("PORT") or os.environ.get("WEB_PORT") or "8088")
    await web.TCPSite(runner, host, port).start()
    log.info("Web server on %s:%s", host, port)
    return runner


def webhook_secret(token):
    import hashlib
    return os.environ.get("WEBHOOK_SECRET") or hashlib.sha256(("zz-secret:" + token).encode()).hexdigest()[:48]


def webhook_path(token):
    import hashlib
    return "/tg/" + hashlib.sha256(("zz-path:" + token).encode()).hexdigest()[:32]


async def keep_awake():
    """Pings our own public /health every 10 min so free hosts that sleep on idle (Render) stay up."""
    import aiohttp
    url = os.environ["PUBLIC_URL"].rstrip("/") + "/health"
    while True:
        await asyncio.sleep(int(os.environ.get("KEEPALIVE_SECONDS", "600")))
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.get(url) as r:
                    await r.read()
        except Exception as ex:
            log.warning("keep_awake: %s", type(ex).__name__)


# ---------- order history ----------
@router.message(F.text == B_ORDERS)
@router.message(Command("orders"))
async def my_orders(m: Message):
    rows = db.all("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (m.from_user.id,))
    if not rows:
        return await m.answer("📦 Sizda hali buyurtmalar yo'q.\n🍽 Menyudan birinchi buyurtmangizni bering!")
    L = ["📦 <b>Oxirgi buyurtmalaringiz:</b>\n"]
    b = InlineKeyboardBuilder()
    for o in rows:
        L.append(f"🔖 <b>#{o['id']}</b> · {o['created'][:16].replace('T', ' ')} · {money(o['total'])}\n    {STATUSES[o['status']]}")
        b.button(text=f"#{o['id']} batafsil", callback_data=f"oh:{o['id']}")
        b.button(text=f"🔁 #{o['id']} takrorlash", callback_data=f"rep:{o['id']}")
    b.adjust(2)
    await m.answer("\n".join(L), reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("oh:"))
async def cb_oh(c: CallbackQuery):
    oid = int(c.data[3:])
    o = db.order(oid)
    if not o or (o["user_id"] != c.from_user.id and not db.is_admin(c.from_user.id)):
        return await c.answer("Topilmadi", show_alert=True)
    await c.message.answer(order_text(oid, for_admin=False), disable_web_page_preview=True)
    await c.answer()


@router.callback_query(F.data.startswith("rep:"))
async def cb_repeat(c: CallbackQuery):
    oid = int(c.data[4:])
    o = db.order(oid)
    if not o or o["user_id"] != c.from_user.id:
        return await c.answer("Topilmadi", show_alert=True)
    n = 0
    for it in db.order_items(oid):
        cur = db.item(it["item_id"])
        if cur and cur["available"]:
            db.cart_add(c.from_user.id, it["item_id"], it["qty"]); n += 1
    await c.answer("🔁 Savatga qo'shildi" if n else "Bu taomlar hozir mavjud emas", show_alert=not n)
    if n:
        t, k = cart_view(c.from_user.id)
        await c.message.answer(t, reply_markup=k)


# ================= ADMIN =================
def admin_kb(uid):
    b = InlineKeyboardBuilder()
    b.button(text="➕ Taom qo'shish", callback_data="ad:add")
    b.button(text="✏️ Narxni o'zgartirish", callback_data="ad:pick:price")
    b.button(text="🚫 Mavjud / tugagan", callback_data="ad:pick:toggle")
    b.button(text="🗑 Taomni o'chirish", callback_data="ad:pick:del")
    b.button(text="📊 Statistika", callback_data="ad:stat")
    b.button(text="📢 Xabar yuborish", callback_data="ad:bc")
    b.button(text="⚙️ Sozlamalar", callback_data="ad:set")
    b.button(text="🧾 Faol buyurtmalar", callback_data="ad:active")
    if db.is_owner(uid):
        b.button(text="👥 Adminlar", callback_data="ad:admins")
        b.button(text="🔔 Buyurtma qabul qiluvchilar", callback_data="ad:recv")
    b.adjust(2)
    return b.as_markup()


def back_admin():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Admin panel", callback_data="ad:home")
    return b


async def admin_only(ev):
    uid = ev.from_user.id
    if db.is_admin(uid):
        return True
    if isinstance(ev, CallbackQuery):
        await ev.answer("⛔ Faqat adminlar uchun", show_alert=True)
    else:
        await ev.answer("⛔ Bu buyruq faqat adminlar uchun.")
    return False


@router.message(Command("admin"))
@router.message(F.text == "⚙️ Admin panel")
async def admin(m: Message, state: FSMContext):
    if not await admin_only(m):
        return
    await state.clear()
    role = "👑 Egasi" if db.is_owner(m.from_user.id) else "🛠 Admin"
    await m.answer(f"⚙️ <b>Admin panel</b> ({role})\nNima qilamiz?", reply_markup=admin_kb(m.from_user.id))


@router.callback_query(F.data == "ad:home")
async def ad_home(c: CallbackQuery, state: FSMContext):
    if not await admin_only(c):
        return
    await state.clear()
    await replace_with_text(c, "⚙️ <b>Admin panel</b>\nNima qilamiz?", admin_kb(c.from_user.id))
    await c.answer()


# --- add item dialog ---
@router.callback_query(F.data == "ad:add")
async def ad_add(c: CallbackQuery, state: FSMContext):
    if not await admin_only(c):
        return
    await state.set_state(A.add_photo)
    await c.message.answer("📸 1/5. Taom rasmini yuboring (yoki ⏭ rasmsiz):", reply_markup=kb([[B_SKIP], [B_CANCEL]]))
    await c.answer()


@router.message(A.add_photo, F.photo | (F.text == B_SKIP))
async def ad_add_photo(m: Message, state: FSMContext):
    await state.update_data(file_id=m.photo[-1].file_id if m.photo else None)
    await state.set_state(A.add_name)
    await m.answer("🏷 2/5. Taom nomini yozing:", reply_markup=kb([[B_CANCEL]]))


@router.message(A.add_photo)
async def ad_add_photo_bad(m: Message):
    await m.answer("⚠️ Rasm yuboring yoki ⏭ tugmasini bosing.")


@router.message(A.add_name, F.text)
async def ad_add_name(m: Message, state: FSMContext):
    if not (2 <= len(m.text.strip()) <= 60):
        return await m.answer("⚠️ Nom 2–60 belgi bo'lsin.")
    await state.update_data(name=m.text.strip())
    await state.set_state(A.add_desc)
    await m.answer("📝 3/5. Qisqa tavsif yozing (tarkibi):", reply_markup=kb([[B_SKIP], [B_CANCEL]]))


@router.message(A.add_desc, F.text)
async def ad_add_desc(m: Message, state: FSMContext):
    await state.update_data(descr="" if m.text == B_SKIP else m.text.strip()[:300])
    await state.set_state(A.add_price)
    await m.answer("💰 4/5. Narxini so'mda yozing (masalan: 28000):", reply_markup=kb([[B_CANCEL]]))


def parse_price(s):
    d = re.sub(r"[\s.,']|so'?m", "", (s or "").lower())
    if d.isdigit() and 500 <= int(d) <= 10_000_000:
        return int(d)
    return None


@router.message(A.add_price, F.text)
async def ad_add_price(m: Message, state: FSMContext):
    p = parse_price(m.text)
    if not p:
        return await m.answer("⚠️ Narx noto'g'ri. Faqat raqam yozing, masalan: 28000")
    await state.update_data(price=p)
    await state.set_state(A.add_cat)
    b = InlineKeyboardBuilder()
    for k, n in CATEGORIES:
        b.button(text=n, callback_data=f"adcat:{k}")
    b.adjust(2)
    await m.answer("📂 5/5. Bo'limni tanlang:", reply_markup=b.as_markup())


@router.callback_query(A.add_cat, F.data.startswith("adcat:"))
async def ad_add_cat(c: CallbackQuery, state: FSMContext):
    cat = c.data[6:]
    d = await state.get_data()
    await state.clear()
    cur = db.x("INSERT INTO items(cat,name,descr,price,file_id) VALUES(?,?,?,?,?)",
               (cat, d["name"], d.get("descr", ""), d["price"], d.get("file_id")))
    log.info("Item %s added by %s", cur.lastrowid, c.from_user.id)
    await c.message.edit_text(f"✅ <b>{e(d['name'])}</b> — {money(d['price'])} «{CAT_NAME[cat]}» bo'limiga qo'shildi!")
    await c.message.answer("⚙️ Admin panel", reply_markup=admin_kb(c.from_user.id))
    await c.message.answer("👇", reply_markup=main_kb(c.from_user.id))
    await c.answer()


# --- pick item for price/toggle/delete ---
ACT_TITLE = {"price": "✏️ Narxini o'zgartirish", "toggle": "🚫 Mavjudlikni o'zgartirish", "del": "🗑 O'chirish"}


@router.callback_query(F.data.startswith("ad:pick:"))
async def ad_pick(c: CallbackQuery):
    if not await admin_only(c):
        return
    act = c.data.split(":")[2]
    b = InlineKeyboardBuilder()
    for k, n in CATEGORIES:
        b.button(text=n, callback_data=f"apc:{act}:{k}")
    b.adjust(2)
    b.row(*back_admin().buttons)
    await replace_with_text(c, f"{ACT_TITLE[act]}\nBo'limni tanlang:", b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("apc:"))
async def ad_pick_cat_cb(c: CallbackQuery):
    if not await admin_only(c):
        return
    _, act, cat = c.data.split(":")
    await ad_pick_cat(c, act, cat)


async def ad_pick_cat(c: CallbackQuery, act, cat):
    b = InlineKeyboardBuilder()
    for it in db.items(cat):
        st = "✅" if it["available"] else "🚫"
        b.button(text=f"{st} {it['name']} — {money(it['price'])}", callback_data=f"aact:{act}:{it['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text=B_BACK, callback_data=f"ad:pick:{act}"))
    await replace_with_text(c, f"{ACT_TITLE[act]} · {CAT_NAME[cat]}\nTaomni tanlang:", b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("aact:"))
async def ad_act(c: CallbackQuery, state: FSMContext):
    if not await admin_only(c):
        return
    _, act, iid = c.data.split(":")
    it = db.item(int(iid))
    if not it:
        return await c.answer("Topilmadi", show_alert=True)
    if act == "toggle":
        db.x("UPDATE items SET available=1-available WHERE id=?", (it["id"],))
        await c.answer("🚫 Tugagan deb belgilandi" if it["available"] else "✅ Yana mavjud", show_alert=True)
        return await ad_pick_cat(c, "toggle", it["cat"])
    if act == "price":
        await state.set_state(A.price)
        await state.update_data(iid=it["id"])
        await c.message.answer(f"✏️ <b>{e(it['name'])}</b>\nHozirgi narx: {money(it['price'])}\nYangi narxni yozing:",
                               reply_markup=kb([[B_CANCEL]]))
        return await c.answer()
    if act == "del":
        b = InlineKeyboardBuilder()
        b.button(text="🗑 Ha, o'chirish", callback_data=f"adel:{it['id']}")
        b.button(text="⬅️ Yo'q", callback_data=f"apc:del:{it['cat']}")
        await replace_with_text(c, f"❗ <b>{e(it['name'])}</b> o'chirilsinmi?", b.as_markup())
        return await c.answer()


@router.callback_query(F.data.startswith("adel:"))
async def ad_del(c: CallbackQuery):
    if not await admin_only(c):
        return
    it = db.item(int(c.data[5:]))
    if it:
        db.x("UPDATE items SET deleted=1 WHERE id=?", (it["id"],))
        db.x("DELETE FROM cart WHERE item_id=?", (it["id"],))
        log.info("Item %s deleted by %s", it["id"], c.from_user.id)
        await c.answer(f"🗑 {it['name']} o'chirildi", show_alert=True)
        return await ad_pick_cat(c, "del", it["cat"])
    await c.answer()


@router.message(A.price, F.text)
async def ad_price(m: Message, state: FSMContext):
    p = parse_price(m.text)
    if not p:
        return await m.answer("⚠️ Narx noto'g'ri. Masalan: 30000")
    d = await state.get_data()
    await state.clear()
    db.x("UPDATE items SET price=? WHERE id=?", (p, d["iid"]))
    it = db.item(d["iid"])
    await m.answer(f"✅ <b>{e(it['name'] if it else '')}</b> yangi narxi: {money(p)}", reply_markup=main_kb(m.from_user.id))
    await m.answer("⚙️ Admin panel", reply_markup=admin_kb(m.from_user.id))


# --- stats ---
def stat_text():
    n = now()
    day = n.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - dt.timedelta(days=day.weekday())
    month = day.replace(day=1)
    L = ["📊 <b>Statistika</b>\n"]
    for title, since in (("Bugun", day), ("Shu hafta", week), ("Shu oy", month)):
        cnt, s, canc = db.stats(since.isoformat())
        L.append(f"<b>{title}:</b> {cnt} ta buyurtma · {money(s)}" + (f" · ❌ {canc}" if canc else ""))
    top = db.top_items(week.isoformat())
    if top:
        L.append("\n🏆 <b>Haftaning top taomlari:</b>")
        L += [f"{i}. {e(r['name'])} — {r['q']} ta ({money(r['s'])})" for i, r in enumerate(top, 1)]
    act = db.one("SELECT COUNT(*) n FROM orders WHERE status IN ('new','accepted','cooking','onway')")["n"]
    users = db.one("SELECT COUNT(*) n FROM users")["n"]
    L.append(f"\n🧾 Faol buyurtmalar: {act}\n👥 Mijozlar: {users}")
    return "\n".join(L)


@router.message(Command("stat"))
async def stat_cmd(m: Message):
    if await admin_only(m):
        await m.answer(stat_text())


@router.callback_query(F.data == "ad:stat")
async def ad_stat(c: CallbackQuery):
    if not await admin_only(c):
        return
    await replace_with_text(c, stat_text(), back_admin().as_markup())
    await c.answer()


@router.callback_query(F.data == "ad:active")
async def ad_active(c: CallbackQuery):
    if not await admin_only(c):
        return
    rows = db.all("SELECT id FROM orders WHERE status IN ('new','accepted','cooking','onway') ORDER BY id LIMIT 15")
    await c.answer()
    if not rows:
        return await c.message.answer("🧾 Faol buyurtmalar yo'q.")
    for r in rows:
        msg = await c.message.answer(order_text(r["id"]), reply_markup=status_kb(r["id"]), disable_web_page_preview=True)
        db.x("INSERT INTO admin_msgs VALUES(?,?,?)", (r["id"], c.from_user.id, msg.message_id))


# --- admins (owner only) ---
def admins_view():
    L = ["👥 <b>Adminlar</b>\n"]
    b = InlineKeyboardBuilder()
    for a in db.admins():
        nm = a["full_name"] or str(a["user_id"])
        L.append(f"{'👑' if a['is_owner'] else '🛠'} {e(nm)} — <code>{a['user_id']}</code> {'🔔' if a['receives'] else '🔕'}")
        if not a["is_owner"]:
            b.button(text=f"➖ {nm[:25]}", callback_data=f"arm:{a['user_id']}")
    b.button(text="➕ Admin qo'shish", callback_data="aadd")
    b.button(text="👑 Egalikni topshirish", callback_data="oxfer")
    b.adjust(1)
    b.row(*back_admin().buttons)
    return "\n".join(L), b.as_markup()


async def owner_only(c: CallbackQuery):
    if db.is_owner(c.from_user.id):
        return True
    await c.answer("⛔ Faqat bot egasi uchun", show_alert=True)
    return False


@router.callback_query(F.data == "ad:admins")
async def ad_admins(c: CallbackQuery):
    if not await owner_only(c):
        return
    t, k = admins_view()
    await replace_with_text(c, t, k)
    await c.answer()


@router.callback_query(F.data.startswith("arm:"))
async def ad_rm(c: CallbackQuery):
    if not await owner_only(c):
        return
    db.x("DELETE FROM admins WHERE user_id=? AND is_owner=0", (int(c.data[4:]),))
    t, k = admins_view()
    await replace_with_text(c, t, k)
    await c.answer("Admin o'chirildi")


@router.callback_query(F.data == "aadd")
async def ad_add_admin(c: CallbackQuery, state: FSMContext):
    if not await owner_only(c):
        return
    await state.set_state(A.admin_add)
    await c.message.answer("➕ Yangi adminning <b>Telegram ID</b> raqamini yozing, kontaktini yuboring yoki uning biror xabarini forward qiling.\n"
                           "💡 U avval botga /start bosgan bo'lishi kerak (buyurtma xabarlari borishi uchun).",
                           reply_markup=kb([[B_CANCEL]]))
    await c.answer()


@router.message(A.admin_add)
async def ad_admin_input(m: Message, state: FSMContext):
    if not db.is_owner(m.from_user.id):
        await state.clear(); return
    uid, err = parse_uid(m)
    if err:
        return await m.answer(err)
    await state.clear()
    db.add_admin(uid)
    log.info("Admin %s added by %s", uid, m.from_user.id)
    await m.answer(f"✅ <code>{uid}</code> admin qilindi va buyurtmalarni qabul qiladi.", reply_markup=main_kb(m.from_user.id))
    try:
        await m.bot.send_message(uid, "🛠 Siz ZAM ZAM botida admin etib tayinlandingiz! Boshqaruv: /admin")
    except (TelegramForbiddenError, TelegramBadRequest):
        await m.answer("ℹ️ Unga xabar yuborib bo'lmadi — u botga /start bosishi kerak.")
    t, k = admins_view()
    await m.answer(t, reply_markup=k)


@router.callback_query(F.data == "ad:recv")
async def ad_recv(c: CallbackQuery):
    if not await owner_only(c):
        return
    b = InlineKeyboardBuilder()
    for a in db.admins():
        nm = a["full_name"] or str(a["user_id"])
        b.button(text=f"{'🔔' if a['receives'] else '🔕'} {nm[:30]}", callback_data=f"arcv:{a['user_id']}")
    b.adjust(1)
    b.row(*back_admin().buttons)
    await replace_with_text(c, "🔔 <b>Buyurtmalar kimga borsin?</b>\nBosib yoqing/o'chiring:", b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("arcv:"))
async def ad_recv_toggle(c: CallbackQuery):
    if not await owner_only(c):
        return
    uid = int(c.data[5:])
    db.x("UPDATE admins SET receives=1-receives WHERE user_id=?", (uid,))
    if not db.recipients():
        db.x("UPDATE admins SET receives=1 WHERE user_id=?", (uid,))
        await c.answer("⚠️ Kamida bitta qabul qiluvchi bo'lishi shart", show_alert=True)
    await ad_recv(c)


def parse_uid(m: Message):
    if m.contact:
        return m.contact.user_id, None if m.contact.user_id else "⚠️ Bu kontakt Telegramga bog'lanmagan. ID raqamini yozing."
    if m.forward_origin is not None:
        u = getattr(m.forward_origin, "sender_user", None)
        return (u.id, None) if u else (None, "⚠️ Bu foydalanuvchi forward'ni yashirgan. ID raqamini yozing.")
    if m.text and m.text.strip().isdigit():
        return int(m.text.strip()), None
    return None, "⚠️ ID raqam (faqat raqamlar), kontakt yoki forward yuboring."


@router.callback_query(F.data == "oxfer")
async def ad_xfer(c: CallbackQuery, state: FSMContext):
    if not await owner_only(c):
        return
    await state.set_state(A.owner_xfer)
    await c.message.answer("👑 Bosh admin (egalik) kimga o'tsin?\nUning <b>Telegram ID</b> raqamini yozing, kontaktini yuboring yoki xabarini forward qiling.\n"
                           "⚠️ U avval botga /start bosgan bo'lishi kerak. Siz oddiy admin bo'lib qolasiz.",
                           reply_markup=kb([[B_CANCEL]]))
    await c.answer()


@router.message(A.owner_xfer)
async def ad_xfer_input(m: Message, state: FSMContext):
    if not db.is_owner(m.from_user.id):
        await state.clear(); return
    uid, err = parse_uid(m)
    if err:
        return await m.answer(err)
    if uid == m.from_user.id:
        return await m.answer("ℹ️ Siz allaqachon egasiz. Boshqa foydalanuvchini tanlang.")
    if not db.user(uid):
        return await m.answer("⚠️ Bu foydalanuvchi botga hali /start bosmagan. Avval u botni ishga tushirsin.")
    await state.clear()
    u = db.user(uid); nm = (u["full_name"] if u else None) or str(uid)
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ha, topshiraman", callback_data=f"oxok:{uid}")
    b.button(text="❌ Bekor qilish", callback_data="ad:admins")
    b.adjust(1)
    await m.answer("✅", reply_markup=main_kb(m.from_user.id))
    await m.answer(f"❓ Bosh adminlikni <b>{e(nm)}</b> (<code>{uid}</code>) ga topshirasizmi?\nBu amalni faqat yangi ega qaytara oladi.", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("oxok:"))
async def ad_xfer_ok(c: CallbackQuery):
    if not await owner_only(c):
        return
    uid = int(c.data[5:]); me = c.from_user.id
    db.transfer_owner(me, uid)
    log.info("Ownership transferred %s -> %s", me, uid)
    await c.answer("👑 Egalik topshirildi", show_alert=True)
    try:
        await c.message.edit_text(f"✅ Bosh adminlik <code>{uid}</code> ga topshirildi. Siz endi oddiy adminsiz.")
    except TelegramBadRequest:
        pass
    try:
        await c.bot.send_message(uid, "👑 Siz ZAM ZAM botining bosh admini (egasi) etib tayinlandingiz! Boshqaruv: /admin", reply_markup=main_kb(uid))
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


# --- broadcast ---
@router.callback_query(F.data == "ad:bc")
async def ad_bc(c: CallbackQuery, state: FSMContext):
    if not await admin_only(c):
        return
    await state.set_state(A.broadcast)
    n = db.one("SELECT COUNT(*) n FROM users WHERE blocked=0")["n"]
    await c.message.answer(f"📢 Barcha mijozlarga ({n} kishi) yuboriladigan xabarni yuboring (matn, rasm, video...):",
                           reply_markup=kb([[B_CANCEL]]))
    await c.answer()


@router.message(A.broadcast)
async def ad_bc_msg(m: Message, state: FSMContext):
    await state.update_data(chat=m.chat.id, mid=m.message_id)
    await state.set_state(A.broadcast_ok)
    b = InlineKeyboardBuilder()
    b.button(text="📤 Yuborish", callback_data="bcgo")
    b.button(text="❌ Bekor", callback_data="ad:home")
    await m.answer("👆 Shu xabar hammaga yuborilsinmi?", reply_markup=b.as_markup())


@router.callback_query(A.broadcast_ok, F.data == "bcgo")
async def ad_bc_go(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await c.message.edit_text("⏳ Yuborilmoqda...")
    await c.answer()
    ok = fail = 0
    for r in db.all("SELECT id FROM users WHERE blocked=0"):
        try:
            await c.bot.copy_message(r["id"], d["chat"], d["mid"]); ok += 1
        except TelegramRetryAfter as ex:
            await asyncio.sleep(ex.retry_after)
            try:
                await c.bot.copy_message(r["id"], d["chat"], d["mid"]); ok += 1
            except Exception:
                fail += 1
        except TelegramForbiddenError:
            db.x("UPDATE users SET blocked=1 WHERE id=?", (r["id"],)); fail += 1
        except TelegramBadRequest:
            fail += 1
        await asyncio.sleep(0.05)
    log.info("Broadcast by %s: ok=%s fail=%s", c.from_user.id, ok, fail)
    await c.message.answer(f"✅ Yuborildi: {ok}\n❌ Yetkazilmadi: {fail}", reply_markup=main_kb(c.from_user.id))


# --- settings ---
SETTINGS = [("hours", "🕙 Ish vaqti"), ("delivery_fee", "🚚 Yetkazish narxi"), ("free_delivery_from", "🎁 Bepul yetkazish chegarasi"),
            ("phone", "☎️ Telefon"), ("address", "📍 Manzil"), ("telegram", "✈️ Telegram"), ("about", "ℹ️ Biz haqimizda")]


def settings_view():
    vals = {"hours": hours(), "delivery_fee": money(db.geti("delivery_fee")),
            "free_delivery_from": money(db.geti("free_delivery_from"))}
    L = ["⚙️ <b>Sozlamalar</b>\n"]
    b = InlineKeyboardBuilder()
    for k, n in SETTINGS:
        v = vals.get(k, db.get(k))
        L.append(f"{n}: <b>{e(v if len(v) < 60 else v[:57] + '...')}</b>")
        b.button(text=n, callback_data=f"aset:{k}")
    b.adjust(2)
    b.row(*back_admin().buttons)
    return "\n".join(L), b.as_markup()


@router.callback_query(F.data == "ad:set")
async def ad_set(c: CallbackQuery):
    if not await admin_only(c):
        return
    t, k = settings_view()
    await replace_with_text(c, t, k)
    await c.answer()


HINT = {"hours": "Masalan: 10:00-23:00 (tungi ish vaqti ham mumkin: 18:00-03:00)",
        "delivery_fee": "So'mda, masalan: 10000 (0 — bepul)", "free_delivery_from": "So'mda, masalan: 150000"}


@router.callback_query(F.data.startswith("aset:"))
async def ad_set_key(c: CallbackQuery, state: FSMContext):
    if not await admin_only(c):
        return
    k = c.data[5:]
    await state.set_state(A.setting)
    await state.update_data(key=k)
    await c.message.answer(f"{dict(SETTINGS)[k]} uchun yangi qiymatni yozing.\n{HINT.get(k, '')}", reply_markup=kb([[B_CANCEL]]))
    await c.answer()


@router.message(A.setting, F.text)
async def ad_set_val(m: Message, state: FSMContext):
    k = (await state.get_data())["key"]
    v = m.text.strip()
    if k == "hours":
        mm = re.fullmatch(r"(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})", v)
        if not mm or int(mm[1]) > 23 or int(mm[3]) > 24 or int(mm[2]) > 59 or int(mm[4]) > 59:
            return await m.answer("⚠️ Format: 10:00-23:00")
        db.set("open_time", f"{int(mm[1]):02d}:{mm[2]}")
        db.set("close_time", f"{int(mm[3]) % 24:02d}:{mm[4]}")
    elif k in ("delivery_fee", "free_delivery_from"):
        d = re.sub(r"\D", "", v)
        if not d or int(d) > 10_000_000:
            return await m.answer("⚠️ Faqat raqam yozing, masalan: 10000")
        db.set(k, int(d))
    else:
        if not (2 <= len(v) <= 1000):
            return await m.answer("⚠️ Juda qisqa yoki juda uzun.")
        db.set(k, v)
    await state.clear()
    await m.answer("✅ Saqlandi!", reply_markup=main_kb(m.from_user.id))
    t, kk = settings_view()
    await m.answer(t, reply_markup=kk)


# ---------- fallbacks ----------
@router.message(StateFilter(S, A))
async def bad_input_in_state(m: Message):
    await m.answer("⚠️ Kutilgan ma'lumot emas. Qaytadan urinib ko'ring yoki ❌ Bekor qilish.")


@router.message()
async def fallback(m: Message):
    await m.answer("🤔 Tushunmadim. Quyidagi tugmalardan foydalaning 👇", reply_markup=main_kb(m.from_user.id))


@router.callback_query()
async def cb_fallback(c: CallbackQuery):
    await c.answer("Bu tugma eskirgan. /start bosing.", show_alert=False)


async def on_error(event: ErrorEvent):
    log.exception("Unhandled error: %s", event.exception, exc_info=event.exception)
    upd = event.update
    try:
        if upd.callback_query:
            await upd.callback_query.answer("⚠️ Xatolik yuz berdi, qaytadan urinib ko'ring.", show_alert=True)
        elif upd.message:
            await upd.message.answer("⚠️ Xatolik yuz berdi, qaytadan urinib ko'ring. /start")
    except Exception:
        pass
    return True


# ---------- setup ----------
def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not os.environ.get("PUBLIC_URL"):  # on a cloud host logs go to stdout only
        fh = RotatingFileHandler(os.path.join(BASE, "bot.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    sh = logging.StreamHandler(); sh.setFormatter(fmt); root.addHandler(sh)


def setup(db_path=None):
    global db
    db = DB(db_path or os.environ.get("DB_PATH") or os.path.join(BASE, "zamzam.db"))
    db.x("CREATE TABLE IF NOT EXISTS loc_wait(user_id INTEGER PRIMARY KEY, order_id INTEGER)")
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UserMW())
    dp.include_router(router)
    dp.errors.register(on_error)
    return dp


POLLING_TIMEOUT = 25          # long-poll seconds per getUpdates
SESSION_TIMEOUT = 20          # extra HTTP slack; aiogram adds polling_timeout on top
STALL_LIMIT = 180             # no successful getUpdates for this long -> restart
PENDING_LIMIT = 60            # pending updates stuck this long -> restart
HEALTH = {"last_ok": time.monotonic()}


def start_watchdog(token):
    """Thread-based watchdog (works even if the asyncio loop is wedged).
    Exits the process; run.sh supervisor restarts it. Data lives in SQLite (WAL), so nothing is lost."""
    import threading, json, urllib.request

    def loop():
        pending_since = None
        url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
        while True:
            time.sleep(15)
            stalled = time.monotonic() - HEALTH["last_ok"]
            if stalled > STALL_LIMIT:
                log.error("Watchdog: no successful getUpdates for %ds, restarting", stalled)
                logging.shutdown(); os._exit(3)
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    pending = json.load(r)["result"].get("pending_update_count", 0)
            except Exception as e:
                log.warning("Watchdog: getWebhookInfo failed: %s", type(e).__name__)
                continue
            if pending > 0:
                pending_since = pending_since or time.monotonic()
                if time.monotonic() - pending_since > PENDING_LIMIT:
                    log.error("Watchdog: %d updates pending >%ds, restarting", pending, PENDING_LIMIT)
                    logging.shutdown(); os._exit(4)
            else:
                pending_since = None

    threading.Thread(target=loop, name="watchdog", daemon=True).start()


async def main():
    setup_logging()
    token = os.environ.get("ZAMZAM_APP_TOKEN")
    if not token:
        raise SystemExit("ZAMZAM_APP_TOKEN env var is not set")
    dp = setup()
    from aiogram.client.session.aiohttp import AiohttpSession
    session = AiohttpSession(timeout=SESSION_TIMEOUT)
    _orig = session.make_request
    async def _tracked(bot_, method, timeout=None):
        res = await _orig(bot_, method, timeout=timeout)
        if type(method).__name__ == "GetUpdates":
            HEALTH["last_ok"] = time.monotonic()
        return res
    session.make_request = _tracked
    bot = Bot(token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    public = os.environ.get("PUBLIC_URL", "").rstrip("/")
    me = await bot.get_me()
    log.info("Starting @%s (%s mode)", me.username, "webhook" if public else "polling")
    await bot.set_my_commands([BotCommand(command="start", description="Bosh menyu"),
                               BotCommand(command="menu", description="🍽 Menyu"),
                               BotCommand(command="app", description="📱 Ilovani ochish"),
                               BotCommand(command="cart", description="🛒 Savat"),
                               BotCommand(command="orders", description="📦 Buyurtmalarim"),
                               BotCommand(command="help", description="Yordam")])
    if public:
        WEB["url"] = public
        runner = await start_web(bot, token, dp)
        await bot.set_webhook(public + webhook_path(token), secret_token=webhook_secret(token),
                              allowed_updates=dp.resolve_used_update_types(), drop_pending_updates=False)
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🍽 Menyu", web_app=WebAppInfo(url=public + "/")))
        log.info("Webhook + Mini App set to %s", public)
        if os.environ.get("KEEPALIVE", "1") == "1":
            asyncio.create_task(keep_awake())
        stop = asyncio.Event()
        import signal
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        await stop.wait()
        await runner.cleanup()
        await bot.session.close()
        return
    start_watchdog(token)
    await bot.delete_webhook(drop_pending_updates=False)
    await start_web(bot, token)
    asyncio.create_task(tunnel_watcher(bot))
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(),
                           polling_timeout=POLLING_TIMEOUT, handle_signals=True)


if __name__ == "__main__":
    asyncio.run(main())
