"""Oylik chiquvchi trafik hisobi.

Render bepul tarifida butun hisob (barcha botlar) uchun oyiga 5 GB chiquvchi trafik beriladi; oshib ketsa va kartangiz
qo'shilmagan bo'lsa, Render hamma xizmatlarni oy oxirigacha to'xtatadi. Shuning uchun bot o'zi sarflaganini sanaydi:
Telegram'ga yuklangan rasm va fayllar, Gemini'ga yuborilgan sahifalar, ilova va sayt javoblari.
Chegaraga (standart 2 GB) yetganda hamkor kanallardan avtomatik olish va Render orqali rasm berish to'xtaydi,
bosh adminlarga xabar boradi. Hisob taxminiy (mayda so'rovlar o'rtacha hajm bilan sanaladi)."""
import os
import time

import catalog
from core import CAT, bot, log

GB = 1024 ** 3
DEFAULT_GB = 2.0
STATE = {"month": "", "bytes": 0, "warned": 0, "dirty": False, "saved": 0.0}


def month() -> str:
    return catalog.today()[:7]


def add(n):
    """Tashqariga yuborilgan baytlarni qo'shadi."""
    n = int(n or 0)
    if n <= 0:
        return
    m = month()
    if STATE["month"] != m:
        STATE.update(month=m, bytes=0, warned=0)
    STATE["bytes"] += n
    STATE["dirty"] = True


def used() -> int:
    return STATE["bytes"] if STATE["month"] == month() else 0


def limit_gb() -> float:
    try:
        return max(0.2, float(CAT.cfg.get("traffic_gb") or DEFAULT_GB))
    except (TypeError, ValueError):
        return DEFAULT_GB


def ratio() -> float:
    return used() / (limit_gb() * GB)


def over() -> bool:
    return ratio() >= 1.0


def human(n) -> str:
    n = float(n or 0)
    if n >= GB:
        return f"{n / GB:.2f} GB".replace(".", ",")
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def line() -> str:
    """Admin panel uchun qisqa satr."""
    r = ratio()
    mark = "🔴" if r >= 1 else "🟠" if r >= 0.8 else "🟢"
    return f"📶 Trafik (shu oy): {mark} {human(used())} / {limit_gb():g} GB".replace(".", ",")


async def load():
    d = await CAT.s.get("stats", "traffic") or {}
    m = month()
    if d.get("month") == m:
        STATE.update(month=m, bytes=int(d.get("bytes") or 0) + STATE["bytes"] * (STATE["month"] == m),
                     warned=int(d.get("warned") or 0))
    else:
        STATE.update(month=m, bytes=STATE["bytes"] if STATE["month"] == m else 0, warned=0)
    STATE["dirty"] = False


async def save():
    STATE["dirty"] = False
    STATE["saved"] = time.time()
    try:
        await CAT.s.set("stats", "traffic", {"month": STATE["month"] or month(), "bytes": used(),
                                             "warned": STATE["warned"]}, merge=False)
    except Exception as e:
        log.warning("trafik hisobini saqlab bo‘lmadi: %s", e)


async def tick():
    """Rejalashtiruvchi har 20 soniyada chaqiradi: daqiqada bir saqlaydi, 80% va 100% da ogohlantiradi."""
    if STATE["month"] and STATE["month"] != month():
        add(1)                                     # yangi oy — hisob noldan
    level = 2 if ratio() >= 1 else 1 if ratio() >= 0.8 else 0
    if level > STATE["warned"]:
        STATE["warned"] = level
        await save()
        if level == 2:
            text = (f"🔴 <b>Trafik chegarasiga yetdi</b>: {human(used())} / {limit_gb():g} GB (shu oy).\n\n"
                    "Hamkor kanallardan avtomatik olish va ilova rasmlarini Render orqali berish oy oxirigacha "
                    "to‘xtatildi. Render bepul tarifida barcha botlar uchun oyiga jami 5 GB bor — oshib ketsa, "
                    "hamma botlar to‘xtab qoladi.\nChegarani «📊 Statistika» bo‘limida o‘zgartirsa bo‘ladi.")
        else:
            text = (f"🟠 Trafikning 80% i ishlatildi: {human(used())} / {limit_gb():g} GB (shu oy). "
                    "Chegaraga yetganda hamkor kanallardan avtomatik olish to‘xtaydi.")
        for oid in CAT.owners:
            try:
                await bot.send_message(oid, text)
            except Exception:
                pass
    if STATE["dirty"] and time.time() - STATE["saved"] > 60:
        await save()


# ---------------------------------------------------------------- hisoblagichlar

def _size(v, depth=0) -> int:
    """Telegram so'rovi maydonining taxminiy hajmi (yuklanayotgan fayllar — aniq)."""
    if v is None or depth > 4:
        return 0
    name = type(v).__name__
    if name == "BufferedInputFile":
        return len(getattr(v, "data", b"") or b"")
    if name == "FSInputFile":
        try:
            return os.path.getsize(v.path)
        except (OSError, AttributeError, TypeError):
            return 0
    if isinstance(v, (bytes, bytearray)):
        return len(v)
    if isinstance(v, str):
        return len(v.encode("utf-8", "ignore"))
    if isinstance(v, (list, tuple)):
        return sum(_size(x, depth + 1) for x in v)
    fields = getattr(type(v), "model_fields", None)
    if isinstance(fields, dict):
        return sum(_size(getattr(v, k, None), depth + 1) for k in fields)
    return 0


def estimate(method) -> int:
    return 600 + _size(method)


def install(bot_obj) -> bool:
    """Botning barcha Telegram so'rovlarini sanaydigan oraliq qatlam (aiogram request middleware)."""
    try:
        from aiogram.client.session.middlewares.base import BaseRequestMiddleware
    except Exception:
        return False

    class Meter(BaseRequestMiddleware):
        async def __call__(self, make_request, bot, method):
            try:
                add(estimate(method))
            except Exception:
                pass
            return await make_request(bot, method)

    try:
        bot_obj.session.middleware(Meter())
        return True
    except Exception as e:
        log.warning("trafik hisoblagichi o‘rnatilmadi: %s", e)
        return False


def http_middleware():
    """aiohttp javoblari hajmini sanaydi (ilova, API, rasmlar, webhook javoblari)."""
    from aiohttp import web

    @web.middleware
    async def meter(request, handler):
        resp = await handler(request)
        try:
            body = getattr(resp, "body", None)
            n = len(body) if isinstance(body, (bytes, bytearray)) else int(getattr(resp, "content_length", 0) or 0)
            add(n + 250)
        except Exception:
            pass
        return resp

    return meter
