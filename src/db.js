'use strict';
// Ma’lumotlar xotirada saqlanadi va yopiq Telegram kanaliga (BAZA_KANAL)
// siqilgan fayl ko‘rinishida zaxira qilinadi. Server qayta yonganda
// qadab qo‘yilgan fayldan tiklanadi.
const zlib = require('zlib');
const cfg = require('./config');
const tg = require('./tg');
const clock = require('./clock');
const defaults = require('./defaults');
const { sleep } = require('./util');

let S = null;
let ready = false;
const waiters = [];
let pinnedId = null;
let dirty = false;
let timer = null;
let saving = null;
let lastSaveReal = 0;
const status = { mode: cfg.BAZA_KANAL ? 'kanal' : 'xotira', lastOk: 0, error: '', size: 0 };

function fresh() {
  return {
    v: 1,
    seq: { vac: 0, res: 0, app: 0, pay: 0 },
    users: {},
    vacancies: {},
    resumes: {},
    apps: {},
    payments: {},
    sessions: {},
    admins: {},
    settings: defaults(),
    stats: { uzs: 0, xtr: 0 },
    meta: { createdAt: clock.now() },
  };
}

function deepMerge(base, over) {
  for (const [k, v] of Object.entries(over || {})) {
    if (v && typeof v === 'object' && !Array.isArray(v) && base[k] && typeof base[k] === 'object' && !Array.isArray(base[k])) {
      base[k] = deepMerge(base[k], v);
    } else {
      base[k] = v;
    }
  }
  return base;
}

function upgrade(o) {
  const f = fresh();
  if (!o || typeof o !== 'object') return f;
  for (const k of ['users', 'vacancies', 'resumes', 'apps', 'payments', 'sessions', 'admins']) f[k] = o[k] || {};
  for (const k of ['seq', 'stats', 'meta']) f[k] = Object.assign(f[k], o[k] || {});
  f.settings = deepMerge(defaults(), o.settings || {});
  return f;
}

function normChannel(c) {
  c = String(c || '').trim();
  if (!c) return '';
  if (/^-?\d+$/.test(c)) return c;
  c = c.replace(/^https?:\/\//, '').replace(/^(t\.me|telegram\.me)\//, '').replace(/^@/, '').split(/[/?]/)[0];
  return c ? `@${c}` : '';
}

function afterLoad() {
  if (!S.settings.channel && cfg.KANAL) S.settings.channel = normChannel(cfg.KANAL);
}

async function restore() {
  if (!cfg.BAZA_KANAL) { S = fresh(); return 'xotira'; }
  const chat = await tg.call('getChat', { chat_id: cfg.BAZA_KANAL });
  const pm = chat.pinned_message;
  if (!pm) { S = fresh(); return 'yangi'; }
  const doc = pm.document;
  if (!doc || !/^baza/i.test(doc.file_name || '')) {
    const e = new Error('Qadalgan xabar baza fayli emas');
    e.fatal = true;
    throw e;
  }
  pinnedId = pm.message_id;
  const buf = await tg.download(doc.file_id);
  const json = buf[0] === 0x1f && buf[1] === 0x8b ? zlib.gunzipSync(buf).toString('utf8') : buf.toString('utf8');
  S = upgrade(JSON.parse(json));
  status.size = buf.length;
  return 'tiklandi';
}

async function init() {
  let delay = 2000;
  let warned = false;
  for (;;) {
    try {
      const how = await restore();
      afterLoad();
      ready = true;
      waiters.splice(0).forEach((w) => w());
      console.log(`[baza] tayyor: ${how}; foydalanuvchilar: ${Object.keys(S.users).length}, vakansiyalar: ${Object.keys(S.vacancies).length}`);
      return how;
    } catch (e) {
      status.error = e.message;
      console.error(`[baza] tiklashda xato: ${e.message}`);
      if (e.fatal && !warned) {
        warned = true;
        await tg.quiet('sendMessage', {
          chat_id: cfg.BAZA_KANAL,
          text: '⚠️ Bot bazani tiklay olmadi: qadab qo‘yilgan xabar baza fayli emas.\nIltimos, eng oxirgi «baza_...json.gz» faylini qadab qo‘ying (Pin).',
        });
      }
      await sleep(delay);
      delay = Math.min(delay * 2, 60000);
    }
  }
}

function whenReady(timeoutMs = 25000) {
  if (ready) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('baza hali tayyor emas')), timeoutMs);
    waiters.push(() => { clearTimeout(t); resolve(); });
  });
}

function markDirty() {
  dirty = true;
  if (!cfg.BAZA_KANAL || !ready || timer) return;
  const wait = Math.max(4000, 15000 - (Date.now() - lastSaveReal));
  timer = setTimeout(() => { timer = null; save().catch(() => {}); }, wait);
}

async function persist(gz) {
  const name = `baza_${clock.dayKey()}.json.gz`;
  const kb = Math.max(1, Math.round(gz.length / 1024));
  const caption = `💾 Baza zaxirasi · ${clock.fmtDT()} · ${kb} KB\n⚠️ Bu xabarni o‘chirmang va qadashdan olmang.`;
  const file = { baza: { data: gz, name, type: 'application/gzip' } };
  if (pinnedId) {
    try {
      await tg.call('editMessageMedia', {
        chat_id: cfg.BAZA_KANAL,
        message_id: pinnedId,
        media: { type: 'document', media: 'attach://baza', caption },
      }, file);
      return;
    } catch (e) {
      if (/not modified/i.test(e.message)) return;
      console.warn(`[baza] eski zaxira xabarini yangilab bo‘lmadi: ${e.message}`);
    }
  }
  const m = await tg.call('sendDocument', { chat_id: cfg.BAZA_KANAL, caption, disable_notification: true }, { document: file.baza });
  try {
    await tg.call('pinChatMessage', { chat_id: cfg.BAZA_KANAL, message_id: m.message_id, disable_notification: true });
  } catch (e) {
    await tg.quiet('deleteMessage', { chat_id: cfg.BAZA_KANAL, message_id: m.message_id });
    throw new Error(`Baza faylini qadab bo‘lmadi — botga baza kanalida «Tahrirlash» ruxsatini bering (${e.message})`);
  }
  pinnedId = m.message_id;
}

async function save() {
  if (!ready || !cfg.BAZA_KANAL) return;
  while (saving) await saving;
  if (!dirty) return;
  saving = (async () => {
    dirty = false;
    const gz = zlib.gzipSync(Buffer.from(JSON.stringify(S)));
    try {
      await persist(gz);
      lastSaveReal = Date.now();
      status.lastOk = clock.now();
      status.error = '';
      status.size = gz.length;
    } catch (e) {
      dirty = true;
      status.error = e.message;
      console.error(`[baza] saqlashda xato: ${e.message}`);
      if (!timer) timer = setTimeout(() => { timer = null; save().catch(() => {}); }, 30000);
    }
  })();
  try { await saving; } finally { saving = null; }
}

async function flush() {
  if (timer) { clearTimeout(timer); timer = null; }
  if (saving) await saving;
  if (dirty) await save();
}

// Kunlik alohida nusxa (tarix uchun)
async function snapshot() {
  if (!ready || !cfg.BAZA_KANAL) return;
  const day = clock.dayKey();
  if (S.meta.snapDay === day) return;
  const gz = zlib.gzipSync(Buffer.from(JSON.stringify(S)));
  await tg.call('sendDocument', {
    chat_id: cfg.BAZA_KANAL,
    caption: `🗓 Kunlik nusxa · ${clock.fmtDT()}`,
    disable_notification: true,
  }, { document: { data: gz, name: `nusxa_${day}.json.gz`, type: 'application/gzip' } });
  S.meta.snapDay = day;
  markDirty();
}

function exportGz() {
  return zlib.gzipSync(Buffer.from(JSON.stringify(S)));
}

function importData(buf) {
  const json = buf[0] === 0x1f && buf[1] === 0x8b ? zlib.gunzipSync(buf).toString('utf8') : buf.toString('utf8');
  const loaded = upgrade(JSON.parse(json));
  loaded.admins = S.admins; // hozirgi adminlar chiqib ketmasin
  loaded.sessions = {};
  S = loaded;
  afterLoad();
  markDirty();
  return S;
}

// ---- yordamchilar
const state = () => S;
const settings = () => S.settings;
const isReady = () => ready;

function nextId(kind) {
  S.seq[kind] = (S.seq[kind] || 0) + 1;
  return S.seq[kind];
}

function touchUser(from) {
  const id = from.id;
  let u = S.users[id];
  const name = [from.first_name, from.last_name].filter(Boolean).join(' ').slice(0, 64) || 'Foydalanuvchi';
  const username = from.username || '';
  if (!u) {
    u = S.users[id] = { id, name, username, joinedAt: clock.now(), lastSeen: clock.now(), credits: 0, subs: { on: false, cats: [], regions: [] } };
    markDirty();
    return u;
  }
  if (u.name !== name || u.username !== username || u.inactive || clock.now() - (u.lastSeen || 0) > 3600e3) {
    u.name = name;
    u.username = username;
    u.inactive = false;
    u.lastSeen = clock.now();
    markDirty();
  }
  return u;
}

function sess(id) {
  let s = S.sessions[id];
  if (!s) { s = S.sessions[id] = { prompts: [] }; }
  if (!s.prompts) s.prompts = [];
  s.at = clock.now();
  return s;
}

function resetSess(id) {
  const old = S.sessions[id];
  S.sessions[id] = { prompts: old && old.prompts ? old.prompts : [], at: clock.now() };
  markDirty();
  return S.sessions[id];
}

function isAdmin(id) {
  const a = S.admins[id];
  return !!(a && a.until > clock.now());
}

function adminIds() {
  return Object.keys(S.admins).map(Number).filter(isAdmin);
}

function findUser(ref) {
  ref = String(ref || '').trim();
  if (/^\d+$/.test(ref)) return S.users[ref] || null;
  const un = ref.replace(/^@/, '').toLowerCase();
  return Object.values(S.users).find((u) => (u.username || '').toLowerCase() === un) || null;
}

module.exports = {
  init, whenReady, isReady, markDirty, save, flush, snapshot, exportGz, importData,
  state, settings, nextId, touchUser, sess, resetSess, isAdmin, adminIds, findUser, normChannel, status,
  _setState: (s) => { S = s; ready = true; }, // sinov uchun
  _fresh: fresh,
};
