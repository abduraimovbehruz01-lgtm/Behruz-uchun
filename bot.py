"""Manhora — manga va manhvalarni o'zbek tilida o'qish boti (tarjima vositasi bilan).

Qismlar:
  reader.py    — foydalanuvchilar: kod, katalog, qidiruv, o'qish, sevimlilar, kuzatish, jeton bilan ochish
  admin.py     — admin panel: manga va boblar, bittalab va ko'p bobni yuklash, chop etish, rejalashtirish
  staff.py     — adminlar va huquqlar, jurnal, majburiy obuna, ombor kanali
  editor.py    — bob sahifalarini tahrirlash, bob nomi, glossariy
  scheduler.py — rejalashtirilgan boblarni vaqtida chop etish
  autosrc.py   — hamkor kanallar: u yerga tashlangan boblarni avtomatik olish, tarjima qilish va chop etish
  promo.py     — yangi bob e'loni: kuzatuvchilarga xabar va reklama kanaliga post
  webapp.py    — Mini App (ilova): sahifa va API; rasmlar Cloudflare Worker (worker/) orqali
  cloudflare.py — /cloudflare: bosh admin token yuborsa, bot rasmlar xizmatini o'zi joylaydi va ulaydi
  torii.py     — Torii tarjimoni (TORII_API_KEY): sahifani to'liq o'zi tarjima qiladi; ishlamasa — Gemini
  rating.py    — reyting (noyob o'quvchilar, baholar); thumbs.py — ilova uchun kichik rasmlar
  traffic.py   — oylik chiquvchi trafik hisobi va chegarasi (Render bepul tarifi: oyiga 5 GB)
  tools.py     — adminlar uchun tezkor tarjima (sahifa/fayl yuborsa — tarjima qilib qaytaradi)
  core.py      — umumiy sozlamalar; catalog.py — katalog mantiqi; store.py — Firebase/xotira
  translator.py, typeset.py, chapters.py — tarjima, rasmga yozish, PDF/ZIP

Sozlamalar (Render → Environment):
  BOT_TOKEN, GEMINI_API_KEY — majburiy
  FIREBASE_SERVICE_ACCOUNT  — Firebase kalit fayli (JSON matni); bo'lmasa ma'lumotlar faqat xotirada
  OWNER_IDS                 — bosh admin(lar) Telegram ID si, vergul bilan
  REQUIRED_CHANNEL          — majburiy obuna kanali (@kanal), ixtiyoriy
  STORAGE_CHAT              — rasmlar ombori kanali ID si (odatda bot o'zi aniqlaydi)
"""
import asyncio
import hashlib

import aiohttp
from aiohttp import web
from aiogram import Dispatcher, F
from aiogram.types import BotCommand

import admin
import autosrc
import cloudflare
import editor
import reader
import scheduler
import staff
import tools
import torii
import traffic
import typeset
import translator
import webapp
from core import CAT, GEM, PORT, PUBLIC_URL, STATE, TEST_KEY, TOKEN, bot, http, log, tell_owners, translate_image

dp = Dispatcher()
GEM.meter = traffic.add          # Gemini'ga yuborilgan sahifalar trafik hisobiga kiradi
for r in (admin.router, staff.router, editor.router, autosrc.router, cloudflare.router, tools.router, reader.router):
    r.message.filter(F.chat.type == "private")
    dp.include_router(r)


async def health(_req):
    return web.Response(text="ok")


async def sinov(req: web.Request):
    """Tekshirish uchun: POST /sinov (X-Test-Key sarlavhasi, tanasi — rasm). ?json=1 — bloklar ro'yxati."""
    if not TEST_KEY or req.headers.get("X-Test-Key") != TEST_KEY:
        return web.Response(status=404)
    try:
        img = await asyncio.to_thread(typeset.load_image, await req.read())
        out, blocks, lang = await translate_image(img, 0)
    except translator.GeminiError as e:
        return web.json_response({"error": str(e), "kind": e.kind}, status=502)
    except Exception as e:
        log.exception("sinov")
        return web.json_response({"error": repr(e)}, status=500)
    if req.query.get("json"):
        return web.json_response({"lang": lang, "model": GEM.last_good, "blocks": [
            {"box": list(b.box), "kind": b.kind, "original": b.original, "translation": b.text} for b in blocks]})
    return web.Response(body=await asyncio.to_thread(typeset.to_jpeg, out, 90), content_type="image/jpeg")


def busy() -> bool:
    return bool(tools.QUEUES or tools.CURRENT or admin.JOBS or admin.DOWNLOADING or reader.READING
                or scheduler.soon() or autosrc.busy())


async def keepalive():
    """Bepul serverda 15 daqiqa so'rov kelmasa, server uxlaydi. Uzun ish paytida va yaqin orada rejadagi bob
    chiqishi kerak bo'lganda o'zimizni uyg'oq tutamiz."""
    while True:
        await asyncio.sleep(240)
        if PUBLIC_URL and busy():
            try:
                async with http().get(PUBLIC_URL + "/health", timeout=aiohttp.ClientTimeout(total=20)) as r:
                    await r.read()
            except Exception:
                pass


async def on_startup():
    try:
        await CAT.load()
        log.info("Katalog yuklandi: %d manga, %d admin", len(CAT.manga), len(CAT.admins))
        await autosrc.load()
        await traffic.load()
        if autosrc.QUEUE or autosrc.ALBUMS:
            log.info("Hamkor manbalar navbati: %d bob, %d albom", len(autosrc.QUEUE), len(autosrc.ALBUMS))
    except Exception as e:
        log.exception("Katalogni yuklab bo‘lmadi")
        await tell_owners(f"⚠️ Bazadan katalogni yuklab bo‘lmadi: <code>{str(e)[:300]}</code>", force=True)
    asyncio.create_task(keepalive())
    asyncio.create_task(scheduler.loop())
    asyncio.create_task(cloudflare.ensure_latest())
    log.info("Gemini: %s", await GEM.check(http()))
    if torii.configured():
        cr = await torii.credits(force=True)
        log.info("Torii: %s", f"ulandi, {cr:g} kredit" if isinstance(cr, (int, float)) else "kreditlarni bilib bo‘lmadi")
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Bosh sahifa"),
            BotCommand(command="yordam", description="Qanday foydalaniladi"),
        ])
        me = await bot.get_me()
        STATE["username"] = me.username
        log.info("Telegram bot ulandi: @%s", me.username)
    except Exception as e:
        log.error("Telegram bilan aloqa yo‘q yoki token noto‘g‘ri: %s", e)
    await webapp.set_menu_button()


async def on_shutdown():
    await traffic.save()
    if STATE["http"] is not None:
        await STATE["http"].close()


def main():
    app = web.Application(client_max_size=16 * 1024 * 1024, middlewares=[traffic.http_middleware()])
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/sinov", sinov)
    webapp.setup(app)
    if bot is None:
        log.error("BOT_TOKEN kiritilmagan yoki noto‘g‘ri — Render → Environment bo‘limida BOT_TOKEN ni qo‘shing.")
        web.run_app(app, host="0.0.0.0", port=PORT)
        return
    traffic.install(bot)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    if PUBLIC_URL:
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        path = "/tg/" + hashlib.sha256(TOKEN.encode()).hexdigest()[:24]
        secret = hashlib.sha256(("maxfiy:" + TOKEN).encode()).hexdigest()[:48]

        async def set_hook():
            for attempt in range(5):
                try:
                    await bot.set_webhook(PUBLIC_URL + path, secret_token=secret,
                                          allowed_updates=dp.resolve_used_update_types())
                    log.info("Webhook o‘rnatildi: %s/tg/…", PUBLIC_URL)
                    return
                except Exception as e:
                    log.error("Webhook o‘rnatilmadi (%s) — qayta urinamiz", e)
                    await asyncio.sleep(5 * (attempt + 1))

        dp.startup.register(set_hook)
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=path)
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        async def run():  # kompyuterda sinash: polling + /health
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", PORT).start()
            await bot.delete_webhook(drop_pending_updates=False)
            log.info("Polling rejimi (PUBLIC_URL yo‘q)")
            await dp.start_polling(bot)

        asyncio.run(run())


if __name__ == "__main__":
    main()
