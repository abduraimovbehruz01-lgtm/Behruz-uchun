"""Manga o'qish boti: admin manga va bob qo'shadi, foydalanuvchi kuzatadi, o'qiydi, pullik bobni ochadi."""
import asyncio, io, os, sys, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_tg as T
T.install()
os.environ.update(BOT_TOKEN="1:x", GEMINI_API_KEY="k", OWNER_IDS="1")
os.environ.pop("FIREBASE_SERVICE_ACCOUNT", None)
from PIL import Image
import core, reader, admin, tools, samples, translator
import bot as B   # routerlar ulanadi

PAGE, ITEMS = samples.page_jp(jitter=0.03)
def jpg(img): b = io.BytesIO(); img.save(b, "JPEG", quality=85); return b.getvalue()
async def fake_read(session, jpeg, **k): return {"blocks": ITEMS, "source_language": "yaponcha", "model": "fake"}
core.GEM.read_page = fake_read
CAT = core.CAT
def last(kind=None, chat=None):
    for e in reversed(T.LOG):
        if (kind is None or e[0] == kind) and (chat is None or e[1] == chat): return e
def cb(uid, data, message=None): return T.CallbackQuery(data, T.User(uid), message or T.Sent(uid, "x"))

async def main():
    await CAT.load()
    OWN, USR = 1, 7
    st = T.FSM()
    # 1) admin menyu: ombor ulanmagan
    await admin.cmd_admin(T.Msg(OWN, "/admin"), st)
    assert "ulanmagan" in last("msg", OWN)[2]
    # 2) bot kanalga admin qilindi → ombor
    ev = T.Rec(chat=T.Chat(-100500, "channel", "Ombor"), new_chat_member=T.Rec(status="administrator"), from_user=T.User(OWN))
    await admin.on_member(ev)
    assert core.storage_chat() == -100500 and "ombori" in last("msg", OWN)[2]
    print("ok: ombor kanali avtomatik aniqlandi")
    # 3) manga qo'shish
    T.FILES["COVER"] = jpg(PAGE)
    await admin.cb_add(cb(OWN, "adm:add"), st)
    await admin.am_title(T.Msg(OWN, "Solo Leveling"), st)
    await admin.am_desc(T.Msg(OWN, "Eng kuchsiz ovchi eng kuchliga aylanadi."), st)
    await admin.am_genres(T.Msg(OWN, "jangari, fantastika"), st)
    await admin.am_cover(T.Msg(OWN, photo=[T.Rec(file_id="COVER")]), st)
    await admin.cb_code(cb(OWN, "adm:code"), st)
    m = CAT.get("101")
    assert m and m["title"] == "Solo Leveling" and m["genres"] == ["Jangari", "Fantastika"] and m["cover"] == "COVER"
    assert any(e[0] == "photo" and "Solo Leveling" in (e[2] or "") for e in T.LOG)
    print("ok: manga qo'shildi, kod 101")
    # 4) foydalanuvchi kuzatadi
    await reader.cmd_start(T.Msg(USR, "/start"), T.Rec(args=None))
    assert "Assalomu alaykum" in last("msg", USR)[2]
    await reader.cb_toggle(cb(USR, "fol:101"))
    assert USR in CAT.get("101")["followers"]
    # 5) 1-bob: ZIP + tarjima
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in ("2.jpg", "1.jpg", "3.jpg"): z.writestr(n, jpg(PAGE))
    T.FILES["ZIP1"] = buf.getvalue()
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_doc(T.Msg(OWN, document=T.Rec(file_id="ZIP1", file_name="bob1.zip", mime_type="application/zip", file_size=1000)), st)
    assert st.state == "AddChap:num"
    await admin.cb_num(cb(OWN, "adm:num:1"), st)
    await admin.cb_tr(cb(OWN, "adm:tr:1"), st)
    await admin.JOBS[OWN]["task"]
    ch = CAT.chapter("101", 1)
    assert ch and not ch["published"] and ch["pages"] == 3, ch
    pub = last("msg", OWN); assert "tayyor" in pub[2] and "pub:101:1:0" in T.buttons(pub[3]), pub[2]
    up = [e for e in T.LOG if e[0] == "album" and e[1] == -100500]
    assert up and up[0][2] == 3
    print("ok: 1-bob tarjima qilinib omborga yuklandi (qoralama)")
    # 6) chop etish → kuzatuvchiga xabar
    await admin.cb_pub(cb(OWN, "pub:101:1:0"))
    note = [e for e in T.LOG if e[0] == "msg" and e[1] == USR and "yangi" in e[2]]
    assert note and "r:101:1" in T.buttons(note[-1][3]), note
    print("ok: chop etildi, kuzatuvchiga xabar ketdi")
    # 7) foydalanuvchi kod yuboradi → karta; o'qiydi
    await reader.on_code(T.Msg(USR, "101"))
    card = last("photo", USR); assert card and "Solo Leveling" in card[2] and "r:101:1" in T.buttons(card[3])
    await reader.cb_read(cb(USR, "r:101:1"))
    alb = last("album", USR); assert alb and alb[2] == 3 and not alb[3]
    u = await CAT.user(USR); assert u["progress"]["101"] == 1.0 and CAT.get("101")["views"] == 1
    print("ok: kod → karta → bob albom bo'lib keldi, progress saqlandi")
    # 8) PDF
    await reader.cb_read(cb(USR, "pdf:101:1"))
    d = last("doc", USR); assert d and d[2].endswith("1-bob.pdf"), d
    assert (await CAT.get_pages("101", 1))["pdf"]
    print("ok: PDF yasaldi va saqlandi")
    # 9) 2-bob: rasmlar (tarjimasiz), pullik
    T.FILES["IMG1"] = jpg(PAGE); T.FILES["IMG2"] = jpg(PAGE)
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_photo(T.Msg(OWN, photo=[T.Rec(file_id="IMG2")]), st)
    await admin.ac_photo(T.Msg(OWN, photo=[T.Rec(file_id="IMG1")]), st)
    await admin.cb_fdone(cb(OWN, "adm:fdone"), st)
    await admin.ac_num(T.Msg(OWN, "2"), st)
    await admin.cb_tr(cb(OWN, "adm:tr:0"), st)
    await admin.JOBS[OWN]["task"]
    await admin.cb_pub(cb(OWN, "pub:101:2:2"))
    assert CAT.chapter("101", 2)["price"] == 2
    await reader.cb_read(cb(USR, "r:101:2"))
    lock = last("msg", USR); assert "pullik" in lock[2] and not any(b and b.startswith("buy:") for b in T.buttons(lock[3]))
    await CAT.add_jetons(USR, 3)
    await reader.cb_read(cb(USR, "r:101:2"))
    lock = last("msg", USR); assert "buy:101:2" in T.buttons(lock[3])
    await reader.cb_buy(cb(USR, "buy:101:2"))
    alb = last("album", USR); assert alb[2] == 2 and alb[3] is True   # pullik bob — himoyalangan
    assert (await CAT.user(USR))["jetons"] == 1
    print("ok: pullik bob: qulf → jeton bilan ochildi (himoyalangan)")
    # 10) qidiruv, davom ettirish
    await reader.on_text(T.Msg(USR, "solo"))
    assert "Solo Leveling" in last("photo", USR)[2]
    await reader.on_continue(T.Msg(USR, reader.B_CONT))
    assert "r:101:2" in T.buttons(last("msg", USR)[3])
    print("ok: qidiruv va davom ettirish")
    # 11) admin tahrir, yashirish
    await admin.cb_ae(cb(OWN, "ae:101:title"), st)
    await admin.ef_value(T.Msg(OWN, "Solo Leveling: Ragnarok"), st)
    assert CAT.get("101")["title"] == "Solo Leveling: Ragnarok"
    await admin.cb_hide(cb(OWN, "ahide:101"))
    assert CAT.get("101")["hidden"] and CAT.search("solo") == []
    await admin.cb_hide(cb(OWN, "ahide:101"))
    print("ok: tahrirlash va yashirish")
    # 12) oddiy foydalanuvchi rasm yuborsa — tarjima emas, maslahat
    await reader.on_media(T.Msg(USR, photo=[T.Rec(file_id="IMG1")]))
    assert "manga o‘qish uchun" in last("msg", USR)[2]
    assert not tools.can_translate(T.Msg(USR)) and tools.can_translate(T.Msg(OWN))
    # 13) admin tezkor tarjima
    T.FILES["Q"] = jpg(PAGE)
    await tools.on_photo(T.Msg(OWN, photo=[T.Rec(file_id="Q")]))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if not tools.QUEUES and not tools.CURRENT: break
    assert last("photo", OWN) and "Tayyor" in last("photo", OWN)[2]
    print("ok: admin tezkor tarjimasi ishlaydi")
    # 14) bobni o'chirish
    await admin.cb_cdel2(cb(OWN, "cdel2:101:2"))
    assert CAT.chapter("101", 2) is None
    s = await CAT.stats(); print("ok: o'chirish; statistika", s)

asyncio.run(main()); print("HAMMASI O'TDI")
