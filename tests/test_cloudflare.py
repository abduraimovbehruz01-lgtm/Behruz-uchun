"""/cloudflare: bosh admin token yuboradi — bot Cloudflare'da rasmlar xizmatini o'zi joylaydi va ilovaga ulaydi."""
import asyncio, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_tg as T
T.install()
OWN = 100001
os.environ.update(BOT_TOKEN="1:x", GEMINI_API_KEY="k", OWNER_IDS=str(OWN))
os.environ.pop("FIREBASE_SERVICE_ACCOUNT", None)
import core, admin, cloudflare, webapp

CAT = core.CAT
GOOD = "Abc123_def456-GHI789jklMNO012pqrSTU345v"


def last(kind=None, chat=None, has=None):
    for e in reversed(T.LOG):
        if (kind is None or e[0] == kind) and (chat is None or e[1] == chat) and (has is None or has in (e[2] or "")):
            return e


class R:
    def __init__(self, status, data=None, text=""):
        self.status, self.data, self._t = status, data, text
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self, content_type=None): return self.data
    async def text(self): return self._t
    async def read(self): return self._t.encode()


class CF:
    """Soxta Cloudflare API va Worker."""
    def __init__(self, has_subdomain=False, healthy_after=0):
        self.calls, self.sub = [], ("mavjud" if has_subdomain else None)
        self.script, self.enabled, self.health_left = None, False, healthy_after

    def request(self, method, url, headers=None, data=None, json=None, timeout=None):
        path = url.replace(cloudflare.API, "")
        self.calls.append((method, path, headers or {}, data, json))
        auth = (headers or {}).get("Authorization", "")
        if auth != f"Bearer {GOOD}":
            return R(200 if path == "/user/tokens/verify" else 403,
                     {"success": False, "errors": [{"code": 1000, "message": "Invalid API Token"}]})
        if path == "/user/tokens/verify":
            return R(200, {"success": True, "result": {"status": "active"}})
        if path.startswith("/accounts?"):
            return R(200, {"success": True, "result": [{"id": "acc1", "name": "Behruz"}]})
        if path == "/accounts/acc1/workers/subdomain" and method == "GET":
            return R(200, {"success": True, "result": {"subdomain": self.sub}}) if self.sub else \
                R(404, {"success": False, "errors": [{"code": 10007, "message": "not found"}]})
        if path == "/accounts/acc1/workers/subdomain" and method == "PUT":
            self.sub = json["subdomain"]
            return R(200, {"success": True, "result": {"subdomain": self.sub}})
        if path == f"/accounts/acc1/workers/scripts/{cloudflare.SCRIPT}" and method == "PUT":
            self.script = (headers.get("Content-Type"), data)
            return R(200, {"success": True, "result": {"id": cloudflare.SCRIPT}})
        if path == f"/accounts/acc1/workers/scripts/{cloudflare.SCRIPT}/subdomain" and method == "POST":
            self.enabled = json == {"enabled": True, "previews_enabled": False}
            return R(200, {"success": True, "result": json})
        return R(404, {"success": False, "errors": [{"code": 7003, "message": "no route"}]})

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, {}, None, None))
        if self.health_left > 0:
            self.health_left -= 1
            return R(530, text="")
        if url.endswith("/health"):
            return R(200, text="Manhora rasmlar xizmati ishlayapti ✅")
        return R(200, text="img")


async def main():
    await CAT.load()
    import types
    _sleep = asyncio.sleep
    cloudflare.asyncio = types.SimpleNamespace(sleep=lambda s: _sleep(0))   # kutishlarsiz
    # noto'g'ri token
    cf = CF()
    cloudflare.http = lambda: cf
    try:
        await cloudflare.deploy("bad-token-0000000000000000000")
        assert False
    except cloudflare.CfError as e:
        assert "noto‘g‘ri" in str(e)
    print("ok: noto‘g‘ri token — tushunarli xato")

    # to'liq ulanish (workers.dev manzili yo'q — yaratiladi)
    steps = []
    doc = await cloudflare.connect(GOOD, lambda t: steps.append(t) or asyncio.sleep(0))
    assert doc["ok"] and doc["account"] == "acc1" and doc["subdomain"].startswith("manhora-")
    assert doc["url"] == f"https://manhora-img.{doc['subdomain']}.workers.dev" and CAT.cfg["img_base"] == doc["url"]
    ctype, body = cf.script
    assert ctype.startswith("multipart/form-data; boundary=")
    meta = json.loads(body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
    assert meta["main_module"] == "index.js" and meta["bindings"] == [{"type": "secret_text", "name": "BOT_TOKEN", "text": "1:x"}]
    assert b'filename="index.js"' in body and b"Manhora rasmlar xizmati" in body and b"export default" in body
    assert cf.enabled and any("Token" in s for s in steps)
    saved = await CAT.s.get("settings", "cloudflare")
    assert saved["token"] == GOOD and saved["version"] == cloudflare.version() and saved["ok"]
    assert last("msg", OWN, "Cloudflare ulandi")
    print("ok: token → hisob → workers.dev manzili → joylash (BOT_TOKEN maxfiy) → yoqish → tekshiruv → ilovaga ulandi")

    # manzil kech tarqalsa — fon rejimida ulanadi
    await CAT.set_cfg(img_base="")
    cloudflare.STATE["doc"] = None
    await CAT.s.set("settings", "cloudflare", {}, merge=False)
    cf2 = CF(has_subdomain=True, healthy_after=50)
    cloudflare.http = lambda: cf2
    doc = await cloudflare.connect(GOOD)
    assert not doc["ok"] and doc["subdomain"] == "mavjud" and not CAT.cfg.get("img_base")
    assert not any(c[0] == "PUT" and c[1].endswith("/workers/subdomain") for c in cf2.calls)   # bor manzil ishlatildi
    cf2.health_left = 0
    await cloudflare.tick(now=10 ** 10)
    assert CAT.cfg["img_base"] == "https://manhora-img.mavjud.workers.dev"
    print("ok: manzil tarqalguncha kutiladi, keyin o‘zi ulanadi")

    # Worker kodi o'zgarsa — saqlangan token bilan qayta joylanadi
    d = await cloudflare.load_doc()
    d["version"] = "eski"
    await cloudflare.save_doc(d)
    cf3 = CF(has_subdomain=True)
    cloudflare.http = lambda: cf3
    await cloudflare.ensure_latest()
    assert cf3.script and (await CAT.s.get("settings", "cloudflare"))["version"] == cloudflare.version()
    assert not any("/accounts?" in c[1] for c in cf3.calls)                                    # hisob qayta so'ralmaydi
    print("ok: worker/index.js o‘zgarsa — bot xizmatni o‘zi yangilaydi")

    # bosh admin oqimi: /cloudflare → token xabari o'chiriladi → tayyor
    await CAT.set_cfg(img_base="")
    cloudflare.STATE["doc"] = None
    await CAT.s.set("settings", "cloudflare", {}, merge=False)
    cf4 = CF()
    cloudflare.http = lambda: cf4
    st = T.FSM()
    await cloudflare.cmd_cloudflare(T.Msg(OWN, "/cloudflare"), st)
    assert st.state == "CfSet:token" and "Edit Cloudflare Workers" in last("msg", OWN)[2]
    await cloudflare.cf_token(T.Msg(OWN, "salom"), st)
    assert "o‘xshamaydi" in last("msg", OWN)[2] and st.state == "CfSet:token"
    m = T.Msg(OWN, GOOD)
    deleted = []
    async def _del(): deleted.append(True)
    m.delete = _del
    await cloudflare.cf_token(m, st)
    assert deleted and st.state is None and CAT.cfg["img_base"].startswith("https://manhora-img.")
    assert "/cloudflare" not in admin.menu_text(OWN) and "Cloudflare orqali ✅" in admin.menu_text(OWN)
    await CAT.set_cfg(img_base="")
    assert "ulash: /cloudflare" in admin.menu_text(OWN)
    assert not cloudflare.owner(T.Msg(555, "/cloudflare"))
    print("ok: /cloudflare — faqat bosh admin, token xabari o‘chiriladi, menyuda holat ko‘rinadi")

    # Worker fayli rasmlarni beradigan va faqat bitta (default) eksportli
    code = cloudflare.worker_code().decode()
    assert code.count("export ") == 1 and "export default" in code
    print("ok: worker/index.js joyida")


asyncio.run(main()); print("HAMMASI O'TDI")
