"""Yangi bob chop etilganda e'lon qilish: kuzatuvchilarga xabar va reklama kanaliga post
(muqova, bob raqami, manga kodi, tarjimon yozuvi va «📖 O'qish» tugmasi)."""
import time

import catalog
from core import CAT, STATE, bot, esc, kb, log
from reader import notify_followers

fmt = catalog.fmt_num
WARN = {"t": 0.0}


def promo_cfg() -> dict:
    p = CAT.cfg.get("promo") or {}
    return p if isinstance(p, dict) else {}


def credit_of(m, chap=None) -> str:
    return ((chap or {}).get("credit") or m.get("credit") or "").strip()


def post_text(code, nums):
    m = CAT.get(code)
    nums = sorted(float(n) for n in nums)
    first = nums[0]
    chap = CAT.chapter(code, first) or {}
    if len(nums) == 1:
        line = f"📖 Yangi bob: <b>{fmt(first)}-bob</b>" + (f" — {esc(chap['title'])}" if chap.get("title") else "")
    else:
        line = f"📖 <b>{len(nums)} ta yangi bob</b>: {catalog.ranges(nums)}"
    lines = [f"🔥 <b>{esc(m['title'])}</b>", line]
    if m.get("genres"):
        lines.append("🏷 " + esc(", ".join(m["genres"][:4])))
    lines.append(f"🔢 Kod: <code>{code}</code>")
    credit = credit_of(m, chap)
    if credit:
        lines.append(f"✍️ {esc(credit)}")
    user = STATE["username"]
    markup = kb([[("📖 O‘qish", f"url:https://t.me/{user}?start=c{code}_{fmt(first).replace('.', '-')}")],
                 [("📚 Barcha boblar", f"url:https://t.me/{user}?start={code}")]])
    return "\n".join(lines), markup


async def channel_post(code, nums, force=False) -> bool:
    """Reklama kanaliga post. Kanal ulanmagan, to'xtatilgan yoki manga yashirin bo'lsa — hech narsa qilmaydi."""
    p = promo_cfg()
    m = CAT.get(code)
    if not p.get("id") or (not p.get("on", True) and not force) or not m or not nums or m.get("hidden"):
        return False
    text, markup = post_text(code, nums)
    try:
        if m.get("cover"):
            try:
                await bot.send_photo(p["id"], m["cover"], caption=text, reply_markup=markup)
                return True
            except Exception as e:  # muqova eskirgan bo'lsa — matn bilan
                log.info("reklama posti (muqova bilan) chiqmadi: %s", e)
        await bot.send_message(p["id"], text, reply_markup=markup)
        return True
    except Exception as e:
        log.warning("reklama kanaliga post chiqmadi: %s", e)
        if time.time() - WARN["t"] > 3600:
            WARN["t"] = time.time()
            for oid in CAT.owners:
                try:
                    await bot.send_message(oid, f"⚠️ «{esc(p.get('title') or 'Reklama kanali')}» ga post chiqmadi. Botni u "
                                                "kanalda <b>post yozish</b> huquqi bilan admin qiling.")
                except Exception:
                    pass
        return False


async def announce(code, nums):
    """Yangi chop etilgan bob(lar) haqida: kuzatuvchilarga xabar + reklama kanaliga post.
    Qaytaradi: (xabar olganlar soni, kanalga post chiqdimi)."""
    nums = list(nums) if isinstance(nums, (list, tuple, set)) else [nums]
    sent = await notify_followers(code, nums)
    posted = await channel_post(code, nums)
    return sent, posted
