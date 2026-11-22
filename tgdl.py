"""Katta fayllarni (20 MB dan katta, 2 GB gacha) Telegram'dan yuklab olish.
Oddiy Bot API faqat 20 MB gacha beradi, shuning uchun bot o'sha token bilan Telethon (MTProto) orqali ham ulanadi."""
import asyncio
import logging
import os

from telethon import TelegramClient
from telethon.sessions import StringSession

log = logging.getLogger("tgdl")

REQ = 512 * 1024          # bitta so'rov hajmi
PART = 32 * 1024 * 1024   # parallel bo'lak hajmi (REQ ga karrali)


class BigFiles:
    def __init__(self, token, api_id, api_hash, workers=6):
        self.token = token
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.workers = max(1, int(workers))
        self._client = None
        self._lock = asyncio.Lock()

    async def client(self):
        async with self._lock:
            if self._client is None or not self._client.is_connected():
                c = TelegramClient(StringSession(), self.api_id, self.api_hash,
                                   receive_updates=False, connection_retries=5, request_retries=5)
                await c.start(bot_token=self.token)
                self._client = c
                log.info("Telethon ulandi")
            return self._client

    async def close(self):
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass

    async def download(self, message_id, path, progress=None):
        """Bot chatidagi xabardan faylni bir nechta parallel oqimda yuklab oladi."""
        c = await self.client()
        msg = await c.get_messages(None, ids=message_id)
        if not msg or not msg.media or not msg.document:
            raise RuntimeError("Faylli xabar topilmadi")
        doc = msg.document
        size = int(doc.size)
        with open(path, "wb") as f:
            f.truncate(size)
        fd = os.open(path, os.O_WRONLY)
        done = 0
        sem = asyncio.Semaphore(self.workers)

        async def get_part(off, length):
            nonlocal done
            async with sem:
                pos = off
                end = off + length
                count = (length + REQ - 1) // REQ
                async for chunk in c.iter_download(doc, offset=off, request_size=REQ, limit=count):
                    if pos >= end:
                        break
                    chunk = bytes(chunk[: end - pos])
                    os.pwrite(fd, chunk, pos)
                    pos += len(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, size)
                if pos < end:
                    raise RuntimeError(f"Fayl to‘liq kelmadi ({pos - off}/{length})")

        tasks = [asyncio.create_task(get_part(o, min(PART, size - o))) for o in range(0, size, PART)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            os.close(fd)
        return size
