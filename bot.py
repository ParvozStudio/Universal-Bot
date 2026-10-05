import asyncio, logging, os, random, sqlite3, time
from html import escape as esc

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, ChatJoinRequest, InlineKeyboardButton as IB,
                           InlineKeyboardMarkup as IM, InlineQuery, InlineQueryResultArticle,
                           InputTextMessageContent, KeyboardButton, Message, ReplyKeyboardMarkup)

TOKEN = os.getenv("BOT_TOKEN", "8987418051:AAGIYXcs-_RIiIwlWwzcERgYJsYYw1UowsM")
OWNER = int(os.getenv("OWNER_ID", "7435698745"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
BOT_USERNAME = ""

KINDS = {"kino": "🎬 Kino", "serial": "📺 Serial", "anime": "🌸 Anime",
         "drama": "🎭 Drama", "multfilm": "🧸 Multfilm"}
B_SEARCH, B_RANDOM, B_LATER = "🔍 Qidiruv", "🎲 Random", "⏳ Keyinroq"
B_STATS, B_VIP, B_CAT = "📊 Ko'rilganlar", "💎 VIP", "🗂 Bo'limlar"

# ---------------------------------------------------------------- DB
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, joined INT, vip_until INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, fmt TEXT, title TEXT,
  descr TEXT, poster TEXT, vip INT DEFAULT 0, views INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS parts(id INTEGER PRIMARY KEY AUTOINCREMENT, media_id INT, season INT,
  number INT, ptype TEXT, file_id TEXT, ftype TEXT);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, chat_id TEXT,
  title TEXT, url TEXT, lim INT DEFAULT 0, joined INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS join_req(user_id INT, chat_id TEXT, PRIMARY KEY(user_id, chat_id));
CREATE TABLE IF NOT EXISTS sub_log(user_id INT, ch_id INT, PRIMARY KEY(user_id, ch_id));
CREATE TABLE IF NOT EXISTS ratings(user_id INT, media_id INT, score INT, PRIMARY KEY(user_id, media_id));
CREATE TABLE IF NOT EXISTS later(user_id INT, media_id INT, PRIMARY KEY(user_id, media_id));
CREATE TABLE IF NOT EXISTS history(user_id INT, media_id INT, PRIMARY KEY(user_id, media_id));
CREATE TABLE IF NOT EXISTS post_ch(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, title TEXT,
  kind TEXT DEFAULT 'all');
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
""")
db.commit()


def run(sql, *a):
    cur = db.execute(sql, a)
    db.commit()
    return cur


def one(sql, *a):
    return db.execute(sql, a).fetchone()


def many(sql, *a):
    return db.execute(sql, a).fetchall()


def get_s(k, d=""):
    r = one("SELECT v FROM settings WHERE k=?", k)
    return r["v"] if r else d


def set_s(k, v):
    run("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, v)


def is_admin(uid):
    return uid == OWNER or bool(one("SELECT 1 FROM admins WHERE id=?", uid))


def is_vip(uid):
    if is_admin(uid):
        return True
    u = one("SELECT vip_until FROM users WHERE id=?", uid)
    return bool(u and u["vip_until"] > time.time())


def cid(x):
    return x if str(x).startswith("@") else int(x)


# ---------------------------------------------------------------- Premium emoji (ixtiyoriy)
# CUSTOM_EMOJI = {"fire": "5368324170671202286"}  -> E("fire", "🔥")
CUSTOM_EMOJI: dict = {}


def E(name, fallback):
    i = CUSTOM_EMOJI.get(name)
    return f'<tg-emoji emoji-id="{i}">{fallback}</tg-emoji>' if i else fallback


# ---------------------------------------------------------------- Majburiy obuna
async def unsubscribed(bot, uid):
    run("DELETE FROM channels WHERE lim>0 AND joined>=lim")
    missing = []
    for ch in many("SELECT * FROM channels WHERE kind!='external'"):
        ok = False
        try:
            m = await bot.get_chat_member(cid(ch["chat_id"]), uid)
            ok = m.status in ("member", "administrator", "creator")
        except Exception:
            pass
        if not ok and ch["kind"] == "request":
            ok = bool(one("SELECT 1 FROM join_req WHERE user_id=? AND chat_id=?", uid, str(ch["chat_id"])))
        if ok:
            if not one("SELECT 1 FROM sub_log WHERE user_id=? AND ch_id=?", uid, ch["id"]):
                run("INSERT INTO sub_log VALUES(?,?)", uid, ch["id"])
                run("UPDATE channels SET joined=joined+1 WHERE id=?", ch["id"])
        else:
            missing.append(ch)
    return missing


async def gate(bot, uid, chat, payload=""):
    if is_admin(uid):
        return True
    miss = await unsubscribed(bot, uid)
    if not miss:
        return True
    rows = [[IB(text=f"➕ {c['title']}", url=c["url"])] for c in miss]
    rows += [[IB(text=f"🔗 {c['title']}", url=c["url"])] for c in many("SELECT * FROM channels WHERE kind='external'")]
    rows.append([IB(text="✅ Tekshirish", callback_data=f"chk:{payload}")])
    await bot.send_message(chat, "❗ Botdan foydalanish uchun quyidagilarga obuna bo'ling:",
                           reply_markup=IM(inline_keyboard=rows))
    return False


# ---------------------------------------------------------------- Ko'rsatish
def card_text(m):
    r = one("SELECT AVG(score) a, COUNT(*) c FROM ratings WHERE media_id=?", m["id"])
    star = f"🌟 {r['a']:.1f} ({r['c']})" if r["c"] else "🌟 baho yo'q"
    return (f"{'💎 ' if m['vip'] else ''}<b>{esc(m['title'])}</b>\n"
            f"{KINDS[m['kind']]} • 🆔 <code>{m['id']}</code>\n{star} • 👁 {m['views']}\n\n{esc(m['descr'] or '')}")


def action_rows(uid, mid):
    rate = [IB(text=f"{i}🌟", callback_data=f"r:{mid}:{i}") for i in range(1, 6)]
    lat = one("SELECT 1 FROM later WHERE user_id=? AND media_id=?", uid, mid)
    return [rate, [IB(text="❌ Keyinroqdan olib tashlash" if lat else "⏳ Keyinroq ko'raman",
                      callback_data=f"l:{mid}")]]


def protect():
    return get_s("protect") == "1"


async def send_part(bot, chat, uid, p, caption):
    kb = IM(inline_keyboard=action_rows(uid, p["media_id"]))
    f = bot.send_video if p["ftype"] == "video" else bot.send_document
    kw = {"video": p["file_id"]} if p["ftype"] == "video" else {"document": p["file_id"]}
    await f(chat, caption=caption[:1024], protect_content=protect(), reply_markup=kb, **kw)


def season_kb(mid):
    ss = [r["season"] for r in many("SELECT DISTINCT season FROM parts WHERE media_id=? ORDER BY season", mid)]
    rows, row = [], []
    for s in ss:
        row.append(IB(text=f"{s}-fasl", callback_data=f"s:{mid}:{s}:0"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return IM(inline_keyboard=rows + action_rows(0, mid)[1:] if False else rows)


PAGE = 30


def eps_kb(mid, season, page):
    ps = many("SELECT * FROM parts WHERE media_id=? AND season=? ORDER BY number LIMIT ? OFFSET ?",
              mid, season, PAGE, page * PAGE)
    total = one("SELECT COUNT(*) c FROM parts WHERE media_id=? AND season=?", mid, season)["c"]
    rows, row = [], []
    for p in ps:
        label = str(p["number"]) if p["ptype"] == "ep" else f"{'🎬' if p['ptype'] == 'film' else 'OVA'}{p['number']}"
        row.append(IB(text=label, callback_data=f"p:{p['id']}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(IB(text="⬅️", callback_data=f"s:{mid}:{season}:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(IB(text="➡️", callback_data=f"s:{mid}:{season}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([IB(text="🔙 Fasllar", callback_data=f"m:{mid}")])
    return IM(inline_keyboard=rows)


async def show_media(bot, chat, uid, mid):
    m = one("SELECT * FROM media WHERE id=?", mid)
    if not m:
        return await bot.send_message(chat, "❌ Topilmadi.")
    if m["vip"] and not is_vip(uid):
        return await bot.send_message(chat, "💎 Bu VIP kontent. Obuna uchun «💎 VIP» bo'limiga o'ting.")
    run("UPDATE media SET views=views+1 WHERE id=?", mid)
    run("INSERT OR IGNORE INTO history VALUES(?,?)", uid, mid)
    m = one("SELECT * FROM media WHERE id=?", mid)
    if m["fmt"] == "film":
        p = one("SELECT * FROM parts WHERE media_id=? ORDER BY id LIMIT 1", mid)
        if not p:
            return await bot.send_message(chat, "Hali video yuklanmagan.")
        return await send_part(bot, chat, uid, p, card_text(m))
    kb = season_kb(mid)
    if not kb.inline_keyboard:
        return await bot.send_message(chat, card_text(m) + "\n\nHali qismlar yuklanmagan.")
    if m["poster"]:
        await bot.send_photo(chat, m["poster"], caption=card_text(m)[:1024], reply_markup=kb)
    else:
        await bot.send_message(chat, card_text(m), reply_markup=kb)


def list_kb(rows, nav=None):
    kb = [[IB(text=f"{'💎 ' if r['vip'] else ''}{r['title']} • {r['id']}", callback_data=f"v:{r['id']}")] for r in rows]
    if nav:
        kb.append(nav)
    return IM(inline_keyboard=kb)


def main_kb(uid):
    rows = [[KeyboardButton(text=B_SEARCH), KeyboardButton(text=B_RANDOM)],
            [KeyboardButton(text=B_CAT), KeyboardButton(text=B_LATER)],
            [KeyboardButton(text=B_STATS), KeyboardButton(text=B_VIP)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def after_gate(bot, chat, uid, arg=""):
    if arg.startswith("m") and arg[1:].isdigit():
        return await show_media(bot, chat, uid, int(arg[1:]))
    txt = f"{E('film', '🎬')} Xush kelibsiz! Nom yoki ID yuboring, yoki menyudan tanlang."
    if is_admin(uid):
        txt += "\n\n👮 Admin panel: /admin"
    await bot.send_message(chat, txt, reply_markup=main_kb(uid))


# ---------------------------------------------------------------- Foydalanuvchi router
user = Router()


@user.chat_join_request()
async def on_join_request(r: ChatJoinRequest):
    run("INSERT OR IGNORE INTO join_req VALUES(?,?)", r.from_user.id, str(r.chat.id))


@user.message(CommandStart())
async def start(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    run("INSERT OR IGNORE INTO users(id,joined) VALUES(?,?)", uid, int(time.time()))
    arg = command.args or ""
    if await gate(m.bot, uid, m.chat.id, arg):
        await after_gate(m.bot, m.chat.id, uid, arg)


@user.callback_query(F.data.startswith("chk:"))
async def chk(c: CallbackQuery):
    if await unsubscribed(c.bot, c.from_user.id):
        return await c.answer("❌ Hali hammasiga a'zo bo'lmadingiz", show_alert=True)
    await c.message.delete()
    await after_gate(c.bot, c.message.chat.id, c.from_user.id, c.data[4:])


@user.callback_query(F.data.startswith("v:"))
async def cb_view(c: CallbackQuery):
    await c.answer()
    await show_media(c.bot, c.message.chat.id, c.from_user.id, int(c.data[2:]))


@user.callback_query(F.data.startswith("m:"))
async def cb_back(c: CallbackQuery):
    await c.message.edit_reply_markup(reply_markup=season_kb(int(c.data[2:])))
    await c.answer()


@user.callback_query(F.data.startswith("s:"))
async def cb_season(c: CallbackQuery):
    _, mid, season, page = c.data.split(":")
    await c.message.edit_reply_markup(reply_markup=eps_kb(int(mid), int(season), int(page)))
    await c.answer()


@user.callback_query(F.data.startswith("p:"))
async def cb_part(c: CallbackQuery):
    p = one("SELECT * FROM parts WHERE id=?", int(c.data[2:]))
    if not p:
        return await c.answer("Topilmadi", show_alert=True)
    m = one("SELECT * FROM media WHERE id=?", p["media_id"])
    if m["vip"] and not is_vip(c.from_user.id):
        return await c.answer("💎 VIP kerak", show_alert=True)
    await c.answer()
    cap = f"<b>{esc(m['title'])}</b>\n{p['season']}-fasl • {p['number']}-{ {'ep': 'qism', 'film': 'film', 'ova': 'OVA'}[p['ptype']] }"
    await send_part(c.bot, c.message.chat.id, c.from_user.id, p, cap)


@user.callback_query(F.data.startswith("r:"))
async def cb_rate(c: CallbackQuery):
    _, mid, sc = c.data.split(":")
    run("INSERT INTO ratings VALUES(?,?,?) ON CONFLICT(user_id,media_id) DO UPDATE SET score=excluded.score",
        c.from_user.id, int(mid), int(sc))
    await c.answer(f"Baholandi: {sc} 🌟")


@user.callback_query(F.data.startswith("l:"))
async def cb_later(c: CallbackQuery):
    mid = int(c.data[2:])
    if one("SELECT 1 FROM later WHERE user_id=? AND media_id=?", c.from_user.id, mid):
        run("DELETE FROM later WHERE user_id=? AND media_id=?", c.from_user.id, mid)
        await c.answer("Olib tashlandi")
    else:
        run("INSERT INTO later VALUES(?,?)", c.from_user.id, mid)
        await c.answer("Saqlandi ⏳")
    try:
        await c.message.edit_reply_markup(reply_markup=IM(inline_keyboard=action_rows(c.from_user.id, mid)))
    except Exception:
        pass


@user.callback_query(F.data.startswith("k:"))
async def cb_cat(c: CallbackQuery):
    _, kind, page = c.data.split(":")
    page = int(page)
    if kind == "all":
        rows = many("SELECT * FROM media ORDER BY id DESC LIMIT 10 OFFSET ?", page * 10)
    else:
        rows = many("SELECT * FROM media WHERE kind=? ORDER BY id DESC LIMIT 10 OFFSET ?", kind, page * 10)
    nav = []
    if page > 0:
        nav.append(IB(text="⬅️", callback_data=f"k:{kind}:{page - 1}"))
    if len(rows) == 10:
        nav.append(IB(text="➡️", callback_data=f"k:{kind}:{page + 1}"))
    await c.message.edit_text(f"{KINDS.get(kind, '🗂 Hammasi')} — {page + 1}-sahifa",
                              reply_markup=list_kb(rows, nav))
    await c.answer()


@user.message(F.text == B_CAT, StateFilter(None))
async def menu_cat(m: Message):
    kb = [[IB(text=v, callback_data=f"k:{k}:0")] for k, v in KINDS.items()]
    kb.append([IB(text="🗂 Hammasi", callback_data="k:all:0")])
    await m.answer("Bo'limni tanlang:", reply_markup=IM(inline_keyboard=kb))


@user.message(F.text == B_SEARCH, StateFilter(None))
async def menu_search(m: Message):
    await m.answer("Nom yoki ID yuboring, yoki inline qidiruvdan foydalaning:",
                   reply_markup=IM(inline_keyboard=[[IB(text="🔎 Inline qidiruv", switch_inline_query_current_chat="")]]))


@user.message(F.text == B_RANDOM, StateFilter(None))
async def menu_random(m: Message):
    r = one("SELECT id FROM media ORDER BY RANDOM() LIMIT 1")
    if not r:
        return await m.answer("Hali kontent yo'q.")
    await show_media(m.bot, m.chat.id, m.from_user.id, r["id"])


@user.message(F.text == B_LATER, StateFilter(None))
async def menu_later(m: Message):
    rows = many("SELECT media.* FROM later JOIN media ON media.id=later.media_id WHERE later.user_id=?", m.from_user.id)
    await m.answer("⏳ Keyinroq ko'raman:" if rows else "Ro'yxat bo'sh.", reply_markup=list_kb(rows) if rows else None)


@user.message(F.text == B_STATS, StateFilter(None))
async def menu_stats(m: Message):
    rows = many("SELECT media.kind k, COUNT(*) c FROM history JOIN media ON media.id=history.media_id "
                "WHERE history.user_id=? GROUP BY media.kind", m.from_user.id)
    if not rows:
        return await m.answer("Hali hech narsa ko'rmagansiz.")
    await m.answer("📊 <b>Ko'rilganlar</b>\n" + "\n".join(f"{KINDS[r['k']]}: {r['c']}" for r in rows))


@user.message(F.text == B_VIP, StateFilter(None))
async def menu_vip(m: Message):
    u = one("SELECT vip_until FROM users WHERE id=?", m.from_user.id)
    active = u and u["vip_until"] > time.time()
    st = f"✅ {time.strftime('%d.%m.%Y', time.localtime(u['vip_until']))} gacha" if active else "❌ faol emas"
    await m.answer(f"💎 <b>VIP kabinet</b>\n\nHolat: {st}\n\n{get_s('vip_info') or 'Narxlar uchun admin bilan bog‘laning.'}")


@user.message(F.text, StateFilter(None), ~F.text.startswith("/"))
async def search(m: Message):
    uid = m.from_user.id
    if not await gate(m.bot, uid, m.chat.id):
        return
    t = m.text.strip()
    if t.isdigit():
        return await show_media(m.bot, m.chat.id, uid, int(t))
    rows = many("SELECT * FROM media WHERE title LIKE ? ORDER BY id DESC LIMIT 20", f"%{t}%")
    await m.answer("🔎 Natijalar:" if rows else "❌ Hech narsa topilmadi.", reply_markup=list_kb(rows) if rows else None)


@user.inline_query()
async def inline(q: InlineQuery):
    t = q.query.strip()
    if t.isdigit():
        rows = many("SELECT * FROM media WHERE id=?", int(t))
    elif t:
        rows = many("SELECT * FROM media WHERE title LIKE ? LIMIT 20", f"%{t}%")
    else:
        rows = many("SELECT * FROM media ORDER BY id DESC LIMIT 20")
    res = [InlineQueryResultArticle(
        id=str(r["id"]), title=("💎 " if r["vip"] else "") + r["title"],
        description=f"{KINDS[r['kind']]} • ID {r['id']}",
        input_message_content=InputTextMessageContent(
            message_text=f"<b>{esc(r['title'])}</b>\n▶️ https://t.me/{BOT_USERNAME}?start=m{r['id']}"))
        for r in rows]
    await q.answer(res, cache_time=5)


# ---------------------------------------------------------------- Admin
class AdminF(BaseFilter):
    async def __call__(self, event):
        return is_admin(event.from_user.id)


adm = Router()
adm.message.filter(AdminF())
adm.callback_query.filter(AdminF())


class AddM(StatesGroup):
    title = State(); descr = State(); poster = State()
class Up(StatesGroup):
    pick = State(); active = State()
class Dl(StatesGroup):
    media = State(); part = State()
class Bc(StatesGroup):
    target = State(); msg = State()
class SubS(StatesGroup):
    ref = State(); url = State(); lim = State()
class PostAdd(StatesGroup):
    ref = State()
class PostS(StatesGroup):
    mid = State(); extra = State(); pick = State()
class AdmS(StatesGroup):
    edit = State()


def admin_menu():
    b = lambda t, d: [IB(text=t, callback_data=d)]
    return IM(inline_keyboard=[
        [IB(text="➕ Media qo'shish", callback_data="a:add"), IB(text="📤 Qism yuklash", callback_data="a:up")],
        [IB(text="🗑 O'chirish", callback_data="a:del"), IB(text="📊 Statistika", callback_data="a:stat")],
        [IB(text="📣 Xabar yuborish", callback_data="a:bc"), IB(text="📡 Majburiy obuna", callback_data="a:sub")],
        [IB(text="📢 Post kanallari", callback_data="a:post"), IB(text="👮 Adminlar", callback_data="a:adm")],
        [IB(text=f"🔒 Video uzatish: {'o‘chiq' if protect() else 'yoqiq'}", callback_data="a:prot"),
         IB(text=f"🤖 Avto-post: {'yoqiq' if get_s('autopost') == '1' else 'o‘chiq'}", callback_data="a:auto")],
    ])


@adm.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("👮 <b>Admin panel</b>\n\nVIP: /vip user_id kun • /vipinfo matn • /vipmedia media_id 1|0\n"
                   "Bekor qilish: /cancel", reply_markup=admin_menu())


@adm.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.", reply_markup=admin_menu())


@adm.message(Command("vip"), F.text)
async def vip_cmd(m: Message, command: CommandObject):
    try:
        uid, days = map(int, command.args.split())
    except Exception:
        return await m.answer("Format: /vip user_id kun")
    u = one("SELECT vip_until FROM users WHERE id=?", uid)
    base = max(time.time(), u["vip_until"]) if u else time.time()
    run("INSERT OR IGNORE INTO users(id,joined) VALUES(?,?)", uid, int(time.time()))
    run("UPDATE users SET vip_until=? WHERE id=?", int(base + days * 86400), uid)
    await m.answer("✅ VIP berildi.")
    try:
        await m.bot.send_message(uid, f"💎 Sizga {days} kunlik VIP berildi!")
    except Exception:
        pass


@adm.message(Command("vipinfo"))
async def vipinfo(m: Message, command: CommandObject):
    set_s("vip_info", command.args or "")
    await m.answer("✅ VIP matni yangilandi.")


@adm.message(Command("vipmedia"))
async def vipmedia(m: Message, command: CommandObject):
    try:
        mid, v = map(int, command.args.split())
    except Exception:
        return await m.answer("Format: /vipmedia media_id 1|0")
    run("UPDATE media SET vip=? WHERE id=?", v, mid)
    await m.answer("✅ Yangilandi.")


@adm.callback_query(F.data == "a:prot")
async def a_prot(c: CallbackQuery):
    set_s("protect", "0" if protect() else "1")
    await c.message.edit_reply_markup(reply_markup=admin_menu())
    await c.answer()


@adm.callback_query(F.data == "a:auto")
async def a_auto(c: CallbackQuery):
    set_s("autopost", "0" if get_s("autopost") == "1" else "1")
    await c.message.edit_reply_markup(reply_markup=admin_menu())
    await c.answer()


@adm.callback_query(F.data == "a:stat")
async def a_stat(c: CallbackQuery):
    kinds = "\n".join(f"{v}: {one('SELECT COUNT(*) c FROM media WHERE kind=?', k)['c']}" for k, v in KINDS.items())
    await c.message.answer(
        f"📊 <b>Statistika</b>\n👥 Foydalanuvchilar: {one('SELECT COUNT(*) c FROM users')['c']}\n"
        f"💎 VIP: {one('SELECT COUNT(*) c FROM users WHERE vip_until>?', time.time())['c']}\n"
        f"{kinds}\n📼 Qismlar: {one('SELECT COUNT(*) c FROM parts')['c']}\n"
        f"👁 Ko'rishlar: {one('SELECT COALESCE(SUM(views),0) c FROM media')['c']}")
    await c.answer()


# ---- Media qo'shish
@adm.callback_query(F.data == "a:add")
async def a_add(c: CallbackQuery):
    kb = [[IB(text="🎬 Kino", callback_data="ak:kino:film"), IB(text="📺 Serial", callback_data="ak:serial:serial")],
          [IB(text="🌸 Anime", callback_data="ak:anime:serial"), IB(text="🎭 Drama", callback_data="ak:drama:serial")],
          [IB(text="🧸 Multfilm (film)", callback_data="ak:multfilm:film"),
           IB(text="🧸 Multfilm (serial)", callback_data="ak:multfilm:serial")]]
    await c.message.answer("Yo'nalishni tanlang:", reply_markup=IM(inline_keyboard=kb))
    await c.answer()


@adm.callback_query(F.data.startswith("ak:"))
async def a_kind(c: CallbackQuery, state: FSMContext):
    _, kind, fmt = c.data.split(":")
    await state.update_data(kind=kind, fmt=fmt)
    await state.set_state(AddM.title)
    await c.message.answer("Nomini yuboring:")
    await c.answer()


@adm.message(AddM.title, F.text)
async def a_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text)
    await state.set_state(AddM.descr)
    await m.answer("Tavsifini yuboring (yoki /skip):")


@adm.message(AddM.descr, F.text)
async def a_descr(m: Message, state: FSMContext):
    await state.update_data(descr="" if m.text == "/skip" else m.text)
    await state.set_state(AddM.poster)
    await m.answer("Poster rasmini yuboring (yoki /skip):")


@adm.message(AddM.poster)
async def a_poster(m: Message, state: FSMContext):
    poster = m.photo[-1].file_id if m.photo else None
    if not poster and m.text != "/skip":
        return await m.answer("Rasm yuboring yoki /skip")
    await state.update_data(poster=poster)
    await m.answer("VIP bo'limga yuklansinmi?", reply_markup=IM(inline_keyboard=[[
        IB(text="💎 Ha", callback_data="av:1"), IB(text="Yo'q", callback_data="av:0")]]))


@adm.callback_query(F.data.startswith("av:"))
async def a_vipchoice(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "title" not in d:
        return await c.answer()
    cur = run("INSERT INTO media(kind,fmt,title,descr,poster,vip) VALUES(?,?,?,?,?,?)",
              d["kind"], d["fmt"], d["title"], d["descr"], d["poster"], int(c.data[3:]))
    await c.answer()
    await begin_upload(c.message, state, cur.lastrowid, 1)


def up_kb():
    return IM(inline_keyboard=[
        [IB(text="📺 Qism", callback_data="up:ep"), IB(text="🎬 Film", callback_data="up:film"),
         IB(text="📼 OVA", callback_data="up:ova")],
        [IB(text="➕ Yangi fasl", callback_data="up:ns"), IB(text="✅ Tugatish", callback_data="up:done")]])


async def begin_upload(msg, state, mid, season):
    await state.clear()
    await state.set_state(Up.active)
    await state.update_data(mid=mid, season=season, ptype="ep")
    m = one("SELECT * FROM media WHERE id=?", mid)
    await msg.answer(f"📤 <b>{esc(m['title'])}</b> (ID {mid})\nVideo/fayllarni ketma-ket yuboring — avtomatik qabul qilinadi.\n"
                     f"Raqam yuborsangiz fasl o'zgaradi. Hozir: {season}-fasl, tur: qism.", reply_markup=up_kb())


@adm.callback_query(F.data == "a:up")
async def a_up(c: CallbackQuery, state: FSMContext):
    await state.set_state(Up.pick)
    await c.message.answer("Media ID sini yuboring:")
    await c.answer()


@adm.message(Up.pick, F.text)
async def up_pick(m: Message, state: FSMContext):
    if not m.text.isdigit() or not one("SELECT 1 FROM media WHERE id=?", int(m.text)):
        return await m.answer("Topilmadi. ID ni tekshiring.")
    mx = one("SELECT COALESCE(MAX(season),1) s FROM parts WHERE media_id=?", int(m.text))["s"]
    await begin_upload(m, state, int(m.text), mx)


@adm.callback_query(F.data.startswith("up:"), Up.active)
async def up_ctl(c: CallbackQuery, state: FSMContext):
    act = c.data[3:]
    d = await state.get_data()
    if act == "done":
        await state.clear()
        await c.message.answer("✅ Tugadi.", reply_markup=admin_menu())
        return await c.answer()
    if act == "ns":
        mx = one("SELECT COALESCE(MAX(season),0) s FROM parts WHERE media_id=?", d["mid"])["s"]
        await state.update_data(season=max(mx, d["season"]) + 1, ptype="ep")
        await c.answer(f"{max(mx, d['season']) + 1}-fasl")
    else:
        await state.update_data(ptype=act)
        await c.answer({"ep": "Qism", "film": "Film", "ova": "OVA"}[act])


@adm.message(Up.active, F.text.regexp(r"^\d+$"))
async def up_season(m: Message, state: FSMContext):
    await state.update_data(season=int(m.text), ptype="ep")
    await m.answer(f"✅ Fasl: {m.text}")


@adm.message(Up.active, F.video | F.document)
async def up_file(m: Message, state: FSMContext):
    d = await state.get_data()
    fid, ft = (m.video.file_id, "video") if m.video else (m.document.file_id, "document")
    n = one("SELECT COALESCE(MAX(number),0)+1 n FROM parts WHERE media_id=? AND season=?", d["mid"], d["season"])["n"]
    run("INSERT INTO parts(media_id,season,number,ptype,file_id,ftype) VALUES(?,?,?,?,?,?)",
        d["mid"], d["season"], n, d["ptype"], fid, ft)
    await m.reply(f"✅ {d['season']}-fasl, {n} ({d['ptype']})")
    media = one("SELECT * FROM media WHERE id=?", d["mid"])
    if get_s("autopost") == "1":
        await post_media(m.bot, d["mid"], auto=True,
                         note=f"🆕 {d['season']}-fasl {n}-{'qism' if d['ptype'] == 'ep' else d['ptype']}")
    if media["fmt"] == "film":
        await state.clear()
        await m.answer("✅ Film saqlandi.", reply_markup=admin_menu())


# ---- O'chirish
@adm.callback_query(F.data == "a:del")
async def a_del(c: CallbackQuery):
    kb = [[IB(text="🗑 Butun media", callback_data="d:media")], [IB(text="🗑 Alohida qism", callback_data="d:part")]]
    await c.message.answer("Nimani o'chiramiz?", reply_markup=IM(inline_keyboard=kb))
    await c.answer()


@adm.callback_query(F.data.startswith("d:"))
async def d_pick(c: CallbackQuery, state: FSMContext):
    if c.data == "d:media":
        await state.set_state(Dl.media)
        await c.message.answer("Media ID sini yuboring:")
    else:
        await state.set_state(Dl.part)
        await c.message.answer("Format: <code>media_id fasl qism</code>  (masalan: 12 1 5)")
    await c.answer()


@adm.message(Dl.media, F.text)
async def d_media(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("ID raqam bo'lishi kerak.")
    i = int(m.text)
    for t, col in (("parts", "media_id"), ("ratings", "media_id"), ("later", "media_id"), ("history", "media_id"), ("media", "id")):
        run(f"DELETE FROM {t} WHERE {col}=?", i)
    await state.clear()
    await m.answer("✅ O'chirildi.")


@adm.message(Dl.part, F.text)
async def d_part(m: Message, state: FSMContext):
    try:
        mid, s, n = map(int, m.text.split())
    except Exception:
        return await m.answer("Format: media_id fasl qism")
    cur = run("DELETE FROM parts WHERE media_id=? AND season=? AND number=?", mid, s, n)
    await state.clear()
    await m.answer("✅ O'chirildi." if cur.rowcount else "Topilmadi.")


# ---- Xabar yuborish
@adm.callback_query(F.data == "a:bc")
async def a_bc(c: CallbackQuery):
    kb = [[IB(text="↪️ Forward", callback_data="bm:fwd"), IB(text="🤖 Bot nomida", callback_data="bm:bot")],
          [IB(text="👤 Bitta foydalanuvchiga", callback_data="bm:one")]]
    await c.message.answer("Usulni tanlang:", reply_markup=IM(inline_keyboard=kb))
    await c.answer()


@adm.callback_query(F.data.startswith("bm:"))
async def bc_mode(c: CallbackQuery, state: FSMContext):
    mode = c.data[3:]
    await state.update_data(mode=mode)
    if mode == "one":
        await state.set_state(Bc.target)
        await c.message.answer("Foydalanuvchi ID sini yuboring:")
    else:
        await state.set_state(Bc.msg)
        await c.message.answer("Xabarni yuboring:")
    await c.answer()


@adm.message(Bc.target, F.text)
async def bc_target(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("ID raqam bo'lishi kerak.")
    await state.update_data(target=int(m.text))
    await state.set_state(Bc.msg)
    await m.answer("Xabarni yuboring:")


@adm.message(Bc.msg)
async def bc_send(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    targets = [d["target"]] if d["mode"] == "one" else [r["id"] for r in many("SELECT id FROM users")]
    ok = 0
    for t in targets:
        try:
            if d["mode"] == "fwd":
                await m.bot.forward_message(t, m.chat.id, m.message_id)
            else:
                await m.bot.copy_message(t, m.chat.id, m.message_id)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await m.answer(f"✅ Yuborildi: {ok}/{len(targets)}", reply_markup=admin_menu())


# ---- Majburiy obuna
@adm.callback_query(F.data == "a:sub")
async def a_sub(c: CallbackQuery):
    rows = many("SELECT * FROM channels")
    kb = [[IB(text=f"🗑 {r['title']} [{r['kind']}] {r['joined']}/{r['lim'] or '∞'}", callback_data=f"sd:{r['id']}")] for r in rows]
    kb += [[IB(text="🌐 Ommaviy", callback_data="sk:public"), IB(text="🔒 Maxfiy", callback_data="sk:private")],
           [IB(text="📝 Zayafka", callback_data="sk:request"), IB(text="🔗 Boshqa (Instagram...)", callback_data="sk:external")]]
    await c.message.answer("📡 Majburiy obuna. Qo'shish uchun turini tanlang (o'chirish uchun ustiga bosing):",
                           reply_markup=IM(inline_keyboard=kb))
    await c.answer()


@adm.callback_query(F.data.startswith("sd:"))
async def sub_del(c: CallbackQuery):
    run("DELETE FROM channels WHERE id=?", int(c.data[3:]))
    await c.answer("O'chirildi", show_alert=True)


@adm.callback_query(F.data.startswith("sk:"))
async def sub_kind(c: CallbackQuery, state: FSMContext):
    kind = c.data[3:]
    await state.update_data(kind=kind)
    await state.set_state(SubS.ref)
    await c.message.answer("Nom yuboring:" if kind == "external" else
                           "Kanal @username yoki ID sini yuboring (bot kanalda admin bo'lishi shart):")
    await c.answer()


@adm.message(SubS.ref, F.text)
async def sub_ref(m: Message, state: FSMContext):
    d = await state.get_data()
    if d["kind"] == "external":
        await state.update_data(title=m.text, chat_id="")
    else:
        try:
            ch = await m.bot.get_chat(cid(m.text.strip()))
        except Exception as e:
            return await m.answer(f"❌ Kanal topilmadi yoki bot admin emas: {e}")
        await state.update_data(title=ch.title, chat_id=str(ch.id))
        if d["kind"] == "public" and ch.username:
            await state.update_data(url=f"https://t.me/{ch.username}")
            await state.set_state(SubS.lim)
            return await m.answer("Nechta odam qo'shilishi kerak? (0 = cheksiz)")
    await state.set_state(SubS.url)
    await m.answer("Havolani yuboring (zayafka/maxfiy uchun taklif havolasi):")


@adm.message(SubS.url, F.text)
async def sub_url(m: Message, state: FSMContext):
    await state.update_data(url=m.text.strip())
    await state.set_state(SubS.lim)
    await m.answer("Nechta odam qo'shilishi kerak? (0 = cheksiz)")


@adm.message(SubS.lim, F.text)
async def sub_lim(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("Raqam yuboring.")
    d = await state.get_data()
    run("INSERT INTO channels(kind,chat_id,title,url,lim) VALUES(?,?,?,?,?)",
        d["kind"], d["chat_id"], d["title"], d["url"], int(m.text))
    await state.clear()
    await m.answer("✅ Qo'shildi.", reply_markup=admin_menu())


# ---- Post kanallari
@adm.callback_query(F.data == "a:post")
async def a_post(c: CallbackQuery):
    rows = many("SELECT * FROM post_ch")
    kb = [[IB(text=f"🗑 {r['title']} [{r['kind']}]", callback_data=f"pcd:{r['id']}")] for r in rows]
    kb += [[IB(text="➕ Kanal qo'shish", callback_data="pca"), IB(text="📤 Post tashlash", callback_data="pgo")]]
    await c.message.answer("📢 Post kanallari (maxfiy kanal ham bo'ladi):", reply_markup=IM(inline_keyboard=kb))
    await c.answer()


@adm.callback_query(F.data.startswith("pcd:"))
async def pc_del(c: CallbackQuery):
    run("DELETE FROM post_ch WHERE id=?", int(c.data[4:]))
    await c.answer("O'chirildi", show_alert=True)


@adm.callback_query(F.data == "pca")
async def pc_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(PostAdd.ref)
    await c.message.answer("Kanal @username yoki ID (bot admin bo'lishi shart):")
    await c.answer()


@adm.message(PostAdd.ref, F.text)
async def pc_ref(m: Message, state: FSMContext):
    try:
        ch = await m.bot.get_chat(cid(m.text.strip()))
    except Exception as e:
        return await m.answer(f"❌ {e}")
    await state.update_data(chat_id=str(ch.id), title=ch.title)
    kb = [[IB(text="Hammasi", callback_data="pk:all")]] + \
         [[IB(text=v, callback_data=f"pk:{k}")] for k, v in KINDS.items()]
    await m.answer("Qaysi yo'nalish shu kanalga tushsin?", reply_markup=IM(inline_keyboard=kb))


@adm.callback_query(F.data.startswith("pk:"))
async def pc_kind(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "chat_id" not in d:
        return await c.answer()
    run("INSERT INTO post_ch(chat_id,title,kind) VALUES(?,?,?)", d["chat_id"], d["title"], c.data[3:])
    await state.clear()
    await c.message.answer("✅ Qo'shildi.")
    await c.answer()


async def post_media(bot, mid, chat_ids=None, extra=None, note="", auto=False):
    m = one("SELECT * FROM media WHERE id=?", mid)
    if chat_ids is None:
        chat_ids = [r["chat_id"] for r in many("SELECT * FROM post_ch WHERE kind IN ('all',?)", m["kind"])]
    kb = [[IB(text="▶️ Tomosha qilish", url=f"https://t.me/{BOT_USERNAME}?start=m{mid}")]]
    for t, u in (extra or [])[:20]:
        kb.append([IB(text=t, url=u)])
    text = (f"{note}\n\n" if note else "") + card_text(m).split("\n\n")[0] + "\n\n" + esc(m["descr"] or "")
    for ch in chat_ids:
        try:
            if m["poster"]:
                await bot.send_photo(cid(ch), m["poster"], caption=text[:1024], reply_markup=IM(inline_keyboard=kb))
            else:
                await bot.send_message(cid(ch), text, reply_markup=IM(inline_keyboard=kb))
        except Exception as e:
            logging.warning("post xato %s: %s", ch, e)


@adm.callback_query(F.data == "pgo")
async def pc_go(c: CallbackQuery, state: FSMContext):
    await state.set_state(PostS.mid)
    await c.message.answer("Post qilinadigan media ID:")
    await c.answer()


@adm.message(PostS.mid, F.text)
async def ps_mid(m: Message, state: FSMContext):
    if not m.text.isdigit() or not one("SELECT 1 FROM media WHERE id=?", int(m.text)):
        return await m.answer("Topilmadi.")
    await state.update_data(mid=int(m.text), sel=[])
    await state.set_state(PostS.extra)
    await m.answer("Qo'shimcha havolalar (1–20): har qatorda <code>matn - url</code>. Kerak bo'lmasa /skip")


@adm.message(PostS.extra, F.text)
async def ps_extra(m: Message, state: FSMContext):
    extra = []
    if m.text != "/skip":
        for line in m.text.splitlines()[:20]:
            if " - " in line:
                t, u = line.rsplit(" - ", 1)
                extra.append((t.strip(), u.strip()))
    await state.update_data(extra=extra)
    await state.set_state(PostS.pick)
    await m.answer("Kanallarni tanlang:", reply_markup=await pick_kb(state))


async def pick_kb(state):
    sel = (await state.get_data()).get("sel", [])
    kb = [[IB(text=("✅ " if str(r["id"]) in sel else "▫️ ") + r["title"], callback_data=f"pt:{r['id']}")]
          for r in many("SELECT * FROM post_ch")]
    kb.append([IB(text="📤 Yuborish", callback_data="pt:go")])
    return IM(inline_keyboard=kb)


@adm.callback_query(F.data.startswith("pt:"), PostS.pick)
async def ps_pick(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if c.data == "pt:go":
        ids = [one("SELECT chat_id FROM post_ch WHERE id=?", int(i))["chat_id"] for i in d["sel"]]
        await post_media(c.bot, d["mid"], ids, d.get("extra"))
        await state.clear()
        await c.message.answer(f"✅ {len(ids)} ta kanalga yuborildi.")
        return await c.answer()
    sel = d["sel"]
    i = c.data[3:]
    sel.remove(i) if i in sel else sel.append(i)
    await state.update_data(sel=sel)
    await c.message.edit_reply_markup(reply_markup=await pick_kb(state))
    await c.answer()


# ---- Adminlar
@adm.callback_query(F.data == "a:adm")
async def a_adm(c: CallbackQuery, state: FSMContext):
    ids = [str(r["id"]) for r in many("SELECT id FROM admins")]
    await state.set_state(AdmS.edit)
    await c.message.answer(f"👮 Adminlar: {', '.join(ids) or 'yo‘q'}\n\n<code>+ID</code> qo'shish, <code>-ID</code> olib tashlash:")
    await c.answer()


@adm.message(AdmS.edit, F.text)
async def adm_edit(m: Message, state: FSMContext):
    t = m.text.strip()
    if t[:1] in "+-" and t[1:].isdigit():
        if t[0] == "+":
            run("INSERT OR IGNORE INTO admins VALUES(?)", int(t[1:]))
        else:
            run("DELETE FROM admins WHERE id=?", int(t[1:]))
        await state.clear()
        return await m.answer("✅ Bajarildi.")
    await m.answer("Format: +123456 yoki -123456")


# ---------------------------------------------------------------- Ishga tushirish
async def main():
    global BOT_USERNAME
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(adm)
    dp.include_router(user)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "inline_query", "chat_join_request"])


if __name__ == "__main__":
    asyncio.run(main())
