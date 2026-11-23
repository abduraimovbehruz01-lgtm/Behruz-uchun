'use strict';
// Kanal postlari va xabarlar matnlari.
const { esc, som, tag, fmtPhone, clip } = require('./util');
const clock = require('./clock');
const db = require('./db');

function channelLink(chatId, chatUser, msgId) {
  if (!msgId) return '';
  if (chatUser) return `https://t.me/${chatUser}/${msgId}`;
  return `https://t.me/c/${String(chatId).replace(/^-100/, '')}/${msgId}`;
}
const postLink = (x) => channelLink(x.chatId, x.chatUser, x.msgId);

function channelUrl() {
  const c = db.settings().channel || '';
  return c.startsWith('@') ? `https://t.me/${c.slice(1)}` : '';
}
function channelName() {
  const c = db.settings().channel || '';
  return c.startsWith('@') ? c : '';
}

function contactLine(d) {
  switch (d.contactMode) {
    case 'phone': return fmtPhone(d.contact);
    case 'user': return `@${d.contact}`;
    case 'bot': return 'pastdagi «📩 Ariza berish» tugmasi orqali';
    default: return d.contact || '';
  }
}

function dur(h) {
  h = Number(h) || 0;
  if (h % 24 === 0) return `${h / 24} kun`;
  if (h < 24) return `${h} soat`;
  return `${Math.round((h / 24) * 10) / 10} kun`;
}
const priceLabel = (uzs) => (uzs > 0 ? som(uzs) : 'bepul');

function vacancyPost(v) {
  const L = [];
  if (v.tariff === 'top') L.push('🔥 <b>TOP VAKANSIYA</b>');
  L.push(`💼 <b>${esc(v.title)}</b>`);
  if (v.company) L.push(`🏢 <b>Ish joyi:</b> ${esc(v.company)}`);
  L.push(`📍 <b>Hudud:</b> ${esc(v.region)}`);
  L.push(`💰 <b>Maosh:</b> ${esc(v.salary)}`);
  L.push(`🕘 <b>Ish vaqti:</b> ${esc(v.schedule)}`);
  L.push('', '📋 <b>Talablar va vazifalar:</b>', esc(v.req));
  L.push('', `📞 <b>Aloqa:</b> ${esc(contactLine(v))}`);
  if (v.verified) L.push('✅ Tasdiqlangan ish beruvchi');
  L.push('', [tag(v.category), tag(v.region), '#vakansiya'].filter(Boolean).join(' '));
  return L.join('\n');
}

const vacancyClosed = (v) => `✅ <b>YOPILDI — ishchi topildi</b>\n\n💼 ${esc(v.title)} · 📍 ${esc(v.region)}\n\n${[tag(v.category), '#vakansiya'].filter(Boolean).join(' ')}`;
const vacancyExpired = (v) => `⌛ <b>E’lon muddati tugagan</b>\n\n💼 ${esc(v.title)} · 📍 ${esc(v.region)}`;

function resumePost(r) {
  const L = [
    '🙋 <b>ISH QIDIRYAPMAN</b>',
    `👤 <b>${esc(r.name)}</b>${r.age ? `, ${esc(r.age)} yosh` : ''}`,
    `🗂 <b>Soha:</b> ${esc(r.category)}`,
    `📍 <b>Hudud:</b> ${esc(r.region)}`,
    `💼 <b>Tajriba:</b> ${esc(r.exp)}`,
    `💰 <b>Kutilayotgan maosh:</b> ${esc(r.salary)}`,
    '',
    '📝 <b>O‘zi haqida:</b>',
    esc(r.about),
    '',
    `📞 <b>Aloqa:</b> ${esc(contactLine(r))}`,
    '',
    [tag(r.category), tag(r.region), '#ish_qidiryapman'].filter(Boolean).join(' '),
  ];
  return L.join('\n');
}
const resumeExpired = (r) => `⌛ <b>E’lon muddati tugagan</b>\n\n🙋 ${esc(r.name)} · ${esc(r.category)}`;

const vShort = (v) => `💼 <b>${esc(v.title)}</b> — ${esc(v.region)}\n💰 ${esc(v.salary)} · 🕘 ${esc(v.schedule)}`;

const VST = {
  draft: '📝 Qoralama (to‘lanmagan)',
  moderation: '🟡 Tekshiruvda',
  queued: '🕒 Navbatda',
  published: '🟢 Kanalda',
  closed: '✅ Yopilgan',
  expired: '⌛ Muddati tugagan',
  rejected: '❌ Rad etilgan',
  cancelled: '🚫 Bekor qilingan',
  active: '🟢 Bazada',
  replaced: '♻️ Almashtirilgan',
  removed: '🗑 O‘chirilgan',
};
const VICON = { draft: '📝', moderation: '🟡', queued: '🕒', published: '🟢', closed: '✅', expired: '⌛', rejected: '❌', cancelled: '🚫' };

function userLine(u) {
  if (!u) return '—';
  return `<a href="tg://user?id=${u.id}">${esc(u.name)}</a>${u.username ? ` (@${esc(u.username)})` : ''} · ID <code>${u.id}</code>`;
}

function payLine(item, p) {
  if (item.paidBy === 'free') return 'bepul';
  if (item.paidBy === 'credit') return `📦 paketdan (${item.creditCost} kredit)`;
  if (!p) return '—';
  if (p.method === 'stars') return `⭐ ${p.xtr} Stars — to‘langan`;
  if (p.method === 'card') {
    if (p.status === 'pending') return `💳 karta, ${som(p.uzs)} — chekni tekshiring (yuqorida)`;
    if (p.status === 'approved') return `💳 karta, ${som(p.uzs)} — tasdiqlangan`;
    return `💳 karta, ${som(p.uzs)} — ${p.status}`;
  }
  return p.status;
}

function adminVacCard(v, owner, p) {
  const S = db.settings();
  const prev = Object.values(db.state().vacancies).filter((x) => x.ownerId === v.ownerId && x.publishedAt).length;
  const L = [`🆕 <b>Vakansiya #${v.id}</b> — tekshiruv`, '', vacancyPost(v), '', '———'];
  L.push(`👤 ${userLine(owner)}`);
  L.push(`📱 Telefon: ${owner && owner.phoneVerified ? `${fmtPhone(owner.phone)} ✅` : 'tasdiqlanmagan'}`);
  L.push(`💼 Tarif: ${v.tariff === 'top' ? '🔥 TOP' : 'Oddiy'} · ${dur(S.hours[v.tariff] || 48)}`);
  L.push(`💳 To‘lov: ${payLine(v, p)}`);
  L.push(`📊 Avval chiqqan e’lonlari: ${prev}`);
  if (v.flags && v.flags.length) L.push(`⚠️ <b>Shubhali so‘zlar:</b> ${esc(v.flags.join(', '))}`);
  return L.join('\n');
}

function adminResCard(r, owner, p) {
  const L = [`🆕 <b>Rezyume #${r.id}</b> — tekshiruv`, '', resumePost(r), '', '———', `👤 ${userLine(owner)}`];
  if (p || r.paidBy) L.push(`💳 To‘lov: ${payLine(r, p)}`);
  if (r.flags && r.flags.length) L.push(`⚠️ <b>Shubhali so‘zlar:</b> ${esc(r.flags.join(', '))}`);
  return L.join('\n');
}

function adminPayCard(p, u) {
  return [
    `💳 <b>To‘lov #${p.id}</b>`,
    esc(p.title),
    `Summa: <b>${som(p.uzs)}</b>`,
    `👤 ${userLine(u)}`,
    `🕒 ${clock.fmtDT(p.chekAt || p.createdAt)}`,
    '',
    'Chek yuqorida. Pul kartangizga kelganini tekshirib, tasdiqlang.',
  ].join('\n');
}

function appMsg(a, v, applicant) {
  const L = [`📩 <b>Yangi ariza</b> — «${esc(v.title)}»`, '', `👤 <b>${esc(a.name)}</b>`, `📞 ${esc(fmtPhone(a.phone))}`];
  L.push(applicant && applicant.username ? `💬 @${esc(applicant.username)}` : `💬 <a href="tg://user?id=${a.userId}">Telegram orqali yozish</a>`);
  if (a.about) L.push('', `📝 ${esc(a.about)}`);
  if (a.fileId) L.push('', '📎 Rezyume fayli quyida');
  return L.join('\n');
}

function digest(list, ts) {
  const byCat = {};
  for (const v of list) (byCat[v.category] = byCat[v.category] || []).push(v);
  let out = `📋 <b>Bugungi vakansiyalar</b> · ${clock.fmtDate(ts)}\n`;
  let shown = 0;
  for (const [cat, arr] of Object.entries(byCat)) {
    let block = `\n<b>${esc(cat)}</b>\n`;
    for (const v of arr) block += `• <a href="${postLink(v)}">${esc(clip(v.title, 60))}</a> — ${esc(v.region)} · ${esc(clip(v.salary, 40))}\n`;
    if (out.length + block.length > 3500) break;
    out += block;
    shown += arr.length;
  }
  if (shown < list.length) out += `\n…va yana ${list.length - shown} ta`;
  out += `\nJami: <b>${list.length}</b> ta vakansiya`;
  return out;
}

function welcome(u) {
  const ch = channelName();
  return [
    `👋 <b>Assalomu alaykum, ${esc(u.name)}!</b>`,
    '',
    'Bu bot orqali:',
    '👔 ish beruvchilar vakansiya joylaydi;',
    '🙋 ish qidiruvchilar ish topadi va ariza yuboradi.',
    ch ? `\n📢 Kanalimiz: ${ch}` : '',
    '',
    'Quyidagi menyudan tanlang 👇',
  ].filter((x) => x !== '').join('\n');
}

function help() {
  const S = db.settings();
  const ch = channelName();
  return [
    'ℹ️ <b>Yordam</b>',
    '',
    '👔 <b>Ish beruvchilarga:</b> «➕ Vakansiya joylash» tugmasini bosing va savollarga javob bering. E’lon tekshiruvdan o‘tgach kanalga chiqadi, arizalar shu botga keladi. Ishchi topilgach «📂 Mening e’lonlarim»dan e’lonni yoping.',
    '',
    '🙋 <b>Ish qidiruvchilarga:</b> «🔎 Vakansiyalar»dan ish toping va «📩 Ariza berish»ni bosing. «🔔 Obuna»ni yoqsangiz, yangi ishlar haqida bot o‘zi xabar beradi. «🙋 Ish qidiryapman» orqali o‘zingiz haqingizda e’lon joylashingiz mumkin.',
    '',
    '⚠️ Ish uchun hech qachon oldindan pul to‘lamang. Shubhali e’lon ko‘rsangiz, adminga yozing.',
    ch ? `\n📢 Kanal: ${ch}` : '',
    S.support ? `💬 Savollar uchun: ${esc(S.support)}` : '',
  ].filter((x) => x !== '').join('\n');
}

module.exports = {
  channelLink, postLink, channelUrl, channelName, contactLine, dur, priceLabel,
  vacancyPost, vacancyClosed, vacancyExpired, resumePost, resumeExpired, vShort,
  VST, VICON, userLine, payLine, adminVacCard, adminResCard, adminPayCard, appMsg, digest, welcome, help,
};
