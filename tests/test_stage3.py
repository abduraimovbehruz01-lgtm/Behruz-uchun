"""3-bosqich sinovi: hamkor kanal manbalari (avtomatik olish, tarjima, bepul chop etish), reklama kanali,
tarjimon yozuvi, sifat nazorati, takroriy bob, Gemini limiti, navbatni saqlash."""
import asyncio, io, os, sys, time, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_tg as T
T.install()
OWN = 100001
os.environ.update(BOT_TOKEN="1:x", GEMINI_API_KEY="k", OWNER_IDS=str(OWN))
os.environ.pop("FIREBASE_SERVICE_ACCOUNT", None)
import core, reader, admin, staff, scheduler, samples, catalog, promo, autosrc, translator
import bot as B

CAT = core.CAT
_, ITEMS = samples.page_jp(jitter=0.03)
CALLS = []
MODE = {"quota": False, "bad": False}


async def fake_read(session, jpeg, **k):
    CALLS.append(k)
    if MODE["quota"]:
        raise translator.GeminiError(429, "kunlik limit tugadi", per_day=True, kind="quota")
    if MODE["bad"]:
        raise translator.GeminiError(200, "javob buzuq", kind="bad")
    return {"blocks": ITEMS, "source_language": "inglizcha", "model": "fake", "names": []}
core.GEM.read_page = fake_read


def jpg(img, q=85):
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=q); return b.getvalue()


PG = {s: jpg(samples.page_jp(seed=s)[0]) for s in range(60, 84)}


def last(kind=None, chat=None, has=None):
    for e in reversed(T.LOG):
        if (kind is None or e[0] == kind) and (chat is None or e[1] == chat) and (has is None or has in (e[2] or "")):
            return e


def kb_texts(markup):
    return [b.k.get("text") for row in markup.k["keyboard"] for b in row]


def cb(uid, data, message=None):
    return T.CallbackQuery(data, T.User(uid), message or T.Sent(uid, "x"))


def zip_bytes(*seeds):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for i, s in enumerate(seeds):
            z.writestr(f"{i + 1:02d}.jpg", PG[s])
    return b.getvalue()


def doc(fid, name, size=1000, mime="application/zip"):
    return T.Rec(file_id=fid, file_name=name, mime_type=mime, file_size=size)


def post(chat, text=None, caption=None, photo=None, document=None, group=None, entities=None, markup=None):
    return T.Rec(chat=T.Chat(chat, "channel", "kanal"), message_id=next(T._ids), text=text, caption=caption,
                 photo=photo, document=document, media_group_id=group, entities=entities, caption_entities=None,
                 reply_markup=markup, from_user=None)


def photo(fid):
    return [T.Rec(file_id=fid + "_s"), T.Rec(file_id=fid)]


async def drain():
    for _ in range(50):
        t = autosrc.STATE.get("worker")
        if t is None or t.done():
            return
        await t


def journal(has):
    return any(has in j["a"] for j in CAT.journal)


class TResp:
    def __init__(self, status=200, body=b"", data=None):
        self.status, self.body, self.data, self.headers = status, body, data, {"Content-Length": str(len(body))}
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def read(self): return self.body
    async def json(self, content_type=None): return self.data


class TSess:
    def __init__(self, page, imgs): self.page, self.imgs, self.urls = page, imgs, []
    def get(self, url, **k):
        self.urls.append(url)
        if "api.telegra.ph/getPage/" in url:
            return TResp(data=self.page)
        return TResp(body=self.imgs[url]) if url in self.imgs else TResp(404)


async def main():
    await CAT.load()
    await autosrc.load()
    USR = 7
    st = T.FSM()
    # ---------------------------------------------------------------- tayyorgarlik
    await staff.on_member(T.Rec(chat=T.Chat(-100500, "channel", "Ombor"), new_chat_member=T.Rec(status="administrator"),
                                from_user=T.User(OWN)))
    assert core.storage_chat() == -100500
    await CAT.add_manga("Solo Leveling", "Ovchi", ["Jangari"], code="101", by=OWN)
    await CAT.add_manga("Bizning maktab", "Kulgili", ["Komediya"], code="102", by=OWN)
    await reader.cmd_start(T.Msg(USR, "/start"), T.Rec(args=None))
    await reader.cb_toggle(cb(USR, "fol:101"))
    mk = kb_texts(core.admin_keyboard(OWN))
    assert core.A_SRC in mk and core.A_PROMO in mk
    await CAT.set_admin(555, ["chapter"], "Vali", by=OWN)
    mk = kb_texts(core.admin_keyboard(555))
    assert core.A_SRC in mk and core.A_PROMO not in mk
    print("ok: ombor, mangalar, admin tugmalarida «Manbalar» va «Reklama kanali»")

    # ---------------------------------------------------------------- reklama kanali
    T.CHATS[-100777] = T.Rec(id=-100777, type="channel", title="Manhora yangiliklar", username="manhora_news")
    T.MEMBERS[(-100777, 999)] = "administrator"
    await staff.on_member(T.Rec(chat=T.Rec(id=-100777, type="channel", title="Manhora yangiliklar", username="manhora_news"),
                                new_chat_member=T.Rec(status="administrator"), from_user=T.User(OWN)))
    ask = last("msg", OWN, "nima uchun")
    assert ask and {"chan:promo:-100777", "src:new:-100777", "chan:sub:-100777"} <= set(T.buttons(ask[3]))
    await staff.cb_chan(cb(OWN, "chan:promo:-100777"))
    assert CAT.cfg["promo"] == {"id": -100777, "title": "Manhora yangiliklar", "on": True}
    c = cb(OWN, "adm:prtest")
    await staff.cb_promo(c, st)
    assert "Hali chop etilgan bob yo‘q" in c.answers[-1]
    await admin.on_admin_button(T.Msg(OWN, core.A_PROMO), st)
    assert "Reklama kanali" in last("msg", OWN)[2] and "adm:prtog" in T.buttons(last("msg", OWN)[3])
    print("ok: reklama kanali ulandi (kanal qo‘shilganda so‘raladi), panel bo‘limi")

    # ---------------------------------------------------------------- hamkor botni o'z kanaliga qo'shdi
    T.CHATS[-100900] = T.Rec(id=-100900, type="channel", title="Nur Team", username="nurteam")
    T.MEMBERS[(-100900, 999)] = "administrator"
    await staff.on_member(T.Rec(chat=T.Rec(id=-100900, type="channel", title="Nur Team", username="nurteam"),
                                new_chat_member=T.Rec(status="administrator"), from_user=T.User(424242, "Hamkor")))
    note = last("msg", OWN, "Nur Team")
    assert note and "src:new:-100900" in T.buttons(note[3]) and "hamkor" in note[2].lower()
    # manba qilib ulash: manga → kalit so'z → til → ruxsat izohi → yozuv
    await autosrc.cb_src(cb(OWN, "src:new:-100900"), st)
    assert st.state == "SrcAdd:manga" and "src:m:101" in T.buttons(last("msg", OWN)[3])
    await autosrc.cb_src(cb(OWN, "src:m:101"), st)
    assert st.state == "SrcAdd:kw" and "src:kw:-" in T.buttons(last("msg", OWN)[3])
    await autosrc.sa_kw(T.Msg(OWN, "Solo Leveling"), st)
    assert st.state == "SrcAdd:tr"
    await autosrc.cb_src(cb(OWN, "src:tr:1"), st)
    assert st.state == "SrcAdd:note"
    await autosrc.sa_note(T.Msg(OWN, "ok"), st)
    assert "batafsilroq" in last("msg", OWN)[2] and st.state == "SrcAdd:note"
    await autosrc.sa_note(T.Msg(OWN, "Nur Team, 26.11.2026, Telegram’da yozma ruxsat"), st)
    assert st.state == "SrcAdd:credit" and "src:cr:-" in T.buttons(last("msg", OWN)[3])
    await autosrc.cb_src(cb(OWN, "src:cr:-"), st)
    s1 = autosrc.sources()[0]
    assert s1["chat"] == -100900 and s1["manga"] == "101" and s1["kw"] == "Solo Leveling" and s1["tr"] and s1["auto"]
    assert s1["credit"] == "Tarjima: Nur Team" and "yozma ruxsat" in s1["note"] and s1["by"] == OWN and st.state is None
    assert "Manba ulandi" in last("msg", OWN)[2] and journal("Manba ulandi")
    print("ok: hamkor botni kanaliga qo‘shdi → bosh adminga xabar → manba ulandi (kalit so‘z, tarjima, ruxsat, yozuv)")

    # ---------------------------------------------------------------- ikkinchi manba: forward orqali, o'zbekcha
    T.CHATS[-100901] = T.Rec(id=-100901, type="channel", title="Uzbek Manga", username="")
    await autosrc.cb_src(cb(OWN, "src:add"), st)
    assert st.state == "SrcAdd:chat" and OWN in staff.PENDING_SUB
    await autosrc.sa_forward(T.Msg(OWN, forward_origin=T.Rec(chat=T.Rec(id=-100901))), st)
    assert "admin emas" in last("msg", OWN)[2] and st.state == "SrcAdd:chat"
    T.CHATS["@ombor"] = T.Rec(id=-100500, type="channel", title="Ombor", username="ombor")
    T.MEMBERS[(-100500, 999)] = "administrator"
    await autosrc.sa_text(T.Msg(OWN, "@ombor"), st)
    assert "ombori" in last("msg", OWN)[2] and st.state == "SrcAdd:chat"
    T.MEMBERS[(-100901, 999)] = "administrator"
    await autosrc.sa_forward(T.Msg(OWN, forward_origin=T.Rec(chat=T.Rec(id=-100901))), st)
    assert st.state == "SrcAdd:manga" and OWN not in staff.PENDING_SUB
    await autosrc.sa_manga(T.Msg(OWN, "999"), st)
    assert "yo‘q" in last("msg", OWN)[2] and st.state == "SrcAdd:manga"
    await autosrc.sa_manga(T.Msg(OWN, "102"), st)
    await autosrc.cb_src(cb(OWN, "src:kw:-"), st)
    await autosrc.cb_src(cb(OWN, "src:tr:0"), st)
    await autosrc.sa_note(T.Msg(OWN, "Kanal egasi Ali, 25.11.2026, shaxsan ruxsat berdi"), st)
    await autosrc.sa_credit(T.Msg(OWN, "Tarjima: Uzbek Manga jamoasi"), st)
    assert len(autosrc.sources()) == 2
    s2 = autosrc.sources()[1]
    assert s2["manga"] == "102" and s2["kw"] == "" and not s2["tr"] and s2["credit"] == "Tarjima: Uzbek Manga jamoasi"
    await admin.on_admin_button(T.Msg(OWN, core.A_SRC), st)
    lst = last("msg", OWN)
    assert "Nur Team" in lst[2] and "Uzbek Manga" in lst[2] and f"src:v:{s1['id']}" in T.buttons(lst[3])
    print("ok: forward orqali ikkinchi manba (bot admin emasligi va ombor kanali tekshirildi), «🔗 Manbalar» ro‘yxati")

    # ---------------------------------------------------------------- kanalga ZIP tashlandi → tarjima → bepul chop etildi
    T.FILES["Z45"] = zip_bytes(60, 61, 62)
    n_calls = len(CALLS)
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling — 45-bob", document=doc("Z45", "sl_45.zip")))
    await drain()
    ch = CAT.chapter("101", 45)
    assert ch and ch["published"] and ch["price"] == 0 and ch["credit"] == "Tarjima: Nur Team" and ch["source"] == s1["id"]
    assert len(CALLS) - n_calls >= 3                                   # har bir sahifa tarjima qilindi
    assert last("msg", USR, "45-bob")                                  # kuzatuvchiga xabar
    pr = last("msg", -100777)
    assert pr and "45-bob" in pr[2] and "Tarjima: Nur Team" in pr[2] and \
        "https://t.me/ManhoraBot?start=c101_45" in T.buttons(pr[3])     # reklama kanaliga post
    assert last("msg", OWN, "bepul chop etildi") and "Reklama kanaliga" in last("msg", OWN, "bepul chop etildi")[2]
    assert autosrc.sources()[0]["count"] == 1 and not autosrc.QUEUE and journal("45-bob «Nur Team» dan olindi")
    # tarjimon yozuvi o'quvchiga ko'rinadi
    await reader.read_chapter(USR, USR, "101", 45)
    assert last("msg", USR, "45-bob") and "✍️ Tarjima: Nur Team" in last("msg", USR, "sahifa")[2]
    print("ok: kanal ZIP → tarjima → bepul chop etildi, kuzatuvchiga xabar, reklama posti, yozuv o‘qishda ko‘rinadi")

    # kalit so'zsiz post (boshqa manga) — olinmaydi; bor bob va takroriy bob — qayta olinmaydi
    await autosrc.on_channel_post(post(-100900, caption="Boshqa manga 3-bob", document=doc("Z45", "x.zip")))
    assert not autosrc.QUEUE
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 45-bob", document=doc("Z45", "sl.zip")))
    await drain()
    assert journal("45-bob allaqachon bor")
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 46-bob", document=doc("Z45", "sl.zip")))
    await drain()
    assert CAT.chapter("101", 46) is None and journal("45-bob bilan bir xil") and not autosrc.QUEUE
    # 20 MB dan katta fayl — maslahat
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 47-bob", document=doc("ZBIG", "big.zip", 30 << 20)))
    assert not autosrc.QUEUE and "20 MB" in last("msg", OWN)[2]
    print("ok: kalit so‘zsiz post, bor bob, takroriy bob o‘tkazib yuborildi; 20 MB+ fayl haqida maslahat")

    # ---------------------------------------------------------------- rasmlar albomi (tarjimasiz)
    for s in range(63, 75):
        T.FILES[f"F{s}"] = PG[s]
    n_calls = len(CALLS)
    await autosrc.on_channel_post(post(-100901, caption="12-bob", photo=photo("F63"), group="g1"))
    await autosrc.on_channel_post(post(-100901, photo=photo("F64"), group="g1"))
    await autosrc.on_channel_post(post(-100901, photo=photo("F65"), group="g1"))
    assert autosrc.ALBUMS and B.busy()
    await autosrc.tick(time.time() + 10)                     # hali yig'ilyapti
    assert autosrc.ALBUMS
    await autosrc.tick(time.time() + autosrc.IDLE + 5)       # 2 daqiqa yangi rasm kelmadi → bob tugadi
    await drain()
    ch = CAT.chapter("102", 12)
    assert ch and ch["published"] and ch["pages"] >= 3 and ch["credit"] == "Tarjima: Uzbek Manga jamoasi"
    assert not autosrc.ALBUMS and len(CALLS) == n_calls      # o'zbekcha manba — tarjima chaqirilmadi
    # avval matn «13-bob», keyin izohsiz rasmlar; keyin ikkita raqamsiz rasm (e'lon) — bob emas
    await autosrc.on_channel_post(post(-100901, text="Yangi qism: 13-bob 🔥"))
    for s in (66, 67, 68):
        await autosrc.on_channel_post(post(-100901, photo=photo(f"F{s}"), group="g2"))
    # rasmlardan keyin yangi raqamli albom keldi — oldingisi darhol yopiladi
    await autosrc.on_channel_post(post(-100901, caption="14-bob", photo=photo("F69"), group="g3"))
    await autosrc.on_channel_post(post(-100901, photo=photo("F70"), group="g3"))
    await autosrc.on_channel_post(post(-100901, photo=photo("F71"), group="g3"))
    await drain()
    assert CAT.chapter("102", 13) and CAT.chapter("102", 13)["published"]
    await autosrc.tick(time.time() + autosrc.IDLE + 5)
    await drain()
    assert CAT.chapter("102", 14) and CAT.chapter("102", 14)["published"]
    await autosrc.on_channel_post(post(-100901, photo=photo("F72")))
    await autosrc.on_channel_post(post(-100901, photo=photo("F73")))
    await autosrc.tick(time.time() + autosrc.IDLE + 5)
    await drain()
    assert not CAT.chapter("102", 15) and not autosrc.ALBUMS and not autosrc.QUEUE
    print("ok: rasmlar albomi → bob (12), matndagi raqam (13), yangi albom oldingisini yopdi (14), 2 ta rasm — bob emas")

    # ---------------------------------------------------------------- Telegraph havolasi
    page = {"ok": True, "result": {"title": "Bizning maktab 15-bob", "content": [
        {"tag": "p", "children": ["Yoqimli o‘qish!"]},
        {"tag": "figure", "children": [{"tag": "img", "attrs": {"src": "/file/a1.jpg"}}]},
        {"tag": "img", "attrs": {"src": "https://telegra.ph/file/a2.jpg"}},
        {"tag": "img", "attrs": {"src": "/file/a3.jpg"}}]}}
    sess = TSess(page, {"https://telegra.ph/file/a1.jpg": PG[75], "https://telegra.ph/file/a2.jpg": PG[76],
                        "https://telegra.ph/file/a3.jpg": PG[77]})
    autosrc.http = lambda: sess
    link = T.Rec(type="text_link", url="https://telegra.ph/Bizning-maktab-15-11-26")
    await autosrc.on_channel_post(post(-100901, text="Yangi bob chiqdi — o‘qish", entities=[link]))
    await drain()
    ch = CAT.chapter("102", 15)
    assert ch and ch["published"] and ch["pages"] >= 3, ch
    assert sess.urls[0] == "https://api.telegra.ph/getPage/Bizning-maktab-15-11-26?return_content=true"
    rules = TSess({"ok": True, "result": {"title": "Kanal qoidalari", "content": [
        {"tag": "img", "attrs": {"src": "/file/r.jpg"}}]}}, {"https://telegra.ph/file/r.jpg": PG[78]})
    autosrc.http = lambda: rules
    await autosrc.on_channel_post(post(-100901, text="Qoidalar: https://telegra.ph/Kanal-qoidalari-11-26"))
    await drain()
    assert not CAT.chapter("102", 16) and journal("Telegraph sahifasi bob emas") and not autosrc.QUEUE
    print("ok: Telegraph havolasi (yashirin havola) → sahifalar olindi, raqam sarlavhadan (15); qoidalar sahifasi — bob emas")

    # ---------------------------------------------------------------- sifat nazorati → qoralama
    T.FILES["Z47"] = zip_bytes(78, 79, 80)
    MODE["bad"] = True
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 47-bob", document=doc("Z47", "sl47.zip")))
    await drain()
    MODE["bad"] = False
    ch = CAT.chapter("101", 47)
    assert ch and not ch["published"]
    dr = last("msg", OWN, "qoralama")
    assert dr and "tarjima bo‘lmadi" in dr[2] and "pub:101:47:0" in T.buttons(dr[3])
    print("ok: tarjima sifati past → bob qoralama qoldi, admin «chop etish» tugmalari bilan xabar oldi")

    # ---------------------------------------------------------------- Gemini limiti → navbatda qoladi, keyin chop etiladi
    T.FILES["Z48"] = zip_bytes(81, 82, 83)
    MODE["quota"] = True
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 48-bob", document=doc("Z48", "sl48.zip")))
    await drain()
    assert not CAT.chapter("101", 48) and len(autosrc.QUEUE) == 1 and autosrc.QUEUE[0]["wait"] > time.time() + 3600
    assert last("msg", OWN, "navbatda qoldi")
    # navbat bazada saqlanadi — server qayta ishga tushsa ham yo'qolmaydi
    await autosrc.persist()
    autosrc.QUEUE.clear()
    await autosrc.load()
    assert len(autosrc.QUEUE) == 1 and autosrc.QUEUE[0]["num"] == 48
    MODE["quota"] = False
    await autosrc.tick()
    await drain()
    assert len(autosrc.QUEUE) == 1                            # vaqti kelmagan
    autosrc.QUEUE[0]["wait"] = 0
    await autosrc.tick()
    await drain()
    assert CAT.chapter("101", 48) and CAT.chapter("101", 48)["published"] and not autosrc.QUEUE
    # yig'ilayotgan albom ham saqlanadi
    T.FILES["G1"], T.FILES["G2"], T.FILES["G3"] = PG[60], PG[62], PG[64]
    await autosrc.on_channel_post(post(-100901, caption="20-bob", photo=photo("G1"), group="g9"))
    await autosrc.persist()
    autosrc.ALBUMS.clear()
    await autosrc.load()
    assert autosrc.ALBUMS["-100901"]["num"] == 20 and len(autosrc.ALBUMS["-100901"]["photos"]) == 1
    autosrc.ALBUMS.clear(); await autosrc.persist()
    print("ok: Gemini limiti → navbatda qoldi va keyin chop etildi; navbat va albomlar bazada saqlanadi")

    # ---------------------------------------------------------------- kunlik chegara
    autosrc.STATE["done"] = 20
    autosrc.STATE["day"] = catalog.today()
    T.FILES["Z49"] = zip_bytes(61, 63, 65)
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 49-bob", document=doc("Z49", "sl49.zip")))
    await drain()
    assert not CAT.chapter("101", 49) and len(autosrc.QUEUE) == 1 and autosrc.QUEUE[0]["wait"] == catalog.slot_ts(1, 0)
    autosrc.STATE["done"] = 0
    autosrc.QUEUE[0]["wait"] = 0
    await autosrc.tick(); await drain()
    assert CAT.chapter("101", 49)["published"]
    print("ok: kunlik chegara (20 bob) — ortig‘i ertaga")

    # ---------------------------------------------------------------- manba kartasi: avto o'chirish, yozuv, o'chirish
    sid = s1["id"]
    await autosrc.cb_src(cb(OWN, f"src:v:{sid}"), st)
    card = last("msg", OWN)
    assert "Nur Team" in card[2] and "yozma ruxsat" in card[2] and f"src:t:{sid}:auto" in T.buttons(card[3])
    await autosrc.cb_src(cb(OWN, f"src:t:{sid}:auto"), st)
    assert autosrc.get_src(sid)["auto"] is False
    await autosrc.cb_src(cb(OWN, f"src:e:{sid}:credit"), st)
    assert st.state == "SrcEdit:value"
    await autosrc.se_value(T.Msg(OWN, "Tarjima: Nur Team (yangi)"), st)
    assert autosrc.get_src(sid)["credit"] == "Tarjima: Nur Team (yangi)" and st.state is None
    T.FILES["Z50"] = zip_bytes(66, 68, 70)
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 50-bob", document=doc("Z50", "sl50.zip")))
    await drain()
    ch = CAT.chapter("101", 50)
    assert ch and not ch["published"] and ch["credit"] == "Tarjima: Nur Team (yangi)"
    assert "avto chop etish o‘chiq" in last("msg", OWN, "qoralama")[2]
    # «Sinov posti» endi chiqadi
    c = cb(OWN, "adm:prtest")
    await staff.cb_promo(c, st)
    assert "chiqdi" in c.answers[-1]
    # bot hamkor kanaldan chiqarilsa — xabar
    await staff.on_member(T.Rec(chat=T.Rec(id=-100900, type="channel", title="Nur Team"),
                                new_chat_member=T.Rec(status="kicked"), from_user=T.User(424242)))
    assert last("msg", OWN, "hamkor kanalidan chiqarildi")
    await autosrc.cb_src(cb(OWN, f"src:d:{sid}"), st)
    await autosrc.cb_src(cb(OWN, f"src:d2:{sid}"), st)
    assert [x["id"] for x in autosrc.sources()] == [s2["id"]] and journal("Manba o‘chirildi")
    await autosrc.on_channel_post(post(-100900, caption="Solo Leveling 51-bob", document=doc("Z50", "sl51.zip")))
    assert not autosrc.QUEUE
    print("ok: manba kartasi — avto chop etishni o‘chirish, yozuvni almashtirish, o‘chirish; sinov posti")

    # menyuda manbalar soni
    assert "Hamkor manbalar: 1 ta" in admin.menu_text(OWN)
    print("ok: admin menyusida hamkor manbalar soni")


asyncio.run(main()); print("HAMMASI O'TDI")
