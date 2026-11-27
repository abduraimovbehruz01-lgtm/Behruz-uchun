"""4-bosqich sinovi: Mini App API (initData, katalog, manga, bob, sotib olish, baho, reyting, profil, obuna),
rasm havolalari imzosi, Cloudflare ro'yxatdan o'tishi, reyting (noyob o'quvchilar, ↑↓, baholar) botda,
kichik rasmlar, trafik hisobi va chegarasi, ilova banneri."""
import asyncio, hashlib, hmac, io, json, os, sys, time
from urllib.parse import urlencode
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_tg as T
T.install()
OWN = 100001
os.environ.update(BOT_TOKEN="1:x", GEMINI_API_KEY="k", OWNER_IDS=str(OWN))
os.environ.pop("FIREBASE_SERVICE_ACCOUNT", None)
import core, reader, admin, staff, samples, catalog, autosrc, rating, thumbs, traffic, webapp
from PIL import Image

CAT = core.CAT
USR, USR2 = 7, 8


def jpg(seed, w=600, h=900):
    img = samples.page_jp(seed=seed)[0].resize((w, h))
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=80); return b.getvalue()


def last(kind=None, chat=None, has=None):
    for e in reversed(T.LOG):
        if (kind is None or e[0] == kind) and (chat is None or e[1] == chat) and (has is None or has in (e[2] or "")):
            return e


def cb(uid, data, message=None):
    return T.CallbackQuery(data, T.User(uid), message or T.Sent(uid, "x"))


def make_init(uid, token="1:x", auth=None, start="", tamper=False):
    user = json.dumps({"id": uid, "first_name": "Ali", "username": "ali", "photo_url": "https://t.me/i/u.jpg"},
                      separators=(",", ":"))
    pairs = {"auth_date": str(int(auth or time.time())), "query_id": "AAEx", "user": user}
    if start:
        pairs["start_param"] = start
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if tamper:
        pairs["user"] = pairs["user"].replace(str(uid), str(uid + 1))
    return urlencode(pairs)


async def call(name, uid, **p):
    user = webapp.check_init(make_init(uid))
    u = await CAT.user(uid, user["first_name"], user["username"])
    try:
        return 200, await webapp.dispatch(name, uid, u, user, p)
    except webapp.ApiError as e:
        return e.status, e.payload


class R:
    def __init__(self, status, text): self.status, self._t = status, text
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def text(self): return self._t
    async def read(self): return self._t.encode()


class Sess:
    def __init__(self): self.urls = []
    def get(self, url, **k):
        self.urls.append(url)
        if url.endswith("/health"):
            return R(200, "Manhora rasmlar xizmati ishlayapti ✅")
        return R(200, "img")


async def main():
    await CAT.load()
    await traffic.load()
    await staff.on_member(T.Rec(chat=T.Chat(-100500, "channel", "Ombor"), new_chat_member=T.Rec(status="administrator"),
                                from_user=T.User(OWN)))
    # ---------------------------------------------------------------- katalog tayyorlash
    await CAT.add_manga("Solo Leveling", "Ovchi haqida", ["Jangari", "Fantastika"], code="101", by=OWN)
    await CAT.add_manga("Bizning maktab", "Kulgili", ["Komediya"], code="102", by=OWN)
    await CAT.add_manga("Yashirin manga", "", ["Drama"], code="103", by=OWN)
    await CAT.update_manga("103", hidden=True)
    T.FILES["COV101"] = jpg(1, 700, 1000)
    await CAT.update_manga("101", cover="COV101")
    for i, s in enumerate(range(10, 16)):
        T.FILES[f"PG{s}"] = jpg(s)
    await CAT.save_chapter("101", 1, ["PG10", "PG11"], published=True)
    await CAT.save_chapter("101", 2, ["PG12", "PG13"], price=5, published=False)
    await CAT.publish("101", 2, 5)
    await CAT.save_chapter("101", 3, ["PG14"], published=False)              # qoralama — ko'rinmaydi
    await CAT.save_chapter("102", 1, ["PG15"], published=True)
    assert CAT.chapter("101", 2)["pub"] and CAT.chapter("101", 1)["pub"]
    print("ok: katalog tayyor (chop etilgan vaqt yoziladi)")

    # ---------------------------------------------------------------- initData tekshiruvi
    good = webapp.check_init(make_init(USR, start="c101_2"))
    assert good and good["id"] == USR and good["start_param"] == "c101_2"
    assert webapp.check_init(make_init(USR, tamper=True)) is None
    assert webapp.check_init(make_init(USR, token="2:y")) is None
    assert webapp.check_init(make_init(USR, auth=time.time() - 3 * 86400)) is None
    assert webapp.check_init("") is None and webapp.check_init("hash=abc") is None
    print("ok: initData — to‘g‘ri imzo o‘tadi; o‘zgartirilgan, boshqa token, eskirgan — o‘tmaydi")

    # ---------------------------------------------------------------- rasm havolalari
    assert webapp.img_url("FID") == ""                            # Cloudflare yo'q, sahifa — havola yo'q
    webapp.PUBLIC_URL = "https://bot.example"
    small = webapp.img_url("FIDsmall", small=True)
    assert small.startswith("https://bot.example/img/FIDsmall?e=") and "&s=" in small
    exp = int(small.split("e=")[1].split("&")[0])
    assert small.endswith(webapp.sign("img-r", "FIDsmall", exp)) and exp > time.time() + 7 * 86400
    await CAT.set_cfg(img_base="https://manhora-img.test.workers.dev")
    page = webapp.img_url("FIDpage")
    exp = int(page.split("e=")[1].split("&")[0])
    assert page == f"https://manhora-img.test.workers.dev/i/FIDpage?e={exp}&s={webapp.sign('img-w', 'FIDpage', exp)}"
    assert webapp.sign("img-w", "F", 1) != webapp.sign("img-r", "F", 1)     # Worker imzosi Render'da ishlamaydi
    assert webapp.img_url("FIDpage") == page                                # bir kun davomida o'zgarmaydi
    await CAT.set_cfg(img_base="")
    print("ok: imzolangan rasm havolalari (Worker va Render uchun alohida kalit, kun bo‘yi bir xil)")

    # ---------------------------------------------------------------- boot
    st, b = await call("boot", USR)
    assert st == 200 and b["me"]["id"] == USR and b["img"] is False
    codes = [m["code"] for m in b["catalog"]]
    assert set(codes) == {"101", "102"} and "103" not in codes             # yashirin manga ko'rinmaydi
    m101 = next(m for m in b["catalog"] if m["code"] == "101")
    assert m101["chapters"] == 2 and m101["last"] == "2" and m101["paid"] and m101["genres"] == ["Jangari", "Fantastika"]
    assert b["genres"] == ["Fantastika", "Jangari", "Komediya"] and b["latest"][0]["code"] in ("101", "102")
    assert b["banner"] and b["continue"] == [] and b["need_sub"] == [] and b["bot"] == "ManhoraBot"
    print("ok: /api/boot — katalog (yashirinsiz), janrlar, yangi boblar, banner")

    # ---------------------------------------------------------------- manga va bob
    st, d = await call("manga", USR, code="101")
    assert st == 200 and [c["n"] for c in d["chapters"]] == ["1", "2"]         # qoralama yo'q
    c1, c2 = d["chapters"]
    assert c1["open"] and not c2["open"] and c2["price"] == 5 and not c1["read"]
    assert d["manga"]["fav"] is False and d["manga"]["can_rate"] is False and d["manga"]["progress"] is None
    assert (await call("manga", USR, code="103"))[0] == 404
    assert (await call("manga", OWN, code="103"))[0] == 200                   # admin yashirinni ko'radi
    st, ch = await call("chapter", USR, code="101", num="1")
    assert st == 200 and ch["bot"] is True and ch["pages"] is None and ch["next"] == "2"   # Cloudflare yo'q → botda
    st, e = await call("chapter", USR, code="101", num="2")
    assert st == 402 and e["price"] == 5 and e["balance"] == 0
    assert (await call("chapter", USR, code="101", num="3"))[0] == 404          # qoralama
    # botda o'qish
    n0 = len(T.LOG)
    st, r = await call("readbot", USR, code="101", num="1")
    assert st == 200 and r["ok"]
    await asyncio.sleep(1.6)
    assert any(x[0] in ("album", "photo") and x[1] == USR for x in T.LOG[n0:])
    assert (await CAT.user(USR))["progress"]["101"] == 1.0
    reader.READING.clear()
    print("ok: manga sahifasi (ochiq/pullik boblar), Cloudflare’siz — bob botga yuboriladi, pullik — 402")

    # Cloudflare ulangan: sahifalar havolasi, progress, noyob o'quvchi
    await CAT.set_cfg(img_base="https://manhora-img.test.workers.dev")
    views0 = CAT.get("102")["views"]
    st, ch = await call("chapter", USR, code="102", num="1")
    assert st == 200 and ch["bot"] is False and len(ch["pages"]) == 1 and ch["pages"][0].startswith("https://manhora-img")
    assert CAT.get("102")["views"] == views0 + 1 and (await CAT.user(USR))["progress"]["102"] == 1.0
    await call("chapter", USR, code="102", num="1")
    assert rating.DAYS.get(rating.day(), {}).get("102") == 1 or (await CAT.s.get("reads", rating.day()))["102"] == 1
    print("ok: Cloudflare bilan — imzolangan sahifa havolalari, o‘qish va noyob o‘quvchi (qayta ochish sanalmaydi)")

    # sotib olish
    st, e = await call("buy", USR, code="101", num="2")
    assert st == 400 and "Jeton" in e["error"]
    await CAT.add_jetons(USR, 7)
    st, r = await call("buy", USR, code="101", num="2")
    assert st == 200 and r["balance"] == 2
    st, ch = await call("chapter", USR, code="101", num="2")
    assert st == 200 and len(ch["pages"]) == 2 and ch["prev"] == "1"
    st, d = await call("manga", USR, code="101")
    assert all(c["open"] for c in d["chapters"]) and all(c["read"] for c in d["chapters"]) and d["manga"]["progress"] == "2"
    print("ok: jeton bilan ochish, keyin bob o‘qiladi, boblar «o‘qildi» belgisi")

    # sevimli, kuzatish, baho
    st, r = await call("fav", USR, code="101"); assert r["on"] is True
    st, r = await call("follow", USR, code="101"); assert r["on"] is True and USR in CAT.get("101")["followers"]
    st, e = await call("rate", USR2, code="101", stars=5)
    assert st == 400 and "o‘qing" in e["error"]                                # o'qimagan — baho qo'ya olmaydi
    st, r = await call("rate", USR, code="101", stars=4)
    assert st == 200 and r["stars"] == 4.0 and r["votes"] == 1
    st, r = await call("rate", USR, code="101", stars=2)
    assert r["stars"] == 2.0 and r["votes"] == 1                                # o'zgartirish — ovoz soni o'zgarmaydi
    assert (await call("rate", USR, code="101", stars=9))[0] == 400
    st, me = await call("me", USR)
    assert me["me"]["favs"] == ["101"] and me["favs"][0]["code"] == "101" and me["continue"][0]["code"] in ("101", "102")
    print("ok: sevimli, kuzatish, baho (o‘qimagan — yo‘q, o‘zgartirish), profil")

    # majburiy obuna
    await CAT.set_cfg(channels=[{"id": -100321, "title": "Manhora rasmiy", "url": "https://t.me/manhora_uz"}])
    T.MEMBERS[(-100321, USR2)] = "left"
    await CAT.user(USR2)
    await CAT.set_progress(USR2, "102", 1)
    st, e = await call("chapter", USR2, code="102", num="1")
    assert st == 403 and e["need_sub"] == [{"title": "Manhora rasmiy", "url": "https://t.me/manhora_uz"}]
    st, b2 = await call("boot", USR2)
    assert b2["need_sub"] and b2["need_sub"][0]["url"] == "https://t.me/manhora_uz"
    T.MEMBERS[(-100321, USR2)] = "member"
    st, r = await call("subcheck", USR2)
    assert r["ok"] is True
    st, ch = await call("chapter", USR2, code="102", num="1")
    assert st == 200
    await CAT.set_cfg(channels=[])
    print("ok: majburiy obuna — ilovada ham tekshiriladi, «Tekshirish» dan keyin ochiladi")

    # ---------------------------------------------------------------- reyting (o'tgan hafta bilan solishtirish)
    for i in range(7, 14):
        rating.DAYS[rating.day(-i)] = {"101": 3}                    # o'tgan hafta: 101 birinchi
    rating.DAYS[rating.day(-1)] = {"102": 5}
    await rating.refresh(force=True)
    wk = await rating.ranking("week")
    assert [x["code"] for x in wk] == ["102", "101"], wk
    assert wk[0]["new"] is True and wk[1]["delta"] == -1                       # 101: 1-o'rindan 2-ga tushdi
    st, r = await call("rating", USR, period="week")
    assert [x["code"] for x in r["items"]] == ["102", "101"] and r["items"][0]["title"] == "Bizning maktab"
    st, r = await call("rating", USR, period="stars")
    assert r["items"][0]["code"] == "101" and r["items"][0]["value"] == 2.0 and r["items"][0]["votes"] == 1
    assert rating.rank_of("102") == 1 and rating.rank_of("103") is None
    # botda
    text, markup = await reader.top_view("week")
    assert "shu hafta" in text and "m:102" in T.buttons(markup) and "top:stars" in T.buttons(markup)
    assert any("🥇" in b.k["text"] and "🆕" in b.k["text"] for row in markup.k["inline_keyboard"] for b in row)
    c = cb(USR, "top:month")
    await reader.cb_top(c)
    assert "shu oy" in last("msg", USR)[2] or "shu oy" in (c.message.text or "")
    print("ok: reyting — hafta/oy/barcha/baho, o‘rin o‘zgarishi (🆕, ⬇️), botda «🏆 Reyting»")

    # karta: baho va o'rin, baho tugmasi
    u = await CAT.user(USR)
    assert "⭐ 2.0 (1 baho)" in reader.card_text(CAT.get("101"), u) and "Haftalik reytingda 2-o‘rin" in reader.card_text(CAT.get("101"), u)
    mk = reader.card_markup(CAT.get("101"), u, USR)
    assert "rt:101" in T.buttons(mk)
    c = cb(USR2, "rt:101")
    await reader.cb_rate(c)
    assert "o‘qing" in c.answers[-1]                                           # USR2 101 ni o'qimagan
    sent = T.Sent(USR, "karta")
    c = cb(USR, "rt:101", sent)
    await reader.cb_rate(c)
    c = cb(USR, "rt:101:5", sent)
    await reader.cb_rate(c)
    assert "5 ⭐" in c.answers[-1] and rating.stars(CAT.get("101")) == (5.0, 1)
    print("ok: manga kartasida ⭐ baho va 🏆 o‘rin, «⭐ Baho berish» tugmasi")

    # ilova tugmalari (PUBLIC_URL bo'lsa)
    core.PUBLIC_URL = "https://bot.example"
    hm = reader.home_markup()
    assert "app:https://bot.example/app" in T.buttons(hm)
    assert "app:https://bot.example/app?m=101" in T.buttons(reader.card_markup(CAT.get("101"), u, USR))
    core.PUBLIC_URL = ""
    assert not any(x.startswith("app:") for x in T.buttons(reader.home_markup()))
    print("ok: «📱 Ilovani ochish» tugmalari (bosh menyu, manga kartasi)")

    # ---------------------------------------------------------------- kichik rasmlar
    n = await thumbs.run_once()
    assert n >= 4, n
    assert CAT.get("101").get("cover_t") and CAT.get("101")["cover_t_src"] == "COV101"
    assert CAT.chapter("101", 1).get("thumb") and CAT.chapter("102", 1).get("thumb")
    assert not CAT.chapter("101", 3).get("thumb")                              # qoralama uchun yasalmaydi
    im = Image.open(io.BytesIO(T.FILES[CAT.chapter("101", 1)["thumb"]]))
    assert im.size == (240, 320)
    im = Image.open(io.BytesIO(T.FILES[CAT.get("101")["cover_t"]]))
    assert im.size == (300, 420)
    assert await thumbs.run_once() == 0                                       # hammasi tayyor
    await CAT.set_pages("101", 1, ["PG11", "PG10"])
    assert not CAT.chapter("101", 1).get("thumb")                              # sahifa o'zgardi — qayta yasaladi
    assert await thumbs.run_once() == 1
    st, d = await call("manga", USR, code="101")
    assert d["chapters"][0]["thumb"].startswith("https://manhora-img") and d["manga"]["cover"]
    print("ok: kichik rasmlar — bob (240×320) va muqova (300×420), sahifa o‘zgarsa qayta yasaladi")

    # ---------------------------------------------------------------- Cloudflare ro'yxatdan o'tishi
    await CAT.set_cfg(img_base="")
    sess = Sess()
    webapp.http = lambda: sess
    url, ts = "https://manhora-img.behruz.workers.dev", int(time.time())
    sig = hmac.new(b"1:x", f"reg:{url}:{ts}".encode(), hashlib.sha256).hexdigest()
    try:
        await webapp.register_worker({"url": url, "ts": ts, "sig": "bad"})
        assert False
    except webapp.ApiError as e:
        assert e.status == 403
    try:
        await webapp.register_worker({"url": "https://evil.example.com", "ts": ts,
                                      "sig": hmac.new(b"1:x", f"reg:https://evil.example.com:{ts}".encode(),
                                                      hashlib.sha256).hexdigest()})
        assert False
    except webapp.ApiError as e:
        assert e.status == 400
    r = await webapp.register_worker({"url": url, "ts": ts, "sig": sig})
    assert r["ok"] and CAT.cfg["img_base"] == url and last("msg", OWN, "Cloudflare ulandi")
    assert sess.urls[0] == url + "/health" and sess.urls[1].startswith(url + "/i/")
    assert "Cloudflare orqali ✅" in admin.menu_text(OWN)
    print("ok: Cloudflare Worker o‘zini ulaydi (imzo, manzil va ishlashi tekshiriladi), bosh adminga xabar")

    # ---------------------------------------------------------------- banner
    st = T.FSM()
    await admin.cb_feature(cb(OWN, "afeat:102"))
    assert CAT.cfg["featured"] == ["102"]
    st, b = await call("boot", USR)
    assert [x["code"] for x in b["banner"]] == ["102"]
    await admin.cb_feature(cb(OWN, "afeat:102"))
    assert CAT.cfg["featured"] == []
    text, markup = admin.manga_admin("101")
    assert "afeat:101" in T.buttons(markup)
    print("ok: «⭐ Ilova banneriga» — admin tanlagan mangalar bannerda")

    # ---------------------------------------------------------------- trafik
    traffic.STATE.update(month=traffic.month(), bytes=0, warned=0)
    m = T.Rec(); m.k = {}
    from types import SimpleNamespace
    class Photo:                                     # aiogram usuliga o'xshash: model_fields + BufferedInputFile
        model_fields = {"chat_id": None, "photo": None, "caption": None}
        def __init__(self): self.chat_id, self.caption = 1, "salom"; self.photo = type("BufferedInputFile", (), {"data": b"x" * 5000})()
    assert 5600 <= traffic.estimate(Photo()) <= 6000
    traffic.add(int(1.7 * traffic.GB))
    assert traffic.used() == int(1.7 * traffic.GB) and not traffic.over()
    await traffic.tick()
    assert last("msg", OWN, "80%")                                            # 80% ogohlantirish
    n0 = len(T.LOG)
    await traffic.tick()
    assert len([x for x in T.LOG[n0:] if "80%" in (x[2] or "")]) == 0          # bir marta
    traffic.add(int(0.4 * traffic.GB))
    assert traffic.over()
    await traffic.tick()
    assert last("msg", OWN, "chegarasiga yetdi")
    # avto-manba to'xtaydi
    autosrc_res = await autosrc.process({"kind": "tg", "src": "x"})
    assert autosrc_res == "drop"                                              # manba yo'q — drop (tekshiruv tartibi)
    await CAT.set_cfg(sources=[{"id": "s1", "chat": -1009, "title": "K", "manga": "101", "kw": "", "tr": False,
                                "auto": True, "note": "ruxsat", "credit": "", "by": OWN}])
    assert await autosrc.process({"kind": "tg", "src": "s1", "id": "X", "name": "a.zip"}) == "later"
    assert await thumbs.run_once() == 0                                       # kichik rasmlar ham kutadi
    # statistika va chegara
    await admin.send_stats(OWN)
    e = last("msg", OWN, "Statistika")
    assert "📶 Trafik" in e[2] and "adm:tl:3" in T.buttons(e[3])
    c = cb(OWN, "adm:tl:3")
    await admin.cb_traffic_limit(c)
    assert CAT.cfg["traffic_gb"] == 3.0 and not traffic.over() and traffic.STATE["warned"] == 0   # 2.1/3 GB = 70%
    c = cb(555, "adm:tl:4")
    await admin.cb_traffic_limit(c)
    assert CAT.cfg["traffic_gb"] == 3.0                                        # faqat bosh admin
    await traffic.save()
    saved = await CAT.s.get("stats", "traffic")
    assert saved["bytes"] == traffic.used() and saved["month"] == traffic.month()
    traffic.STATE.update(bytes=0)
    await traffic.load()
    assert traffic.used() == saved["bytes"]
    print("ok: trafik — hisob, 80%/100% ogohlantirish (bir marta), avto-manba va kichik rasmlar to‘xtaydi, chegara tugmalari")

    # ---------------------------------------------------------------- ilova sahifasi fayli
    html = open(os.path.join(ROOT, "webapp", "index.html"), encoding="utf-8").read()
    for s in ("telegram-web-app.js", "api(\"boot\")", "X-Init-Data", "Haftaning topi", "Boblar", "Botda o‘qish",
              "Kanalga obuna bo‘ling", "data-tab=\"top\""):
        assert s in html, s
    print("ok: ilova sahifasi (index.html) joyida")


asyncio.run(main()); print("HAMMASI O'TDI")
