"""translator.py sinovi: soxta HTTP javoblar bilan (internet va kalit kerak emas)."""
import asyncio, json, sys, types, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "aiohttp" not in sys.modules:  # sinov muhitida aiohttp bo'lmasa — soxtasi
    m = types.ModuleType("aiohttp")
    class ClientError(Exception): pass
    m.ClientError = ClientError
    m.ClientTimeout = lambda **k: None
    m.ClientSession = object
    sys.modules["aiohttp"] = m
import translator as T

class Resp:
    def __init__(self, status, body): self.status, self.body = status, body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def text(self): return self.body if isinstance(self.body, str) else json.dumps(self.body)

class Session:
    """Har bir model uchun navbatdagi javoblar ro'yxati."""
    def __init__(self, plan): self.plan, self.calls = plan, []
    def post(self, url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        self.calls.append((model, "thinkingConfig" in json["generationConfig"]))
        q = self.plan.get(model) or [(503, {"error": {"message": "overloaded"}})]
        return Resp(*(q.pop(0) if len(q) > 1 else q[0]))
    def get(self, url, headers=None, timeout=None):
        return Resp(200, {"models": [{"name": "models/a"}, {"name": "models/c"}]})

def ok(blocks, fence=False):
    t = json.dumps({"source_language": "yaponcha", "blocks": blocks})
    if fence: t = "```json\n" + t + "\n```"
    return (200, {"candidates": [{"content": {"parts": [{"text": "o'ylayapman", "thought": True}, {"text": t}]},
                                  "finishReason": "STOP"}]})

B = [{"box_2d": [10, 20, 100, 200], "kind": "speech", "original": "何？", "translation": "Nima?"}]
DAY = (429, {"error": {"message": "Quota exceeded", "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
MIN = (429, {"error": {"message": "Quota exceeded", "details": [{"retryDelay": "3s"}, {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}})

async def fast_sleep(_): pass
T.asyncio.sleep = fast_sleep

async def main():
    # 1) 404 → kunlik limit → ishlaydi
    tr = T.Translator("k", "a,b,c")
    s = Session({"a": [(404, {"error": {"message": "not found"}})], "b": [DAY], "c": [ok(B, fence=True)]})
    r = await tr.read_page(s, b"jpg")
    assert r["model"] == "c" and r["blocks"][0]["translation"] == "Nima?" and r["source_language"] == "yaponcha", r
    assert tr.dead["a"] > tr.dead["b"] > 0 and tr.last_good == "c"
    r = await tr.read_page(s, b"jpg")   # o'lik modellar o'tkazib yuboriladi
    assert [c[0] for c in s.calls[-1:]] == ["c"], s.calls
    print("1 ok: model almashinuvi, o'lik modellar")
    # 2) 400 (thinking) → oddiy so'rov
    tr = T.Translator("k", "a")
    s = Session({"a": [(400, {"error": {"message": "Unknown name \"thinkingConfig\""}}), ok(B)]})
    r = await tr.read_page(s, b"jpg")
    assert s.calls == [("a", True), ("a", False)] and "a" in tr.plain, s.calls
    print("2 ok: qo'shimcha sozlamalarsiz qayta urinish")
    # 3) hamma band → kutib, keyin xato
    tr = T.Translator("k", "a,b")
    notes = []
    async def note(t): notes.append(t)
    s = Session({"a": [MIN], "b": [(503, {"error": {"message": "overloaded"}})]})
    try:
        await tr.read_page(s, b"jpg", notify=note, max_wait=60); assert False
    except T.GeminiError as e:
        assert e.kind == "busy" and notes, (e.kind, notes)
    print("3 ok: band — kutish va xabar:", notes[0])
    # 3b) band, keyin bo'shaydi
    tr = T.Translator("k", "a")
    s = Session({"a": [MIN, ok(B)]})
    r = await tr.read_page(s, b"jpg")
    assert r["blocks"], r
    print("3b ok: bo'shagach ishlaydi")
    # 4) kalit xato
    tr = T.Translator("k", "a,b")
    s = Session({"a": [(403, {"error": {"message": "API key not valid"}})]})
    try:
        await tr.read_page(s, b"jpg"); assert False
    except T.GeminiError as e:
        assert e.kind == "key" and len(s.calls) == 1
    print("4 ok: kalit xatosi darhol")
    # 5) xavfsizlik bloki
    tr = T.Translator("k", "a")
    s = Session({"a": [(200, {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]})]})
    try:
        await tr.read_page(s, b"jpg"); assert False
    except T.GeminiError as e:
        assert e.kind == "blocked"
    print("5 ok: xavfsizlik bloki")
    # 6) ro'yxat ko'rinishidagi javob
    tr = T.Translator("k", "a")
    s = Session({"a": [(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(B)}]}}]})]})
    r = await tr.read_page(s, b"jpg")
    assert r["blocks"] == B
    print("6 ok: ro'yxat javobi")
    # 7) hammasi kunlik limitda
    tr = T.Translator("k", "a,b")
    s = Session({"a": [DAY], "b": [DAY]})
    try:
        await tr.read_page(s, b"jpg"); assert False
    except T.GeminiError as e:
        assert e.kind == "quota"
    try:
        await tr.read_page(s, b"jpg"); assert False
    except T.GeminiError as e:
        assert e.kind == "quota" and len(s.calls) == 2   # ikkinchi marta so'rov ham yuborilmaydi
    print("7 ok: kunlik limit")
    # 8) check() mavjud modellarni saralaydi
    tr = T.Translator("k", "a,b,c")
    st = await tr.check(Session({}))
    assert tr.models == ["a", "c"], tr.models
    print("8 ok: check →", st)
    # 9) prompt: kontekst va webtoon bo'lagi
    p = T.build_prompt(context=[("何？", "Nima?")], part=(1, 3))
    assert "segment 2 of 3" in p and "何？ → Nima?" in p
    print("9 ok: prompt")

asyncio.run(main())
print("HAMMASI O'TDI")
