"""Gemini: kinoning ovozini «eshitib» muhim lahzalarni topish va o'zbekcha hikoya matnini yozish."""
import asyncio
import json
import logging
import os
import re
import time

import aiohttp

log = logging.getLogger("gemini")

BASE = os.environ.get("GEMINI_BASE", "https://generativelanguage.googleapis.com").rstrip("/")
DEFAULT_MODELS = "gemini-3.8-flash,gemini-flash-latest,gemini-3.5-flash,gemini-4-hydrogen,gemini-3.5-flash-lite,gemini-flash-lite-latest"
SAFETY = [{"category": c, "threshold": "BLOCK_NONE"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


class GeminiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def fmt_ts(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def parse_ts(v, default=None):
    """'1:02:03', '62:03', '00:05:10.5', 75 → soniya."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    nums = re.findall(r"\d+(?:[.,]\d+)?", str(v))
    if not nums:
        return default
    p = [float(x.replace(",", ".")) for x in nums[:3]]
    if len(p) == 3:
        return p[0] * 3600 + p[1] * 60 + p[2]
    if len(p) == 2:
        return p[0] * 60 + p[1]
    return p[0]


def _parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


SCENE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "qisqacha": {"type": "STRING"},
        "qahramonlar": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"ism": {"type": "STRING"}, "kim": {"type": "STRING"}},
                "required": ["ism", "kim"],
            },
        },
        "lahzalar": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "vaqt": {"type": "STRING"},
                    "voqea": {"type": "STRING"},
                    "kuchli": {"type": "BOOLEAN"},
                },
                "required": ["vaqt", "voqea"],
            },
        }
    },
    "required": ["qisqacha", "qahramonlar", "lahzalar"],
}

SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "nomi": {"type": "STRING"},
        "jumlalar": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "matn": {"type": "STRING"},
                    "boshlanish": {"type": "STRING"},
                    "tugash": {"type": "STRING"},
                },
                "required": ["matn", "boshlanish", "tugash"],
            },
        },
        "izoh": {"type": "STRING"},
        "heshteglar": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["nomi", "jumlalar", "izoh", "heshteglar"],
}


def scene_prompt(name, k, n, start, length):
    part = f"part {k + 1} of {n} of the" if n > 1 else "the full"
    return (
        f"This audio is {part} soundtrack of the movie «{name}». "
        f"It covers movie time {fmt_ts(start)} – {fmt_ts(start + length)}.\n"
        "Listen carefully to the dialogue, sound effects and music. Answer in Uzbek (Latin script):\n"
        "- \"qisqacha\": 3–5 sentences: what happens in this part — where, who, what the conflict is and how it develops;\n"
        "- \"qahramonlar\": the main characters who appear: \"ism\" (name as said in the movie) and \"kim\" "
        "(who they are, 3–8 words: role, job, relation to others, e.g. «bosh qahramon, yosh qiz», «Rubining asrab olgan onasi»);\n"
        "- \"lahzalar\": the key plot moments in chronological order, roughly one every 1–2 minutes (more where the "
        "action is dense). For each: \"vaqt\" — timestamp FROM THE START OF THIS AUDIO FILE (H:MM:SS); \"voqea\" — "
        "1–2 clear sentences: who does what, why, and what changes; \"kuchli\" — true if especially dramatic, scary, "
        "emotional or action-packed.\n"
        "Skip the opening logos and the end credits. Do not invent anything that is not in the audio; if speech is "
        "unclear, describe by sounds and tone."
    )


def script_prompt(name, duration, moments, variant=0, target_sec=120, summary="", characters=None, too_short=0):
    lines = "\n".join(
        f"{fmt_ts(m['t'])} — {m['voqea']}" + (" [KUCHLI]" if m.get("kuchli") else "") for m in moments
    )
    chars = "\n".join(f"- {c['ism']}: {c['kim']}" for c in (characters or [])[:25])
    words = int(round(target_sec * 2.0))
    w_lo, w_hi = int(words * 0.9), int(words * 1.1)
    s_lo, s_hi = max(6, round(words / 14)), max(9, round(words / 10))
    extra = "\nWrite a NEW version that differs from earlier ones (another hook and wording)." if variant else ""
    if too_short:
        extra += (f"\nIMPORTANT: your previous attempt had only {too_short} words — far too short. This time write "
                  f"AT LEAST {w_lo} words in {s_lo}–{s_hi} sentences: tell more of the story, more details and "
                  "characters' motives.")
    return (
        "You write viral Uzbek Instagram Reels movie recaps («kino qisqacha mazmuni»).\n"
        f"Movie: «{name}», length {fmt_ts(duration)}.\n"
        + (f"Summary of the movie:\n{summary}\n" if summary else "")
        + (f"Main characters:\n{chars}\n" if chars else "")
        + "Key moments of the movie with timestamps (movie time):\n"
        f"{lines}\n\n"
        "Write a narration script in Uzbek (Latin script; lively spoken but correct literary Uzbek; write o‘ and g‘ "
        f"with the ‘ sign) that a narrator will read aloud in about {int(target_sec)} seconds ({w_lo}–{w_hi} words "
        "in total).\n"
        "Rules:\n"
        f"- {s_lo}–{s_hi} sentences, each 6–18 words, easy to read aloud.\n"
        "- Sentence 1 is a strong hook that makes the viewer keep watching (an intriguing question, a shocking fact, "
        "or the most gripping moment).\n"
        "- The viewer has NEVER seen this movie and must fully understand the story: right after the hook make clear "
        "where it happens and who the main hero is; introduce every character the first time with who they are "
        "(e.g. «Rubining asrab olgan onasi Karla», «maxfiy tashkilot UNIT rahbari Keyt»); explain the main conflict; "
        "connect events with cause and effect (chunki, shuning uchun, ammo, keyin); never leave a name, place, "
        "organization or term unexplained; one idea per sentence.\n"
        "- After the hook retell the story in chronological order, in the present tense, simply and vividly.\n"
        "- Do NOT reveal the ending: stop at the most intense moment and leave intrigue (cliffhanger).\n"
        "- NEVER say the movie's title (in any language) in the narration — viewers must not learn which movie it is; "
        "character names are fine.\n"
        "- No greetings, no call to action, no hashtags, no emojis — an ending phrase will be added automatically.\n"
        "- For every sentence give \"boshlanish\" and \"tugash\" (H:MM:SS, movie time): the part of the movie "
        "whose scenes best illustrate that sentence, 6–30 seconds long, taken from the moments above. "
        "Sentence 1 may use the most dramatic moment of the film.\n"
        "- \"nomi\": the movie title as Uzbek viewers know it (for internal use only).\n"
        "- \"izoh\": an intriguing Instagram caption in Uzbek, 2–3 short sentences, 1–2 fitting emojis allowed, that "
        "makes people want to watch. It must NOT contain the movie title, the year, actor names or anything that "
        "reveals which movie it is; no hashtags here.\n"
        "- \"heshteglar\": 10–14 popular hashtags for this video (genre and mood, in Uzbek and English, e.g. "
        "#qorqinchli #horror #triller #drama). No hashtags with the movie title, actors or year."
        f"{extra}"
    )


class Gemini:
    def __init__(self, key, models=None):
        self.key = key
        self.models = [m.strip() for m in (models or DEFAULT_MODELS).split(",") if m.strip()]
        self.last_good = None
        self.dead = {}

    def _headers(self):
        return {"x-goog-api-key": self.key}

    def _order(self):
        now = time.time()
        order = [m for m in self.models if self.dead.get(m, 0) <= now] or list(self.models)
        if self.last_good in order:
            order.remove(self.last_good)
            order.insert(0, self.last_good)
        return order

    async def upload(self, session, path, mime="audio/mpeg"):
        size = os.path.getsize(path)
        h = dict(self._headers())
        h.update({
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
        })
        async with session.post(f"{BASE}/upload/v1beta/files", headers=h,
                                json={"file": {"display_name": os.path.basename(path)}},
                                timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                raise GeminiError(r.status, "yuklash boshlanmadi: " + (await r.text())[:300])
            url = r.headers.get("X-Goog-Upload-URL")
        if not url:
            raise GeminiError(0, "yuklash manzili kelmadi")
        with open(path, "rb") as f:
            data = f.read()
        async with session.post(url, data=data, headers={
            "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
        }, timeout=aiohttp.ClientTimeout(total=900)) as r:
            if r.status != 200:
                raise GeminiError(r.status, "fayl yuklanmadi: " + (await r.text())[:300])
            info = (await r.json(content_type=None)).get("file") or {}
        name = info.get("name")
        for _ in range(150):
            st = info.get("state")
            if st == "ACTIVE" or (st is None and info.get("uri")):
                return info
            if st == "FAILED":
                raise GeminiError(0, "Gemini faylni o‘qiy olmadi")
            await asyncio.sleep(2)
            async with session.get(f"{BASE}/v1beta/{name}", headers=self._headers(),
                                   timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status == 200:
                    info = await r.json(content_type=None)
        raise GeminiError(0, "Gemini faylni tayyorlashga ulgurmadi")

    async def delete(self, session, name):
        try:
            async with session.delete(f"{BASE}/v1beta/{name}", headers=self._headers(),
                                      timeout=aiohttp.ClientTimeout(total=30)):
                pass
        except Exception:
            pass

    async def _call(self, session, model, parts, schema, temperature, timeout):
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
            "safetySettings": SAFETY,
        }
        if schema:
            body["generationConfig"]["responseSchema"] = schema
        async with session.post(f"{BASE}/v1beta/models/{model}:generateContent", headers=self._headers(),
                                json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            txt = await r.text()
            if r.status != 200:
                raise GeminiError(r.status, re.sub(r"\s+", " ", txt)[:300])
        j = json.loads(txt)
        cands = j.get("candidates") or []
        if not cands:
            fb = (j.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(0, f"javob yo‘q ({fb or 'noma’lum'})")
        cand = cands[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        if not text.strip():
            raise GeminiError(0, f"javob bo‘sh ({cand.get('finishReason')})")
        return text

    async def generate_json(self, session, parts, schema, temperature=0.7, timeout=480, prefer_strong=False,
                            budget=900):
        """Modellarni navbat bilan sinaydi. prefer_strong=True bo'lsa, kuchli modellar band (503/429) bo'lganda
        biroz kutib ularni yana bir bor sinaydi va faqat keyin yengil (lite) modelga o'tadi — sifat uchun."""
        errors = []
        order = self._order()
        if prefer_strong:
            strong = [m for m in order if "lite" not in m]
            lite = [m for m in order if "lite" in m]
            plan = strong + ["…"] + strong + lite
        else:
            plan = order
        t_start = time.time()
        tried_busy = set()
        for model in plan:
            if model == "…":
                if not tried_busy or time.time() - t_start > budget * 0.6:
                    continue
                await asyncio.sleep(20)
                continue
            if prefer_strong and model in tried_busy and time.time() - t_start > budget * 0.6:
                continue
            t0 = time.time()
            try:
                data = _parse_json(await self._call(session, model, parts, schema, temperature, timeout))
                self.last_good = model
                log.info("gemini ok: %s %.0fs", model, time.time() - t0)
                return data
            except GeminiError as e:
                log.warning("gemini %s xato: %s %s", model, e.status, str(e)[:200])
                errors.append(f"{model}: {e.status} {str(e)[:120]}")
                if e.status in (401, 403) and "PERMISSION" not in str(e).upper():
                    break  # kalit noto'g'ri — boshqa modelni sinash befoyda
                if e.status == 404:
                    self.dead[model] = time.time() + 6 * 3600
                if e.status in (429, 500, 502, 503, 504):
                    tried_busy.add(model)
            except (asyncio.TimeoutError, aiohttp.ClientError, ValueError, KeyError) as e:
                log.warning("gemini %s: %r", model, e)
                errors.append(f"{model}: {type(e).__name__}")
                tried_busy.add(model)
        raise GeminiError(0, "; ".join(errors)[-700:] or "modellar javob bermadi")

    async def scene_log(self, session, audio_path, mime, name, k, n, start, length):
        info = await self.upload(session, audio_path, mime)
        try:
            parts = [
                {"file_data": {"mime_type": info.get("mimeType") or mime, "file_uri": info["uri"]}},
                {"text": scene_prompt(name, k, n, start, length)},
            ]
            data = await self.generate_json(session, parts, SCENE_SCHEMA, temperature=0.3, prefer_strong=True)
        finally:
            await self.delete(session, info.get("name"))
        raw = []
        for m in data.get("lahzalar") or []:
            t = parse_ts(m.get("vaqt"))
            text = (m.get("voqea") or "").strip()
            if t is not None and text:
                raw.append((max(0.0, t), text, bool(m.get("kuchli"))))
        # Ba'zan model 2-bo'lakda ham kino boshidan hisoblangan vaqtni yozadi — buni aniqlab to'g'rilaymiz
        absolute = start > 0 and raw and sum(1 for t, _, _ in raw if t > length + 30) > len(raw) / 2
        res = []
        for t, text, strong in raw:
            mt = t if absolute else start + t
            if start - 30 <= mt <= start + length + 30:
                res.append({"t": mt, "voqea": text, "kuchli": strong})
        chars = [{"ism": str(c.get("ism", "")).strip(), "kim": str(c.get("kim", "")).strip()}
                 for c in (data.get("qahramonlar") or []) if str(c.get("ism", "")).strip()]
        return {"moments": res, "summary": str(data.get("qisqacha") or "").strip(), "characters": chars}

    async def write_script(self, session, name, duration, moments, variant=0, target_sec=120, summary="",
                           characters=None, too_short=0):
        parts = [{"text": script_prompt(name, duration, moments, variant, target_sec, summary, characters, too_short)}]
        return await self.generate_json(session, parts, SCRIPT_SCHEMA, temperature=0.9 if variant else 0.75,
                                        timeout=300, prefer_strong=True)


ALIGN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "boshlanishlar": {
            "type": "ARRAY",
            "items": {"type": "OBJECT", "properties": {"n": {"type": "INTEGER"}, "t": {"type": "NUMBER"}},
                      "required": ["n", "t"]},
        }
    },
    "required": ["boshlanishlar"],
}


async def align_voice(gem, session, audio_path, sentences):
    """Diktor yozuvida har bir gap necha soniyada boshlanishini Gemini'dan so'raydi. Qaytaradi: {raqam: soniya}."""
    info = await gem.upload(session, audio_path, "audio/mpeg")
    try:
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(sentences))
        prompt = ("This is a recording of a narrator reading the following numbered Uzbek sentences (the narrator may "
                  "change a few words):\n" + numbered + "\n\nFor every sentence give \"n\" (its number) and \"t\" — "
                  "the time in seconds from the start of the recording (0.1 s precision) when the narrator starts "
                  "saying it. Listen carefully; times must increase. Skip a sentence only if it was not read at all.")
        parts = [{"file_data": {"mime_type": info.get("mimeType") or "audio/mpeg", "file_uri": info["uri"]}},
                 {"text": prompt}]
        data = await gem.generate_json(session, parts, ALIGN_SCHEMA, temperature=0.0, timeout=120)
    finally:
        await gem.delete(session, info.get("name"))
    res = {}
    for it in data.get("boshlanishlar") or []:
        try:
            n, t = int(it.get("n")), float(it.get("t"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(sentences) and t >= 0:
            res[n - 1] = t
    return res
