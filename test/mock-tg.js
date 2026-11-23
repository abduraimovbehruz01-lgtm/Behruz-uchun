'use strict';
// Sinov uchun soxta Telegram Bot API serveri (va soxta Gemini).
const http = require('http');

const ok = (result) => ({ ok: true, result });
const err = (code, description) => ({ ok: false, error_code: code, description });

class MockTG {
  constructor(clock) {
    this.clock = clock;
    this.calls = [];
    this.chats = new Map();
    this.pins = new Map();
    this.members = new Map();
    this.files = new Map();
    this.fileSeq = 0;
    this.failNext = {};
    this.bot = { id: 999, is_bot: true, first_name: 'Ish bot', username: 'ish_test_bot' };
    const kanal = { id: -1001111, type: 'channel', title: 'Ish kanal', username: 'ish_kanal' };
    const baza = { id: -100500, type: 'channel', title: 'Ish bot baza' };
    this.channels = { '@ish_kanal': kanal, '-1001111': kanal, '-100500': baza };
    this.gemini = { calls: 0, text: '• 1 yil tajriba\n• Rus tilini bilish\n• Mijozlar bilan muloyim muomala' };
  }

  resolveChat(chatId) {
    const k = String(chatId);
    if (this.channels[k]) return this.channels[k];
    return { id: Number(chatId), type: 'private', first_name: `U${chatId}` };
  }

  store(chatId) {
    const k = String(chatId);
    let c = this.chats.get(k);
    if (!c) { c = { seq: 0, msgs: new Map() }; this.chats.set(k, c); }
    return c;
  }

  addMsg(chat, fields) {
    const c = this.store(chat.id);
    c.seq += 1;
    const msg = { message_id: c.seq, chat, date: Math.floor(this.clock.now() / 1000), ...fields };
    c.msgs.set(msg.message_id, msg);
    return msg;
  }

  getMsg(chatId, id) {
    const chat = this.resolveChat(chatId);
    return this.store(chat.id).msgs.get(Number(id)) || null;
  }

  storeFile(f) {
    this.fileSeq += 1;
    const id = `F${this.fileSeq}`;
    this.files.set(id, f.data);
    return id;
  }

  // ---- sinov yordamchilari
  messages(chatId, { all = false } = {}) {
    const chat = this.resolveChat(chatId);
    return [...this.store(chat.id).msgs.values()].filter((m) => all || !m.deleted);
  }
  botMessages(chatId) { return this.messages(chatId).filter((m) => m.from && m.from.id === this.bot.id); }
  lastBot(chatId) { const a = this.botMessages(chatId); return a[a.length - 1] || null; }
  lastText(chatId) { const m = this.lastBot(chatId); return m ? (m.text || m.caption || '') : ''; }
  allText(chatId) { return this.botMessages(chatId).map((m) => m.text || m.caption || '').join('\n-----\n'); }
  findButtonMsg(chatId, data) {
    const list = this.messages(chatId);
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const kb = list[i].reply_markup && list[i].reply_markup.inline_keyboard;
      if (kb && kb.some((r) => r.some((b) => b.callback_data === data))) return list[i];
    }
    return null;
  }
  callsOf(method) { return this.calls.filter((c) => c.method === method); }
  setMember(chatId, userId, status) {
    const chat = this.resolveChat(chatId);
    const k = String(chat.id);
    if (!this.members.has(k)) this.members.set(k, new Map());
    this.members.get(k).set(Number(userId), status);
  }

  async handle(method, p) {
    this.calls.push({ method, p, t: this.clock.now() });
    if (this.failNext[method]) { const f = this.failNext[method]; delete this.failNext[method]; return err(f.code, f.desc); }
    const files = p._files || {};
    switch (method) {
      case 'getMe': return ok(this.bot);
      case 'setMyCommands':
      case 'setWebhook':
      case 'deleteWebhook':
      case 'answerCallbackQuery':
      case 'answerPreCheckoutQuery':
      case 'refundStarPayment':
        return ok(true);
      case 'sendMessage': {
        if (!p.text) return err(400, 'Bad Request: message text is empty');
        if (String(p.text).length > 4096) return err(400, 'Bad Request: message is too long');
        const chat = this.resolveChat(p.chat_id);
        return ok(this.addMsg(chat, { from: this.bot, text: p.text, reply_markup: p.reply_markup }));
      }
      case 'sendPhoto': {
        const chat = this.resolveChat(p.chat_id);
        const fid = typeof p.photo === 'string' ? p.photo : this.storeFile(files.photo);
        return ok(this.addMsg(chat, { from: this.bot, photo: [{ file_id: fid }], caption: p.caption }));
      }
      case 'sendDocument': {
        const chat = this.resolveChat(p.chat_id);
        let fid = p.document;
        let name = 'file';
        if (typeof fid !== 'string') { fid = this.storeFile(files.document); name = files.document.name; }
        return ok(this.addMsg(chat, { from: this.bot, document: { file_id: fid, file_name: name }, caption: p.caption }));
      }
      case 'sendInvoice': {
        const chat = this.resolveChat(p.chat_id);
        return ok(this.addMsg(chat, { from: this.bot, invoice: { title: p.title, total_amount: p.prices[0].amount, currency: p.currency }, payload: p.payload, reply_markup: p.reply_markup }));
      }
      case 'copyMessage': {
        const src = this.getMsg(p.from_chat_id, p.message_id);
        if (!src || src.deleted) return err(400, 'Bad Request: message to copy not found');
        const chat = this.resolveChat(p.chat_id);
        const m = this.addMsg(chat, { from: this.bot, text: src.text, copied: true });
        return ok({ message_id: m.message_id });
      }
      case 'editMessageText': {
        const m = this.getMsg(p.chat_id, p.message_id);
        if (!m || m.deleted) return err(400, 'Bad Request: message to edit not found');
        if (m.text === p.text && JSON.stringify(m.reply_markup || { inline_keyboard: [] }) === JSON.stringify(p.reply_markup || { inline_keyboard: [] })) {
          return err(400, 'Bad Request: message is not modified');
        }
        m.text = p.text;
        m.reply_markup = p.reply_markup;
        m.edited = true;
        return ok(m);
      }
      case 'editMessageReplyMarkup': {
        const m = this.getMsg(p.chat_id, p.message_id);
        if (!m || m.deleted) return err(400, 'Bad Request: message to edit not found');
        m.reply_markup = p.reply_markup;
        return ok(m);
      }
      case 'editMessageMedia': {
        const m = this.getMsg(p.chat_id, p.message_id);
        if (!m || m.deleted) return err(400, 'Bad Request: message to edit not found');
        const media = typeof p.media === 'string' ? JSON.parse(p.media) : p.media;
        const key = String(media.media).replace('attach://', '');
        const f = files[key];
        if (!f) return err(400, 'Bad Request: file not found');
        m.document = { file_id: this.storeFile(f), file_name: f.name };
        m.caption = media.caption;
        return ok(m);
      }
      case 'deleteMessage': {
        const m = this.getMsg(p.chat_id, p.message_id);
        if (!m || m.deleted) return err(400, 'Bad Request: message to delete not found');
        if (this.clock.now() / 1000 - m.date > 48 * 3600) return err(400, "Bad Request: message can't be deleted");
        m.deleted = true;
        return ok(true);
      }
      case 'pinChatMessage': {
        const chat = this.resolveChat(p.chat_id);
        const m = this.getMsg(chat.id, p.message_id);
        if (!m) return err(400, 'Bad Request: message to pin not found');
        const arr = this.pins.get(String(chat.id)) || [];
        arr.push(m.message_id);
        this.pins.set(String(chat.id), arr);
        return ok(true);
      }
      case 'unpinChatMessage': {
        const chat = this.resolveChat(p.chat_id);
        const arr = (this.pins.get(String(chat.id)) || []).filter((x) => x !== Number(p.message_id));
        this.pins.set(String(chat.id), arr);
        return ok(true);
      }
      case 'getChat': {
        const chat = this.resolveChat(p.chat_id);
        const arr = this.pins.get(String(chat.id)) || [];
        let pm = null;
        for (let i = arr.length - 1; i >= 0; i -= 1) { const m = this.getMsg(chat.id, arr[i]); if (m && !m.deleted) { pm = m; break; } }
        return ok({ ...chat, pinned_message: pm || undefined });
      }
      case 'getChatMember': {
        if (Number(p.user_id) === this.bot.id) {
          return ok({ status: 'administrator', user: this.bot, can_post_messages: true, can_edit_messages: true, can_delete_messages: true });
        }
        const chat = this.resolveChat(p.chat_id);
        const m = this.members.get(String(chat.id));
        return ok({ status: (m && m.get(Number(p.user_id))) || 'left', user: { id: Number(p.user_id) } });
      }
      case 'getFile': return ok({ file_id: p.file_id, file_path: `files/${p.file_id}` });
      case 'exportChatInviteLink': return ok('https://t.me/+taklif');
      default: return err(404, `Not Found: ${method}`);
    }
  }

  start() {
    return new Promise((resolve) => {
      this.server = http.createServer(async (req, res) => {
        const chunks = [];
        for await (const c of req) chunks.push(c);
        const buf = Buffer.concat(chunks);
        const url = req.url;
        const json = (code, o) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(o)); };
        if (url.startsWith('/gemini/')) {
          this.gemini.calls += 1;
          if (req.method === 'GET') return json(200, { models: [{ name: 'models/gemini-flash-latest' }] });
          return json(200, { candidates: [{ content: { parts: [{ text: this.gemini.text }] } }] });
        }
        let m = url.match(/^\/file\/bot[^/]+\/files\/(.+)$/);
        if (m) {
          const data = this.files.get(decodeURIComponent(m[1]));
          if (!data) { res.writeHead(404); res.end(); return null; }
          res.writeHead(200);
          res.end(data);
          return null;
        }
        m = url.match(/^\/bot[^/]+\/(\w+)$/);
        if (!m) { res.writeHead(404); res.end(); return null; }
        const ct = req.headers['content-type'] || '';
        let p = {};
        if (ct.includes('multipart/form-data')) {
          const fd = await new Response(buf, { headers: { 'content-type': ct } }).formData();
          p._files = {};
          for (const [k, v] of fd.entries()) {
            if (typeof v === 'string') { try { p[k] = JSON.parse(v); } catch (e) { p[k] = v; } } else {
              p._files[k] = { name: v.name, data: Buffer.from(await v.arrayBuffer()) };
            }
          }
        } else if (buf.length) {
          p = JSON.parse(buf.toString());
        }
        return json(200, await this.handle(m[1], p));
      });
      this.server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${this.server.address().port}`));
    });
  }
}

module.exports = MockTG;
