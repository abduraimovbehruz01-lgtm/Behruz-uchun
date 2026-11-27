"""Torii tarjimoni: sahifani Torii tarjima qiladi; kredit tugasa / band bo'lsa — Gemini; admin tanlovi va kreditlar."""
import asyncio, base64, io, json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_tg as T
T.install()
OWN = 100001
os.environ.update(BOT_TOKEN="1:x", GEMINI_API_KEY="k", OWNER_IDS=str(OWN), TORII_API_KEY="sk_torii_test")
os.environ.pop("FIREBASE_SERVICE_ACCOUNT", None)
import core, admin, staff, torii, traffic, typeset
from PIL import Image, ImageDraw

CAT = core.CAT
GEM_CALLS = []


async def fake_read(session, jpeg, **k):
    GEM_CALLS.append(k)
    return {"blocks": [], "source_language": "inglizcha", "model": "fake", "names": []}
core.GEM.read_page = fake_read


def png_data_url(w=500, h=700, color=(250, 250, 250)):
    im = Image.new("RGB", (w, h), color)
    ImageDraw.Draw(im).text((40, 40), "Salom!", fill=(0, 0, 0))
    b = io.BytesIO(); im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def parse_form(body: bytes, ctype: str) -> dict:
    boundary = ctype.split("boundary=")[1].encode()
    out = {}
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, val = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        out[name] = val[:-2] if val.endswith(b"\r\n") else val
    return out


class R:
    def __init__(self, status, text="", headers=None):
        self.status, self._t, self.headers = status, text, headers or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def text(self): return self._t


class Torii:
    def __init__(self):
        self.script, self.forms, self.credits = [], [], 120.0

    def post(self, url, data=None, headers=None, timeout=None):
        assert url == "https://api.toriitranslate.com/api/v2/upload"
        assert headers["Authorization"] == "Bearer sk_torii_test"
        form = parse_form(data, headers["Content-Type"])
        self.forms.append(form)
        step = self.script.pop(0) if self.script else "ok"
        if step == "ok":
            n = len(self.forms)
            body = {"image": png_data_url(), "inpainted": png_data_url(), "context": f"ctx-{n}",
                    "text": [{"x": 10, "y": 20, "width": 100, "height": 40, "text": "Salom!", "originalText": "Hello!"},
                             {"x": 5, "y": 300, "width": 80, "height": 30, "text": " ", "originalText": "..."}],
                    "metadata": {"credits_used": 1.5}}
            self.credits -= 1.5
            return R(200, json.dumps(body), {"success": "true", "credits": str(self.credits)})
        if step == "texts":
            return R(422, json.dumps({"error": "texts is required"}))
        if step == "busy":
            return R(429, "Too Many Requests")
        if step == "nocredit":
            return R(402, json.dumps({"error": "Not enough credits"}))
        if step == "badkey":
            return R(401, json.dumps({"error": "Invalid API key"}))
        raise AssertionError(step)

    def get(self, url, headers=None, timeout=None):
        assert url == "https://api.toriitranslate.com/api/credits"
        return R(200, json.dumps({"credits": self.credits}))


def last(kind=None, chat=None, has=None):
    for e in reversed(T.LOG):
        if (kind is None or e[0] == kind) and (chat is None or e[1] == chat) and (has is None or has in (e[2] or "")):
            return e


async def main():
    await CAT.load()
    import types
    _sleep = asyncio.sleep
    torii.asyncio = types.SimpleNamespace(sleep=lambda s: _sleep(0), Lock=asyncio.Lock, to_thread=asyncio.to_thread,
                                          TimeoutError=asyncio.TimeoutError)
    api = Torii()
    torii.http = lambda: api
    page = Image.new("RGB", (2400, 3200), (255, 255, 255))
    assert torii.configured() and torii.chosen() and torii.active()

    # 1) Torii sahifani tarjima qiladi: glossariy, ko'rsatma, o'zbek tili, kreditlar
    t0 = traffic.used()
    out, blocks, lang = await core.translate_image(page, 7, glossary=[("Jinwoo", "Jinvu"), ("Hunter", "Ovchi")])
    assert out.size == (500, 700) and [b.text for b in blocks] == ["Salom!"] and blocks[0].original == "Hello!"
    assert blocks[0].box == (10, 20, 110, 60) and not GEM_CALLS
    f = api.forms[-1]
    assert f["target_lang"] == b"uz" and f["translator"] == b"gemini-3.1-flash-lite" and b"U+2018" in f["custom_prompt"]
    assert json.loads(f["glossary"]) == [{"key": "Jinwoo", "value": "Jinvu"}, {"key": "Hunter", "value": "Ovchi"}]
    assert "context" not in f and "texts" not in f
    img = Image.open(io.BytesIO(f["file"]))
    assert img.width == torii.MAX_WIDTH                                  # 2400 → 1600 (trafik tejaladi)
    assert torii.STATE["credits"] == 118.5 and traffic.used() > t0 + len(f["file"])
    print("ok: Torii sahifani tarjima qiladi (glossariy, o‘zbekcha ko‘rsatma, 1600 px, kredit qoldig‘i, trafik)")

    # 2) kontekst keyingi sahifaga uzatiladi
    await core.translate_image(page, 7)
    assert api.forms[-1]["context"] == b"ctx-1"
    await core.translate_image(page, 8)
    assert "context" not in api.forms[-1]                                # boshqa foydalanuvchi — o'z konteksti
    print("ok: kontekst sahifadan sahifaga (har foydalanuvchiga alohida)")

    # 3) «texts» majburiy bo'lsa — o'zi moslashadi
    api.script = ["texts"]
    out, blocks, _ = await core.translate_image(page, 9)
    assert blocks and api.forms[-1]["texts"] == b"[]" and torii.STATE["texts"] is True
    print("ok: API «texts» maydonini so‘rasa — o‘zi qo‘shib qayta yuboradi")

    # 4) band — qayta urinadi; baribir band bo'lsa — Gemini, 2 daqiqa Torii chetda
    api.script = ["busy", "ok"]
    out, blocks, _ = await core.translate_image(page, 9)
    assert blocks and not GEM_CALLS
    api.script = ["busy"] * 4
    n = len(api.forms)
    out, blocks, _ = await core.translate_image(page, 9)
    assert len(GEM_CALLS) >= 1 and len(api.forms) - n == 4 and not torii.active() and torii.STATE["why"] == "band"
    g = len(GEM_CALLS)
    await core.translate_image(page, 9)
    assert len(GEM_CALLS) > g and len(api.forms) - n == 4                # Torii chetda — to'g'ridan-to'g'ri Gemini
    torii.STATE.update(down_until=0.0, why="")
    print("ok: band — qayta urinish, keyin vaqtincha Gemini")

    # 5) kredit tugadi — Gemini, bosh adminga xabar; kredit qo'shilsa — yana Torii
    api.script = ["nocredit"]
    g = len(GEM_CALLS)
    await core.translate_image(page, 9)
    assert len(GEM_CALLS) > g and torii.STATE["why"] == "kredit tugagan" and last("msg", OWN, "kreditlari tugadi")
    assert "vaqtincha ishlamayapti" in torii.status_line()
    api.credits = 0
    await torii.tick(now=time.time() + 400)
    assert not torii.active()
    api.credits = 50
    await torii.tick(now=time.time() + 800)
    assert torii.active() and last("msg", OWN, "yana Torii orqali")
    print("ok: kredit tugasa — Gemini va xabar; kredit qo‘shilgach — o‘zi Torii’ga qaytadi")

    # 6) kam kredit — bir marta ogohlantirish
    api.credits = 12
    torii.STATE["low_warned"] = False
    await core.translate_image(page, 9)
    assert last("msg", OWN, "kam qoldi")
    n0 = len(T.LOG)
    await core.translate_image(page, 9)
    assert not any("kam qoldi" in (x[2] or "") for x in T.LOG[n0:])
    print("ok: kredit kam qolsa — bir marta ogohlantirish")

    # 7) kalit ishlamasa — Gemini va xabar
    api.script = ["badkey"]
    await core.translate_image(page, 9)
    assert torii.STATE["why"] == "kalit ishlamayapti" and last("msg", OWN, "kaliti ishlamayapti")
    torii.STATE.update(down_until=0.0, why="")
    print("ok: kalit ishlamasa — Gemini va bosh adminga xabar")

    # 8) admin: statistikada holat, tanlov; Gemini tanlansa Torii chaqirilmaydi
    api.credits = 80
    torii.STATE["credits_at"] = 0                                         # kesh eskirdi — yangisi so'raladi
    await admin.send_stats(OWN)
    e = last("msg", OWN, "Statistika")
    assert "🌐 Tarjimon: Torii" in e[2] and "🪙 80 kredit" in e[2] and "adm:trn:gemini" in T.buttons(e[3])
    c = T.CallbackQuery("adm:trn:gemini", T.User(OWN), T.Sent(OWN, "x"))
    await admin.cb_translator(c)
    assert CAT.cfg["translator"] == "gemini" and not torii.active()
    n, g = len(api.forms), len(GEM_CALLS)
    await core.translate_image(page, 9)
    assert len(api.forms) == n and len(GEM_CALLS) == g + 1
    assert "Gemini (Torii o‘chirilgan)" in admin.menu_text(OWN)
    c = T.CallbackQuery("adm:trn:torii", T.User(555), T.Sent(555, "x"))
    await admin.cb_translator(c)
    assert CAT.cfg["translator"] == "gemini"                              # faqat bosh admin
    c = T.CallbackQuery("adm:trn:torii", T.User(OWN), T.Sent(OWN, "x"))
    await admin.cb_translator(c)
    assert CAT.cfg["translator"] == "torii" and torii.active() and "🌐 Tarjimon: Torii" in admin.menu_text(OWN)
    print("ok: admin — tarjimon holati, kreditlar va Torii/Gemini tanlovi (faqat bosh admin)")

    # 9) bob yuklashda (build_pages) Torii ishlaydi
    raw = typeset.to_jpeg(Image.new("RGB", (800, 1200), (255, 255, 255)), 85)
    res = await admin.build_pages(OWN, [("01.jpg", raw), ("02.jpg", raw)], True, [], [], None, "", None, {"cancel": False})
    assert len(res["jpegs"]) >= 2 and res["skipped"] == 0 and not res["stop"]
    print("ok: bob yuklashda sahifalar Torii orqali tarjima qilinadi")


asyncio.run(main()); print("HAMMASI O'TDI")
