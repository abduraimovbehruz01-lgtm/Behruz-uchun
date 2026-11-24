"""bot.py oqimlari sinovi: soxta Telegram va soxta Gemini bilan (aiogram/aiohttp o'rnatilmagan muhitda ham ishlaydi)."""
import asyncio, io, os, sys, types, zipfile, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from PIL import Image

# ---------- soxta kutubxonalar
class Magic:
    def __getattr__(self, k): return self
    def __call__(self, *a, **k): return self
    def __eq__(self, o): return self
    def __hash__(self): return 1
def mod(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m
class Rec:
    def __init__(self, *a, **k): self.a, self.k = a, k; self.__dict__.update(k)
class Observer:
    def __call__(self, *f, **k): return lambda fn: fn
    def filter(self, *f): pass
    def register(self, fn): pass
class Dispatcher:
    def __init__(self): self.message = Observer(); self.callback_query = Observer(); self.startup = Observer(); self.shutdown = Observer()
class TelegramBadRequest(Exception): pass
if "aiohttp" not in sys.modules:
    web = mod("aiohttp.web", Application=Rec, Response=Rec, json_response=Rec, run_app=lambda *a, **k: None,
              AppRunner=Rec, TCPSite=Rec, Request=object)
    mod("aiohttp", ClientSession=Rec, ClientTimeout=Rec, ClientError=type("ClientError", (Exception,), {}), web=web)
mod("aiogram", Bot=lambda *a, **k: FakeBot(), Dispatcher=Dispatcher, F=Magic())
mod("aiogram.client"); mod("aiogram.client.default", DefaultBotProperties=Rec)
mod("aiogram.enums", ChatAction=Magic(), ParseMode=Magic())
mod("aiogram.exceptions", TelegramBadRequest=TelegramBadRequest)
mod("aiogram.filters", Command=Rec, CommandStart=Rec)
mod("aiogram.types", BotCommand=Rec, BufferedInputFile=Rec, CallbackQuery=object, FSInputFile=Rec,
    InlineKeyboardButton=Rec, InlineKeyboardMarkup=Rec, Message=object, ReplyParameters=Rec)
mod("aiogram.webhook"); mod("aiogram.webhook.aiohttp_server", SimpleRequestHandler=Rec, setup_application=lambda *a, **k: None)

FILES, OUT = {}, []
class FakeBot:
    async def download(self, file_id, timeout=30): return io.BytesIO(FILES[file_id])
    async def send_chat_action(self, **k): pass
    async def send_message(self, chat_id, text, **k): OUT.append(("text", text)); return Sent(text)
    async def send_document(self, chat_id, doc, **k): OUT.append(("doc", doc.k.get("filename") or doc.a[1])); return Sent("")
    async def get_chat_member(self, **k): return Rec(status="left")
class Sent:
    def __init__(self, text): self.text = text
    async def edit_text(self, text, reply_markup=None): self.text = text; OUT.append(("edit", text))
    async def delete(self): OUT.append(("del", self.text))
class User: id = 7; first_name = "Behruz"
class Chat: id = 7; type = "private"
class Msg:
    _n = 100
    def __init__(self, photo=None, document=None):
        Msg._n += 1; self.message_id = Msg._n; self.from_user = User(); self.chat = Chat()
        self.photo, self.document = photo, document
    async def answer(self, text, **k): OUT.append(("answer", text)); return Sent(text)
    async def answer_photo(self, f, caption=None, **k): OUT.append(("photo", caption, f.a[0])); return Sent("")
    async def answer_document(self, f, caption=None, **k):
        data = f.a[0] if f.a and isinstance(f.a[0], bytes) else open(f.a[0], "rb").read()
        OUT.append(("document", f.k.get("filename") or f.a[1], caption, data)); return Sent("")

os.environ.update(BOT_TOKEN="1:x", GEMINI_API_KEY="k", DAILY_LIMIT="5")
import bot as B, translator, samples

PAGE, ITEMS = samples.page_jp(jitter=0.03)
def jpg(img): b = io.BytesIO(); img.save(b, "JPEG", quality=90); return b.getvalue()
CALLS = {"n": 0, "fail_at": None, "fail_kind": "quota"}
async def fake_read_page(session, jpeg, context=None, part=None, glossary=None, notify=None, **k):
    CALLS["n"] += 1
    if CALLS["fail_at"] and CALLS["n"] >= CALLS["fail_at"]:
        raise translator.GeminiError(429, "limit", per_day=True, kind=CALLS["fail_kind"])
    return {"blocks": ITEMS, "source_language": "yaponcha", "model": "fake"}
B.GEM.read_page = fake_read_page

async def run_queue():
    for _ in range(400):
        await asyncio.sleep(0.05)
        if not B.QUEUES and not B.CURRENT and all(t.done() for t in B.WORKERS.values()): return
    raise TimeoutError("navbat tugamadi")

async def main():
    # 1) rasm (photo) → rasm qaytadi, tugmalar
    FILES["p1"] = jpg(PAGE)
    m = Msg(photo=[Rec(file_id="p1", file_size=1)])
    await B.on_photo(m); await run_queue()
    ph = [o for o in OUT if o[0] == "photo"]
    assert ph and "ta matn" in ph[0][1], OUT
    im = Image.open(io.BytesIO(ph[0][2])); assert im.size == PAGE.size
    print("1 ok:", ph[0][1])
    # 2) 📝 Matn tugmasi
    cid = list(B.CACHE)[-1]
    await B.cb_text(types.SimpleNamespace(data="t:" + cid, from_user=User(), message=Rec(message_id=5),
                                          answer=lambda *a, **k: asyncio.sleep(0)))
    t = [o for o in OUT if o[0] == "text"][-1][1]
    assert "Sen bu yerda" in t and "お前" in t, t
    print("2 ok: matn tugmasi")
    # 3) rasm fayl qilib → fayl qaytadi
    OUT.clear(); FILES["d1"] = jpg(PAGE)
    m = Msg(document=Rec(file_id="d1", file_name="bet_12.png", mime_type="image/png", file_size=1000))
    await B.on_document(m); await run_queue()
    d = [o for o in OUT if o[0] == "document"]
    assert d and d[0][1] == "bet_12_uz.jpg", OUT
    print("3 ok: fayl → fayl", d[0][1])
    # 4) ZIP bob → ZIP + PDF
    OUT.clear(); B.USAGE.clear()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in ("2.jpg", "1.jpg", "3.jpg"): zf.writestr(f"bob/{n}", jpg(PAGE))
    FILES["z1"] = buf.getvalue()
    m = Msg(document=Rec(file_id="z1", file_name="Bob 7.cbz", mime_type="application/octet-stream", file_size=len(FILES["z1"])))
    await B.on_document(m); await run_queue()
    d = [o for o in OUT if o[0] == "document"]
    assert [x[1] for x in d] == ["Bob 7_uz.cbz", "Bob 7_uz.pdf"], [x[1] for x in d]
    assert "3</b> sahifa" in d[0][2], d[0][2]
    with zipfile.ZipFile(io.BytesIO(d[0][3])) as zf:
        assert zf.namelist() == ["001.jpg", "002.jpg", "003.jpg", "tarjima_matni.txt"], zf.namelist()
        assert "Sen bu yerda" in zf.read("tarjima_matni.txt").decode()
    print("4 ok: CBZ → CBZ + PDF;", d[0][2].splitlines()[0])
    # 5) PDF bob → faqat PDF; limit (5) — 3 tasi ishlatilgan, 2 tasi qoladi
    OUT.clear()
    import chapters, tempfile
    tmp = tempfile.mkdtemp(); ps = []
    for i in range(4):
        p = os.path.join(tmp, f"{i}.jpg"); PAGE.save(p, quality=85); ps.append(p)
    pdf = chapters.write_pdf(ps, os.path.join(tmp, "in.pdf")); FILES["f1"] = open(pdf, "rb").read()
    m = Msg(document=Rec(file_id="f1", file_name="Naruto 700.pdf", mime_type="application/pdf", file_size=len(FILES["f1"])))
    await B.on_document(m); await run_queue()
    d = [o for o in OUT if o[0] == "document"]
    assert [x[1] for x in d] == ["Naruto 700_uz.pdf"], [x[1] for x in d]
    assert "2</b> sahifa" in d[0][2] and "limitingizdan 2" in d[0][2], d[0][2]
    print("5 ok: PDF → PDF, limit hisobga olindi")
    # 6) limit tugagan
    OUT.clear(); FILES["p2"] = jpg(PAGE)
    await B.on_photo(Msg(photo=[Rec(file_id="p2", file_size=1)])); await run_queue()
    assert any("limit tugadi" in str(o[1]) for o in OUT), OUT
    print("6 ok: kunlik limit xabari")
    # 7) bob o'rtasida Gemini limiti → tayyor sahifalar yuboriladi
    OUT.clear(); B.USAGE.clear(); CALLS.update(n=0, fail_at=3)
    m = Msg(document=Rec(file_id="z1", file_name="Bob 8.zip", mime_type="application/zip", file_size=len(FILES["z1"])))
    await B.on_document(m); await run_queue()
    d = [o for o in OUT if o[0] == "document"]
    assert d and d[0][1] == "Bob 8_uz.zip" and "2</b> sahifa" in d[0][2] and "limiti tugadi" in d[0][2], (d[0][1:3] if d else OUT)
    print("7 ok: qisman natija + sabab")
    # 8) bitta sahifada Gemini xatosi
    OUT.clear(); CALLS.update(n=0, fail_at=1, fail_kind="busy")
    await B.on_photo(Msg(photo=[Rec(file_id="p2", file_size=1)])); await run_queue()
    assert any("band" in str(o[1]) for o in OUT if o[0] == "edit"), OUT
    CALLS.update(fail_at=None)
    print("8 ok: Gemini band xabari")
    # 9) albom: 3 ta rasm teskari tartibda kelsa ham tartib bilan
    OUT.clear(); B.USAGE.clear(); order = []
    orig = B.do_page
    async def spy(item): order.append(item.order); await orig(item)
    B.do_page = spy
    ms = [Msg(photo=[Rec(file_id="p2", file_size=1)]) for _ in range(3)]
    for mm in reversed(ms): await B.on_photo(mm)
    await run_queue(); B.do_page = orig
    assert order == sorted(order) and len(order) == 3, order
    print("9 ok: albom tartibi", order)
    # 10) RAR va noma'lum fayl
    OUT.clear()
    await B.on_document(Msg(document=Rec(file_id="x", file_name="a.rar", mime_type="", file_size=1)))
    await B.on_document(Msg(document=Rec(file_id="x", file_name="a.docx", mime_type="", file_size=1)))
    assert "RAR" in OUT[0][1] and "tushunmadim" in OUT[1][1], OUT
    print("10 ok: noto'g'ri fayllar")

asyncio.run(main())
print("HAMMASI O'TDI")
