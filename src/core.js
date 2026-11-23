'use strict';
// Asosiy amallar: tekshiruv, to‘lov, kanalga chiqarish, yopish, o‘chirish.
const tg = require('./tg');
const db = require('./db');
const ui = require('./ui');
const T = require('./texts');
const clock = require('./clock');
const { esc, som, ikb, link, norm, sleep, clip } = require('./util');

const { HOUR, DAY } = clock;
const me = { id: 0, username: '' }; // ishga tushganda to‘ldiriladi
const S = () => db.settings();
const st = () => db.state();

// ---------------------------------------------------------------- yuborish
const outbox = [];
let outRunning = false;
function enqueue(fn) {
  outbox.push(fn);
  if (!outRunning) runOutbox();
}
async function runOutbox() {
  outRunning = true;
  while (outbox.length) {
    const fn = outbox.shift();
    try { await fn(); } catch (e) { console.warn(`[navbat-xabar] ${e.message}`); }
    await sleep(45);
  }
  outRunning = false;
}
const outboxIdle = async () => { while (outRunning || outbox.length) await sleep(20); };

async function safeSend(uid, text, markup, extra) {
  try {
    return await ui.send(uid, text, markup, extra);
  } catch (e) {
    if (e.code === 403) {
      const u = st().users[uid];
      if (u && !u.inactive) { u.inactive = true; db.markDirty(); }
    } else {
      console.warn(`[xabar] ${uid}: ${e.message}`);
    }
    return null;
  }
}

const startLink = (param) => `https://t.me/${me.username}?start=${param}`;

// ---------------------------------------------------------------- adminlar
async function notifyAdmins(text, markup) {
  const out = [];
  for (const id of db.adminIds()) {
    const m = await safeSend(id, text, markup);
    if (m) out.push({ chat: id, msg: m.message_id });
  }
  return out;
}

const lastNotice = {};
function adminNotice(key, text, everyMs = 30 * 60e3) {
  const t = Date.now();
  if (everyMs && lastNotice[key] && t - lastNotice[key] < everyMs) return;
  lastNotice[key] = t;
  notifyAdmins(text).catch(() => {});
}

async function sendChek(chat, p) {
  const caption = `🧾 Chek · to‘lov #${p.id} · ${som(p.uzs)}`;
  if (p.photoType === 'photo') return tg.quiet('sendPhoto', { chat_id: chat, photo: p.photo, caption });
  return tg.quiet('sendDocument', { chat_id: chat, document: p.photo, caption });
}

function vacButtons(v, p) {
  const rows = [[['✅ Tasdiqlash', `a:vok:${v.id}`], ['❌ Rad etish', `a:vrj:${v.id}`]]];
  if (p && p.method === 'card' && p.status === 'pending') rows.push([['🧾 Chek noto‘g‘ri', `a:vbp:${v.id}`]]);
  rows.push([['🚫 Bloklash', `a:blk:${v.ownerId}`]]);
  return ikb(rows);
}
function resButtons(r, p) {
  const rows = [[['✅ Tasdiqlash', `a:rok:${r.id}`], ['❌ Rad etish', `a:rrj:${r.id}`]]];
  if (p && p.method === 'card' && p.status === 'pending') rows.push([['🧾 Chek noto‘g‘ri', `a:rbp:${r.id}`]]);
  rows.push([['🚫 Bloklash', `a:blk:${r.ownerId}`]]);
  return ikb(rows);
}

function rememberCard(item, chat, m) {
  if (!m) return;
  item.cards = (item.cards || []).filter((c) => c.chat !== chat);
  item.cards.push({ chat, msg: m.message_id });
  db.markDirty();
}

async function sendVacCard(chat, v) {
  const p = v.payId ? st().payments[v.payId] : null;
  if (p && p.method === 'card' && p.photo) await sendChek(chat, p);
  const m = await safeSend(chat, T.adminVacCard(v, st().users[v.ownerId], p), vacButtons(v, p));
  rememberCard(v, chat, m);
  return m;
}
async function sendResCard(chat, r) {
  const p = r.payId ? st().payments[r.payId] : null;
  if (p && p.method === 'card' && p.photo) await sendChek(chat, p);
  const m = await safeSend(chat, T.adminResCard(r, st().users[r.ownerId], p), resButtons(r, p));
  rememberCard(r, chat, m);
  return m;
}
async function sendPayCard(chat, p) {
  if (p.photo) await sendChek(chat, p);
  const m = await safeSend(chat, T.adminPayCard(p, st().users[p.userId]), ikb([
    [['✅ Tasdiqlash', `a:pok:${p.id}`], ['❌ Rad etish', `a:prj:${p.id}`]],
    [['🚫 Bloklash', `a:blk:${p.userId}`]],
  ]));
  rememberCard(p, chat, m);
  return m;
}

async function cardsToAdmins(item, sender) {
  for (const id of db.adminIds()) await sender(id, item);
}

async function closeCards(item, line, render) {
  const cards = item.cards || [];
  item.cards = [];
  db.markDirty();
  for (const c of cards) {
    let text = render();
    const add = `\n\n<b>${line}</b>`;
    if (text.length + add.length > 4000) text = text.slice(0, 4000 - add.length);
    await tg.quiet('editMessageText', {
      chat_id: c.chat,
      message_id: c.msg,
      text: text + add,
      parse_mode: 'HTML',
      link_preview_options: { is_disabled: true },
      reply_markup: { inline_keyboard: [] },
    });
  }
}

function findFlags(parts) {
  const hay = norm(parts.filter(Boolean).join(' \n '));
  const out = [];
  for (const w of S().badWords || []) {
    const nw = norm(w);
    if (nw && hay.includes(nw) && !out.includes(w)) out.push(w);
  }
  return out;
}

// ---------------------------------------------------------------- navbat
function queueItems() {
  const items = [];
  for (const v of Object.values(st().vacancies)) {
    if (v.status === 'queued') items.push({ kind: 'vac', item: v, prio: v.tariff === 'top' ? 0 : 1, at: v.approvedAt || 0 });
  }
  for (const r of Object.values(st().resumes)) {
    if (r.status === 'queued') items.push({ kind: 'res', item: r, prio: 1, at: r.approvedAt || 0 });
  }
  return items.sort((a, b) => a.prio - b.prio || a.at - b.at);
}
function queuePosition(item) {
  const i = queueItems().findIndex((x) => x.item === item);
  return i < 0 ? 0 : i + 1;
}
function queueText(item) {
  const q = S().queue;
  const h = clock.hourFloat();
  const pos = queuePosition(item);
  if (h >= q.from && h < q.to) return pos <= 1 ? 'Tez orada kanalga chiqadi.' : `Navbatda: ${pos}-o‘rin. Kanalga chiqqach xabar beramiz.`;
  return `Kanalga ertalab soat ${q.from}:00 dan keyin chiqadi (navbatda: ${pos}-o‘rin).`;
}

let kickFn = () => {};
const setKick = (fn) => { kickFn = fn; };
const kickScheduler = () => kickFn();

// ---------------------------------------------------------------- tekshiruv
const creditCost = (v) => (v.tariff === 'top' ? S().topCredits : 1);

function approvePaymentRecord(p, admin) {
  p.status = 'approved';
  p.decidedAt = clock.now();
  p.decidedBy = admin ? admin.id : null;
  if (p.method === 'card') st().stats.uzs += p.uzs;
  db.markDirty();
}

async function refundStars(p) {
  try {
    await tg.call('refundStarPayment', { user_id: p.userId, telegram_payment_charge_id: p.chargeId });
    p.status = 'refunded';
    st().stats.xtr -= p.xtr;
    db.markDirty();
    return true;
  } catch (e) {
    console.warn(`[stars-qaytarish] ${e.message}`);
    return false;
  }
}

async function submitVacancy(v, { notifyUser = true } = {}) {
  v.status = 'moderation';
  v.submittedAt = clock.now();
  v.flags = findFlags([v.title, v.company, v.salary, v.schedule, v.req, v.contact]);
  db.markDirty();
  await cardsToAdmins(v, sendVacCard);
  if (notifyUser) await safeSend(v.ownerId, `✅ «${esc(v.title)}» e’loningiz tekshiruvga yuborildi.\nTasdiqlangach kanalga chiqadi va sizga xabar beramiz.`);
}

async function submitResume(r, { notifyUser = true } = {}) {
  r.status = 'moderation';
  r.submittedAt = clock.now();
  r.flags = findFlags([r.name, r.exp, r.salary, r.about, r.contact]);
  db.markDirty();
  await cardsToAdmins(r, sendResCard);
  if (notifyUser) await safeSend(r.ownerId, '✅ Rezyumengiz tekshiruvga yuborildi. Tasdiqlangach xabar beramiz.');
}

const adminName = (a) => esc((a && a.name) || 'admin');

async function approveVacancy(v, admin) {
  if (v.status !== 'moderation') return false;
  const p = v.payId ? st().payments[v.payId] : null;
  if (p && p.status === 'pending') approvePaymentRecord(p, admin);
  v.status = 'queued';
  v.approvedAt = clock.now();
  v.decidedBy = admin.id;
  db.markDirty();
  await closeCards(v, `✅ Tasdiqlandi · ${adminName(admin)} · ${clock.fmtTime()}`, () => T.adminVacCard(v, st().users[v.ownerId], p));
  await safeSend(v.ownerId, `✅ «${esc(v.title)}» e’loningiz tasdiqlandi!\n${queueText(v)}`);
  kickScheduler();
  return true;
}

async function rejectVacancy(v, reason, admin) {
  if (v.status !== 'moderation') return false;
  const owner = st().users[v.ownerId] || { credits: 0 };
  const p = v.payId ? st().payments[v.payId] : null;
  const cost = creditCost(v);
  let extra = '';
  if (v.paidBy === 'credit') {
    owner.credits = (owner.credits || 0) + (v.creditCost || cost);
    extra = `\n📦 ${v.creditCost || cost} ta kredit hisobingizga qaytarildi.`;
  } else if (p && p.method === 'stars' && p.status === 'approved') {
    if (await refundStars(p)) extra = `\n⭐ ${p.xtr} Stars hisobingizga qaytarildi.`;
    else {
      owner.credits = (owner.credits || 0) + cost;
      extra = `\n📦 To‘lovingiz saqlandi: hisobingizga ${cost} ta e’lon krediti qo‘shildi.`;
    }
  } else if (p && p.method === 'card' && (p.status === 'pending' || p.status === 'approved')) {
    if (p.status === 'pending') approvePaymentRecord(p, admin);
    owner.credits = (owner.credits || 0) + cost;
    extra = `\n💳 To‘lovingiz saqlandi: hisobingizga ${cost} ta e’lon krediti qo‘shildi. E’lonni tuzatib qayta yuborsangiz, pul olinmaydi.`;
  }
  v.status = 'rejected';
  v.rejectReason = reason;
  v.decidedAt = clock.now();
  v.decidedBy = admin.id;
  db.markDirty();
  await closeCards(v, `❌ Rad etildi · ${adminName(admin)}: ${esc(reason)}`, () => T.adminVacCard(v, st().users[v.ownerId], p));
  await safeSend(v.ownerId, `❌ «${esc(v.title)}» e’loningiz rad etildi.\nSabab: ${esc(reason)}${extra}`, ikb([[['✏️ Tuzatib qayta yuborish', `my:re:${v.id}`]]]));
  return true;
}

async function badChekVacancy(v, admin) {
  const p = v.payId ? st().payments[v.payId] : null;
  if (v.status !== 'moderation' || !p || p.status !== 'pending') return false;
  p.status = 'rejected';
  p.decidedAt = clock.now();
  p.decidedBy = admin.id;
  v.status = 'draft';
  v.payId = null;
  v.paidBy = null;
  db.markDirty();
  await closeCards(v, `🧾 Chek tasdiqlanmadi · ${adminName(admin)}`, () => T.adminVacCard(v, st().users[v.ownerId], p));
  await safeSend(v.ownerId, `❌ «${esc(v.title)}» uchun yuborilgan to‘lov cheki tasdiqlanmadi.\nE’lon qoralama sifatida saqlandi, to‘lovni qayta qilishingiz mumkin.`, ikb([[['💳 Qayta to‘lash', `my:p:${v.id}`]]]));
  return true;
}

async function retireResume(o) {
  if (o.status === 'published') await endPost(o, 'res');
  o.status = 'replaced';
  o.dbUntil = clock.now();
  db.markDirty();
}

async function approveResume(r, admin) {
  if (r.status !== 'moderation') return false;
  const p = r.payId ? st().payments[r.payId] : null;
  if (p && p.status === 'pending') approvePaymentRecord(p, admin);
  r.approvedAt = clock.now();
  r.dbUntil = r.approvedAt + S().resDbDays * DAY;
  r.decidedBy = admin.id;
  r.status = S().resToChannel && S().channel ? 'queued' : 'active';
  for (const o of Object.values(st().resumes)) {
    if (o !== r && o.ownerId === r.ownerId && ['active', 'published', 'queued', 'expired'].includes(o.status)) await retireResume(o);
  }
  db.markDirty();
  await closeCards(r, `✅ Tasdiqlandi · ${adminName(admin)} · ${clock.fmtTime()}`, () => T.adminResCard(r, st().users[r.ownerId], p));
  await safeSend(r.ownerId, `✅ Rezyumengiz tasdiqlandi!\n🗂 U ${S().resDbDays} kun nomzodlar bazasida turadi, ish beruvchilar ko‘rib siz bilan bog‘lanishadi.${r.status === 'queued' ? `\n📢 ${queueText(r)}` : ''}`);
  kickScheduler();
  return true;
}

async function rejectResume(r, reason, admin) {
  if (r.status !== 'moderation') return false;
  const owner = st().users[r.ownerId] || {};
  const p = r.payId ? st().payments[r.payId] : null;
  let extra = '';
  if (p && p.method === 'stars' && p.status === 'approved') {
    if (await refundStars(p)) extra = `\n⭐ ${p.xtr} Stars qaytarildi.`;
    else { owner.resCredit = true; extra = '\n💳 To‘lovingiz saqlandi: tuzatib qayta yuborsangiz, pul olinmaydi.'; }
  } else if (p && p.method === 'card' && (p.status === 'pending' || p.status === 'approved')) {
    if (p.status === 'pending') approvePaymentRecord(p, admin);
    owner.resCredit = true;
    extra = '\n💳 To‘lovingiz saqlandi: tuzatib qayta yuborsangiz, pul olinmaydi.';
  } else if (r.paidBy === 'credit') {
    owner.resCredit = true;
  }
  r.status = 'rejected';
  r.rejectReason = reason;
  r.decidedAt = clock.now();
  r.decidedBy = admin.id;
  db.markDirty();
  await closeCards(r, `❌ Rad etildi · ${adminName(admin)}: ${esc(reason)}`, () => T.adminResCard(r, st().users[r.ownerId], p));
  await safeSend(r.ownerId, `❌ Rezyumengiz rad etildi.\nSabab: ${esc(reason)}${extra}`, ikb([[['✍️ Qayta yozish', 'rs:n']]]));
  return true;
}

async function badChekResume(r, admin) {
  const p = r.payId ? st().payments[r.payId] : null;
  if (r.status !== 'moderation' || !p || p.status !== 'pending') return false;
  p.status = 'rejected';
  p.decidedAt = clock.now();
  p.decidedBy = admin.id;
  r.status = 'draft';
  r.payId = null;
  r.paidBy = null;
  db.markDirty();
  await closeCards(r, `🧾 Chek tasdiqlanmadi · ${adminName(admin)}`, () => T.adminResCard(r, st().users[r.ownerId], p));
  await safeSend(r.ownerId, '❌ Rezyume uchun yuborilgan to‘lov cheki tasdiqlanmadi. To‘lovni qayta qilishingiz mumkin.', ikb([[['💳 Qayta to‘lash', `rs:p:${r.id}`]]]));
  return true;
}

// ---------------------------------------------------------------- kanal
const effHours = (h) => { h = Number(h) || 48; return h <= 48 ? Math.min(h, 47.5) : h; };

const vacPostKb = (v) => ({
  inline_keyboard: [
    [{ text: '📩 Ariza berish', url: startLink(`a_${v.id}`) }],
    [{ text: '➕ E’lon berish', url: startLink('post') }],
  ],
});
const resPostKb = () => ({
  inline_keyboard: [
    [{ text: '🙋 Rezyume joylash', url: startLink('seek') }],
    [{ text: '➕ Vakansiya joylash', url: startLink('post') }],
  ],
});

async function postToChannel(text, markup) {
  const ch = S().channel;
  if (!ch) throw new Error('Kanal sozlanmagan');
  return tg.call('sendMessage', { chat_id: ch, text, parse_mode: 'HTML', link_preview_options: { is_disabled: true }, reply_markup: markup });
}

async function publishVacancy(v) {
  const m = await postToChannel(T.vacancyPost(v), vacPostKb(v));
  v.chatId = m.chat.id;
  v.chatUser = m.chat.username || '';
  v.msgId = m.message_id;
  v.publishedAt = clock.now();
  v.expiresAt = v.publishedAt + effHours(S().hours[v.tariff] || 48) * HOUR;
  v.status = 'published';
  db.markDirty();
  if (v.tariff === 'top') {
    try {
      await tg.call('pinChatMessage', { chat_id: v.chatId, message_id: v.msgId, disable_notification: true });
      v.pinned = true;
      db.markDirty();
    } catch (e) {
      adminNotice('pin', `⚠️ TOP e’lonni kanalda qadab bo‘lmadi. Botga kanalda «Tahrirlash» ruxsatini bering.\n${esc(e.message)}`);
    }
  }
  await safeSend(v.ownerId, `📢 «${esc(v.title)}» e’loningiz kanalga chiqdi!\nMuddati: ${clock.fmtDT(v.expiresAt)} gacha.\nArizalar shu botga keladi.`, ikb([
    [['🔗 Kanalda ko‘rish', link(T.postLink(v))]],
    [['📂 Mening e’lonlarim', 'my:l:0']],
  ]));
  notifySubscribers(v);
}

async function publishResume(r) {
  const m = await postToChannel(T.resumePost(r), resPostKb());
  r.chatId = m.chat.id;
  r.chatUser = m.chat.username || '';
  r.msgId = m.message_id;
  r.publishedAt = clock.now();
  r.expiresAt = r.publishedAt + effHours(S().hours.res || 48) * HOUR;
  r.status = 'published';
  db.markDirty();
  await safeSend(r.ownerId, '📢 «Ish qidiryapman» e’loningiz kanalga chiqdi!', ikb([[['🔗 Kanalda ko‘rish', link(T.postLink(r))]]]));
}

function notifySubscribers(v) {
  const users = Object.values(st().users).filter((u) => u.subs && u.subs.on && !u.blocked && !u.inactive && u.id !== v.ownerId
    && (!u.subs.cats.length || u.subs.cats.includes(v.category))
    && (!u.subs.regions.length || u.subs.regions.includes(v.region)));
  const text = `🔔 <b>Yangi vakansiya</b>\n\n${T.vShort(v)}`;
  const kb = ikb([[['🔗 Batafsil', link(T.postLink(v))], ['📩 Ariza berish', `ap:${v.id}`]]]);
  for (const u of users) enqueue(() => safeSend(u.id, text, kb));
  return users.length;
}

// Muddati tugaganda: 48 soatgacha — o‘chadi, undan keyin — «Muddati tugagan» deb belgilanadi
async function endPost(item, kind) {
  const age = clock.now() - (item.publishedAt || 0);
  let deleted = false;
  if (item.msgId) {
    if (S().autoDelete && age < 47.9 * HOUR) {
      deleted = (await tg.quiet('deleteMessage', { chat_id: item.chatId, message_id: item.msgId })) === true;
    }
    if (!deleted && item.status === 'published') {
      await tg.quiet('editMessageText', {
        chat_id: item.chatId,
        message_id: item.msgId,
        text: kind === 'vac' ? T.vacancyExpired(item) : T.resumeExpired(item),
        parse_mode: 'HTML',
        reply_markup: { inline_keyboard: [] },
      });
    }
    if (!deleted && item.pinned) await tg.quiet('unpinChatMessage', { chat_id: item.chatId, message_id: item.msgId });
  }
  item.pinned = false;
  item.endedAt = clock.now();
  item.deleted = deleted;
  if (item.status === 'published') item.status = 'expired';
  db.markDirty();
}

async function closeVacancy(v) {
  if (v.status !== 'published') return false;
  await tg.quiet('editMessageText', {
    chat_id: v.chatId,
    message_id: v.msgId,
    text: T.vacancyClosed(v),
    parse_mode: 'HTML',
    reply_markup: { inline_keyboard: [] },
  });
  if (v.pinned) {
    await tg.quiet('unpinChatMessage', { chat_id: v.chatId, message_id: v.msgId });
    v.pinned = false;
  }
  v.status = 'closed';
  v.closedAt = clock.now();
  db.markDirty();
  return true;
}

function extendVacancy(v) {
  v.expiresAt = Math.max(v.expiresAt || clock.now(), clock.now()) + (Number(S().hours.ext) || 48) * HOUR;
  v.reminded = false;
  v.extCount = (v.extCount || 0) + 1;
  db.markDirty();
}

// ---------------------------------------------------------------- to‘lov
function createPayment(u, product, ref, uzs, xtr, title, meta) {
  const id = db.nextId('pay');
  const p = {
    id, userId: u.id, product, ref, uzs: Math.round(uzs || 0), xtr: Math.round(xtr || 0), title, meta: meta || null, status: 'new', createdAt: clock.now(),
  };
  st().payments[id] = p;
  db.markDirty();
  return p;
}

async function offerPayment(u, p) {
  const rows = [];
  if (S().card) rows.push([[`💳 Karta orqali — ${som(p.uzs)}`, `p:c:${p.id}`]]);
  if (S().stars && p.xtr > 0) rows.push([[`⭐ Telegram Stars — ${p.xtr} ⭐`, `p:s:${p.id}`]]);
  if (!rows.length) {
    adminNotice('nopay', '⚠️ Foydalanuvchi to‘lov qilmoqchi, lekin to‘lov usuli sozlanmagan.\nAdmin panel → Sozlamalar → «Karta raqami» yoki «Stars».');
    return ui.ask(u.id, '⚠️ Hozircha to‘lov qabul qilinmayapti. Birozdan so‘ng urinib ko‘ring.');
  }
  rows.push([['❌ Bekor qilish', `p:x:${p.id}`]]);
  return ui.ask(u.id, `💳 <b>${esc(p.title)}</b>\nSumma: <b>${som(p.uzs)}</b>\n\nTo‘lov usulini tanlang:`, ikb(rows));
}

async function sendTemplate(u) {
  const s = S();
  if (!s.tplFileId) return safeSend(u.id, '📄 Shablon fayli hali yuklanmagan. Admin bilan bog‘laning.');
  const isPhoto = s.tplType === 'photo';
  try {
    await tg.call(isPhoto ? 'sendPhoto' : 'sendDocument', { chat_id: u.id, [isPhoto ? 'photo' : 'document']: s.tplFileId, caption: '📄 Rezyume shablonlari. Omad tilaymiz! 🍀' });
  } catch (e) {
    adminNotice('tpl', `⚠️ Shablon faylini yuborib bo‘lmadi: ${esc(e.message)}`);
    await safeSend(u.id, '⚠️ Faylni yuborishda xatolik bo‘ldi. Admin bilan bog‘laning.');
  }
}

async function fulfill(p) {
  const u = st().users[p.userId];
  if (!u) return;
  switch (p.product) {
    case 'vac': {
      const v = st().vacancies[p.ref];
      if (!v) return;
      v.payId = p.id;
      v.paidBy = 'pay';
      if (v.status === 'draft') await submitVacancy(v);
      return;
    }
    case 'res': {
      const r = st().resumes[p.ref];
      if (!r) return;
      r.payId = p.id;
      r.paidBy = 'pay';
      if (r.status === 'draft') await submitResume(r);
      return;
    }
    case 'ext': {
      const v = st().vacancies[p.ref];
      if (v && v.status === 'published') {
        extendVacancy(v);
        await safeSend(u.id, `✅ «${esc(v.title)}» muddati uzaytirildi: ${clock.fmtDT(v.expiresAt)} gacha.`);
      } else {
        u.credits = (u.credits || 0) + 1;
        db.markDirty();
        await safeSend(u.id, '⌛ E’lon muddati tugab bo‘lgan edi, shuning uchun hisobingizga 1 ta e’lon krediti qo‘shildi.');
      }
      return;
    }
    case 'pack': {
      const n = (p.meta && p.meta.n) || 0;
      u.credits = (u.credits || 0) + n;
      db.markDirty();
      await safeSend(u.id, `✅ Paket faollashtirildi: hisobingizga <b>${n}</b> ta e’lon krediti qo‘shildi.\nJami kredit: ${u.credits} ta.`);
      return;
    }
    case 'cand': {
      u.candUntil = Math.max(clock.now(), u.candUntil || 0) + S().candDays * DAY;
      db.markDirty();
      await safeSend(u.id, `✅ Nomzodlar bazasi ochildi: ${clock.fmtDT(u.candUntil)} gacha.`, ikb([[['🗂 Nomzodlarni ko‘rish', 'cd:o']]]));
      return;
    }
    case 'tpl': {
      u.tpl = true;
      db.markDirty();
      await sendTemplate(u);
      return;
    }
    default:
  }
}

async function approvePayment(p, admin) {
  if (p.status !== 'pending') return false;
  approvePaymentRecord(p, admin);
  await closeCards(p, `✅ Tasdiqlandi · ${adminName(admin)}`, () => T.adminPayCard(p, st().users[p.userId]));
  await fulfill(p);
  return true;
}

async function rejectPayment(p, admin) {
  if (p.status !== 'pending') return false;
  p.status = 'rejected';
  p.decidedAt = clock.now();
  p.decidedBy = admin.id;
  db.markDirty();
  await closeCards(p, `❌ Rad etildi · ${adminName(admin)}`, () => T.adminPayCard(p, st().users[p.userId]));
  const sup = S().support;
  await safeSend(p.userId, `❌ «${esc(p.title)}» uchun to‘lov tasdiqlanmadi.${sup ? `\nSavollar bo‘lsa: ${esc(sup)}` : ''}`);
  return true;
}

// ---------------------------------------------------------------- majburiy obuna
async function channelInviteUrl() {
  const u = T.channelUrl();
  if (u) return u;
  if (st().meta.invite) return st().meta.invite;
  try {
    const l = await tg.call('exportChatInviteLink', { chat_id: S().channel });
    st().meta.invite = l;
    db.markDirty();
    return l;
  } catch (e) {
    return '';
  }
}

async function isSubscribed(u) {
  if (!S().forceSub || !S().channel || db.isAdmin(u.id)) return true;
  if ((u.subOk || 0) > clock.now()) return true;
  try {
    const m = await tg.call('getChatMember', { chat_id: S().channel, user_id: u.id });
    const ok = ['member', 'administrator', 'creator'].includes(m.status) || (m.status === 'restricted' && m.is_member);
    if (ok) { u.subOk = clock.now() + 10 * 60e3; db.markDirty(); }
    return ok;
  } catch (e) {
    return true; // tekshirib bo‘lmasa, foydalanuvchini to‘xtatmaymiz
  }
}

async function blockUser(u, admin) {
  u.blocked = true;
  u.blockedNotified = false;
  db.markDirty();
  for (const v of Object.values(st().vacancies)) {
    if (v.ownerId === u.id && v.status === 'moderation') {
      v.status = 'rejected';
      v.rejectReason = 'Foydalanuvchi bloklandi';
      await closeCards(v, `🚫 Bloklandi · ${adminName(admin)}`, () => T.adminVacCard(v, u, v.payId ? st().payments[v.payId] : null));
    }
  }
  for (const r of Object.values(st().resumes)) {
    if (r.ownerId === u.id && r.status === 'moderation') {
      r.status = 'rejected';
      r.rejectReason = 'Foydalanuvchi bloklandi';
      await closeCards(r, `🚫 Bloklandi · ${adminName(admin)}`, () => T.adminResCard(r, u, null));
    }
  }
  db.markDirty();
}

module.exports = {
  me, enqueue, outboxIdle, safeSend, startLink, notifyAdmins, adminNotice,
  sendVacCard, sendResCard, sendPayCard, cardsToAdmins, closeCards, findFlags,
  queueItems, queuePosition, queueText, setKick, kickScheduler, creditCost,
  submitVacancy, submitResume, approveVacancy, rejectVacancy, badChekVacancy,
  approveResume, rejectResume, badChekResume, retireResume,
  effHours, publishVacancy, publishResume, notifySubscribers, endPost, closeVacancy, extendVacancy,
  createPayment, offerPayment, fulfill, approvePayment, rejectPayment, sendTemplate,
  channelInviteUrl, isSubscribed, blockUser, clip,
};
