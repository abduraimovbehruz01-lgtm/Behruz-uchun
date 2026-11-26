"""Hamkor manbalar: ruxsat bergan tarjimon jamoa yoki muallifning Telegram kanali.

Kanal egasi botni o'z kanaliga admin qiladi, admin uni «🔗 Manbalar» bo'limida mangaga bog'laydi.
Shundan keyin kanalga tashlangan har bir bob — ZIP/CBZ/PDF fayl, rasmlar albomi yoki Telegraph havolasi —
avtomatik olinadi, kerak bo'lsa tarjima qilinadi va botda bepul chop etiladi (reklama kanaliga ham chiqadi).

Ishonchlilik: navbat va yig'ilayotgan albomlar bazada saqlanadi (server qayta ishga tushsa ham yo'qolmaydi);
bor yoki takroriy bob qayta olinmaydi; tarjima sifati past bo'lsa — qoralama qilib adminlarga yuboriladi;
Gemini limiti tugasa — bob navbatda qoladi va keyinroq qayta urinadi; kunlik chegara bor."""
import asyncio
import os
import re
import time
from urllib.parse import quote

import aiohttp
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import catalog
import chapters as chapfiles
import promo
from core import (CAT, MAX_DOWNLOAD, MAX_PAGES, STATE as BOT, admin_keyboard, bot, download, download_to, esc, http,
                  jlog, kb, log, safe_edit, storage_chat, upload_photos, user_tmp)

router = Router(name="autosrc")
fmt = catalog.fmt_num
IDLE = 120                 # albom: oxirgi rasmdan keyin shuncha soniya o'tsa — bob tugadi deb hisoblanadi
MIN_PHOTOS = 3             # bob raqami yozilmagan va bundan kam rasm bo'lsa — e'lon rasmi deb o'tkazib yuboriladi
RETRY_AFTER = 2 * 3600     # Gemini limiti yoki tarmoq xatosida qayta urinish oralig'i
MAX_TRIES = 12            # 12 × 2 soat — Gemini kunlik limiti tiklanguncha yetadi
MAX_IMAGE = 20 * 1024 * 1024
QUEUE: list[dict] = []     # navbatdagi boblar
ALBUMS: dict[str, dict] = {}   # kanal → yig'ilayotgan rasmlar (bob)
HINTS: dict[str, tuple] = {}   # kanal → (bob raqami, vaqt, manba) — rasmlardan oldin kelgan matnli post
STATE = {"worker": None, "day": "", "done": 0, "pending": False}
TELEGRAPH = re.compile(r"https?://(?:telegra\.ph|graph\.org)/([^\s/?#<>\"'()]+)")
CANCEL = ("❌ Bekor qilish", "adm:cancel")


# ---------------------------------------------------------------- manbalar ro'yxati

def sources() -> list:
    return [s for s in CAT.cfg.get("sources") or [] if isinstance(s, dict) and s.get("id")]


def get_src(sid):
    return next((s for s in sources() if s["id"] == sid), None)


def for_chat(chat_id) -> list:
    return [s for s in sources() if str(s.get("chat")) == str(chat_id)]


async def save_sources(lst):
    await CAT.set_cfg(sources=lst)


async def update_src(sid, **fields):
    lst = sources()
    for s in lst:
        if s["id"] == sid:
            s.update(fields)
    await save_sources(lst)


def pick_source(chat_id, text):
    """Kanal postini qaysi mangaga yozish: kalit so'zi matnda uchragan manba, bo'lmasa — kalit so'zsiz manba."""
    cands = for_chat(chat_id)
    if not cands:
        return None
    t = f" {catalog.norm(text or '')} "
    keyed = [s for s in cands if s.get("kw") and catalog.norm(s["kw"]) and f" {catalog.norm(s['kw'])} " in t]
    if keyed:
        return max(keyed, key=lambda s: len(s["kw"]))
    plain = [s for s in cands if not s.get("kw")]
    return plain[0] if plain else None


async def notify(src, text, markup=None):
    """Manbani ulagan admin va bosh adminlarga xabar."""
    targets = set(CAT.owners)
    if src and src.get("by"):
        targets.add(int(src["by"]))
    for uid in targets:
        try:
            await bot.send_message(uid, text, reply_markup=markup)
        except Exception:
            pass


# ---------------------------------------------------------------- saqlash (server qayta ishga tushsa ham yo'qolmaydi)

async def load():
    d = await CAT.s.get("settings", "autoq") or {}
    QUEUE[:] = [x for x in d.get("items") or [] if isinstance(x, dict)]
    ALBUMS.clear()
    ALBUMS.update({str(k): v for k, v in (d.get("albums") or {}).items() if isinstance(v, dict)})
    STATE.update(day=d.get("day") or "", done=int(d.get("done") or 0))


async def persist():
    try:
        await CAT.s.set("settings", "autoq", {"items": QUEUE[-300:], "albums": ALBUMS, "day": STATE["day"],
                                              "done": STATE["done"]}, merge=False)
    except Exception as e:
        log.warning("avto-manba navbatini saqlab bo‘lmadi: %s", e)


async def persist_soon(delay=2.0):
    """Albom rasmlari ketma-ket keladi — har biri uchun emas, bir oz kutib bir marta saqlaymiz."""
    if STATE["pending"]:
        return
    STATE["pending"] = True

    async def later():
        await asyncio.sleep(delay)
        STATE["pending"] = False
        await persist()
    STATE["ptask"] = asyncio.create_task(later())


# ---------------------------------------------------------------- kanal postlari

def post_urls(msg) -> list:
    """Postdagi Telegraph havolalari: matnda, yashirin havolada yoki tugmada."""
    found = [m.group(0) for m in TELEGRAPH.finditer(msg.text or msg.caption or "")]
    for ent in list(getattr(msg, "entities", None) or []) + list(getattr(msg, "caption_entities", None) or []):
        if getattr(ent, "url", None):
            found.append(ent.url)
    for row in getattr(getattr(msg, "reply_markup", None), "inline_keyboard", None) or []:
        for b in row:
            if getattr(b, "url", None):
                found.append(b.url)
    out = []
    for u in found:
        if TELEGRAPH.search(u) and u not in out:
            out.append(u)
    return out


@router.channel_post()
async def on_channel_post(msg: Message):
    chat_id = msg.chat.id
    if not for_chat(chat_id):
        return
    text = (msg.caption or msg.text or "").strip()
    src = pick_source(chat_id, text)
    if src is None:
        return
    links = post_urls(msg)
    if links:
        num = chapfiles.guess_num(text, strict=True) if len(links) == 1 else None
        for u in links[:10]:
            await enqueue({"kind": "telegraph", "url": u, "src": src["id"], "caption": text[:300], "num": num})
        return
    doc = getattr(msg, "document", None)
    if doc is not None:
        name, mime = doc.file_name or "bob", doc.mime_type or ""
        kind = chapfiles.kind_of(name, mime)
        if kind in ("archive", "pdf"):
            if (doc.file_size or 0) > MAX_DOWNLOAD:
                await notify(src, f"📦 «{esc(src.get('title') or '')}» kanalidagi «{esc(name)}» 20 MB dan katta — Telegram "
                                  "botga bunday faylni bermaydi. Uni «📤 Bob qo‘shish» orqali havola bilan yuklang.")
                return
            num = chapfiles.guess_num(text, strict=True)
            if num is None:
                num = chapfiles.guess_num(name)
            await enqueue({"kind": "tg", "id": doc.file_id, "name": name, "mime": mime, "src": src["id"],
                           "caption": text[:300], "num": num})
        elif kind == "image" and (doc.file_size or 0) <= MAX_DOWNLOAD:
            await add_photo(msg, doc.file_id, src, text)
        return
    if getattr(msg, "photo", None):
        await add_photo(msg, msg.photo[-1].file_id, src, text)
        return
    if text:
        num = chapfiles.guess_num(text, strict=True)
        if num is None:
            return
        key = str(chat_id)
        buf = ALBUMS.get(key)
        if buf is not None and buf.get("num") is None and buf["src"] == src["id"] and time.time() - float(buf["t"]) < 30:
            buf["num"] = num            # «12-bob» degan matn rasmlardan darhol keyin keldi
            await persist_soon()
            return
        if buf is not None:
            await finalize(key)         # matn — yangi bob boshlanishi
        HINTS[key] = (num, time.time(), src["id"])


async def add_photo(msg, fid, src, text):
    """Kanal rasmlari bobga yig'iladi. Yangi bob boshlanishi: boshqa manba, boshqa bob raqami yoki raqamsiz
    rasmlardan keyin boshqa albomda raqamli izoh kelishi."""
    key = str(msg.chat.id)
    now = time.time()
    num = chapfiles.guess_num(text, strict=True) if text else None
    group = str(getattr(msg, "media_group_id", None) or "")
    buf = ALBUMS.get(key)
    if buf is not None:
        new = buf["src"] != src["id"]
        if num is not None and not new:
            if buf.get("num") is not None:
                new = num != buf["num"]
            else:
                new = bool(buf["photos"]) and not (group and group == buf.get("g"))
        if new:
            await finalize(key)         # yangi bob boshlandi — oldingisini yopamiz
            buf = None
    if buf is None:
        hint = HINTS.pop(key, None)
        if num is None and hint and now - hint[1] < 900 and hint[2] == src["id"]:
            num = hint[0]
        buf = ALBUMS[key] = {"src": src["id"], "num": num, "caption": text[:300], "photos": [], "t": now, "g": group}
    elif num is not None and buf.get("num") is None:
        buf["num"] = num
    buf["photos"].append({"m": int(msg.message_id), "f": fid})
    buf["t"] = now
    buf["g"] = group or buf.get("g") or ""
    await persist_soon()


async def finalize(key):
    buf = ALBUMS.pop(key, None)
    if not buf:
        return
    photos = [p["f"] for p in sorted(buf["photos"], key=lambda p: p["m"])]
    if len(photos) < MIN_PHOTOS and buf.get("num") is None:
        await persist()                 # bitta-ikkita raqamsiz rasm — bob emas (e'lon, muqova)
        return
    await enqueue({"kind": "photos", "photos": photos, "src": buf["src"], "caption": buf.get("caption", ""),
                   "num": buf.get("num")})


async def tick(now=None):
    """Rejalashtiruvchi har 20 soniyada chaqiradi: tugagan albomlarni yopadi, navbatni ishga tushiradi."""
    now = now or time.time()
    for key in [k for k, b in ALBUMS.items() if now - float(b.get("t") or 0) > IDLE]:
        await finalize(key)
    if any(float(it.get("wait") or 0) <= now for it in QUEUE):
        kick()


def busy() -> bool:
    t = STATE.get("worker")
    return bool(ALBUMS) or (t is not None and not t.done())


# ---------------------------------------------------------------- navbat

async def enqueue(item):
    item.setdefault("id", os.urandom(4).hex())
    item["added"] = time.time()
    QUEUE.append(item)
    await persist()
    kick()


def kick():
    t = STATE.get("worker")
    if t is None or t.done():
        STATE["worker"] = asyncio.create_task(worker())


async def worker():
    while True:
        now = time.time()
        item = next((it for it in QUEUE if float(it.get("wait") or 0) <= now), None)
        if item is None:
            return
        try:
            res = await process(item)
        except Exception:
            log.exception("avto-manba: bobni tayyorlashda xato")
            res = "retry"
        if res in ("retry", "later"):
            if res == "retry":
                item["tries"] = int(item.get("tries") or 0) + 1
            if int(item.get("tries") or 0) > MAX_TRIES:
                QUEUE.remove(item)
                await notify(get_src(item.get("src")), "❌ Hamkor kanaldan kelgan bobni bir necha marta urinib ham olib "
                                                     "bo‘lmadi — uni «📤 Bob qo‘shish» orqali qo‘lda yuklang.")
            else:
                item["wait"] = catalog.slot_ts(1, 0) if res == "later" else now + RETRY_AFTER
        elif item in QUEUE:
            QUEUE.remove(item)
        await persist()


async def telegraph_pages(url):
    m = TELEGRAPH.search(url or "")
    if not m:
        raise chapfiles.ChapterError("Telegraph havolasi noto‘g‘ri.")
    path = quote(m.group(1), safe="-_.%~")
    async with http().get(f"https://api.telegra.ph/getPage/{path}?return_content=true",
                          timeout=aiohttp.ClientTimeout(total=60)) as r:
        data = await r.json(content_type=None)
    if not (data or {}).get("ok"):
        raise chapfiles.ChapterError("Telegraph sahifasi ochilmadi.")
    res = data.get("result") or {}
    srcs = []

    def walk(nodes):
        for n in nodes or []:
            if isinstance(n, dict):
                if n.get("tag") == "img" and (n.get("attrs") or {}).get("src"):
                    srcs.append(n["attrs"]["src"])
                walk(n.get("children"))

    walk(res.get("content"))
    pages = []
    for i, s in enumerate(srcs[:MAX_PAGES]):
        u = s if s.startswith("http") else "https://telegra.ph" + (s if s.startswith("/") else "/" + s)
        async with http().get(u, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200 or int(r.headers.get("Content-Length") or 0) > MAX_IMAGE:
                continue
            raw = await r.read()
        if raw:
            pages.append((f"{i + 1:03d}.jpg", raw))
    return pages, len(srcs), res.get("title") or ""


async def load_item(it):
    """Navbatdagi bobning sahifalari: ([(nom, bayt)], jami_soni, sarlavha)."""
    kind = it["kind"]
    if kind == "tg":
        path = os.path.join(user_tmp("auto"), "manba_" + os.urandom(4).hex())
        try:
            await download_to(it["id"], path)
            pages, total = await asyncio.to_thread(chapfiles.extract, path, it.get("name", ""), it.get("mime", ""),
                                                   MAX_PAGES)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return pages, total, ""
    if kind == "photos":
        pages = []
        for i, fid in enumerate(it["photos"][:MAX_PAGES]):
            pages.append((f"{i + 1:03d}.jpg", await download(fid)))
        return pages, len(it["photos"]), ""
    if kind == "telegraph":
        return await telegraph_pages(it["url"])
    raise chapfiles.ChapterError("Noma’lum manba turi.")


async def process(it) -> str:
    """Bitta bobni oladi va chop etadi. Qaytaradi: 'done' | 'drop' | 'retry' | 'later'."""
    import admin
    src = get_src(it.get("src"))
    if src is None:
        return "drop"
    code = str(src.get("manga"))
    m = CAT.get(code)
    name = esc(src.get("title") or "kanal")
    if not m:
        await notify(src, f"⚠️ «{name}» manbasi bog‘langan manga (kod {code}) topilmadi — «🔗 Manbalar» dan tekshiring.")
        return "drop"
    if not storage_chat():
        if not it.get("nostore"):
            it["nostore"] = True
            await notify(src, "📦 Rasmlar ombori kanali ulanmagan — hamkor kanaldan kelgan bob navbatda turibdi. "
                              "Yopiq kanal oching va botni unga admin qiling.")
        return "retry"
    today = catalog.today()
    if STATE["day"] != today:
        STATE["day"], STATE["done"] = today, 0
    if STATE["done"] >= int(CAT.cfg.get("auto_daily") or 20):
        return "later"
    try:
        pages, _total, title = await load_item(it)
    except chapfiles.ChapterError as e:
        await notify(src, f"❌ «{name}» kanalidan bob olinmadi: {esc(str(e))}")
        return "drop"
    except Exception as e:
        log.warning("avto-manba: yuklab bo‘lmadi: %r", e)
        return "retry"
    num = it.get("num")
    if num is None and title:
        num = chapfiles.guess_num(title, strict=True)
    if it["kind"] == "telegraph" and (not pages or (num is None and len(pages) < MIN_PHOTOS)):
        await CAT.log_action(0, "🔗 Manba", f"⏭ «{src.get('title')}» dagi Telegraph sahifasi bob emas ({len(pages)} rasm)")
        return "drop"           # qoidalar, e'lon va h.k. sahifalar
    if not pages:
        await notify(src, f"❌ «{name}» kanalidagi postda rasm topilmadi.")
        return "drop"
    num = float(num) if num is not None else CAT.next_num(code)
    if CAT.chapter(code, num):
        await CAT.log_action(0, "🔗 Manba", f"⏭ {m['title']} — {fmt(num)}-bob allaqachon bor, «{src.get('title')}» dan olinmadi")
        return "drop"
    fp = await asyncio.to_thread(chapfiles.fingerprint, pages)
    twin = await CAT.find_duplicate(code, fp)
    if twin:
        await CAT.log_action(0, "🔗 Manba", f"♻️ {m['title']} — «{src.get('title')}» dagi bob {fmt(twin['num'])}-bob bilan "
                                           "bir xil, qayta olinmadi")
        return "drop"
    tr = bool(src.get("tr"))
    n_pages = len(pages)
    res = await admin.build_pages(-abs(int(src["chat"])), pages, tr, CAT.glossary_pairs(code) if tr else [], [],
                                  None, "", None, {"cancel": False})
    del pages
    if res["stop"]:
        if not it.get("warned"):
            it["warned"] = True
            await notify(src, f"⏳ «{name}» kanalidan kelgan {fmt(num)}-bob navbatda qoldi:\n{res['stop']}\n"
                              "Keyinroq o‘zim qayta urinaman.")
        return "retry"
    if not res["jpegs"]:
        await notify(src, f"❌ «{name}» kanalidagi bob rasmlarini ochib bo‘lmadi.")
        return "drop"
    broken = res["bad"] / max(1, n_pages) > 0.1
    untranslated = tr and res["skipped"] / max(1, n_pages) > 0.3
    weak = broken or untranslated
    ids = await upload_photos(res["jpegs"], storage_chat(), caption=f"{m['title']} · {fmt(num)}-bob (manba: {src.get('title')})")
    await CAT.save_chapter(code, num, ids, price=0, published=False, fp=fp)
    await CAT.set_chapter(code, num, credit=src.get("credit") or "", source=src["id"])
    STATE["done"] += 1
    await update_src(src["id"], count=int(src.get("count") or 0) + 1, last=time.time())
    if src.get("auto", True) and not weak:
        await CAT.publish(code, num, 0)
        sent, posted = await promo.announce(code, [num])
        await CAT.log_action(0, "🔗 Manba", f"✅ {m['title']} — {fmt(num)}-bob «{src.get('title')}» dan olindi va chop etildi")
        await notify(src, f"🔗 <b>{esc(m['title'])}</b> — {fmt(num)}-bob «{name}» kanalidan olindi va bepul chop etildi "
                          f"({len(ids)} rasm). 🔔 {sent} ta kuzatuvchiga xabar ketdi."
                          + (" 📣 Reklama kanaliga ham chiqdi." if posted else ""))
    else:
        why = (f"{res['bad']} ta sahifa ochilmadi" if broken else f"{res['skipped']} ta sahifa tarjima bo‘lmadi"
               if untranslated else "bu manbada avto chop etish o‘chiq")
        await CAT.log_action(0, "🔗 Manba", f"📝 {m['title']} — {fmt(num)}-bob «{src.get('title')}» dan olindi (qoralama)")
        await notify(src, f"📝 <b>{esc(m['title'])}</b> — {fmt(num)}-bob «{name}» kanalidan olindi, lekin qoralama qoldi: "
                          f"{why}.\nKo‘rib chiqib chop eting:", admin.publish_markup(code, num))
    return "done"


# ---------------------------------------------------------------- «🔗 Manbalar» bo'limi

class SrcAdd(StatesGroup):
    chat = State()
    manga = State()
    kw = State()
    tr = State()
    note = State()
    credit = State()


class SrcEdit(StatesGroup):
    value = State()


def adm(e) -> bool:
    return CAT.is_admin(e.from_user.id)


def can(e) -> bool:
    return CAT.has(e.from_user.id, "chapter")


def sources_view():
    lst = sources()
    lines = ["🔗 <b>Hamkor manbalar</b>", "",
             "Ruxsat bergan jamoa yoki muallif botni o‘z kanaliga admin qiladi. Kanalga tashlangan boblar — ZIP/CBZ/PDF, "
             "rasmlar albomi yoki Telegraph havolasi — botga avtomatik olinadi va bepul chop etiladi."]
    if lst:
        lines.append("")
        for i, x in enumerate(lst, 1):
            m = CAT.get(x.get("manga")) or {}
            lines.append(f"{i}. «{esc(x.get('title') or '')}» → {esc(m.get('title') or '?')} ({x.get('manga')})"
                         + (f" · 🔑 {esc(x['kw'])}" if x.get("kw") else "")
                         + (" · ⚡ avto" if x.get("auto", True) else " · 📝 qoralama")
                         + f" · {int(x.get('count') or 0)} bob")
    else:
        lines.append("\nHozircha manba yo‘q.")
    if QUEUE:
        lines.append(f"\n⏳ Navbatda: {len(QUEUE)} ta bob")
    rows = [[(f"⚙️ {i}. {(x.get('title') or '')[:28]}", f"src:v:{x['id']}")] for i, x in enumerate(lst, 1)]
    rows.append([("➕ Manba qo‘shish", "src:add")])
    return "\n".join(lines), kb(rows)


async def send_sources(uid):
    text, markup = sources_view()
    await bot.send_message(uid, text, reply_markup=markup)


def source_card(sid):
    x = get_src(sid)
    m = CAT.get(x.get("manga")) or {}
    last = f" · oxirgisi {catalog.when_text(x['last'])}" if x.get("last") else ""
    text = (f"🔗 <b>{esc(x.get('title') or '')}</b>" + (f" (@{esc(x['username'])})" if x.get("username") else "")
            + f"\n📚 Manga: {esc(m.get('title') or '?')} (kod {x.get('manga')})"
            + (f"\n🔑 Kalit so‘z: «{esc(x['kw'])}» — faqat shu so‘z bor postlar olinadi" if x.get("kw") else "")
            + f"\n🌐 Tarjima: {'qilinadi' if x.get('tr') else 'kerak emas (o‘zbekcha)'}"
            + f"\n⚡ Avto chop etish: {'✅ yoqiq (bepul)' if x.get('auto', True) else '❌ o‘chiq — qoralama bo‘ladi'}"
            + f"\n✍️ Yozuv: «{esc(x.get('credit') or '—')}»"
            + f"\n📝 Ruxsat: «{esc(x.get('note') or '—')}»"
            + f"\n📊 Olingan boblar: {int(x.get('count') or 0)}{last}")
    rows = [[("🌐 Tarjimani " + ("o‘chirish" if x.get("tr") else "yoqish"), f"src:t:{sid}:tr"),
             ("⚡ Avtoni " + ("o‘chirish" if x.get("auto", True) else "yoqish"), f"src:t:{sid}:auto")],
            [("✍️ Yozuv", f"src:e:{sid}:credit"), ("📝 Ruxsat izohi", f"src:e:{sid}:note")],
            [("🗑 O‘chirish", f"src:d:{sid}"), ("⬅️ Manbalar", "src:list")]]
    return text, kb(rows)


async def verify_chat(ref):
    """Kanal bormi va bot u yerda adminmi (kanal postlarini faqat admin bot ko'radi)."""
    try:
        chat = await bot.get_chat(ref)
    except Exception:
        return None, (f"🤷 Kanal topilmadi. Kanal egasi avval @{BOT['username']} ni kanalga admin qilishi kerak, keyin "
                      "kanaldan biror postni shu yerga forward qiling.")
    if str(getattr(chat.type, "value", chat.type)) != "channel":
        return None, "Bu kanal emas. Hamkor manba — Telegram kanal bo‘lishi kerak."
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        ok = str(getattr(me.status, "value", me.status)) in ("administrator", "creator")
    except Exception:
        ok = False
    if not ok:
        return None, (f"⚠️ Bot «{esc(chat.title or '')}» kanalida admin emas. Kanal egasidan @{BOT['username']} ni kanalga "
                      "admin qilishini so‘rang — shunda bot kanal postlarini ko‘ra oladi.")
    return chat, ""


async def chat_chosen(event, state: FSMContext, ref):
    chat, err = await verify_chat(ref)
    uid = event.from_user.id
    if chat is None:
        await bot.send_message(uid, err)
        return
    st = storage_chat()
    if st is not None and str(st) == str(chat.id):
        await bot.send_message(uid, "📦 Bu rasmlar ombori kanali — uni manba qilib bo‘lmaydi.")
        return
    import staff
    staff.PENDING_SUB.pop(uid, None)
    await state.update_data(chat=chat.id, title=chat.title or "", username=getattr(chat, "username", None) or "")
    await bot.send_message(uid, f"✅ Kanal: «{esc(chat.title or '')}»", reply_markup=admin_keyboard(uid))
    await state.set_state(SrcAdd.manga)
    items = CAT.manga_list(hidden=True)[:10]
    rows = [[(f"{m['title'][:36]} ({m['code']})", f"src:m:{m['code']}")] for m in items]
    rows.append([CANCEL])
    await bot.send_message(uid, "📚 Bu kanaldan kelgan boblar qaysi mangaga qo‘shilsin? Tanlang yoki kodini yozing.\n"
                                "(Manga hali yo‘q bo‘lsa — avval «➕ Manga qo‘shish».)", reply_markup=kb(rows))


async def manga_chosen(uid, state: FSMContext, code):
    m = CAT.get(code)
    if not m:
        await bot.send_message(uid, "🤷 Bunday kodli manga yo‘q. Qayta yozing.")
        return
    await state.update_data(manga=m["code"])
    await state.set_state(SrcAdd.kw)
    await bot.send_message(uid, f"🔑 Bu kanalda <b>{esc(m['title'])}</b> dan boshqa mangalar ham chiqadimi?\n\n"
                                "Chiqsa — shu manga postlarida doim uchraydigan so‘zni yozing (masalan nomini). "
                                "Faqat shu manga chiqsa — tugmani bosing.",
                           reply_markup=kb([[("⏭ Faqat shu manga", "src:kw:-")], [CANCEL]]))


async def ask_tr(uid, state: FSMContext):
    await state.set_state(SrcAdd.tr)
    await bot.send_message(uid, "🌐 Kanaldagi boblar qaysi tilda?",
                           reply_markup=kb([[("✅ O‘zbekcha — tarjima kerak emas", "src:tr:0")],
                                            [("🌐 Boshqa tilda — tarjima qilinsin", "src:tr:1")], [CANCEL]]))


async def ask_note(uid, state: FSMContext):
    await state.set_state(SrcAdd.note)
    await bot.send_message(uid, "📝 Ruxsat haqida qisqa yozib qo‘ying: kim, qachon va qanday ruxsat bergan.\n"
                                "Masalan: <i>Nur Team, 26.11.2026, Telegram’da yozma ruxsat</i>",
                           reply_markup=kb([[CANCEL]]))


async def ask_credit(uid, state: FSMContext):
    d = await state.get_data()
    default = f"Tarjima: {d.get('title') or 'hamkor jamoa'}"[:100]
    await state.update_data(default_credit=default)
    await state.set_state(SrcAdd.credit)
    await bot.send_message(uid, "✍️ Boblar ostida va reklama postida qanday yozilsin?\nO‘zingiz yozing yoki tugmani bosing.",
                           reply_markup=kb([[(f"⏭ «{default[:40]}»", "src:cr:-")], [CANCEL]]))


async def save_new(event, state: FSMContext, credit):
    d = await state.get_data()
    uid = event.from_user.id
    lst = sources()
    if any(str(x.get("chat")) == str(d["chat"]) and x.get("manga") == d["manga"]
           and catalog.norm(x.get("kw") or "") == catalog.norm(d.get("kw") or "") for x in lst):
        await state.clear()
        await bot.send_message(uid, "ℹ️ Bu manba allaqachon ulangan.")
        return
    src = {"id": os.urandom(3).hex(), "chat": d["chat"], "title": d.get("title") or "", "username": d.get("username") or "",
           "manga": d["manga"], "kw": d.get("kw") or "", "tr": bool(d.get("tr")), "auto": True, "note": d.get("note") or "",
           "credit": credit, "by": uid, "added": time.time(), "count": 0, "last": 0}
    lst.append(src)
    await save_sources(lst)
    await state.clear()
    await jlog(event, f"🔗 Manba ulandi: «{src['title']}» → {CAT.get(src['manga'])['title']} ({src['note']})")
    text, markup = source_card(src["id"])
    await bot.send_message(uid, "✅ Manba ulandi! Endi kanalga bob tashlansa, bot uni o‘zi oladi va bepul chop etadi.\n\n"
                           + text, reply_markup=markup)


@router.callback_query(F.data.startswith("src:"), adm)
async def cb_src(c: CallbackQuery, state: FSMContext):
    if not can(c):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    parts = c.data.split(":")
    act, uid = parts[1], c.from_user.id
    st = await state.get_state()
    if act == "list":
        await c.answer()
        text, markup = sources_view()
        await safe_edit(c.message, text, markup)
    elif act == "add":
        import staff
        await state.clear()
        await state.set_state(SrcAdd.chat)
        staff.PENDING_SUB[uid] = time.time()
        await c.answer()
        await bot.send_message(uid, "➕ <b>Hamkor manba</b>\n\n"
                                    f"1) Kanal egasi @{BOT['username']} ni kanaliga <b>admin</b> qiladi.\n"
                                    "2) Siz kanaldan istalgan postni shu yerga <b>forward</b> qilasiz yoki kanal "
                                    "manzilini yozasiz (<code>@kanal</code>).\n\n"
                                    "O‘zingizning kanalingiz bo‘lsa — «📢 Kanalni tanlash» tugmasini bosing.",
                               reply_markup=staff.chat_keyboard())
    elif act == "new":
        await state.clear()
        await state.set_state(SrcAdd.chat)
        await c.answer()
        await chat_chosen(c, state, int(parts[2]))
    elif act == "m":
        if st != SrcAdd.manga.state:
            await c.answer("Eskirgan", show_alert=True)
            return
        await c.answer()
        await manga_chosen(uid, state, parts[2])
    elif act == "kw":
        if st != SrcAdd.kw.state:
            await c.answer("Eskirgan", show_alert=True)
            return
        await c.answer()
        await state.update_data(kw="")
        await ask_tr(uid, state)
    elif act == "tr":
        if st != SrcAdd.tr.state:
            await c.answer("Eskirgan", show_alert=True)
            return
        await c.answer()
        await state.update_data(tr=parts[2] == "1")
        await ask_note(uid, state)
    elif act == "cr":
        if st != SrcAdd.credit.state:
            await c.answer("Eskirgan", show_alert=True)
            return
        await c.answer()
        await save_new(c, state, (await state.get_data()).get("default_credit") or "")
    elif act in ("v", "t", "e", "d", "d2"):
        sid = parts[2]
        x = get_src(sid)
        if x is None:
            await c.answer("Bu manba endi yo‘q", show_alert=True)
            return
        if act == "t":
            field = parts[3]
            val = not (x.get(field, True) if field == "auto" else x.get(field))
            await update_src(sid, **{field: val})
            await jlog(c, f"🔗 «{x.get('title')}»: {'tarjima' if field == 'tr' else 'avto chop etish'} "
                          f"{'yoqildi' if val else 'o‘chirildi'}")
            await c.answer("Saqlandi")
        elif act == "e":
            await state.set_state(SrcEdit.value)
            await state.update_data(sid=sid, field=parts[3])
            await c.answer()
            await bot.send_message(uid, "✍️ Yangi yozuvni yuboring (masalan <i>Tarjima: Nur Team</i>):" if parts[3] == "credit"
                                   else "📝 Ruxsat izohini yuboring (kim, qachon, qanday ruxsat bergan):",
                                   reply_markup=kb([[CANCEL]]))
            return
        elif act == "d":
            await c.answer()
            await safe_edit(c.message, f"🗑 «{esc(x.get('title') or '')}» manbasi o‘chirilsinmi?\n"
                                       "(Olingan boblar botda qoladi, faqat yangi boblar kelmaydi.)",
                            kb([[("✅ Ha", f"src:d2:{sid}"), ("❌ Yo‘q", f"src:v:{sid}")]]))
            return
        elif act == "d2":
            await save_sources([s for s in sources() if s["id"] != sid])
            await jlog(c, f"🗑 Manba o‘chirildi: «{x.get('title')}»")
            await c.answer("O‘chirildi")
            text, markup = sources_view()
            await safe_edit(c.message, text, markup)
            return
        else:
            await c.answer()
        text, markup = source_card(sid)
        if act == "v":
            await bot.send_message(uid, text, reply_markup=markup)
        else:
            await safe_edit(c.message, text, markup)
    else:
        await c.answer()


@router.message(SrcAdd.chat, F.chat_shared)
async def sa_shared(msg: Message, state: FSMContext):
    await chat_chosen(msg, state, msg.chat_shared.chat_id)


@router.message(SrcAdd.chat, F.forward_origin)
async def sa_forward(msg: Message, state: FSMContext):
    chat = getattr(msg.forward_origin, "chat", None) or getattr(msg.forward_origin, "sender_chat", None)
    if chat is None:
        await msg.answer("Bu kanaldan forward qilingan post emas. Hamkor kanaldan istalgan postni forward qiling.")
        return
    await chat_chosen(msg, state, chat.id)


@router.message(SrcAdd.chat, F.text, ~F.text.startswith("/"))
async def sa_text(msg: Message, state: FSMContext):
    t = msg.text.strip()
    if t == "❌ Bekor qilish":
        import staff
        staff.PENDING_SUB.pop(msg.from_user.id, None)
        await state.clear()
        await msg.answer("❌ Bekor qilindi.", reply_markup=admin_keyboard(msg.from_user.id))
        return
    m = re.search(r"(?:t\.me/|telegram\.me/|@)([A-Za-z][A-Za-z0-9_]{3,})", t)
    if not m:
        await msg.answer("Kanaldan postni forward qiling yoki manzilini <code>@kanal</code> ko‘rinishida yozing.")
        return
    await chat_chosen(msg, state, "@" + m.group(1))


@router.message(SrcAdd.manga, F.text, ~F.text.startswith("/"))
async def sa_manga(msg: Message, state: FSMContext):
    await manga_chosen(msg.from_user.id, state, msg.text.strip())


@router.message(SrcAdd.kw, F.text, ~F.text.startswith("/"))
async def sa_kw(msg: Message, state: FSMContext):
    kw = msg.text.strip()[:60]
    if len(catalog.norm(kw)) < 2:
        await msg.answer("Kamida 2 harfli so‘z yozing yoki «⏭ Faqat shu manga» ni bosing.")
        return
    await state.update_data(kw=kw)
    await ask_tr(msg.from_user.id, state)


@router.message(SrcAdd.note, F.text, ~F.text.startswith("/"))
async def sa_note(msg: Message, state: FSMContext):
    note = msg.text.strip()[:200]
    if len(note) < 5:
        await msg.answer("Biroz batafsilroq yozing: kim, qachon va qanday ruxsat bergan.")
        return
    await state.update_data(note=note)
    await ask_credit(msg.from_user.id, state)


@router.message(SrcAdd.credit, F.text, ~F.text.startswith("/"))
async def sa_credit(msg: Message, state: FSMContext):
    await save_new(msg, state, msg.text.strip()[:100])


@router.message(SrcEdit.value, F.text, ~F.text.startswith("/"))
async def se_value(msg: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    x = get_src(d.get("sid"))
    if x is None:
        await msg.answer("Bu manba endi yo‘q.")
        return
    field = "credit" if d.get("field") == "credit" else "note"
    await update_src(x["id"], **{field: msg.text.strip()[:200 if field == "note" else 100]})
    await jlog(msg, f"🔗 «{x.get('title')}»: {'yozuv' if field == 'credit' else 'ruxsat izohi'} o‘zgartirildi")
    text, markup = source_card(x["id"])
    await msg.answer(text, reply_markup=markup)
