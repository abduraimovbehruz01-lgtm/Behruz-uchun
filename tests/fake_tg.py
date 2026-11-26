"""Sinov uchun soxta aiogram/aiohttp (haqiqiy kutubxonalar o'rnatilmagan muhitda ham ishlaydi)."""
import io, itertools, sys, types

class Magic:
    def __getattr__(self, k): return self
    def __call__(self, *a, **k): return self
    def __eq__(self, o): return self
    def __or__(self, o): return self
    __ror__ = __and__ = __rand__ = __or__
    def __invert__(self): return self
    def __hash__(self): return 1

def mod(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m

class Rec:
    def __init__(self, *a, **k): self.a, self.k = a, k; self.__dict__.update(k)

class Observer:
    def __call__(self, *f, **k): return lambda fn: fn
    def filter(self, *f): pass
    def register(self, fn): pass

class Router:
    def __init__(self, name=None):
        self.message = Observer(); self.callback_query = Observer(); self.my_chat_member = Observer()
class Dispatcher(Router):
    def __init__(self): super().__init__(); self.startup = Observer(); self.shutdown = Observer()
    def include_router(self, r): pass

class State:
    def __set_name__(self, owner, name): self.state = f"{owner.__name__}:{name}"
class StatesGroup: pass

class FSM:
    def __init__(self): self.state, self.data = None, {}
    async def get_state(self): return self.state
    async def set_state(self, s): self.state = s.state if isinstance(s, State) else s
    async def update_data(self, **k): self.data.update(k)
    async def get_data(self): return dict(self.data)
    async def clear(self): self.state, self.data = None, {}

class TelegramBadRequest(Exception): pass
class TelegramRetryAfter(Exception): retry_after = 1
class TelegramForbiddenError(Exception): pass
class CallbackQuery:
    def __init__(self, data, user, message=None):
        self.data, self.from_user, self.message, self.answers = data, user, message, []
    async def answer(self, text=None, show_alert=False): self.answers.append(text)

LOG, FILES = [], {}
_ids = itertools.count(1)

class Sent:
    def __init__(self, chat, text="", photo=None):
        self.chat_id, self.text, self.message_id = chat, text, next(_ids)
        self.photo = photo
    async def edit_text(self, text, reply_markup=None): self.text = text; LOG.append(("edit", self.chat_id, text, reply_markup))
    async def edit_caption(self, caption=None, reply_markup=None): LOG.append(("edit", self.chat_id, caption, reply_markup))
    async def edit_reply_markup(self, reply_markup=None): LOG.append(("markup", self.chat_id, reply_markup))
    async def delete(self): LOG.append(("del", self.chat_id, self.text))

def _data(f):
    if isinstance(f, Rec) and f.a and isinstance(f.a[0], bytes): return f.a[0]
    if isinstance(f, Rec) and f.a: return open(f.a[0], "rb").read()
    return FILES.get(f, b"")

class FakeBot:
    async def download(self, file_id, timeout=30): return io.BytesIO(FILES[file_id])
    async def send_chat_action(self, **k): pass
    async def get_me(self): return Rec(username="ManhoraBot")
    async def get_chat_member(self, chat_id, user_id): return Rec(status="member")
    async def send_message(self, chat_id, text, reply_markup=None, **k):
        LOG.append(("msg", chat_id, text, reply_markup)); return Sent(chat_id, text)
    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **k):
        fid = f"P{next(_ids)}"; FILES[fid] = _data(photo) or FILES.get(photo, b"")
        LOG.append(("photo", chat_id, caption, reply_markup, photo)); return Sent(chat_id, caption or "", [Rec(file_id=fid)])
    async def send_media_group(self, chat_id, media, **k):
        out = []
        for m in media:
            fid = f"P{next(_ids)}"; FILES[fid] = _data(m.media) or FILES.get(m.media, b""); out.append(Sent(chat_id, "", [Rec(file_id=fid)]))
        LOG.append(("album", chat_id, len(media), k.get("protect_content"))); return out
    async def send_document(self, chat_id, doc, **k):
        fid = f"D{next(_ids)}"; LOG.append(("doc", chat_id, getattr(doc, "k", {}).get("filename") if isinstance(doc, Rec) else doc, k.get("protect_content")))
        s = Sent(chat_id); s.document = Rec(file_id=fid); return s

BOT = FakeBot()

def install():
    if "aiohttp" not in sys.modules:
        web = mod("aiohttp.web", Application=Rec, Response=Rec, json_response=Rec, run_app=lambda *a, **k: None,
                  AppRunner=Rec, TCPSite=Rec, Request=object)
        mod("aiohttp", ClientSession=Rec, ClientTimeout=Rec, ClientError=type("ClientError", (Exception,), {}), web=web)
    mod("aiogram", Bot=lambda *a, **k: BOT, Dispatcher=Dispatcher, F=Magic(), Router=Router)
    mod("aiogram.client"); mod("aiogram.client.default", DefaultBotProperties=Rec)
    mod("aiogram.enums", ChatAction=Magic(), ParseMode=Magic())
    mod("aiogram.exceptions", TelegramBadRequest=TelegramBadRequest, TelegramRetryAfter=TelegramRetryAfter,
        TelegramForbiddenError=TelegramForbiddenError)
    mod("aiogram.filters", Command=Rec, CommandStart=Rec, CommandObject=Rec, StateFilter=Rec)
    mod("aiogram.fsm"); mod("aiogram.fsm.context", FSMContext=FSM); mod("aiogram.fsm.state", State=State, StatesGroup=StatesGroup)
    mod("aiogram.types", BotCommand=Rec, BufferedInputFile=Rec, CallbackQuery=CallbackQuery, FSInputFile=Rec,
        InlineKeyboardButton=Rec, InlineKeyboardMarkup=Rec, Message=object, ReplyParameters=Rec, InputMediaPhoto=Rec,
        KeyboardButton=Rec, ReplyKeyboardMarkup=Rec, ChatMemberUpdated=object)
    mod("aiogram.webhook"); mod("aiogram.webhook.aiohttp_server", SimpleRequestHandler=Rec, setup_application=lambda *a, **k: None)

class User:
    def __init__(self, uid, name="Ali"): self.id, self.first_name, self.username = uid, name, name.lower()
class Chat:
    def __init__(self, cid, type="private", title=""): self.id, self.type, self.title = cid, type, title

class Msg:
    def __init__(self, uid, text=None, photo=None, document=None):
        self.message_id = next(_ids); self.from_user = User(uid); self.chat = Chat(uid)
        self.text, self.photo, self.document = text, photo, document
    async def answer(self, text, reply_markup=None, **k): LOG.append(("msg", self.chat.id, text, reply_markup)); return Sent(self.chat.id, text)
    async def answer_photo(self, f, caption=None, **k): LOG.append(("photo", self.chat.id, caption, k.get("reply_markup"), f)); return Sent(self.chat.id)
    async def answer_document(self, f, caption=None, **k): LOG.append(("doc", self.chat.id, f.k.get("filename") or (f.a[1] if len(f.a) > 1 else ""), caption)); return Sent(self.chat.id)

def buttons(markup):
    if markup is None: return []
    return [b.k.get("callback_data") or b.k.get("url") for row in markup.k["inline_keyboard"] for b in row]
