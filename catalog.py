"""Manga katalogi: mangalar, boblar, kodlar, foydalanuvchilar, jeton/VIP, adminlar va sozlamalar.

Bazadagi tuzilma (Firestore kolleksiyalari):
  manga/{kod}      — nomi, tavsifi, janrlari, muqovasi, kuzatuvchilar, boblar ro'yxati (sahifalarsiz)
  chapters/{id}    — bob sahifalari (Telegram file_id lari) va tayyor PDF
  users/{uid}      — jeton, VIP muddati, sotib olingan boblar, sevimlilar, qayerda to'xtagani
  admins/{uid}     — admin huquqlari
  settings/main    — sozlamalar (ombor kanali, keyingi kod, narxlar ...)
  stats/{sana}     — kunlik hisoblagichlar
Server uyg'onganda faqat manga hujjatlari o'qiladi; sahifalar kerak bo'lganda yuklanadi (bepul limitni tejash uchun).
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))

RIGHTS = {
    "manga": "📚 Manga qo‘shish va tahrirlash",
    "chapter": "📤 Bob yuklash va tarjima",
    "payments": "💳 To‘lovlarni tasdiqlash",
    "broadcast": "📣 Hammaga xabar yuborish",
    "users": "👥 Foydalanuvchilarni boshqarish",
    "stats": "📊 Statistika",
    "settings": "⚙️ Sozlamalar",
}

DEFAULT_CFG = {
    "next_code": 101,
    "storage_chat": None,
    "start_jetons": 0,
    "channels": [],
    "card": "",
    "card_owner": "",
    "packages": [{"jetons": 5, "sum": 10000, "stars": 50}, {"jetons": 12, "sum": 20000, "stars": 100},
                 {"jetons": 35, "sum": 50000, "stars": 250}],
    "vip": [{"days": 7, "sum": 15000, "stars": 75}, {"days": 30, "sum": 45000, "stars": 220}],
}


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def fmt_num(num) -> str:
    f = float(num)
    return str(int(f)) if f.is_integer() else ("%g" % f)


def parse_num(text):
    t = (text or "").strip().replace(",", ".")
    m = re.fullmatch(r"(\d{1,5}(?:\.\d{1,2})?)", t)
    return float(m.group(1)) if m else None


def chapter_id(code, num) -> str:
    return f"{code}_{fmt_num(num).replace('.', '-')}"


def norm(s: str) -> str:
    s = (s or "").lower().replace("‘", "").replace("’", "").replace("'", "").replace("ʻ", "").replace("`", "")
    return re.sub(r"[^\w]+", " ", s, flags=re.UNICODE).strip()


class Catalog:
    def __init__(self, store, owners=()):
        self.s = store
        self.owners = set(owners)
        self.manga: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}       # bob id → {"pages": [...], "pdf": ...} (kesh)
        self.users: dict[int, dict] = {}
        self.admins: dict[int, dict] = {}
        self.cfg: dict = dict(DEFAULT_CFG)
        self._locks: dict[int, asyncio.Lock] = {}
        self.loaded = False

    # ------------------------------------------------------------ yuklash va sozlamalar
    async def load(self):
        self.manga = {}
        for mid, d in await self.s.all("manga"):
            d["code"] = str(mid)
            d.setdefault("chapters", {})
            d.setdefault("followers", [])
            self.manga[str(mid)] = d
        self.admins = {int(k): v for k, v in await self.s.all("admins") if str(k).lstrip("-").isdigit()}
        self.cfg = {**DEFAULT_CFG, **(await self.s.get("settings", "main") or {})}
        self.loaded = True

    async def set_cfg(self, **kw):
        self.cfg.update(kw)
        await self.s.set("settings", "main", kw)

    # ------------------------------------------------------------ adminlar
    def is_owner(self, uid) -> bool:
        return uid in self.owners

    def is_admin(self, uid) -> bool:
        return uid in self.owners or uid in self.admins

    def rights(self, uid) -> set:
        if uid in self.owners:
            return set(RIGHTS)
        return set((self.admins.get(uid) or {}).get("rights", []))

    def has(self, uid, right) -> bool:
        return right in self.rights(uid)

    async def set_admin(self, uid: int, rights, name="", by=0):
        rights = [r for r in rights if r in RIGHTS]
        d = {"rights": rights, "name": name or (self.admins.get(uid) or {}).get("name", ""), "by": by,
             "added": (self.admins.get(uid) or {}).get("added") or time.time()}
        self.admins[uid] = d
        await self.s.set("admins", uid, d, merge=False)

    async def remove_admin(self, uid: int):
        self.admins.pop(uid, None)
        await self.s.delete("admins", uid)

    # ------------------------------------------------------------ mangalar
    def manga_list(self, hidden=False, genre=None):
        items = [m for m in self.manga.values() if hidden or not m.get("hidden")]
        if genre:
            g = norm(genre)
            items = [m for m in items if any(norm(x) == g for x in m.get("genres", []))]
        return sorted(items, key=lambda m: (m.get("updated") or m.get("created") or 0), reverse=True)

    def popular(self, limit=20):
        items = [m for m in self.manga.values() if not m.get("hidden")]
        return sorted(items, key=lambda m: m.get("views", 0), reverse=True)[:limit]

    def genres(self):
        seen = {}
        for m in self.manga.values():
            if m.get("hidden"):
                continue
            for g in m.get("genres", []):
                seen.setdefault(norm(g), g)
        return sorted(seen.values(), key=str.lower)

    def get(self, code):
        return self.manga.get(str(code).strip())

    def search(self, query: str, limit=10, hidden=False):
        q = norm(query)
        if not q:
            return []
        toks = q.split()
        scored = []
        for m in self.manga.values():
            if m.get("hidden") and not hidden:
                continue
            names = [norm(m.get("title", ""))] + [norm(a) for a in m.get("alt", [])]
            best = 0
            for n in names:
                if n == q:
                    best = max(best, 5)
                elif n.startswith(q):
                    best = max(best, 4)
                elif q in n:
                    best = max(best, 3)
                elif all(t in n for t in toks):
                    best = max(best, 2)
                elif any(len(t) >= 3 and t in n for t in toks):
                    best = max(best, 1)
            if best:
                scored.append((best, m.get("views", 0), m))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [m for _b, _v, m in scored[:limit]]

    async def next_code(self) -> str:
        code = int(self.cfg.get("next_code") or 101)
        while str(code) in self.manga:
            code += 1
        return str(code)

    async def add_manga(self, title, desc="", genres=None, cover=None, code=None, by=0) -> dict:
        auto = not code
        code = str(code) if code else await self.next_code()
        if code in self.manga:
            raise ValueError(f"{code} kodi band")
        now = time.time()
        d = {"code": code, "title": title.strip(), "alt": [], "desc": (desc or "").strip(),
             "genres": [g.strip() for g in (genres or []) if g.strip()], "cover": cover, "status": "davom etmoqda",
             "hidden": False, "created": now, "updated": now, "views": 0, "followers": [], "chapters": {}, "by": by}
        self.manga[code] = d
        await self.s.set("manga", code, {k: v for k, v in d.items() if k != "code"}, merge=False)
        if auto:  # o'zingiz yozgan kod avtomatik raqamlashni o'zgartirmaydi (band kodlar baribir o'tkazib yuboriladi)
            await self.set_cfg(next_code=int(code) + 1)
        return d

    async def update_manga(self, code, **fields):
        m = self.manga[str(code)]
        m.update(fields)
        await self.s.set("manga", str(code), fields)

    async def delete_manga(self, code):
        code = str(code)
        m = self.manga.pop(code, None)
        if not m:
            return
        for cid in list(m.get("chapters", {})):
            self.pages.pop(cid, None)
            await self.s.delete("chapters", cid)
        await self.s.delete("manga", code)

    # ------------------------------------------------------------ boblar
    def chapter_list(self, code, unpublished=False):
        m = self.get(code)
        if not m:
            return []
        items = [dict(v, id=k) for k, v in m.get("chapters", {}).items() if unpublished or v.get("published")]
        return sorted(items, key=lambda c: float(c["num"]))

    def chapter(self, code, num):
        m = self.get(code)
        if not m:
            return None
        cid = chapter_id(code, num)
        c = m.get("chapters", {}).get(cid)
        return dict(c, id=cid) if c else None

    def neighbor(self, code, num, step=1, unpublished=False):
        lst = self.chapter_list(code, unpublished)
        nums = [float(c["num"]) for c in lst]
        try:
            i = nums.index(float(num))
        except ValueError:
            return None
        j = i + step
        return lst[j] if 0 <= j < len(lst) else None

    def next_num(self, code) -> float:
        lst = self.chapter_list(code, unpublished=True)
        return float(int(max(float(c["num"]) for c in lst)) + 1) if lst else 1.0

    async def save_chapter(self, code, num, pages, title="", price=0, published=False) -> dict:
        code = str(code)
        m = self.manga[code]
        cid = chapter_id(code, num)
        meta = {"num": float(num), "title": title, "price": int(price), "published": bool(published),
                "pages": len(pages), "created": time.time()}
        await self.s.set("chapters", cid, {"manga": code, "num": float(num), "pages": list(pages), "pdf": None},
                         merge=False)
        self.pages[cid] = {"pages": list(pages), "pdf": None}
        m.setdefault("chapters", {})[cid] = meta
        upd = {"chapters": {cid: meta}}
        if published:
            m["updated"] = upd["updated"] = time.time()
        await self.s.set("manga", code, upd)
        return dict(meta, id=cid)

    async def set_chapter(self, code, num, **fields):
        code = str(code)
        m = self.manga[code]
        cid = chapter_id(code, num)
        meta = m["chapters"][cid]
        newly = fields.get("published") and not meta.get("published")
        meta.update(fields)
        upd = {"chapters": {cid: fields}}
        if newly:
            m["updated"] = upd["updated"] = time.time()
        await self.s.set("manga", code, upd)
        return bool(newly)

    async def delete_chapter(self, code, num):
        code = str(code)
        cid = chapter_id(code, num)
        m = self.manga[code]
        m.get("chapters", {}).pop(cid, None)
        self.pages.pop(cid, None)
        await self.s.delete("chapters", cid)
        # xaritadan bitta kalitni o'chirish uchun manga hujjatini to'liq qayta yozamiz
        doc = {k: v for k, v in m.items() if k != "code"}
        await self.s.set("manga", code, doc, merge=False)

    async def get_pages(self, code, num):
        cid = chapter_id(code, num)
        if cid not in self.pages:
            d = await self.s.get("chapters", cid) or {}
            self.pages[cid] = {"pages": d.get("pages") or [], "pdf": d.get("pdf")}
            if len(self.pages) > 300:
                self.pages.pop(next(iter(self.pages)))
        return self.pages[cid]

    async def set_pdf(self, code, num, file_id):
        cid = chapter_id(code, num)
        (await self.get_pages(code, num))["pdf"] = file_id
        await self.s.set("chapters", cid, {"pdf": file_id})

    async def add_view(self, code):
        m = self.get(code)
        if m:
            m["views"] = m.get("views", 0) + 1
            await self.s.incr("manga", str(code), "views", 1)
        await self.s.incr("stats", today(), "reads", 1)

    # ------------------------------------------------------------ foydalanuvchilar
    def lock(self, uid):
        return self._locks.setdefault(uid, asyncio.Lock())

    async def user(self, uid: int, name=None, username=None) -> dict:
        u = self.users.get(uid)
        if u is None:
            u = await self.s.get("users", uid)
            if u is None:
                u = {"name": name or "", "username": username or "", "joined": time.time(),
                     "jetons": int(self.cfg.get("start_jetons") or 0), "vip_until": 0, "bought": [], "favs": [],
                     "follows": [], "progress": {}, "blocked": False}
                await self.s.set("users", uid, u, merge=False)
                await self.s.incr("stats", today(), "new_users", 1)
                await self.s.incr("stats", "total", "users", 1)
            for k, v in (("jetons", 0), ("vip_until", 0), ("bought", []), ("favs", []), ("follows", []),
                         ("progress", {}), ("blocked", False)):
                u.setdefault(k, v)
            self.users[uid] = u
            if len(self.users) > 5000:
                self.users.pop(next(iter(self.users)))
        changed = {}
        if name and u.get("name") != name:
            changed["name"] = name
        if username is not None and username and u.get("username") != username:
            changed["username"] = username
        if changed:
            u.update(changed)
            await self.s.set("users", uid, changed)
        return u

    async def save_user(self, uid, **fields):
        u = await self.user(uid)
        u.update(fields)
        await self.s.set("users", uid, fields)

    async def toggle_fav(self, uid, code) -> bool:
        u = await self.user(uid)
        favs = list(u.get("favs", []))
        on = str(code) not in favs
        favs = favs + [str(code)] if on else [x for x in favs if x != str(code)]
        await self.save_user(uid, favs=favs)
        return on

    async def toggle_follow(self, uid, code) -> bool:
        code = str(code)
        u = await self.user(uid)
        fol = list(u.get("follows", []))
        on = code not in fol
        fol = fol + [code] if on else [x for x in fol if x != code]
        await self.save_user(uid, follows=fol)
        m = self.manga.get(code)
        if m is not None:
            f = [x for x in m.get("followers", []) if x != uid]
            if on:
                f.append(uid)
            m["followers"] = f
            await self.s.set("manga", code, {"followers": f})
        return on

    async def set_progress(self, uid, code, num):
        u = await self.user(uid)
        u.setdefault("progress", {})[str(code)] = float(num)
        u["last"] = str(code)
        await self.s.set("users", uid, {"progress": {str(code): float(num)}, "last": str(code)})

    def is_vip(self, u) -> bool:
        return float(u.get("vip_until") or 0) > time.time()

    def can_read(self, uid, u, chap) -> bool:
        if not chap:
            return False
        if int(chap.get("price") or 0) <= 0 or self.is_admin(uid) or self.is_vip(u):
            return True
        return chap["id"] in (u.get("bought") or [])

    async def buy(self, uid, code, num):
        """Bobni jeton evaziga ochish. Qaytaradi: (muvaffaqiyat, sabab)."""
        chap = self.chapter(code, num)
        if not chap or not chap.get("published"):
            return False, "Bob topilmadi."
        async with self.lock(uid):
            u = await self.user(uid)
            if self.can_read(uid, u, chap):
                return True, "ochiq"
            price = int(chap.get("price") or 0)
            if int(u.get("jetons") or 0) < price:
                return False, "jeton yetarli emas"
            u["jetons"] = int(u.get("jetons") or 0) - price
            u["bought"] = list(u.get("bought") or []) + [chap["id"]]
            await self.s.set("users", uid, {"jetons": u["jetons"], "bought": u["bought"]})
        await self.s.incr("stats", today(), "jetons_spent", price)
        return True, "sotib olindi"

    async def add_jetons(self, uid, n: int):
        async with self.lock(uid):
            u = await self.user(uid)
            u["jetons"] = max(0, int(u.get("jetons") or 0) + int(n))
            await self.s.set("users", uid, {"jetons": u["jetons"]})
            return u["jetons"]

    async def add_vip(self, uid, days: int):
        async with self.lock(uid):
            u = await self.user(uid)
            start = max(time.time(), float(u.get("vip_until") or 0))
            u["vip_until"] = start + days * 86400
            await self.s.set("users", uid, {"vip_until": u["vip_until"]})
            return u["vip_until"]

    # ------------------------------------------------------------ statistika
    async def stats(self):
        t = await self.s.get("stats", today()) or {}
        tot = await self.s.get("stats", "total") or {}
        chapters = sum(len([c for c in m.get("chapters", {}).values() if c.get("published")])
                       for m in self.manga.values())
        return {"manga": len(self.manga), "chapters": chapters, "users": tot.get("users", 0),
                "new_today": t.get("new_users", 0), "reads_today": t.get("reads", 0),
                "jetons_today": t.get("jetons_spent", 0)}
