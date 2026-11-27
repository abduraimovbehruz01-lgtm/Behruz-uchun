"""Cloudflare rasmlar xizmatini (Worker) botning o'zi joylaydi — GitHub sozlamalari kerak emas.

Bosh admin /cloudflare buyrug'ini yuborib, Cloudflare API tokenini («Edit Cloudflare Workers» shabloni) jo'natadi.
Bot: tokenni tekshiradi → Cloudflare hisobini topadi → workers.dev manzilini oladi (bo'lmasa yaratadi) →
worker/index.js ni «manhora-img» nomi bilan joylaydi (BOT_TOKEN — maxfiy o'zgaruvchi) → workers.dev manzilini yoqadi →
Worker javob bera boshlagach ilovaga ulaydi va bosh adminga xabar beradi.
Token bazada saqlanadi: Worker kodi yangilansa, bot uni o'zi qayta joylaydi. Tokenni istalgan vaqtda
Cloudflare'da (My Profile → API Tokens) o'chirib qo'yish mumkin."""
import asyncio
import hashlib
import json
import os
import re
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from core import CAT, TOKEN, bot, esc, http, kb, log, safe_delete, safe_edit

router = Router(name="cloudflare")
API = "https://api.cloudflare.com/client/v4"
SCRIPT = "manhora-img"
COMPAT = "2025-06-01"
HERE = os.path.dirname(os.path.abspath(__file__))
WORKER_FILE = os.path.join(HERE, "worker", "index.js")
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{20,200}")
STATE = {"busy": False, "last": 0.0, "doc": None}
CANCEL = ("❌ Bekor qilish", "adm:cancel")

HOWTO = ("🖼 <b>Cloudflare — ilova rasmlari uchun (bepul)</b>\n\n"
         "1) <b>cloudflare.com</b> da ro‘yxatdan o‘ting (bepul).\n"
         "2) Shu sahifani oching: https://dash.cloudflare.com/profile/api-tokens (yoki o‘ng yuqoridagi 👤 → "
         "<b>Profile</b> → <b>API Tokens</b>) → <b>Create Token</b>.\n"
         "3) <b>«Edit Cloudflare Workers»</b> qatoridagi <b>Use template</b> → pastda <b>Continue to summary</b> → "
         "<b>Create Token</b>.\n"
         "4) Chiqqan tokenni nusxalab, <b>shu yerga</b> yuboring.\n\n"
         "Qolganini o‘zim qilaman: rasmlar xizmatini joylayman va ilovaga ulayman. Token xabarini darhol o‘chiraman.")


class CfError(Exception):
    pass


class CfSet(StatesGroup):
    token = State()


def worker_code() -> bytes:
    with open(WORKER_FILE, "rb") as f:
        return f.read()


def version() -> str:
    return hashlib.sha256(worker_code()).hexdigest()[:12]


async def load_doc() -> dict:
    if STATE["doc"] is None:
        STATE["doc"] = dict(await CAT.s.get("settings", "cloudflare") or {})
    return STATE["doc"]


async def save_doc(doc: dict):
    STATE["doc"] = dict(doc)
    await CAT.s.set("settings", "cloudflare", doc, merge=False)


# ---------------------------------------------------------------- Cloudflare API

async def cf(method, path, token, **kw):
    import aiohttp
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(kw.pop("headers", {}) or {})
    async with http().request(method, API + path, headers=headers, timeout=aiohttp.ClientTimeout(total=60), **kw) as r:
        status = r.status
        try:
            data = await r.json(content_type=None)
        except Exception:
            data = {}
    return status, (data if isinstance(data, dict) else {})


def err_text(data) -> str:
    errs = [e for e in (data.get("errors") or []) if isinstance(e, dict)]
    return ("; ".join(f"{e.get('code')}: {e.get('message')}" for e in errs) or "noma’lum xato")[:300]


def multipart(meta: dict, code: bytes):
    b = "----manhora" + os.urandom(8).hex()
    body = (f'--{b}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n'.encode()
            + json.dumps(meta).encode() + b"\r\n"
            + (f'--{b}\r\nContent-Disposition: form-data; name="index.js"; filename="index.js"\r\n'
               "Content-Type: application/javascript+module\r\n\r\n").encode()
            + code + b"\r\n" + f"--{b}--\r\n".encode())
    return body, f"multipart/form-data; boundary={b}"


async def deploy(token, progress=None, account=None, subdomain=None) -> dict:
    """Worker'ni joylaydi. Qaytaradi: {account, subdomain, url, version}. Xato bo'lsa CfError."""
    async def say(text):
        if progress is not None:
            try:
                await progress(text)
            except Exception:
                pass

    def active(st, d):
        return st == 200 and d.get("success") and (d.get("result") or {}).get("status") == "active"

    await say("🔑 Token tekshirilmoqda…")
    user_ok = active(*await cf("GET", "/user/tokens/verify", token))     # shaxsiy (My Profile) token
    if not account:
        st, d = await cf("GET", "/accounts?per_page=5", token)
        accs = [a["id"] for a in (d.get("result") or []) if isinstance(a, dict) and a.get("id")]
        if not accs:                                                     # hisoblar ro'yxati yopiq bo'lsa
            st, d = await cf("GET", "/memberships?per_page=5", token)
            accs = [(m.get("account") or {}).get("id") for m in (d.get("result") or []) if isinstance(m, dict)]
            accs = [a for a in accs if a]
        if not accs:
            raise CfError("Token noto‘g‘ri yoki Cloudflare hisobini ko‘ra olmaydi. «Edit Cloudflare Workers» shabloni "
                          "bilan yangi token yarating va to‘liq nusxalab yuboring.")
        account = accs[0]
    if not user_ok and not active(*await cf("GET", f"/accounts/{account}/tokens/verify", token)):  # hisob tokeni
        raise CfError("Token noto‘g‘ri yoki faol emas. «Edit Cloudflare Workers» shabloni bilan yangi token yarating.")
    if not subdomain:
        await say("🌐 workers.dev manzili tayyorlanmoqda…")
        st, d = await cf("GET", f"/accounts/{account}/workers/subdomain", token)
        subdomain = (d.get("result") or {}).get("subdomain") if st == 200 else None
        if not subdomain:
            for _ in range(4):
                name = "manhora-" + os.urandom(3).hex()
                st, d = await cf("PUT", f"/accounts/{account}/workers/subdomain", token, json={"subdomain": name})
                if st == 200 and d.get("success"):
                    subdomain = (d.get("result") or {}).get("subdomain") or name
                    break
            if not subdomain:
                raise CfError("workers.dev manzilini yaratib bo‘lmadi (" + err_text(d) + "). Cloudflare’da "
                              "«Workers & Pages» bo‘limini bir marta ochib, tokenni qayta yuboring.")
    await say("📦 Rasmlar xizmati joylanmoqda…")
    meta = {"main_module": "index.js", "compatibility_date": COMPAT,
            "bindings": [{"type": "secret_text", "name": "BOT_TOKEN", "text": TOKEN}]}
    body, ctype = multipart(meta, worker_code())
    st, d = await cf("PUT", f"/accounts/{account}/workers/scripts/{SCRIPT}", token, data=body,
                     headers={"Content-Type": ctype})
    if st != 200 or not d.get("success"):
        raise CfError("Rasmlar xizmatini joylab bo‘lmadi: " + err_text(d))
    st, d = await cf("POST", f"/accounts/{account}/workers/scripts/{SCRIPT}/subdomain", token,
                     json={"enabled": True, "previews_enabled": False})
    if st != 200 or not d.get("success"):
        raise CfError("workers.dev manzili yoqilmadi: " + err_text(d))
    return {"account": account, "subdomain": subdomain, "url": f"https://{SCRIPT}.{subdomain}.workers.dev",
            "version": version()}


async def healthy(url) -> bool:
    """Worker javob beradimi va rasmni (imzo bilan) bera oladimi."""
    import aiohttp
    import webapp
    try:
        async with http().get(url + "/health", timeout=aiohttp.ClientTimeout(total=15)) as r:
            ok = r.status == 200 and "Manhora" in (await r.text())
        if not ok:
            return False
        sample = next((m.get("cover_t") or m.get("cover") for m in CAT.manga.values()
                       if m.get("cover_t") or m.get("cover")), None)
        if sample:
            exp = int(time.time()) + 3600
            async with http().get(f"{url}/i/{sample}?e={exp}&s={webapp.sign('img-w', sample, exp)}",
                                  timeout=aiohttp.ClientTimeout(total=30)) as r:
                await r.read()
                return r.status == 200
        return True
    except Exception:
        return False


async def activate(doc) -> None:
    first = CAT.cfg.get("img_base") != doc["url"]
    doc = dict(doc, ok=True, at=time.time())
    await save_doc(doc)
    await CAT.set_cfg(img_base=doc["url"])
    if first:
        await CAT.log_action(0, "🖼 Cloudflare", f"Rasmlar xizmati ulandi: {doc['url']}")
        for oid in CAT.owners:
            try:
                await bot.send_message(oid, "🖼 <b>Cloudflare ulandi!</b> Endi boblar ilovaning o‘zida o‘qiladi, rasmlar "
                                            "Render trafigini sarflamaydi.")
            except Exception:
                pass


async def connect(token, progress=None) -> dict:
    """Tokenni qabul qilib, joylaydi va (manzil tarqalgan bo'lsa) darhol ulaydi."""
    info = await deploy(token, progress)
    doc = dict(info, token=token, ok=False, at=time.time())
    await save_doc(doc)
    if progress is not None:
        await progress("⏳ Rasmlar xizmati ishga tushishini kutyapman…")
    for _ in range(9):
        if await healthy(info["url"]):
            await activate(doc)
            return dict(doc, ok=True)
        await asyncio.sleep(10)
    return doc


async def tick(now=None):
    """Rejalashtiruvchidan: joylangan, lekin hali javob bermagan Worker'ni daqiqada bir tekshiradi."""
    now = now or time.time()
    if now - STATE["last"] < 60 or STATE["busy"]:
        return
    STATE["last"] = now
    doc = await load_doc()
    if doc.get("url") and not doc.get("ok") and await healthy(doc["url"]):
        await activate(doc)


async def ensure_latest():
    """Ishga tushganda: Worker kodi o'zgargan bo'lsa — saqlangan token bilan qayta joylaydi."""
    try:
        doc = await load_doc()
        if not doc.get("token") or not doc.get("account"):
            return
        if doc.get("version") != version():
            info = await deploy(doc["token"], account=doc["account"], subdomain=doc.get("subdomain"))
            doc.update(info)
            await save_doc(doc)
            log.info("Cloudflare rasmlar xizmati yangilandi (%s)", info["version"])
        if doc.get("ok") and CAT.cfg.get("img_base") != doc.get("url"):
            await CAT.set_cfg(img_base=doc["url"])
    except Exception as e:
        log.warning("Cloudflare rasmlar xizmatini yangilab bo‘lmadi: %s", e)


# ---------------------------------------------------------------- bosh admin uchun

def owner(e) -> bool:
    return CAT.is_owner(e.from_user.id)


@router.message(Command("cloudflare"), owner)
async def cmd_cloudflare(msg: Message, state: FSMContext):
    await state.set_state(CfSet.token)
    doc = await load_doc()
    head = ""
    if CAT.cfg.get("img_base"):
        head = f"✅ Hozir ulangan: <code>{esc(CAT.cfg['img_base'])}</code>\nBoshqa token bilan qayta ulash uchun yangi tokenni yuboring.\n\n"
    elif doc.get("url"):
        head = "⏳ Rasmlar xizmati joylangan, ishga tushishi kutilmoqda. Qayta urinish uchun tokenni yana yuborishingiz mumkin.\n\n"
    await msg.answer(head + HOWTO, reply_markup=kb([[CANCEL]]))


@router.message(CfSet.token, F.text, ~F.text.startswith("/"))
async def cf_token(msg: Message, state: FSMContext):
    if not owner(msg):
        return
    token = (msg.text or "").strip()
    await safe_delete(msg)                      # token chatda qolmasin
    if not TOKEN_RE.fullmatch(token):
        await msg.answer("🤔 Bu Cloudflare tokeniga o‘xshamaydi. Cloudflare’dagi <b>Create Token</b> dan keyin chiqqan "
                         "uzun matnni to‘liq nusxalab yuboring.", reply_markup=kb([[CANCEL]]))
        return
    if STATE["busy"]:
        await msg.answer("⏳ Ulanish allaqachon ketyapti, biroz kuting.")
        return
    STATE["busy"] = True
    note = await msg.answer("⏳ Cloudflare’ga ulanmoqda…")

    async def progress(text):
        await safe_edit(note, text)

    try:
        doc = await connect(token, progress)
    except CfError as e:
        await safe_edit(note, f"❌ {esc(str(e))}\n\nTokenni qayta yuboring yoki /bekor.")
        return
    except Exception as e:
        log.exception("Cloudflare ulanishi")
        await safe_edit(note, f"❌ Cloudflare bilan bog‘lanib bo‘lmadi: <code>{esc(repr(e))[:200]}</code>\n"
                              "Birozdan keyin tokenni qayta yuboring.")
        return
    finally:
        STATE["busy"] = False
    await state.clear()
    if doc.get("ok"):
        await safe_edit(note, f"✅ <b>Tayyor!</b> Rasmlar xizmati ishlayapti: <code>{esc(doc['url'])}</code>\n"
                              "Endi boblar ilovaning o‘zida o‘qiladi.")
    else:
        await safe_edit(note, "✅ Rasmlar xizmati joylandi. Yangi manzil internetga tarqalishi bir necha daqiqa olishi "
                              "mumkin — tayyor bo‘lgach o‘zim xabar beraman.")
