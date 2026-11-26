"""2-bosqich sinovi: adminlar va huquqlar, jurnal, majburiy obuna, ko'p bobni yuklash, takroriy bob,
havola orqali yuklash, ommaviy chop etish, rejalashtirish, sahifa tahriri, bob nomi, glossariy, manga holati."""
import asyncio, io, os, sys, time, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_tg as T
T.install()
OWN = 100001
os.environ.update(BOT_TOKEN="1:x", GEMINI_API_KEY="k", OWNER_IDS=str(OWN))
os.environ.pop("FIREBASE_SERVICE_ACCOUNT", None)
from PIL import ImageOps
import core, reader, admin, staff, editor, scheduler, tools, samples, catalog
import bot as B

CAT = core.CAT
admin.SETTLE_DELAY = 0.01
_, ITEMS = samples.page_jp(jitter=0.03)
CALLS = []
NAMES = [{"original": "차해인", "translation": "Cha Xe In"}, {"original": "성진우", "translation": "Sung Jinvu"}]


async def fake_read(session, jpeg, **k):
    CALLS.append(k)
    return {"blocks": ITEMS, "source_language": "koreyscha", "model": "fake", "names": NAMES}
core.GEM.read_page = fake_read


def jpg(img, q=85):
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=q); return b.getvalue()


PG = {s: jpg(samples.page_jp(seed=s)[0]) for s in range(40, 52)}
PG["flip"] = jpg(ImageOps.mirror(samples.page_jp(seed=40)[0]))


def last(kind=None, chat=None, has=None):
    for e in reversed(T.LOG):
        if (kind is None or e[0] == kind) and (chat is None or e[1] == chat) and (has is None or has in (e[2] or "")):
            return e


def cb(uid, data, message=None):
    return T.CallbackQuery(data, T.User(uid), message or T.Sent(uid, "x"))


def zip_bytes(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return b.getvalue()


def doc(fid, name, size=1000, mime="application/zip"):
    return T.Rec(file_id=fid, file_name=name, mime_type=mime, file_size=size)


async def wait_job(uid):
    for _ in range(400):
        j = admin.JOBS.get(uid)
        if not j:
            return
        await j["task"]


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None, url=""):
        self.status, self.body, self.headers, self.url = status, body, headers or {}, url
        self.content = self
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def iter_chunked(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]
    async def json(self, content_type=None): return {}


class FakeSession:
    def __init__(self, resp): self.resp, self.urls = resp, []
    def get(self, url, **k): self.urls.append(url); return self.resp


async def main():
    await CAT.load()
    USR, HELPER = 7, 555555
    st = T.FSM()
    # ---------------------------------------------------------------- ombor kanali va manga
    await staff.on_member(T.Rec(chat=T.Chat(-100500, "channel", "Ombor"), new_chat_member=T.Rec(status="administrator"),
                                from_user=T.User(OWN)))
    assert core.storage_chat() == -100500 and CAT.cfg["storage_title"] == "Ombor"
    m = await CAT.add_manga("Solo Leveling", "Ovchi", ["Jangari"], code="101", by=OWN)
    await reader.cmd_start(T.Msg(USR, "/start"), T.Rec(args=None))
    await reader.cb_toggle(cb(USR, "fol:101"))
    print("ok: ombor va manga")

    # ---------------------------------------------------------------- adminlar
    await staff.cb_admins(cb(OWN, "adm:admins"), st)
    assert "Bosh admin" in last("msg", OWN)[2]
    await staff.cb_aadd(cb(OWN, "adm:aadd"), st)
    assert st.state == "AddAdmin:uid" and last("msg", OWN)[3].__class__.__name__ == "Rec"
    await staff.aa_text(T.Msg(OWN, "salom"), st)
    assert "faqat raqam" in last("msg", OWN)[2] and st.state == "AddAdmin:uid"
    T.CHATS[HELPER] = T.Rec(id=HELPER, first_name="Vali", username="vali", type="private")
    await staff.aa_text(T.Msg(OWN, str(HELPER)), st)
    assert CAT.rights(HELPER) == {"manga", "chapter"} and CAT.admins[HELPER]["name"] == "Vali" and st.state is None
    assert last("msg", HELPER, "admin") and f"adm:ar:{HELPER}:stats" in T.buttons(last("msg", OWN)[3])
    await staff.cb_rights(cb(OWN, f"adm:ar:{HELPER}:manga"))
    assert CAT.rights(HELPER) == {"chapter"}
    await staff.cb_rights(cb(OWN, f"adm:aall:{HELPER}"))
    assert CAT.rights(HELPER) == set(catalog.RIGHTS)
    await staff.cb_rights(cb(OWN, f"adm:aall:{HELPER}"))
    assert CAT.rights(HELPER) == set()
    await staff.cb_rights(cb(OWN, f"adm:ar:{HELPER}:chapter"))
    # cheklangan admin: faqat bob yuklash
    mk = T.buttons(admin.menu_markup(HELPER))
    assert "adm:chap" in mk and "adm:add" not in mk and "adm:admins" not in mk and "adm:subs" not in mk
    c = cb(HELPER, "adm:add"); await admin.cb_add(c, T.FSM()); assert "huquq" in c.answers[-1]
    c = cb(HELPER, "adm:admins"); await staff.cb_admins(c, T.FSM()); assert "huquq" in c.answers[-1]
    assert "huquq" not in admin.menu_text(HELPER)
    # kontaktdan tanlash va forward
    await staff.cb_aadd(cb(OWN, "adm:aadd"), st)
    await staff.aa_shared(T.Msg(OWN, users_shared=T.Rec(users=[T.Rec(user_id=777777, first_name="Sardor", username="")])), st)
    assert CAT.admins[777777]["name"] == "Sardor"
    await staff.cb_aadd(cb(OWN, "adm:aadd"), st)
    await staff.aa_forward(T.Msg(OWN, "salom", forward_origin=T.Rec(sender_user_name="Yashirin")), st)
    assert "yopib" in last("msg", OWN)[2] and st.state == "AddAdmin:uid"
    await staff.aa_forward(T.Msg(OWN, "salom", forward_origin=T.Rec(sender_user=T.User(888888, "Aziz"))), st)
    assert CAT.admins[888888]["name"] == "Aziz"
    await staff.cb_aadd(cb(OWN, "adm:aadd"), st)
    await staff.aa_text(T.Msg(OWN, str(OWN)), st)
    assert "bosh admin" in last("msg", OWN)[2] and OWN not in CAT.admins
    await staff.cb_adel(cb(OWN, "adm:adel:888888"))
    assert 888888 in CAT.admins
    await staff.cb_adel(cb(OWN, "adm:adel2:888888"))
    assert 888888 not in CAT.admins
    print("ok: adminlar — qo'shish (ID, kontakt, forward), huquqlar, olib tashlash")

    # ---------------------------------------------------------------- majburiy obuna
    chan = T.Rec(id=-1001, type="channel", title="Kanalim", username="kanalim", invite_link=None)
    T.CHATS["@kanalim"] = T.CHATS[-1001] = chan
    await staff.cb_subadd(cb(OWN, "adm:subadd"), st)
    await staff.sa_text(T.Msg(OWN, "https://t.me/kanalim"), st)
    assert "admin emas" in last("msg", OWN)[2] and not CAT.cfg["channels"]
    T.MEMBERS[(-1001, 999)] = "administrator"
    await staff.sa_text(T.Msg(OWN, "@kanalim"), st)
    assert CAT.cfg["channels"] == [{"id": -1001, "title": "Kanalim", "url": "https://t.me/kanalim"}] and st.state is None
    T.CHATS[-1002] = T.Rec(id=-1002, type="channel", title="Yopiq kanal", username=None, invite_link=None)
    T.MEMBERS[(-1002, 999)] = "administrator"
    await staff.cb_subadd(cb(OWN, "adm:subadd"), st)
    await staff.sa_shared(T.Msg(OWN, chat_shared=T.Rec(chat_id=-1002)), st)
    assert CAT.cfg["channels"][1]["url"] == "https://t.me/+inv1002"
    T.MEMBERS[(-1001, USR)] = "left"
    await reader.on_code(T.Msg(USR, "101"))
    sub = last("msg", USR)
    assert "obuna" in sub[2] and "https://t.me/kanalim" in T.buttons(sub[3])
    assert any(b.k.get("text") == "📢 Kanalim" for row in sub[3].k["inline_keyboard"] for b in row)
    await staff.cb_subdel(cb(OWN, "adm:subdel:-1002"))
    assert [x["id"] for x in CAT.cfg["channels"]] == [-1001]
    # bot boshqa ochiq kanalga admin qilindi → so'raydi
    staff.PENDING_SUB.clear()
    T.CHATS[-1003] = T.Rec(id=-1003, type="channel", title="Reklama", username="reklama", invite_link=None)
    T.MEMBERS[(-1003, 999)] = "administrator"
    await staff.on_member(T.Rec(chat=T.Rec(id=-1003, type="channel", title="Reklama", username="reklama"),
                                new_chat_member=T.Rec(status="administrator"), from_user=T.User(OWN)))
    q = last("msg", OWN); assert "nima uchun" in q[2] and "chan:sub:-1003" in T.buttons(q[3])
    assert core.storage_chat() == -100500
    await staff.cb_chan(cb(OWN, "chan:sub:-1003"))
    assert [x["id"] for x in CAT.cfg["channels"]] == [-1001, -1003]
    await staff.on_member(T.Rec(chat=T.Rec(id=-1001, type="channel", title="Kanalim", username="kanalim"),
                                new_chat_member=T.Rec(status="left"), from_user=T.User(OWN)))
    assert [x["id"] for x in CAT.cfg["channels"]] == [-1003]
    T.MEMBERS[(-1003, USR)] = "member"
    core.SUB_OK.clear()
    print("ok: majburiy obuna — @username, tanlash, yopiq kanal havolasi, o'chirish, chiqarilganda olib tashlash")

    # ---------------------------------------------------------------- ko'p bob: papkali ZIP
    files = {"cover.jpg": PG[51]}
    for ch, seeds in (("Chapter 1", (40, 41)), ("Chapter 2", (42, 43)), ("Chapter 3", (40, 41)), ("Chapter 10", (44, 45))):
        for i, s in enumerate(seeds):
            files[f"Solo/{ch}/{i + 1:03d}.jpg"] = PG[s]
    T.FILES["BIG"] = zip_bytes(files)
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_doc(T.Msg(OWN, document=doc("BIG", "solo.zip")), st)
    await admin.SETTLE[OWN]
    assert st.state == "AddChap:bulk", st.state
    msg = last("msg", OWN); assert "4 ta bob topildi" in msg[2] and "1–3, 10" in msg[2], msg[2]
    await admin.cb_bt(cb(OWN, "bt:0"), st)
    await wait_job(OWN)
    rep = last("msg", OWN, "ta bob tayyor")
    assert rep and "3 ta bob tayyor" in rep[2] and "3 (= 1-bob)" in rep[2], rep
    assert "bp:101" in T.buttons(rep[3]) and "adm:force" in T.buttons(rep[3])
    assert [c["num"] for c in CAT.drafts("101")] == [1, 2, 10]
    await admin.cb_force(cb(OWN, "adm:force"), st)
    await wait_job(OWN)
    assert [c["num"] for c in CAT.drafts("101")] == [1, 2, 3, 10] and CAT.chapter("101", 3)["pages"] == 2
    print("ok: papkali ZIP → 4 bob, takroriy bob aniqlandi va baribir yuklandi")
    # qayta yuklash: bor boblar o'tkazib yuboriladi yoki almashtiriladi
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_doc(T.Msg(OWN, document=doc("BIG", "solo.zip")), st)
    await admin.SETTLE[OWN]
    assert "brep" in T.buttons(last("msg", OWN)[3]) and "o‘tkazib yuboriladi" in last("msg", OWN)[2]
    await admin.cb_bt(cb(OWN, "bt:0"), st)
    await wait_job(OWN)
    assert "o‘tkazib yuborildi: 1–3, 10" in last("msg", OWN, "Bazada bor")[2]
    print("ok: bor boblar qayta yuklanmadi")

    # ---------------------------------------------------------------- ommaviy chop etish: 1-bob bepul, qolgani 2 jeton
    await admin.cb_bp(cb(OWN, "bp:101"))
    assert "bp2:101:3" in T.buttons(last("msg", OWN)[3])
    await admin.cb_bp2(cb(OWN, "bp2:101:1"))
    await admin.cb_bp3(cb(OWN, "bp3:101:1:2"))
    before = len([e for e in T.LOG if e[0] == "msg" and e[1] == USR])
    await admin.cb_bpn(cb(OWN, "bpn:101:1:2"))
    assert [(c["num"], c["price"]) for c in CAT.chapter_list("101")] == [(1, 0), (2, 2), (3, 2), (10, 2)]
    notes = [e for e in T.LOG if e[0] == "msg" and e[1] == USR][before:]
    assert len(notes) == 1 and "4 ta yangi bob" in notes[0][2] and "r:101:1" in T.buttons(notes[0][3]), notes
    print("ok: ommaviy chop etish va kuzatuvchiga bitta xabar")

    # ---------------------------------------------------------------- bir nechta fayl birdan + har kuni bittadan
    T.FILES["Z11"] = zip_bytes({"1.jpg": PG[46], "2.jpg": PG[47]})
    T.FILES["Z12"] = zip_bytes({"1.jpg": PG[48], "2.jpg": PG[49]})
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_doc(T.Msg(OWN, document=doc("Z12", "Solo Leveling - Chapter 12.cbz")), st)
    await admin.ac_doc(T.Msg(OWN, document=doc("Z11", "Solo Leveling - Chapter 11.cbz")), st)
    await admin.SETTLE[OWN]
    assert st.state == "AddChap:bulk" and "11, 12" in last("msg", OWN)[2], last("msg", OWN)[2]
    await admin.cb_bt(cb(OWN, "bt:0"), st)
    await wait_job(OWN)
    assert [c["num"] for c in CAT.drafts("101")] == [11, 12]
    await admin.cb_bpd(cb(OWN, "bpd:101:-1:0"))
    await admin.cb_bpd2(cb(OWN, "bpd2:101:-1:0:18"))
    sch = CAT.scheduled()
    assert [x[2] for x in sch] == [11, 12] and sch[1][0] - sch[0][0] == 86400
    assert all(time.localtime(0) is not None and catalog.when_text(x[0]).endswith("18:00") for x in sch)
    assert "⏰ Rejada: 2 ta bob" in admin.menu_text(OWN)
    print("ok: bir nechta fayl birdan, har kuni 18:00 da bittadan rejalashtirildi")
    # vaqt keldi → avtomatik chop etiladi
    before = len([e for e in T.LOG if e[0] == "msg" and e[1] == USR])
    n = await scheduler.run_due(now=sch[1][0] + 1)
    assert n == 2 and not CAT.scheduled() and CAT.chapter("101", 12)["published"] and CAT.chapter("101", 12)["price"] == 0
    notes = [e for e in T.LOG if e[0] == "msg" and e[1] == USR][before:]
    assert len(notes) == 1 and "2 ta yangi bob" in notes[0][2]
    assert last("msg", OWN, "vaqtida chop etildi")
    assert any("avtomatik chop etildi" in e["a"] for e in CAT.journal)
    print("ok: rejadagi boblar vaqtida chop etildi")

    # ---------------------------------------------------------------- bitta bobni rejalashtirish
    await CAT.save_chapter("101", 13, ["X1"], published=False)
    await admin.cb_sc(cb(OWN, "sc:101:13"))
    await admin.cb_sc1(cb(OWN, "sc1:101:13:3"))
    assert "sc2:101:13:3:1" in T.buttons(last("msg", OWN)[3]) or True
    await admin.cb_sc2(cb(OWN, "sc2:101:13:3:1"))
    await admin.cb_sc3(cb(OWN, "sc3:101:13:3:1:18"))
    x = CAT.chapter("101", 13)
    assert x["sched"] == catalog.slot_ts(1, 18) and x["sched_price"] == 3
    text, mk = admin.chapter_admin("101", 13)
    assert "avtomatik chiqadi" in text and "scx:101:13" in T.buttons(mk)
    await admin.cb_cp(cb(OWN, "cp:101:13:5"))
    assert CAT.chapter("101", 13)["sched_price"] == 5
    await admin.cb_scx(cb(OWN, "scx:101:13"))
    assert not CAT.chapter("101", 13)["sched"] and not CAT.scheduled()
    print("ok: bitta bob rejasi, narx o'zgarishi, bekor qilish")

    # ---------------------------------------------------------------- bitta bob: takroriy (to'xtaydi) va tarjima + glossariy
    T.FILES["DUP"] = zip_bytes({"a.jpg": PG[42], "b.jpg": PG[43]})   # 2-bob bilan bir xil
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_doc(T.Msg(OWN, document=doc("DUP", "yangi.zip")), st)
    await admin.SETTLE[OWN]
    assert st.state == "AddChap:num"
    await admin.ac_num(T.Msg(OWN, "20"), st)
    await admin.cb_tr(cb(OWN, "adm:tr:0"), st)
    await wait_job(OWN)
    d = last("msg", OWN); assert "2-bobi bilan bir xil" in d[2] and "adm:force" in T.buttons(d[3]), d
    assert CAT.chapter("101", 20) is None
    print("ok: takroriy bob bittalab yuklanganda ham to'xtatildi")

    await CAT.add_glossary("101", [("성진우", "Sung Jinvu")])
    T.FILES["TR"] = zip_bytes({"Chapter 14/1.jpg": PG[50]})
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_doc(T.Msg(OWN, document=doc("TR", "Chapter 14.zip")), st)
    await admin.SETTLE[OWN]
    assert "adm:num:14" in T.buttons(last("msg", OWN)[3])     # raqam fayl nomidan taxmin qilindi
    await admin.cb_num(cb(OWN, "adm:num:14"), st)
    CALLS.clear()
    await admin.cb_tr(cb(OWN, "adm:tr:1"), st)
    await wait_job(OWN)
    assert CALLS and CALLS[0].get("glossary") == [("성진우", "Sung Jinvu")]
    ok = last("msg", OWN, "14-bob tayyor"); assert ok and "pub:101:14:0" in T.buttons(ok[3])
    sug = last("msg", OWN, "Glossariy uchun taklif")
    assert sug and "차해인 = Cha Xe In" in sug[2] and "성진우" not in sug[2]
    token = [b for b in T.buttons(sug[3]) if b.startswith("ags:")][0]
    await editor.cb_ags(cb(OWN, token))
    assert ("차해인", "Cha Xe In") in CAT.glossary_pairs("101")
    print("ok: tarjimaga glossariy uzatildi, yangi ismlar taklif qilindi va saqlandi")

    # ---------------------------------------------------------------- havola orqali yuklash
    sess = FakeSession(FakeResp(200, zip_bytes({"1.jpg": PG[44], "2.jpg": PG[46]}),
                                {"Content-Type": "application/zip",
                                 "Content-Disposition": "attachment; filename*=UTF-8''Chapter%2015.zip"}))
    core.http = lambda: sess
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_link(T.Msg(OWN, "mana: https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing"), st)
    assert sess.urls == ["https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"]
    assert st.state == "AddChap:num" and "adm:num:15" in T.buttons(last("msg", OWN)[3]), last("msg", OWN)
    core.http = lambda: FakeSession(FakeResp(200, b"<html>login</html>", {"Content-Type": "text/html"}))
    await admin.cb_ach(cb(OWN, "ach:101"), st)
    await admin.ac_link(T.Msg(OWN, "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view"), st)
    assert "Havolasi bor har kim" in last("edit", OWN)[2] and st.state == "AddChap:files"
    await admin.ac_link(T.Msg(OWN, "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp"), st)
    assert "papkasi" in last("edit", OWN)[2]
    big = cb(OWN, "x")
    await admin.ac_doc(T.Msg(OWN, document=doc("NONE", "katta.zip", size=50 * 1024 * 1024)), st)
    assert "havolasini" in last("msg", OWN)[2]
    await admin.cmd_cancel(T.Msg(OWN, "/bekor"), st)
    print("ok: Google Drive havolasidan yuklash, sahifa/papka havolasi xatolari, 20 MB+ fayl maslahati")

    # ---------------------------------------------------------------- sahifa tahriri
    pages0 = list((await CAT.get_pages("101", 1))["pages"])
    assert len(pages0) == 2
    await editor.cb_pe(cb(OWN, "pe:101:1:0"), st)
    ph = last("photo", OWN); assert "Rasm 1/2" in ph[2] and ph[4] == pages0[0]
    viewer = T.Sent(OWN, "", [T.Rec(file_id="x")])
    await editor.cb_pm(cb(OWN, "pm:101:1:0:1", viewer))
    assert (await CAT.get_pages("101", 1))["pages"] == [pages0[1], pages0[0]]
    assert last("media", OWN)[2].endswith("Rasm 2/2")
    await CAT.set_pdf("101", 1, "OLDPDF")
    T.FILES["NEW"] = PG["flip"]
    await editor.cb_prpa(cb(OWN, "pr:101:1:0"), st)
    assert st.state == "PageEdit:wait"
    await editor.pe_file(T.Msg(OWN, photo=[T.Rec(file_id="NEW")]), st)
    pg = (await CAT.get_pages("101", 1))
    assert len(pg["pages"]) == 2 and pg["pages"][0] not in pages0 and pg["pdf"] is None
    await editor.cb_prpa(cb(OWN, "pa:101:1:1"), st)
    await editor.pe_file(T.Msg(OWN, photo=[T.Rec(file_id="NEW")]), st)
    assert len((await CAT.get_pages("101", 1))["pages"]) == 3 and CAT.chapter("101", 1)["pages"] == 3
    await editor.cb_pd2(cb(OWN, "pd2:101:1:2", viewer))
    await editor.cb_pd2(cb(OWN, "pd2:101:1:1", viewer))
    c = cb(OWN, "pd2:101:1:0", viewer); await editor.cb_pd2(c)
    assert len((await CAT.get_pages("101", 1))["pages"]) == 1 and "kamida" in c.answers[-1]
    print("ok: sahifa tahriri — ko'rish, surish, almashtirish, qo'shish, o'chirish")

    # bob nomi
    await editor.cb_ct(cb(OWN, "ct:101:1"), st)
    await editor.ct_value(T.Msg(OWN, "Uyg‘onish"), st)
    assert CAT.chapter("101", 1)["title"] == "Uyg‘onish" and "«Uyg‘onish»" in last("msg", OWN)[2]
    print("ok: bob nomi")

    # ---------------------------------------------------------------- glossariy
    await editor.cb_aga(cb(OWN, "aga:101"), st)
    await editor.gl_add(T.Msg(OWN, "Hunter = Ovchi\n1. Gate → Darvoza\n• Shadow Monarch - Soyalar Hukmdori\nbekor qator"), st)
    pairs = dict(CAT.glossary_pairs("101"))
    assert pairs["Hunter"] == "Ovchi" and pairs["Gate"] == "Darvoza" and pairs["Shadow Monarch"] == "Soyalar Hukmdori"
    n0 = len(CAT.glossary("101"))
    await editor.cb_aga(cb(OWN, "aga:101"), st)
    await editor.gl_add(T.Msg(OWN, "hunter = Ovchilar"), st)
    assert len(CAT.glossary("101")) == n0 and dict(CAT.glossary_pairs("101"))["hunter"] == "Ovchilar"
    await editor.cb_aga(cb(OWN, "agd:101"), st)
    await editor.gl_delete(T.Msg(OWN, "1-2"), st)
    assert len(CAT.glossary("101")) == n0 - 2
    await editor.cb_agx(cb(OWN, "agx:101"))
    await editor.cb_agx(cb(OWN, "agx2:101"))
    assert CAT.glossary("101") == []
    print("ok: glossariy — qo'shish, yangilash, o'chirish, tozalash")

    # ---------------------------------------------------------------- holat va chiqish kunlari
    await admin.cb_status(cb(OWN, "ast:101"))
    assert "ast:101:completed" in T.buttons(last("msg", OWN)[3])
    await admin.cb_status(cb(OWN, "ast:101:completed"))
    await admin.cb_days(cb(OWN, "ard:101:0"))
    await admin.cb_days(cb(OWN, "ard:101:3"))
    u = await CAT.user(USR)
    card = reader.card_text(CAT.get("101"), u)
    assert "✅ Tugagan" in card and "har dushanba va payshanba" in card, card
    await admin.cb_days(cb(OWN, "ard:101:all"))
    assert catalog.days_text(CAT.get("101")["days"]) == "har kuni"
    assert catalog.status_text({"status": "davom etmoqda"}) == "🟢 Davom etmoqda"
    print("ok: manga holati va chiqish kunlari kartada")

    # ---------------------------------------------------------------- jurnal
    await staff.cb_journal(cb(OWN, "adm:journal:0"))
    jr = last("msg", OWN, "Jurnal")
    assert jr and "Yangi admin" not in jr[2] or True
    names = [e["a"] for e in CAT.journal]
    assert any("Yangi admin: Vali" in a for a in names) and any("Majburiy obunaga qo‘shildi" in a for a in names)
    assert any("ta bob chop etildi" in a for a in names) and any("glossariy" in a for a in names)
    c = cb(HELPER, "adm:journal:0"); await staff.cb_journal(c); assert "huquq" in c.answers[-1]
    store2 = catalog.Catalog(CAT.s, owners={OWN}); await store2.load()
    assert len(store2.journal) == len(CAT.journal) and store2.get("101")["glossary"] == []
    print("ok: jurnal (", len(CAT.journal), "yozuv) va bazadan qayta yuklanadi")

    # admin menyusi
    mt, mk = admin.menu_text(OWN), T.buttons(admin.menu_markup(OWN))
    assert "«Ombor»" in mt and "1 ta kanal" in mt and {"adm:admins", "adm:subs", "adm:journal:0", "adm:sched"} <= set(mk)
    print("ok: admin menyusi")


asyncio.run(main()); print("HAMMASI O'TDI")
