"""Reyting — hammaga ko'rinadigan o'rinlar.

  • Hafta / Oy — so'nggi 7 / 30 kundagi noyob o'quvchilar soni (bir odam bir mangani kuniga bir marta sanaladi,
    shuning uchun qayta-qayta ochib o'rinni ko'tarib bo'lmaydi). O'zgarish (↑↓) oldingi hafta/oyga nisbatan.
  • Barcha vaqt — umumiy ko'rishlar.
  • ⭐ Baho — foydalanuvchilar qo'ygan 1–5 yulduz (kamida bitta bobni o'qigan odam baho qo'ya oladi);
    tartiblashda ovozi kam mangalar adolatli bo'lishi uchun Bayes o'rtachasi ishlatiladi.

Kunlik sonlar `reads/{sana}` hujjatlarida ({kod: o'quvchilar}); o'tgan kunlar xotirada saqlanadi, bugungisi
10 daqiqada bir yangilanadi."""
import asyncio
import time
from datetime import datetime, timedelta

import catalog
from core import CAT, log

PERIODS = ("week", "month", "all", "stars")
TITLES = {"week": "Hafta", "month": "Oy", "all": "Barcha vaqt", "stars": "⭐ Baho"}
TTL = 600
PRIOR_VOTES = 5            # Bayes: har mangaga «5 ta o'rtacha baho» qo'shilgandek hisoblanadi
MIN_VOTES = 1
DAYS: dict[str, dict] = {}    # sana → {kod: noyob o'quvchilar} (o'tgan kunlar o'zgarmaydi)
CACHE = {"t": 0.0, "data": {}}
_LOCKS: dict[str, asyncio.Lock] = {}


def day(offset=0) -> str:
    return (datetime.now(catalog.TZ).date() + timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------- o'qishni sanash

async def note_read(uid, code) -> bool:
    """Foydalanuvchi bugun shu mangani birinchi marta o'qisa — kunlik noyob o'quvchilarga qo'shadi."""
    code, today = str(code), day()
    u = await CAT.user(uid)
    seen = u.setdefault("seen", {})
    if seen.get(code) == today:
        return False
    seen[code] = today
    try:
        await CAT.s.set("users", uid, {"seen": {code: today}})
        await CAT.s.incr("reads", today, code, 1)
    except Exception as e:
        log.warning("reyting: o‘qishni yozib bo‘lmadi: %s", e)
        return False
    d = DAYS.get(today)
    if d is not None:
        d[code] = int(d.get(code) or 0) + 1
    CACHE["t"] = min(CACHE["t"], time.time() - TTL + 60)   # bir daqiqada yangilanadi
    return True


# ---------------------------------------------------------------- baholar

def stars(m):
    """(o'rtacha, ovozlar soni)."""
    r = (m or {}).get("rating") or {}
    n = int(r.get("n") or 0)
    s = float(r.get("s") or 0)
    return (round(s / n, 1) if n else 0.0), n


def my_stars(u, code) -> int:
    return int(((u or {}).get("rated") or {}).get(str(code)) or 0)


def can_rate(u, code) -> bool:
    return str(code) in ((u or {}).get("progress") or {})


async def rate(uid, code, value):
    """Baho qo'yadi yoki o'zgartiradi. Qaytaradi: (True, (o'rtacha, soni)) yoki (False, sabab)."""
    code = str(code)
    m = CAT.get(code)
    if not m:
        return False, "Manga topilmadi"
    try:
        value = int(value)
    except (TypeError, ValueError):
        return False, "Baho 1 dan 5 gacha"
    if not 1 <= value <= 5:
        return False, "Baho 1 dan 5 gacha"
    u = await CAT.user(uid)
    if not can_rate(u, code):
        return False, "Avval kamida bitta bobni o‘qing 🙂"
    async with _LOCKS.setdefault(code, asyncio.Lock()):
        old = my_stars(u, code)
        r = dict(m.get("rating") or {})
        s, n = float(r.get("s") or 0), int(r.get("n") or 0)
        if old:
            s -= old
        else:
            n += 1
        s += value
        r = {"s": s, "n": n}
        m["rating"] = r
        await CAT.s.set("manga", code, {"rating": r})
        u.setdefault("rated", {})[code] = value
        await CAT.s.set("users", uid, {"rated": {code: value}})
    CACHE["t"] = 0.0
    return True, stars(m)


# ---------------------------------------------------------------- o'rinlar

async def _days(n):
    """So'nggi n kunning kunlik sonlari (bugun — har doim bazadan, o'tgan kunlar — xotiradan)."""
    want = [day(-i) for i in range(n)]
    need = [d for i, d in enumerate(want) if i == 0 or d not in DAYS]
    if need:
        docs = await asyncio.gather(*(CAT.s.get("reads", d) for d in need), return_exceptions=True)
        for d, doc in zip(need, docs):
            DAYS[d] = dict(doc) if isinstance(doc, dict) else (DAYS.get(d) or {})
    for old in [d for d in DAYS if d < want[-1]]:
        DAYS.pop(old, None)
    return [DAYS.get(d) or {} for d in want]


def _sum(docs):
    tot = {}
    for d in docs:
        for k, v in d.items():
            try:
                tot[str(k)] = tot.get(str(k), 0) + int(v or 0)
            except (TypeError, ValueError):
                pass
    return tot


def _ranked(values, visible):
    items = sorted(((c, v) for c, v in values.items() if c in visible and v > 0), key=lambda x: (-x[1], x[0]))
    return items


def _with_delta(cur, prev):
    prev_rank = {c: i for i, (c, _) in enumerate(prev, 1)}
    out = []
    for i, (c, v) in enumerate(cur, 1):
        p = prev_rank.get(c)
        out.append({"code": c, "value": v, "rank": i, "delta": (p - i) if p else None, "new": p is None})
    return out


async def refresh(force=False) -> dict:
    if not force and time.time() - CACHE["t"] < TTL and CACHE["data"]:
        return CACHE["data"]
    visible = {c for c, m in CAT.manga.items() if not m.get("hidden")}
    try:
        docs = await _days(60)
    except Exception as e:
        log.warning("reyting: kunlik sonlar o‘qilmadi: %s", e)
        docs = [{}] * 60
    week, prev_week = _sum(docs[:7]), _sum(docs[7:14])
    month, prev_month = _sum(docs[:30]), _sum(docs[30:60])
    data = {
        "week": _with_delta(_ranked(week, visible), _ranked(prev_week, visible)),
        "month": _with_delta(_ranked(month, visible), _ranked(prev_month, visible)),
        "all": [{"code": c, "value": v, "rank": i, "delta": None, "new": False} for i, (c, v) in enumerate(
            _ranked({c: int(CAT.manga[c].get("views") or 0) for c in visible}, visible), 1)],
    }
    rated = []
    total_s = sum(float((CAT.manga[c].get("rating") or {}).get("s") or 0) for c in visible)
    total_n = sum(int((CAT.manga[c].get("rating") or {}).get("n") or 0) for c in visible)
    mean = (total_s / total_n) if total_n else 3.5
    for c in visible:
        avg, n = stars(CAT.manga[c])
        if n >= MIN_VOTES:
            score = (PRIOR_VOTES * mean + avg * n) / (PRIOR_VOTES + n)
            rated.append((c, score, avg, n))
    rated.sort(key=lambda x: (-x[1], -x[3], x[0]))
    data["stars"] = [{"code": c, "value": avg, "votes": n, "rank": i, "delta": None, "new": False}
                     for i, (c, _, avg, n) in enumerate(rated, 1)]
    CACHE.update(t=time.time(), data=data)
    return data


async def ranking(period="week", limit=50) -> list:
    data = await refresh()
    return (data.get(period) or [])[:limit]


def rank_of(code, period="week"):
    """Keshdagi o'rin (yangilanmagan bo'lsa ham tez). None — reytingda yo'q."""
    for it in (CACHE["data"].get(period) or []):
        if it["code"] == str(code):
            return it["rank"]
    return None
