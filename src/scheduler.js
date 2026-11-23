'use strict';
// Jadval: navbat bilan kanalga chiqarish (faqat kunduzi), muddati tugaganlarni
// o‘chirish, uzaytirish eslatmasi, kechki ro‘yxat, tozalash va kunlik nusxa.
const db = require('./db');
const core = require('./core');
const clock = require('./clock');
const tg = require('./tg');
const T = require('./texts');
const { ikb, esc, som, sleep } = require('./util');

const { HOUR, DAY } = clock;
let running = false;
let again = false;
let timer = null;

async function tick() {
  if (!db.isReady()) return;
  if (running) { again = true; return; }
  running = true;
  try {
    await queueTick();
    await expiryTick();
    await reminderTick();
    await digestTick();
    await maintenance();
  } catch (e) {
    console.error('[jadval]', (e && e.stack) || e);
  } finally {
    running = false;
  }
  if (again) { again = false; setTimeout(() => tick(), 50); }
}

async function queueTick() {
  const S = db.settings();
  const q = S.queue;
  if (!S.channel) return;
  const h = clock.hourFloat();
  if (!(h >= q.from && h < q.to)) return;
  const meta = db.state().meta;
  for (let n = 0; n < 30; n += 1) {
    const items = core.queueItems();
    if (!items.length) return;
    if (q.interval > 0 && clock.now() - (meta.lastPostAt || 0) < q.interval * 60e3) return;
    const it = items[0];
    try {
      if (it.kind === 'vac') await core.publishVacancy(it.item);
      else await core.publishResume(it.item);
      meta.lastPostAt = clock.now();
      db.markDirty();
    } catch (e) {
      console.error(`[navbat] chiqarib bo‘lmadi: ${e.message}`);
      core.adminNotice('post', `⚠️ E’lonni kanalga chiqarib bo‘lmadi: ${esc(e.message)}\nBot kanalda admin ekanini va «Xabar joylash» ruxsatini tekshiring.`);
      return;
    }
    if (q.interval > 0) return;
    await sleep(1100);
  }
}

async function expiryTick() {
  const t = clock.now();
  for (const v of Object.values(db.state().vacancies)) {
    if ((v.status === 'published' || v.status === 'closed') && !v.endedAt && v.expiresAt && t >= v.expiresAt) await core.endPost(v, 'vac');
  }
  for (const r of Object.values(db.state().resumes)) {
    if (r.status === 'published' && !r.endedAt && r.expiresAt && t >= r.expiresAt) await core.endPost(r, 'res');
  }
}

async function reminderTick() {
  const S = db.settings();
  if (!(S.remindHours > 0)) return;
  const t = clock.now();
  for (const v of Object.values(db.state().vacancies)) {
    if (v.status !== 'published' || v.reminded || !v.expiresAt) continue;
    if (t < v.expiresAt - S.remindHours * HOUR || t >= v.expiresAt) continue;
    v.reminded = true;
    db.markDirty();
    const [uzs] = S.prices.ext;
    const free = !S.payments || !uzs;
    const left = Math.max(1, Math.round((v.expiresAt - t) / HOUR));
    await core.safeSend(v.ownerId,
      `⏳ «${esc(v.title)}» e’loningiz muddati ~${left} soatdan keyin tugaydi${S.autoDelete ? ' va kanaldan olib tashlanadi' : ''}.\nIshchi hali topilmagan bo‘lsa, muddatni uzaytiring (+${T.dur(S.hours.ext)}).`,
      ikb([
        [[free ? '⏳ Bepul uzaytirish' : `⏳ Uzaytirish — ${som(uzs)}`, `ext:${v.id}`]],
        [['✅ Ishchi topildi — yopish', `my:c:${v.id}`]],
        [['Kerak emas', `extno:${v.id}`]],
      ]));
  }
}

async function digestTick() {
  const S = db.settings();
  const d = S.digest;
  if (!d.on || !S.channel) return;
  const t = clock.now();
  const meta = db.state().meta;
  const today = clock.dayKey(t);
  if (meta.digestDay === today) return;
  const h = clock.hourFloat(t);
  if (h < d.hour || h >= d.hour + 3) return;
  meta.digestDay = today;
  db.markDirty();
  const start = clock.startOfDay(t);
  const list = Object.values(db.state().vacancies)
    .filter((v) => v.status === 'published' && v.publishedAt >= start)
    .sort((a, b) => a.publishedAt - b.publishedAt);
  if (!list.length) return;
  if (meta.digestMsg && t - meta.digestMsg.at < 47.9 * HOUR) {
    await tg.quiet('deleteMessage', { chat_id: meta.digestMsg.chat, message_id: meta.digestMsg.msg });
  }
  try {
    const m = await tg.call('sendMessage', {
      chat_id: S.channel,
      text: T.digest(list, t),
      parse_mode: 'HTML',
      link_preview_options: { is_disabled: true },
      reply_markup: {
        inline_keyboard: [
          [{ text: '➕ E’lon berish', url: core.startLink('post') }],
          [{ text: '🔔 Yangi ishlardan xabardor bo‘lish', url: core.startLink('subs') }],
        ],
      },
    });
    meta.digestMsg = { chat: m.chat.id, msg: m.message_id, at: t };
    db.markDirty();
  } catch (e) {
    console.error(`[ro‘yxat] ${e.message}`);
  }
}

function prune(t) {
  const st = db.state();
  for (const [id, v] of Object.entries(st.vacancies)) {
    const end = v.endedAt || v.decidedAt || v.createdAt;
    if (v.status === 'draft' && t - v.createdAt > 7 * DAY) delete st.vacancies[id];
    else if (['expired', 'rejected', 'cancelled'].includes(v.status) && t - end > 120 * DAY) delete st.vacancies[id];
    else if (v.status === 'closed' && v.endedAt && t - v.endedAt > 120 * DAY) delete st.vacancies[id];
  }
  for (const [id, a] of Object.entries(st.apps)) if (t - a.createdAt > 90 * DAY) delete st.apps[id];
  for (const [id, p] of Object.entries(st.payments)) {
    const old = t - p.createdAt;
    if (['new', 'await', 'cancelled'].includes(p.status) && old > 7 * DAY) delete st.payments[id];
    else if (old > 365 * DAY) delete st.payments[id];
  }
  for (const [id, r] of Object.entries(st.resumes)) {
    if (r.status === 'draft' && t - r.createdAt > 7 * DAY) delete st.resumes[id];
    else if (['replaced', 'removed', 'rejected', 'cancelled'].includes(r.status) && t - (r.dbUntil || r.decidedAt || r.createdAt) > 60 * DAY) delete st.resumes[id];
    else if (r.dbUntil && t - r.dbUntil > 60 * DAY && r.status !== 'published') delete st.resumes[id];
  }
  for (const [id, s] of Object.entries(st.sessions)) if (t - (s.at || 0) > 3 * DAY) delete st.sessions[id];
  const locks = st.meta.locks || {};
  for (const [id, l] of Object.entries(locks)) if (l.until < t) delete locks[id];
}

async function maintenance() {
  const meta = db.state().meta;
  const t = clock.now();
  if (t - (meta.pruneAt || 0) > HOUR) {
    prune(t);
    meta.pruneAt = t;
    db.markDirty();
  }
  if (clock.hourFloat(t) >= 3 && meta.snapDay !== clock.dayKey(t)) {
    await db.snapshot().catch((e) => console.warn(`[nusxa] ${e.message}`));
  }
}

function start() {
  core.setKick(() => setTimeout(() => tick(), 300));
  timer = setInterval(() => tick(), 20000);
  setTimeout(() => tick(), 1500);
}
function stop() { if (timer) clearInterval(timer); timer = null; }

module.exports = { start, stop, tick, prune };
