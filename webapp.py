"""Mini App (ilova) — bot ichida ochiladigan sahifa va uning API'si.

  GET  /app          — ilova sahifasi (webapp/index.html)
  GET  /api/<nom>    — ma'lumotlar; har so'rovda Telegram bergan initData tekshiriladi (X-Init-Data sarlavhasi),
  POST /api/<nom>      shuning uchun parol kerak emas
  GET  /img/<id>     — kichik rasmlar (muqova, bob rasmi) Cloudflare ulanmaguncha
  POST /api/worker   — Cloudflare rasm xizmati o'zini ro'yxatdan o'tkazadi (GitHub Actions'dan)

Rasmlar: Cloudflare Worker orqali imzolangan vaqtinchalik havolalar bilan beriladi — Render trafigi sarflanmaydi,
pullik bob rasmini faqat uni ochgan odam oladi. Worker ulanmaguncha bob sahifalari ilovada emas, botga yuboriladi.
Imzo kalitlari BOT_TOKEN dan hosil qilinadi (Worker ham shu tokenni biladi), alohida maxfiy kalit kerak emas."""
import asyncio
import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import time
from collections import OrderedDict
from functools import lru_cache
from urllib.parse import parse_qsl

import catalog
import rating
from core import CAT, PUBLIC_URL, SUB_OK, TOKEN, bot, download, http, log, missing_channels
from core import STATE as BOT

HERE = os.path.dirname(os.path.abspath(__file__))
APP_FILE = os.path.join(HERE, "webapp", "index.html")
fmt = catalog.fmt_num
INIT_MAX_AGE = 2 * 86400          # ilova ochilganidan keyin initData shuncha vaqt yaroqli
SMALL_MAX = 400 * 1024            # Render orqali beriladigan rasmning eng katta hajmi
SMALL_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
SMALL_LIMIT = 40 * 1024 * 1024
RATE: dict[int, list] = {}        # uid → [daqiqa boshi, so'rovlar]
RATE_MAX = 150
PAGE = {"etag": "", "raw": b"", "gz": b"", "mtime": 0.0}
WORKER_RE = re.compile(r"^https://[a-z0-9][a-z0-9.-]{2,120}\.workers\.dev$")


class ApiError(Exception):
    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status = status
        self.payload = dict(extra, error=error)


# ---------------------------------------------------------------- imzolar

@lru_cache(maxsize=8)
def _key(ctx: str) -> bytes:
    return hmac.new(TOKEN.encode(), ctx.encode(), hashlib.sha256).digest()


def sign(ctx, fid, exp) -> str:
    mac = hmac.new(_key(ctx), f"{fid}:{int(exp)}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:22]


def img_base() -> str:
    return (CAT.cfg.get("img_base") or "").rstrip("/")


def img_url(fid, small=False) -> str:
    """Rasm havolasi. Muddat kun (kichik rasmlar — hafta) bo'yicha yaxlitlanadi, shuning uchun bir kun davomida
    havola o'zgarmaydi va telefon rasmni keshdan oladi."""
    if not fid:
        return ""
    period = 7 * 86400 if small else 86400
    exp = (int(time.time()) // period + 2) * period
    base = img_base()
    if base:
        return f"{base}/i/{fid}?e={exp}&s={sign('img-w', fid, exp)}"
    if small and PUBLIC_URL:
        return f"{PUBLIC_URL}/img/{fid}?e={exp}&s={sign('img-r', fid, exp)}"
    return ""


def cover_url(m, big=False) -> str:
    if img_base():
        fid = m.get("cover") if big else (m.get("cover_t") or m.get("cover"))
        return img_url(fid, small=True)
    return img_url(m.get("cover_t"), small=True)


# ---------------------------------------------------------------- kirish (Telegram initData)

def check_init(init_data: str, token: str = None, now: float = None):
    """Telegram WebApp initData imzosini tekshiradi. To'g'ri bo'lsa — foydalanuvchi (dict), aks holda None."""
    token = token or TOKEN
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    h = pairs.pop("hash", "")
    if not h:
        return None
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest(), h):
        return None
    try:
        if (now or time.time()) - int(pairs.get("auth_date") or 0) > INIT_MAX_AGE:
            return None
        user = json.loads(pairs.get("user") or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(user, dict) or not user.get("id"):
        return None
    user["start_param"] = pairs.get("start_param") or ""
    return user


def limited(uid) -> bool:
    now = time.time()
    w = RATE.get(uid)
    if w is None or now - w[0] > 60:
        RATE[uid] = [now, 1]
        if len(RATE) > 20000:
            RATE.clear()
        return False
    w[1] += 1
    return w[1] > RATE_MAX


# ---------------------------------------------------------------- ma'lumotlar

def visible(code, uid=None):
    m = CAT.get(code)
    if not m or (m.get("hidden") and not (uid and CAT.is_admin(uid))):
        return None
    return m


def manga_brief(m) -> dict:
    code = m["code"]
    chs = CAT.chapter_list(code)
    avg, votes = rating.stars(m)
    last = chs[-1] if chs else None
    return {"code": code, "title": m["title"], "cover": cover_url(m), "genres": m.get("genres") or [],
            "status": catalog.status_key(m), "chapters": len(chs), "last": fmt(last["num"]) if last else None,
            "updated": int(m.get("updated") or m.get("created") or 0), "views": int(m.get("views") or 0),
            "stars": avg, "votes": votes, "rank": rating.rank_of(code, "week"),
            "paid": any(int(c.get("price") or 0) > 0 for c in chs)}


def chapter_obj(uid, u, c) -> dict:
    code = c["id"].rsplit("_", 1)[0]
    num = float(c["num"])
    prog = (u.get("progress") or {}).get(code)
    return {"num": num, "n": fmt(num), "title": c.get("title") or "", "price": int(c.get("price") or 0),
            "open": CAT.can_read(uid, u, c), "read": prog is not None and num <= float(prog),
            "pages": int(c.get("pages") or 0), "pub": int(c.get("pub") or c.get("created") or 0),
            "thumb": img_url(c.get("thumb"), small=True)}


def channel_url(c) -> str:
    cid = str(c.get("id") or "")
    return c.get("url") or (f"https://t.me/{cid.lstrip('@')}" if cid and not cid.lstrip("-").isdigit() else "https://t.me")


async def sub_list(uid) -> list:
    return [{"title": (c.get("title") or "Kanal").strip(), "url": channel_url(c)} for c in await missing_channels(uid)]


def me_obj(uid, u, user=None) -> dict:
    vip_until = float(u.get("vip_until") or 0)
    return {"id": uid, "name": (user or {}).get("first_name") or u.get("name") or "",
            "username": (user or {}).get("username") or u.get("username") or "",
            "photo": (user or {}).get("photo_url") or "",
            "jetons": int(u.get("jetons") or 0), "vip": vip_until > time.time(), "vip_until": int(vip_until),
            "favs": [c for c in (u.get("favs") or []) if visible(c, uid)],
            "follows": [c for c in (u.get("follows") or []) if visible(c, uid)],
            "admin": CAT.is_admin(uid)}


def latest(limit=20) -> list:
    """Yangi boblar: har manga uchun eng oxirgi chop etilgan bob (so'nggi 7 kundagi yangilari soni bilan)."""
    week_ago = time.time() - 7 * 86400
    out = []
    for m in CAT.manga_list():
        chs = CAT.chapter_list(m["code"])
        if not chs:
            continue
        last = max(chs, key=lambda c: (float(c.get("pub") or c.get("created") or 0), float(c["num"])))
        pub = float(last.get("pub") or last.get("created") or 0)
        fresh = sum(1 for c in chs if float(c.get("pub") or c.get("created") or 0) >= week_ago)
        out.append({"code": m["code"], "title": m["title"], "cover": cover_url(m), "num": float(last["num"]),
                    "n": fmt(last["num"]), "price": int(last.get("price") or 0), "pub": int(pub),
                    "fresh": fresh, "thumb": img_url(last.get("thumb"), small=True)})
    out.sort(key=lambda x: -x["pub"])
    return out[:limit]


def continue_list(u, limit=12) -> list:
    prog = dict(u.get("progress") or {})
    order = sorted(prog, key=lambda c: (c != u.get("last"), -float((CAT.get(c) or {}).get("updated") or 0)))
    out = []
    for code in order:
        m = visible(code)
        if not m:
            continue
        cur = float(prog[code])
        nxt = CAT.neighbor(code, cur)
        out.append({"code": code, "title": m["title"], "cover": cover_url(m), "num": cur, "n": fmt(cur),
                    "next": fmt(nxt["num"]) if nxt else None, "total": len(CAT.chapter_list(code))})
        if len(out) >= limit:
            break
    return out


def banner_list(limit=6) -> list:
    codes = [c for c in (CAT.cfg.get("featured") or []) if visible(c)]
    if not codes:
        codes = [it["code"] for it in (rating.CACHE["data"].get("week") or [])[:5] if visible(it["code"])]
    if not codes:
        codes = [m["code"] for m in CAT.manga_list()[:5]]
    out = []
    for c in codes[:limit]:
        m = CAT.get(c)
        out.append(dict(manga_brief(m), desc=(m.get("desc") or "").strip()[:180], big=cover_url(m, big=True)))
    return out


async def rating_items(period, limit=50) -> list:
    items = []
    for it in await rating.ranking(period, limit):
        m = visible(it["code"])
        if not m:
            continue
        items.append({"code": m["code"], "title": m["title"], "cover": cover_url(m), "genres": (m.get("genres") or [])[:3],
                      "value": it["value"], "votes": it.get("votes"), "rank": it["rank"], "delta": it.get("delta"),
                      "new": bool(it.get("new"))})
    return items


# ---------------------------------------------------------------- API amallari

def _num(v):
    try:
        return float(str(v).replace("-", ".").replace(",", "."))
    except (TypeError, ValueError):
        raise ApiError(400, "Bob raqami noto‘g‘ri")


async def dispatch(name, uid, u, user, p) -> dict:
    """API so'rovini bajaradi. Xato bo'lsa ApiError."""
    if name == "boot":
        await rating.refresh()
        return {"me": me_obj(uid, u, user), "catalog": [manga_brief(m) for m in CAT.manga_list()],
                "genres": CAT.genres(), "banner": banner_list(), "latest": latest(),
                "top": await rating_items("week", 10), "continue": continue_list(u), "img": bool(img_base()),
                "bot": BOT.get("username") or "", "need_sub": await sub_list(uid),
                "start": user.get("start_param") or ""}

    if name == "manga":
        code = str(p.get("code") or "")
        m = visible(code, uid)
        if not m:
            raise ApiError(404, "Manga topilmadi")
        favs, fols = u.get("favs") or [], u.get("follows") or []
        prog = (u.get("progress") or {}).get(code)
        return {"manga": dict(manga_brief(m), desc=m.get("desc") or "", credit=m.get("credit") or "",
                              days=catalog.days_text(m.get("days")), status_text=catalog.status_text(m),
                              big=cover_url(m, big=True), fav=code in favs, follow=code in fols,
                              mine=rating.my_stars(u, code), can_rate=rating.can_rate(u, code),
                              progress=fmt(prog) if prog is not None else None,
                              hidden=bool(m.get("hidden"))),
                "chapters": [chapter_obj(uid, u, c) for c in CAT.chapter_list(code)]}

    if name == "chapter":
        code, num = str(p.get("code") or ""), _num(p.get("num"))
        m = visible(code, uid)
        c = CAT.chapter(code, num) if m else None
        if not c or (not c.get("published") and not CAT.is_admin(uid)):
            raise ApiError(404, "Bob topilmadi")
        miss = await sub_list(uid)
        if miss:
            raise ApiError(403, "Avval kanalga obuna bo‘ling", need_sub=miss)
        if not CAT.can_read(uid, u, c):
            raise ApiError(402, "Bu bob pullik", price=int(c.get("price") or 0), balance=int(u.get("jetons") or 0),
                           vip=CAT.is_vip(u))
        prev, nxt = CAT.neighbor(code, num, -1), CAT.neighbor(code, num, 1)
        out = {"code": code, "title": m["title"], "num": float(c["num"]), "n": fmt(c["num"]),
               "ctitle": c.get("title") or "", "credit": c.get("credit") or m.get("credit") or "",
               "prev": fmt(prev["num"]) if prev else None, "next": fmt(nxt["num"]) if nxt else None,
               "follow": code in (u.get("follows") or [])}
        if not img_base():
            return dict(out, pages=None, bot=True)
        pages = [img_url(fid) for fid in (await CAT.get_pages(code, num)).get("pages") or []]
        if not pages:
            raise ApiError(404, "Bu bobda sahifa yo‘q")
        await CAT.set_progress(uid, code, num)
        await CAT.add_view(code)
        await rating.note_read(uid, code)
        return dict(out, pages=pages, bot=False)

    if name == "readbot":
        code, num = str(p.get("code") or ""), _num(p.get("num"))
        c = CAT.chapter(code, num) if visible(code, uid) else None
        if not c or not c.get("published"):
            raise ApiError(404, "Bob topilmadi")
        miss = await sub_list(uid)
        if miss:
            raise ApiError(403, "Avval kanalga obuna bo‘ling", need_sub=miss)
        import reader

        async def send():
            try:
                await reader.read_chapter(uid, uid, code, num)
            except Exception as e:
                log.warning("ilova: bobni botga yuborib bo‘lmadi (%s): %s", uid, e)

        asyncio.create_task(send())
        return {"ok": True}

    if name == "buy":
        code, num = str(p.get("code") or ""), _num(p.get("num"))
        ok, why = await CAT.buy(uid, code, num)
        if not ok:
            raise ApiError(400, "Jeton yetarli emas" if "jeton" in why else why,
                           balance=int((await CAT.user(uid)).get("jetons") or 0))
        return {"ok": True, "balance": int((await CAT.user(uid)).get("jetons") or 0)}

    if name in ("fav", "follow"):
        code = str(p.get("code") or "")
        if not visible(code, uid):
            raise ApiError(404, "Manga topilmadi")
        on = await (CAT.toggle_fav(uid, code) if name == "fav" else CAT.toggle_follow(uid, code))
        return {"on": on}

    if name == "rate":
        ok, res = await rating.rate(uid, str(p.get("code") or ""), p.get("stars"))
        if not ok:
            raise ApiError(400, res)
        return {"stars": res[0], "votes": res[1], "mine": int(p.get("stars"))}

    if name == "rating":
        period = str(p.get("period") or "week")
        if period not in rating.PERIODS:
            period = "week"
        return {"period": period, "items": await rating_items(period)}

    if name == "me":
        return {"me": me_obj(uid, u, user), "continue": continue_list(u, 30),
                "favs": [manga_brief(CAT.get(c)) for c in (u.get("favs") or []) if visible(c, uid)]}

    if name == "subcheck":
        SUB_OK.pop(uid, None)
        miss = await sub_list(uid)
        return {"ok": not miss, "need_sub": miss}

    raise ApiError(404, "Bunday amal yo‘q")


async def register_worker(body) -> dict:
    """GitHub Actions Worker'ni joylagach chaqiradi: imzo (BOT_TOKEN bilan) va Worker ishlashini tekshiradi."""
    url = str(body.get("url") or "").strip().rstrip("/")
    try:
        ts = int(body.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    sig = str(body.get("sig") or "")
    good = hmac.new(TOKEN.encode(), f"reg:{url}:{ts}".encode(), hashlib.sha256).hexdigest()
    if not TOKEN or not hmac.compare_digest(good, sig) or abs(time.time() - ts) > 1800:
        raise ApiError(403, "imzo noto‘g‘ri yoki eskirgan")
    if not WORKER_RE.match(url):
        raise ApiError(400, "Worker manzili noto‘g‘ri")
    try:
        async with http().get(url + "/health", timeout=_timeout(20)) as r:
            txt = await r.text()
        if r.status != 200 or "Manhora" not in txt:
            raise ApiError(502, f"Worker javobi kutilganidek emas ({r.status})")
        sample = next((m.get("cover_t") for m in CAT.manga.values() if m.get("cover_t")), None)
        if sample:
            exp = int(time.time()) + 3600
            async with http().get(f"{url}/i/{sample}?e={exp}&s={sign('img-w', sample, exp)}", timeout=_timeout(30)) as r:
                await r.read()
            if r.status != 200:
                raise ApiError(502, f"Worker rasmni bermadi ({r.status}) — BOT_TOKEN to‘g‘rimi?")
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(502, f"Worker’ga ulanib bo‘lmadi: {e!r}"[:200])
    first = not img_base()
    await CAT.set_cfg(img_base=url)
    await CAT.log_action(0, "🖼 Cloudflare", f"Rasmlar xizmati ulandi: {url}")
    if first:
        for oid in CAT.owners:
            try:
                await bot.send_message(oid, "🖼 <b>Cloudflare ulandi!</b> Endi boblar ilovaning o‘zida o‘qiladi, rasmlar "
                                            "Render trafigini sarflamaydi.")
            except Exception:
                pass
    return {"ok": True, "url": url}


def _timeout(sec):
    import aiohttp
    return aiohttp.ClientTimeout(total=sec)


# ---------------------------------------------------------------- aiohttp

def _page():
    try:
        mt = os.path.getmtime(APP_FILE)
    except OSError:
        return None
    if mt != PAGE["mtime"]:
        raw = open(APP_FILE, "rb").read()
        PAGE.update(raw=raw, gz=gzip.compress(raw, 9), etag='"' + hashlib.sha256(raw).hexdigest()[:20] + '"', mtime=mt)
    return PAGE


def _gzip_ok(request) -> bool:
    return "gzip" in (request.headers.get("Accept-Encoding") or "")


def json_response(request, payload, status=200):
    from aiohttp import web
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {"Cache-Control": "no-store"}
    if len(body) > 1024 and _gzip_ok(request):
        body = gzip.compress(body, 6)
        headers["Content-Encoding"] = "gzip"
    return web.Response(body=body, status=status, content_type="application/json", charset="utf-8", headers=headers)


def setup(app):
    from aiohttp import web

    async def app_page(request):
        pg = _page()
        if pg is None:
            return web.Response(text="Ilova fayli topilmadi", status=500)
        headers = {"Cache-Control": "no-cache", "ETag": pg["etag"]}
        if request.headers.get("If-None-Match") == pg["etag"]:
            return web.Response(status=304, headers=headers)
        if _gzip_ok(request):
            headers["Content-Encoding"] = "gzip"
            return web.Response(body=pg["gz"], content_type="text/html", charset="utf-8", headers=headers)
        return web.Response(body=pg["raw"], content_type="text/html", charset="utf-8", headers=headers)

    async def img_small(request):
        import traffic
        fid = request.match_info["fid"]
        try:
            exp = int(request.query.get("e") or 0)
        except ValueError:
            exp = 0
        if exp < time.time() or not hmac.compare_digest(sign("img-r", fid, exp), request.query.get("s") or ""):
            return web.Response(status=403)
        if traffic.over():
            return web.Response(status=503, text="trafik chegarasi")
        data = SMALL_CACHE.get(fid)
        if data is None:
            try:
                data = await download(fid)
            except Exception:
                return web.Response(status=404)
            if len(data) > SMALL_MAX:
                return web.Response(status=413)
            SMALL_CACHE[fid] = data
            while sum(len(v) for v in SMALL_CACHE.values()) > SMALL_LIMIT:
                SMALL_CACHE.popitem(last=False)
        else:
            SMALL_CACHE.move_to_end(fid)
        return web.Response(body=data, content_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=604800, immutable"})

    async def api(request):
        name = request.match_info["name"]
        body = {}
        if request.method == "POST":
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    body = {}
            except Exception:
                body = {}
        if name == "worker":
            try:
                return json_response(request, await register_worker(body))
            except ApiError as e:
                log.warning("Cloudflare ro‘yxatdan o‘tmadi: %s", e.payload.get("error"))
                return json_response(request, e.payload, e.status)
        user = check_init(request.headers.get("X-Init-Data") or "")
        if not user:
            return json_response(request, {"error": "Ilovani bot ichidan oching (kirish tekshiruvi o‘tmadi)."}, 401)
        uid = int(user["id"])
        if limited(uid):
            return json_response(request, {"error": "Juda ko‘p so‘rov — biroz kuting."}, 429)
        u = await CAT.user(uid, user.get("first_name") or "", user.get("username") or "")
        if u.get("blocked"):
            return json_response(request, {"error": "Kirish cheklangan."}, 403)
        params = dict(request.query)
        params.update(body)
        try:
            return json_response(request, await dispatch(name, uid, u, user, params))
        except ApiError as e:
            return json_response(request, e.payload, e.status)
        except Exception:
            log.exception("ilova API: %s", name)
            return json_response(request, {"error": "Serverda xato, birozdan keyin urinib ko‘ring."}, 500)

    app.router.add_get("/app", app_page)
    app.router.add_get("/app/", app_page)
    app.router.add_get("/img/{fid}", img_small)
    app.router.add_get("/api/{name}", api)
    app.router.add_post("/api/{name}", api)


async def set_menu_button():
    """Pastdagi «Menu» tugmasi ilovani ochadi (barcha foydalanuvchilar uchun)."""
    if not PUBLIC_URL or bot is None:
        return
    try:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="📱 Ilova",
                                                                    web_app=WebAppInfo(url=PUBLIC_URL + "/app")))
        log.info("Ilova tugmasi o‘rnatildi: %s/app", PUBLIC_URL)
    except Exception as e:
        log.warning("Ilova tugmasini o‘rnatib bo‘lmadi: %s", e)
