"""Doimiy saqlash: Firebase Firestore (serverda) yoki xotira (sinov / Firebase ulanmaguncha).

Interfeys juda oddiy — kalit-qiymat hujjatlar:
  get(col, id) → dict | None      set(col, id, data, merge=True)      delete(col, id)
  all(col) → [(id, dict)]          incr(col, id, field, n)
"""
import copy
import json
import logging
import os

log = logging.getLogger("baza")


class MemoryStore:
    """Xotiradagi baza: server qayta ishga tushsa, ma'lumot yo'qoladi."""
    persistent = False

    def __init__(self):
        self.data: dict[str, dict[str, dict]] = {}

    async def get(self, col, id):
        d = self.data.get(col, {}).get(str(id))
        return copy.deepcopy(d) if d is not None else None

    async def set(self, col, id, data, merge=True):
        c = self.data.setdefault(col, {})
        if merge and str(id) in c:
            _merge(c[str(id)], copy.deepcopy(data))
        else:
            c[str(id)] = copy.deepcopy(data)

    async def delete(self, col, id):
        self.data.get(col, {}).pop(str(id), None)

    async def all(self, col):
        return [(k, copy.deepcopy(v)) for k, v in self.data.get(col, {}).items()]

    async def incr(self, col, id, field, n=1):
        c = self.data.setdefault(col, {}).setdefault(str(id), {})
        c[field] = (c.get(field) or 0) + n


def _merge(dst: dict, src: dict):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


class FirestoreStore:
    persistent = True

    def __init__(self, info: dict):
        from google.cloud import firestore
        from google.oauth2 import service_account
        self.creds = service_account.Credentials.from_service_account_info(info)
        self.fs = firestore
        self.project = info["project_id"]
        self._db = None  # mijoz ishlayotgan event loop ichida yaratiladi (gRPC loop'ga bog'lanadi)

    @property
    def db(self):
        if self._db is None:
            self._db = self.fs.AsyncClient(project=self.project, credentials=self.creds)
        return self._db

    def _doc(self, col, id):
        return self.db.collection(col).document(str(id))

    async def get(self, col, id):
        snap = await self._doc(col, id).get()
        return snap.to_dict() if snap.exists else None

    async def set(self, col, id, data, merge=True):
        await self._doc(col, id).set(data, merge=merge)

    async def delete(self, col, id):
        await self._doc(col, id).delete()

    async def all(self, col):
        out = []
        async for snap in self.db.collection(col).stream():
            out.append((snap.id, snap.to_dict() or {}))
        return out

    async def incr(self, col, id, field, n=1):
        await self._doc(col, id).set({field: self.fs.Increment(n)}, merge=True)


def make_store():
    """FIREBASE_SERVICE_ACCOUNT (JSON matni) bo'lsa — Firestore, bo'lmasa — xotira."""
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        log.warning("FIREBASE_SERVICE_ACCOUNT kiritilmagan — ma’lumotlar faqat xotirada (qayta ishga tushsa o‘chadi)")
        return MemoryStore()
    try:
        info = json.loads(raw)
        st = FirestoreStore(info)
        log.info("Firestore ulandi: %s", st.project)
        return st
    except Exception as e:
        log.error("Firestore’ga ulanib bo‘lmadi (%s) — vaqtincha xotira ishlatiladi", e)
        return MemoryStore()
