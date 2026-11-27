"""Manga katalogi: mangalar, boblar, kodlar, foydalanuvchilar, jeton/VIP, adminlar va sozlamalar.

Bazadagi tuzilma (Firestore kolleksiyalari):
  manga/{kod}      — nomi, tavsifi, janrlari, muqovasi, kuzatuvchilar, boblar ro'yxati (sahifalarsiz)
  chapters/{id}    — bob sahifalari (Telegram file_id lari) va tayyor PDF
  fps/{kod}        — boblarning «barmoq izlari» (takroriy bobni aniqlash uchun; faqat yuklashda o'qiladi)
  users/{uid}      — jeton, VIP muddati, sotib olingan boblar, sevimlilar, qayerda to'xtagani
  admins/{uid}     — admin huquqlari
  settings/main    — sozlamalar (ombor kanali, keyingi kod, majburiy kanallar, narxlar ...)
  settings/journal — adminlar harakatlari jurnali (oxirgi yozuvlar)
  stats/{sana}     — kunlik hisoblagichlar
Server uyg'onganda faqat manga hujjatlari o'qiladi; sahifalar kerak bo'lganda yuklanadi (bepul limitni tejash uchun).
Eslatma: Firestore ichma-ich ro'yxatlarni (ro'yxat ichida ro'yxat) saqlamaydi — shuning uchun juftliklar lug'at qilib yoziladi.
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))
JOURNAL_MAX = 300
GLOSSARY_MAX = 300
SLOTS = (9, 12, 15, 18, 21)          # rejalashtirilgan boblar chiqadigan soatlar (Toshkent vaqti)
STATUS = {"ongoing": "🟢 Davom etmoqda", "completed": "✅ Tugagan", "hiatus": "⏸ To‘xtatilgan", "soon": "🔜 Tez orada"}
_LEGACY_STATUS = {"davom etmoqda": "ongoing", "tugagan": "completed", "to‘xtatilgan": "hiatus",
                  "toxtatilgan": "hiatus", "to'xtatilgan": "hiatus", "tez orada": "soon"}
DAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")
DAYS_SHORT = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")

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


def ranges(nums) -> str:
    """[12, 13, 14, 16, 17.5] → '12–14, 16, 17.5'"""
    vals = sorted({float(n) for n in nums})
    out, i = [], 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j].is_integer() and vals[j + 1] == vals[j] + 1:
            j += 1
        out.append(fmt_num(vals[i]) if j == i else (f"{fmt_num(vals[i])}, {fmt_num(vals[j])}" if j == i + 1
                                                     else f"{fmt_num(vals[i])}–{fmt_num(vals[j])}"))
        i = j + 1
    return ", ".join(out)


def status_key(m) -> str:
    s = (m.get("status") or "").strip()
    return s if s in STATUS else _LEGACY_STATUS.get(s.lower(), "")


def status_text(m) -> str:
    return STATUS.get(status_key(m)) or (m.get("status") or "")


def days_text(days) -> str:
    ds = sorted({int(d) for d in (days or []) if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
    if not ds:
        return ""
    if len(ds) == 7:
        return "har kuni"
    names = [DAYS[d] for d in ds]
    return "har " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " va " + names[-1])


def when_text(ts) -> str:
    d = datetime.fromtimestamp(float(ts), TZ)
    return f"{d:%d.%m} ({DAYS_SHORT[d.weekday()]}) {d:%H:%M}"


def slot_ts(day_offset: int, hour: int, now=None) -> float:
    """Bugundan day_offset kun keyingi kunning soat hour:00 (Toshkent) vaqti."""
    base = datetime.fromtimestamp(now or time.time(), TZ).date() + timedelta(days=int(day_offset))
    return datetime(base.year, base.month, base.day, int(hour), 0, tzinfo=TZ).timestamp()


def same_fp(a, b) -> bool:
    """Ikki bobning «barmoq izi» (sahifalar soni + bir nechta sahifaning dHash'i) bir xilmi."""
    if not a or not b or int(a.get("n") or 0) != int(b.get("n") or 0):
        return False
    ha, hb = a.get("h") or [], b.get("h") or []

    def flat(h):  # bir xil rangli (oq/qora) sahifa — solishtirishga yaramaydi
        bits = len(h) * 4
        return not bits * 0.05 <= bin(int(h, 16)).count("1") <= bits * 0.95

    pairs = [(x, y) for x, y in zip(ha, hb) if x and y and len(x) == len(y) and not flat(x) and not flat(y)]
    if not pairs:
        return False
    hits = sum(1 for x, y in pairs if bin(int(x, 16) ^ int(y, 16)).count("1") <= len(x) * 4 * 0.05)
    need = len(pairs) - 2 if len(pairs) >= 6 else len(pairs)
    return hits >= need


class Catalog:
    def __init__(self, store, owners=()):
        self.s = store
        self.owners = set(owners)
        self.manga: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}       # bob id → {"pages": [...], "pdf": ...} (kesh)
        self.users: dict[int, dict] = {}
        self.admins: dict[int, dict] = {}
        self.cfg: dict = dict(DEFAULT_CFG)
        self.journal: list[dict] = []
        self.fps: dict[str, dict] = {}         # manga kodi → {bob id: barmoq izi} (kesh)
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
        self.journal = list((await self.s.get("settings", "journal") or {}).get("items") or [])
        self.loaded = True

    async def set_cfg(self, **kw):
        self.cfg.update(kw)
        await self.s.set("settings", "main", kw)

    async def log_action(self, uid, name, text):
        """Admin harakatini jurnalga yozadi (xato bo'lsa ham asosiy ish to'xtamaydi)."""
        self.journal.append({"t": time.time(), "u": int(uid or 0), "n": (name or "")[:40], "a": str(text)[:300]})
        self.journal = self.journal[-JOURNAL_MAX:]
        try:
            await self.s.set("settings", "journal", {"items": self.journal}, merge=False)
        except Exception:
            pass

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

    async def set_admin(self, uid: int, rights, name="", by=0, username=""):
        old = self.admins.get(uid) or {}
        rights = [r for r in RIGHTS if r in set(rights)]
        d = {"rights": rights, "name": name or old.get("name", ""), "username": username or old.get("username", ""),
             "by": by or old.get("by", 0), "added": old.get("added") or time.time()}
        self.admins[uid] = d
        await self.s.set("admins", uid, d, merge=False)

    def admin_name(self, uid) -> str:
        a = self.admins.get(uid) or {}
        if a.get("name"):
            return a["name"]
        return "Bosh admin" if uid in self.owners else str(uid)

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
             "genres": [g.strip() for g in (genres or []) if g.strip()], "cover": cover, "status": "ongoing",
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
        self.fps.pop(code, None)
        await self.s.delete("fps", code)
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

    async def save_chapter(self, code, num, pages, title="", price=0, published=False, fp=None,
                           keep=False) -> dict:
        """Bobni saqlaydi. keep=True va bob avval bo'lsa — narxi, nomi va chop etilganligi o'zgarmaydi
        (faqat sahifalar yangilanadi)."""
        code = str(code)
        m = self.manga[code]
        cid = chapter_id(code, num)
        old = m.get("chapters", {}).get(cid) if keep else None
        meta = {"num": float(num), "title": title, "price": int(price), "published": bool(published),
                "pages": len(pages), "created": time.time(), "sched": None}
        if old:
            meta.update(title=old.get("title") or title, price=int(old.get("price") or 0),
                        published=bool(old.get("published")), created=old.get("created") or meta["created"],
                        sched=old.get("sched"), sched_price=old.get("sched_price"), sched_by=old.get("sched_by"))
        if published and not (old and old.get("published")):
            meta["pub"] = meta["created"]
        elif old and old.get("pub"):
            meta["pub"] = old["pub"]
        if old and old.get("thumb") is not None:
            meta["thumb"] = None          # sahifalar yangilandi — kichik rasm qayta yasaladi
        if fp:
            await self.chapter_fps(code)
            self.fps[code][cid] = fp
            await self.s.set("fps", code, {cid: fp})
        await self.s.set("chapters", cid, {"manga": code, "num": float(num), "pages": list(pages), "pdf": None},
                         merge=False)
        self.pages[cid] = {"pages": list(pages), "pdf": None}
        m.setdefault("chapters", {})[cid] = meta
        upd = {"chapters": {cid: meta}}
        if published and not old:
            m["updated"] = upd["updated"] = time.time()
        await self.s.set("manga", code, upd)
        return dict(meta, id=cid)

    async def set_pages(self, code, num, pages):
        """Bob sahifalarini almashtiradi (tahrirlashdan keyin). Saqlangan PDF eskiradi."""
        cid = chapter_id(code, num)
        await self.s.set("chapters", cid, {"pages": list(pages), "pdf": None})
        self.pages[cid] = {"pages": list(pages), "pdf": None}
        await self.set_chapter(code, num, pages=len(pages), thumb=None)

    def drafts(self, code):
        return [c for c in self.chapter_list(code, unpublished=True) if not c.get("published")]

    def scheduled(self):
        """Rejalashtirilgan (hali chop etilmagan) boblar: [(vaqt, kod, raqam, meta)], vaqt bo'yicha."""
        out = []
        for code, m in self.manga.items():
            for cid, c in m.get("chapters", {}).items():
                if not c.get("published") and c.get("sched"):
                    out.append((float(c["sched"]), code, float(c["num"]), dict(c, id=cid)))
        return sorted(out, key=lambda x: (x[0], x[1], x[2]))

    def due(self, now=None):
        now = time.time() if now is None else now
        return [x for x in self.scheduled() if x[0] <= now]

    def next_due(self):
        s = self.scheduled()
        return s[0][0] if s else None

    async def schedule(self, code, num, ts, price, by=0):
        await self.set_chapter(code, num, sched=float(ts), sched_price=int(price), sched_by=int(by or 0))

    async def unschedule(self, code, num):
        await self.set_chapter(code, num, sched=None)

    async def publish(self, code, num, price=None) -> bool:
        fields = {"published": True, "sched": None}
        if price is not None:
            fields["price"] = int(price)
        return await self.set_chapter(code, num, **fields)

    async def chapter_fps(self, code) -> dict:
        code = str(code)
        if code not in self.fps:
            self.fps[code] = dict(await self.s.get("fps", code) or {})
        return self.fps[code]

    async def find_duplicate(self, code, fp, exclude=None):
        """Shu mangada xuddi shunday bob bormi (qayta yuklanganini aniqlash uchun)."""
        m = self.get(code)
        if not m or not fp:
            return None
        fps = await self.chapter_fps(code)
        for cid, c in m.get("chapters", {}).items():
            if exclude is not None and float(c["num"]) == float(exclude):
                continue
            if same_fp(fp, fps.get(cid)):
                return dict(c, id=cid)
        return None

    # ------------------------------------------------------------ glossariy (ismlar lug'ati)
    def glossary(self, code) -> list:
        m = self.get(code) or {}
        return [g for g in (m.get("glossary") or []) if isinstance(g, dict) and g.get("o") and g.get("t")]

    def glossary_pairs(self, code):
        return [(g["o"], g["t"]) for g in self.glossary(code)]

    async def set_glossary(self, code, items):
        seen, out = set(), []
        for g in items:
            o, t = str(g.get("o", "")).strip()[:80], str(g.get("t", "")).strip()[:80]
            k = norm(o) or o
            if o and t and k not in seen:
                seen.add(k)
                out.append({"o": o, "t": t})
        out = out[:GLOSSARY_MAX]
        await self.update_manga(code, glossary=out)
        return out

    async def add_glossary(self, code, pairs):
        """Yangi juftliklarni qo'shadi; aslida bor so'z bo'lsa — tarjimasi yangilanadi."""
        cur = {norm(g["o"]) or g["o"]: g for g in self.glossary(code)}
        order = list(cur)
        for o, t in pairs:
            k = norm(o) or o
            if k not in cur:
                order.append(k)
            cur[k] = {"o": o, "t": t}
        return await self.set_glossary(code, [cur[k] for k in order])

    async def set_chapter(self, code, num, **fields):
        code = str(code)
        m = self.manga[code]
        cid = chapter_id(code, num)
        meta = m["chapters"][cid]
        newly = fields.get("published") and not meta.get("published")
        if newly:
            fields = dict(fields, pub=time.time())        # chop etilgan vaqt — ilovadagi «Yangi boblar» uchun
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
        fps = await self.chapter_fps(code)
        if fps.pop(cid, None) is not None:
            await self.s.set("fps", code, fps, merge=False)
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
                     "follows": [], "progress": {}, "blocked": False, "kb": ""}
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
