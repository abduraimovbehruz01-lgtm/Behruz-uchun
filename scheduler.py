"""Rejalashtirilgan boblarni vaqti kelganda avtomatik chop etish.

Bepul serverda dastur uxlab qolishi mumkin: shuning uchun tashqi «uyg'otuvchi» (GitHub Actions) har kuni
belgilangan soatlardan biroz oldin /health ga murojaat qiladi. Server uyg'onganda shu yerdagi tsikl darhol
vaqti o'tgan boblarni chop etadi; yaqin 20 daqiqada chiqadigan bob bo'lsa, bot o'zini uyg'oq tutadi (bot.py)."""
import asyncio
import time

import catalog
import promo
from core import CAT, bot, esc, jlog, log

CHECK_EVERY = 20
LOCK = asyncio.Lock()


async def run_due(now=None) -> int:
    """Vaqti kelgan boblarni chop etadi va kuzatuvchilarga xabar beradi. Qaytaradi: chop etilganlar soni."""
    async with LOCK:
        due = CAT.due(now)
        if not due:
            return 0
        groups: dict[str, list] = {}
        for _ts, code, num, meta in due:
            groups.setdefault(code, []).append((num, meta))
        total = 0
        for code, items in groups.items():
            m = CAT.get(code)
            if not m:
                continue
            newly, by = [], set()
            for num, meta in sorted(items, key=lambda x: x[0]):
                sp = meta.get("sched_price")
                price = int(sp if sp is not None else (meta.get("price") or 0))
                try:
                    if await CAT.publish(code, num, price):
                        newly.append(num)
                except Exception:
                    log.exception("rejadagi bobni chop etib bo‘lmadi: %s %s", code, num)
                    continue
                if meta.get("sched_by"):
                    by.add(int(meta["sched_by"]))
            if not newly:
                continue
            total += len(newly)
            await jlog(0, f"⏰ {m['title']} — {catalog.ranges(newly)}-bob avtomatik chop etildi")
            sent, posted = await promo.announce(code, newly)
            for uid in by:
                try:
                    await bot.send_message(uid, f"⏰ <b>{esc(m['title'])}</b> — {catalog.ranges(newly)}-bob vaqtida chop "
                                                f"etildi. 🔔 {sent} ta kuzatuvchiga xabar ketdi."
                                                + (" 📣 Reklama kanaliga post chiqdi." if posted else ""))
                except Exception:
                    pass
        return total


async def loop():
    await asyncio.sleep(5)
    while True:
        try:
            n = await run_due()
            if n:
                log.info("rejadagi boblar chop etildi: %d", n)
        except Exception:
            log.exception("rejalashtiruvchida xato")
        try:
            import autosrc
            await autosrc.tick()          # hamkor kanallardan kelgan albomlarni yopish va navbatni ishga tushirish
        except Exception:
            log.exception("avto-manbalar")
        try:
            import traffic
            await traffic.tick()          # oylik trafik: saqlash va ogohlantirish
        except Exception:
            log.exception("trafik")
        try:
            import thumbs
            await thumbs.tick()           # ilova uchun kichik rasmlar
        except Exception:
            log.exception("kichik rasmlar")
        try:
            import rating
            await rating.refresh()        # reyting keshi (10 daqiqada bir)
        except Exception:
            log.exception("reyting")
        try:
            import cloudflare
            await cloudflare.tick()       # joylangan rasmlar xizmati ishga tushdimi
        except Exception:
            log.exception("cloudflare")
        await asyncio.sleep(CHECK_EVERY)


def soon(window: float = 20 * 60) -> bool:
    """Yaqin orada (yoki allaqachon) chiqishi kerak bo'lgan bob bormi."""
    nd = CAT.next_due()
    return nd is not None and nd - time.time() < window
