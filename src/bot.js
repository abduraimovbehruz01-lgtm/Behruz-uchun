'use strict';
// Foydalanuvchi bo‘limlari: menyu, vakansiya joylash, ariza, qidirish, obuna,
// «Ish qidiryapman», nomzodlar bazasi, paketlar, shablonlar va to‘lovlar.
const tg = require('./tg');
const db = require('./db');
const ui = require('./ui');
const T = require('./texts');
const core = require('./core');
const clock = require('./clock');
const ai = require('./ai');
const admin = require('./admin');
const { esc, som, ikb, link, chunk, clip, normPhone, fmtPhone } = require('./util');

const { BTN, MENU_ACTIONS } = ui;
const S = () => db.settings();
const st = () => db.state();

// ================================================================ so‘rovnoma qadamlari
const STEPS = {
  vac: [
    { key: 'title', label: 'Lavozim', type: 'text', min: 2, max: 80, q: 'Lavozim nomini yozing.\nMasalan: <i>Sotuvchi</i>, <i>Haydovchi</i>, <i>Buxgalter</i>' },
    { key: 'category', label: 'Soha', type: 'cats', q: 'Sohani tanlang:' },
    { key: 'region', label: 'Hudud', type: 'regions', q: 'Hududni tanlang:' },
    { key: 'company', label: 'Ish joyi', type: 'text', min: 2, max: 80, skip: '⏭ O‘tkazib yuborish', q: 'Ish joyi yoki kompaniya nomini yozing:' },
    { key: 'salary', label: 'Maosh', type: 'text', min: 1, max: 60, quick: ['🤝 Kelishiladi'], q: 'Maoshni yozing.\nMasalan: <i>4–6 mln so‘m</i>' },
    { key: 'schedule', label: 'Ish vaqti', type: 'text', min: 2, max: 60, quick: ['To‘liq kun', 'Yarim kun', 'Smenali', 'Masofaviy'], q: 'Ish vaqtini tanlang yoki yozing.\nMasalan: <i>09:00–18:00, yakshanba dam</i>' },
    { key: 'req', label: 'Talablar', type: 'text', min: 5, max: 800, multiline: true, q: 'Talablar va vazifalarni qisqacha yozing.\nMasalan: <i>1 yil tajriba, rus tilini bilish, mijozlar bilan ishlash</i>' },
    { key: 'contact', label: 'Aloqa', type: 'contact', q: 'Nomzodlar siz bilan qanday bog‘lansin?' },
  ],
  res: [
    { key: 'name', label: 'Ism', type: 'text', min: 2, max: 60, q: 'Ism va familiyangizni yozing:' },
    { key: 'age', label: 'Yosh', type: 'text', num: [14, 80], skip: '⏭ O‘tkazib yuborish', q: 'Yoshingizni yozing:' },
    { key: 'category', label: 'Soha', type: 'cats', q: 'Qaysi sohada ish qidiryapsiz?' },
    { key: 'region', label: 'Hudud', type: 'regions', q: 'Qaysi hududda ishlamoqchisiz?' },
    { key: 'exp', label: 'Tajriba', type: 'text', min: 2, max: 300, quick: ['Tajribam yo‘q'], q: 'Ish tajribangizni yozing (qayerda, qancha vaqt):' },
    { key: 'salary', label: 'Maosh', type: 'text', min: 1, max: 60, quick: ['🤝 Kelishiladi'], q: 'Qancha maosh kutyapsiz?' },
    { key: 'about', label: 'O‘zi haqida', type: 'text', min: 5, max: 600, multiline: true, q: 'O‘zingiz haqingizda qisqacha yozing: ko‘nikmalar, til bilishingiz, ma’lumotingiz.' },
    { key: 'contact', label: 'Aloqa', type: 'contact', q: 'Ish beruvchilar siz bilan qanday bog‘lansin?' },
  ],
};
const quickValue = (s) => s.replace(/^🤝\s*/, '');

// ================================================================ yangilanishlar
const chains = new Map();
function handleUpdate(upd) {
  const key = (upd.message && upd.message.chat && upd.message.chat.id)
    || (upd.callback_query && upd.callback_query.from && upd.callback_query.from.id)
    || (upd.pre_checkout_query && upd.pre_checkout_query.from.id)
    || (upd.channel_post && upd.channel_post.chat.id)
    || (upd.my_chat_member && upd.my_chat_member.chat.id)
    || 0;
  const prev = chains.get(key) || Promise.resolve();
  const next = prev.then(() => route(upd)).catch((e) => onError(e, upd));
  chains.set(key, next);
  next.then(() => { if (chains.get(key) === next) chains.delete(key); });
  return next;
}

async function route(upd) {
  if (upd.message) return onMessage(upd.message);
  if (upd.callback_query) return onCallback(upd.callback_query);
  if (upd.pre_checkout_query) return onPreCheckout(upd.pre_checkout_query);
  if (upd.my_chat_member) return onMyChatMember(upd.my_chat_member);
  if (upd.channel_post) return onChannelPost(upd.channel_post);
  return null;
}

async function onError(e, upd) {
  console.error('[xato]', (e && e.stack) || e);
  try {
    const q = upd.callback_query;
    const uid = (upd.message && upd.message.chat.type === 'private' && upd.message.chat.id) || (q && q.from.id);
    if (q) await ui.answer(q, '⚠️ Xatolik, qaytadan urinib ko‘ring.');
    if (uid) await core.safeSend(uid, '⚠️ Kutilmagan xatolik yuz berdi. Qaytadan urinib ko‘ring yoki /start bosing.');
    core.adminNotice(`err:${String(e && e.message).slice(0, 40)}`, `⚠️ Botda xato: ${esc(String((e && e.message) || e).slice(0, 300))}`, 10 * 60e3);
  } catch (e2) { /* jim */ }
}

// ================================================================ xabarlar
async function onMessage(m) {
  if (!m.chat || m.chat.type !== 'private' || !m.from || m.from.is_bot) return null;
  const u = db.touchUser(m.from);
  const isAdm = db.isAdmin(u.id);
  if (m.successful_payment) return onPaid(u, m);
  if (u.blocked && !isAdm) {
    if (!u.blockedNotified) {
      u.blockedNotified = true;
      db.markDirty();
      await core.safeSend(u.id, '⛔ Siz botdan foydalanishdan cheklangansiz.');
    }
    return null;
  }
  const s = db.sess(u.id);
  const text = (m.text || '').trim();

  if (text.startsWith('/')) {
    const [c0, ...rest] = text.split(/\s+/);
    const cmd = c0.split('@')[0].toLowerCase();
    const arg = rest.join(' ').trim();
    if (cmd === '/start') { await ui.delInput(m); await cancelFlow(u, false); return onStart(u, arg); }
    if (cmd === '/admin') { await ui.delInput(m); await cancelFlow(u); return admin.command(u); }
    if (cmd === '/cancel' || cmd === '/bekor' || cmd === '/menu') {
      await ui.delInput(m);
      await cancelFlow(u, false);
      return ui.showMenu(u.id, 'Menyudan tanlang 👇');
    }
    if (cmd === '/help' || cmd === '/yordam') { await ui.delInput(m); return ui.ask(u.id, T.help()); }
    if (cmd === '/id') return ui.send(u.id, `🆔 Sizning ID raqamingiz: <code>${u.id}</code>`);
  }
  if (text === BTN.cancel) {
    await ui.delInput(m);
    await cancelFlow(u, false);
    return ui.showMenu(u.id, 'Bekor qilindi. Menyudan tanlang 👇');
  }
  const action = MENU_ACTIONS[text];
  if (action) {
    await ui.delInput(m);
    await cancelFlow(u);
    return menuAction(u, action);
  }
  if (m.contact && (s.awaitPhone || !s.flow)) return onContact(u, s, m);
  if (s.flow) {
    if (s.flow.startsWith('adm')) return admin.input(u, s, m);
    if (s.flow === 'form') return formInput(u, s, m);
    if (s.flow === 'apply') return applyInput(u, s, m);
    if (s.flow === 'chek') return chekInput(u, s, m);
    if (s.flow === 'phone') { await ui.delInput(m); return askPhone(u, s, s.awaitPhone || 'post', '⚠️ Iltimos, pastdagi tugmani bosing.'); }
  }
  await ui.delInput(m);
  return ui.showMenu(u.id, 'Quyidagi menyudan tanlang 👇');
}

// Joriy oqimni to‘xtatadi. Telefon tugmasi ko‘rinib turgan bo‘lsa, asosiy menyuni qaytaradi.
async function cancelFlow(u, restoreMenu = true) {
  const s = db.sess(u.id);
  const hadPhoneKb = !!s.awaitPhone;
  db.resetSess(u.id);
  await ui.clearPrompts(u.id);
  if (hadPhoneKb && restoreMenu) await ui.showMenu(u.id, 'Menyudan tanlang 👇');
}

async function onStart(u, arg) {
  if (!(await gate(u, `s:${arg}`))) return null;
  const am = /^a_(\d+)$/.exec(arg);
  if (am) return startApply(u, Number(am[1]));
  await ui.showMenu(u.id, T.welcome(u));
  if (arg === 'post') return startPost(u);
  if (arg === 'seek') return seekMenu(u);
  if (arg === 'subs') return subsView(u);
  return null;
}

// Majburiy obuna
async function gate(u, pending) {
  if (await core.isSubscribed(u)) return true;
  const s = db.sess(u.id);
  s.pending = pending;
  db.markDirty();
  const url = await core.channelInviteUrl();
  const name = T.channelName();
  await ui.ask(u.id, `📢 Botdan foydalanish uchun avval kanalimizga obuna bo‘ling${name ? ` (${name})` : ''}, so‘ng «✅ Obuna bo‘ldim» tugmasini bosing.`, ikb([
    url ? [['📢 Kanalga o‘tish', link(url)]] : null,
    [['✅ Obuna bo‘ldim', 'chk']],
  ]));
  return false;
}

async function checkSubCb(u, q) {
  u.subOk = 0;
  if (!(await core.isSubscribed(u))) return ui.answer(q, '❗ Hali obuna bo‘lmadingiz. Avval kanalga obuna bo‘ling.', true);
  await ui.answer(q, '✅ Rahmat!');
  const s = db.sess(u.id);
  const p = s.pending || '';
  s.pending = null;
  db.markDirty();
  if (p.startsWith('s:')) return onStart(u, p.slice(2));
  if (p.startsWith('m:')) { await ui.showMenu(u.id, 'Menyudan tanlang 👇'); return menuAction(u, p.slice(2)); }
  if (p.startsWith('ap:')) return startApply(u, Number(p.slice(3)));
  return ui.showMenu(u.id, T.welcome(u));
}

async function menuAction(u, action) {
  if (action !== 'help' && !(await gate(u, `m:${action}`))) return null;
  switch (action) {
    case 'post': return startPost(u);
    case 'browse': return browseStart(u, null);
    case 'seek': return seekMenu(u, null);
    case 'subs': return subsView(u, null);
    case 'my': return myList(u, null, 0);
    case 'cands': return candsStart(u, null);
    case 'packs': return packsView(u, null);
    case 'tpl': return tplView(u, null);
    case 'help': return ui.ask(u.id, T.help());
    default: return null;
  }
}

// ================================================================ telefon
async function askPhone(u, s, purpose, intro) {
  s.awaitPhone = purpose;
  db.markDirty();
  await ui.clearPrompts(u.id);
  const m = await ui.send(u.id, `${intro ? `${intro}\n\n` : ''}Pastdagi «${BTN.phone}» tugmasini bosing.`, ui.phoneKb());
  db.sess(u.id).prompts.push(m.message_id);
  db.markDirty();
  return m;
}

async function onContact(u, s, m) {
  const c = m.contact;
  await ui.delInput(m);
  if (!c || (c.user_id && c.user_id !== u.id)) {
    if (s.awaitPhone) return askPhone(u, s, s.awaitPhone, '⚠️ Iltimos, o‘zingizning raqamingizni tugma orqali yuboring.');
    return null;
  }
  u.phone = normPhone(c.phone_number) || `+${String(c.phone_number).replace(/\D/g, '')}`;
  u.phoneVerified = true;
  const purpose = s.awaitPhone;
  s.awaitPhone = null;
  db.markDirty();
  await ui.showMenu(u.id, `✅ Raqamingiz tasdiqlandi: ${fmtPhone(u.phone)}`);
  if (purpose === 'form' && s.flow === 'form') { s.d.contactMode = 'phone'; s.d.contact = u.phone; return nextStep(u, s); }
  if (purpose === 'apply' && s.flow === 'apply') { s.a.phone = u.phone; return applyNext(u, s); }
  if (purpose === 'post') return startForm(u, 'vac');
  return null;
}

// ================================================================ so‘rovnoma (vakansiya / rezyume)
async function startPost(u) {
  if (S().phoneRequired && !u.phoneVerified) {
    const s = db.resetSess(u.id);
    s.flow = 'phone';
    return askPhone(u, s, 'post', '📱 Vakansiya joylash uchun avval telefon raqamingizni tasdiqlang.');
  }
  return startForm(u, 'vac');
}

async function startForm(u, kind, data = {}, { atPreview = false, replaceId = null } = {}) {
  const s = db.resetSess(u.id);
  s.flow = 'form';
  s.kind = kind;
  s.d = { ...data };
  s.i = atPreview ? STEPS[kind].length : 0;
  s.editing = false;
  s.aiLeft = 3;
  s.aiPrev = null;
  s.replaceId = replaceId;
  db.markDirty();
  return atPreview ? preview(u, s) : askStep(u, s);
}

function contactRows(u, s) {
  const rows = [];
  if (u.phoneVerified && u.phone) rows.push([[`📞 ${fmtPhone(u.phone)}`, 'f:c:phone']]);
  else rows.push([['📱 Raqamni tasdiqlash', 'f:c:verify']]);
  if (u.username) rows.push([[`💬 @${u.username}`, 'f:c:user']]);
  rows.push([['✍️ Boshqa raqam yoki aloqa', 'f:c:text']]);
  if (s.kind === 'vac') rows.push([['🤖 Faqat bot orqali ariza', 'f:c:bot']]);
  return rows;
}

async function askStep(u, s, note = '') {
  const steps = STEPS[s.kind];
  const step = steps[s.i];
  const title = s.kind === 'vac' ? '💼 <b>Vakansiya</b>' : '🙋 <b>Ish qidiryapman</b>';
  let text = `${note ? `${note}\n\n` : ''}${title} · <b>${s.i + 1}/${steps.length}</b>\n${step.q}`;
  const cur = s.d[step.key];
  if (s.editing && cur && step.type === 'text') text += `\n\nHozirgi: <i>${esc(clip(cur, 300))}</i>`;
  const rows = [];
  if (step.type === 'cats' || step.type === 'regions') {
    const list = step.type === 'cats' ? S().cats : S().regions;
    chunk(list.map((x, i) => [x, `f:o:${i}`]), 2).forEach((r) => rows.push(r));
  } else if (step.type === 'contact') {
    contactRows(u, s).forEach((r) => rows.push(r));
  } else {
    if (step.quick) chunk(step.quick.map((x, i) => [x, `f:q:${i}`]), 2).forEach((r) => rows.push(r));
    if (step.skip) rows.push([[step.skip, 'f:skip']]);
  }
  if (s.editing) rows.push([['⬅️ Ko‘rinishga qaytish', 'f:pv'], ['❌ Bekor qilish', 'f:x']]);
  else if (s.i > 0) rows.push([['⬅️ Orqaga', 'f:b'], ['❌ Bekor qilish', 'f:x']]);
  else rows.push([['❌ Bekor qilish', 'f:x']]);
  s.awaitContactText = false;
  db.markDirty();
  return ui.ask(u.id, text, ikb(rows));
}

async function nextStep(u, s) {
  if (s.editing) { s.editing = false; return preview(u, s); }
  s.i += 1;
  if (s.i >= STEPS[s.kind].length) return preview(u, s);
  return askStep(u, s);
}

async function formInput(u, s, m) {
  const steps = STEPS[s.kind];
  const step = steps[s.i];
  const t = (m.text || '').trim();
  if (s.awaitPhone === 'form') {
    // Tugma o‘rniga raqamni qo‘lda yozdi
    await ui.delInput(m);
    const p = normPhone(t);
    if (!p) return askPhone(u, s, 'form', '⚠️ Raqamni tugma orqali yuboring yoki to‘g‘ri yozing.');
    s.awaitPhone = null;
    s.d.contactMode = 'text';
    s.d.contact = fmtPhone(p);
    await ui.showMenu(u.id, '✅ Raqam qabul qilindi.');
    return nextStep(u, s);
  }
  if (!step) { await ui.delInput(m); return preview(u, s); }
  if (s.awaitContactText) {
    await ui.delInput(m);
    if (t.length < 3 || t.length > 100) return askContactText(u, s, '⚠️ 3–100 belgi yozing.');
    s.awaitContactText = false;
    s.d.contactMode = 'text';
    s.d.contact = t.replace(/\s*\n\s*/g, ', ');
    return nextStep(u, s);
  }
  await ui.delInput(m);
  if (step.type !== 'text') return askStep(u, s, '👆 Iltimos, tugmalardan birini tanlang.');
  if (!t) return askStep(u, s, '⚠️ Javobni matn ko‘rinishida yozing.');
  if (step.num) {
    const n = parseInt(t.replace(/\D/g, ''), 10);
    if (!(n >= step.num[0] && n <= step.num[1])) return askStep(u, s, `⚠️ ${step.num[0]} dan ${step.num[1]} gacha son yozing.`);
    s.d[step.key] = String(n);
    return nextStep(u, s);
  }
  if (t.length < (step.min || 1)) return askStep(u, s, `⚠️ Juda qisqa. Kamida ${step.min} ta belgi yozing.`);
  if (t.length > step.max) return askStep(u, s, `⚠️ Juda uzun (${t.length} belgi). Ko‘pi bilan ${step.max} ta belgi yozing.`);
  s.d[step.key] = step.multiline ? t.replace(/\n{3,}/g, '\n\n') : t.replace(/\s*\n\s*/g, ' ');
  return nextStep(u, s);
}

async function askContactText(u, s, note = '') {
  s.awaitContactText = true;
  db.markDirty();
  return ui.ask(u.id, `${note ? `${note}\n\n` : ''}✍️ Telefon raqam yoki boshqa aloqa usulini yozing.\nMasalan: <i>+998 90 123 45 67, Ali aka</i>`, ikb([
    [['⬅️ Orqaga', 'f:cb'], ['❌ Bekor qilish', 'f:x']],
  ]));
}

async function preview(u, s, note = '') {
  s.i = STEPS[s.kind].length;
  s.awaitContactText = false;
  const d = s.d;
  if (s.kind === 'vac') { d.verified = !!u.phoneVerified; d.tariff = null; }
  const body = s.kind === 'vac' ? T.vacancyPost(d) : T.resumePost(d);
  const text = `${note ? `${note}\n\n` : ''}👀 <b>E’lon kanalda shunday ko‘rinadi:</b>\n\n${body}`;
  const rows = [[['✅ Yuborish', 'f:ok']], [['✏️ Tahrirlash', 'f:e']]];
  if (s.kind === 'vac' && ai.enabled() && s.aiLeft > 0) rows.push([[`✨ AI bilan yaxshilash (${s.aiLeft})`, 'f:ai']]);
  if (s.aiPrev != null) rows.push([['↩️ Eski matnga qaytish', 'f:aiu']]);
  rows.push([['❌ Bekor qilish', 'f:x']]);
  db.markDirty();
  return ui.ask(u.id, text, ikb(rows));
}

async function formCb(u, q, a, b) {
  const s = db.sess(u.id);
  if (s.flow !== 'form') {
    await ui.answer(q, 'Bu tugma eskirgan.');
    return ui.del(u.id, q.message && q.message.message_id);
  }
  const steps = STEPS[s.kind];
  const step = steps[s.i];
  switch (a) {
    case 'x':
      await ui.answer(q);
      await cancelFlow(u, false);
      return ui.showMenu(u.id, 'Bekor qilindi. Menyudan tanlang 👇');
    case 'b':
      if (s.i > 0) s.i -= 1;
      return askStep(u, s);
    case 'pv':
      s.editing = false;
      return preview(u, s);
    case 'o': {
      if (!step || (step.type !== 'cats' && step.type !== 'regions')) return ui.answer(q);
      const list = step.type === 'cats' ? S().cats : S().regions;
      const val = list[Number(b)];
      if (!val) return ui.answer(q, 'Ro‘yxat yangilangan, qayta tanlang.');
      s.d[step.key] = val;
      return nextStep(u, s);
    }
    case 'q': {
      if (!step || !step.quick || !step.quick[Number(b)]) return ui.answer(q);
      s.d[step.key] = quickValue(step.quick[Number(b)]);
      return nextStep(u, s);
    }
    case 'skip':
      if (!step || !step.skip) return ui.answer(q);
      s.d[step.key] = '';
      return nextStep(u, s);
    case 'c':
      if (!step || step.type !== 'contact') return ui.answer(q);
      return contactChoice(u, s, b);
    case 'cb':
      return askStep(u, s);
    case 'e': {
      const rows = chunk(steps.map((x, i) => [x.label, `f:ef:${i}`]), 2);
      rows.push([['⬅️ Orqaga', 'f:pv']]);
      return ui.screen(u.id, q, '✏️ Qaysi qismini o‘zgartirasiz?', ikb(rows));
    }
    case 'ef': {
      const i = Number(b);
      if (!steps[i]) return ui.answer(q);
      s.i = i;
      s.editing = true;
      return askStep(u, s);
    }
    case 'ai':
      return aiImprove(u, s, q);
    case 'aiu':
      if (s.aiPrev != null) { s.d.req = s.aiPrev; s.aiPrev = null; }
      return preview(u, s, '↩️ Eski matn qaytarildi.');
    case 'ok':
      await ui.answer(q);
      return s.kind === 'vac' ? submitVacancyDraft(u, s) : submitResumeDraft(u, s);
    default:
      return ui.answer(q);
  }
}

async function contactChoice(u, s, mode) {
  if (mode === 'phone' && u.phoneVerified) { s.d.contactMode = 'phone'; s.d.contact = u.phone; return nextStep(u, s); }
  if (mode === 'user' && u.username) { s.d.contactMode = 'user'; s.d.contact = u.username; return nextStep(u, s); }
  if (mode === 'bot' && s.kind === 'vac') { s.d.contactMode = 'bot'; s.d.contact = ''; return nextStep(u, s); }
  if (mode === 'text') return askContactText(u, s);
  if (mode === 'verify') return askPhone(u, s, 'form', '📱 Raqamingizni tasdiqlasangiz, e’loningizda «✅ Tasdiqlangan ish beruvchi» belgisi chiqadi.');
  return askStep(u, s);
}

async function aiImprove(u, s, q) {
  if (!ai.enabled() || s.aiLeft <= 0) return ui.answer(q, 'AI hozir mavjud emas.', true);
  await ui.answer(q, '✨ AI matnni tayyorlayapti...');
  await ui.screen(u.id, q, '⏳ AI e’lon matnini yaxshilayapti, biroz kuting...', null);
  try {
    const out = await ai.improveVacancy(s.d);
    s.aiLeft -= 1;
    if (s.aiPrev == null) s.aiPrev = s.d.req;
    s.d.req = out;
    db.markDirty();
    return preview(u, s, '✨ AI «Talablar va vazifalar» qismini yaxshiladi. Yoqmasa, «↩️ Eski matnga qaytish»ni bosing.');
  } catch (e) {
    console.warn(`[ai] ${e.message}`);
    return preview(u, s, '⚠️ AI hozir javob bermadi. Keyinroq urinib ko‘ring.');
  }
}

async function submitVacancyDraft(u, s) {
  const d = s.d;
  if (!db.isAdmin(u.id)) {
    const since = clock.startOfDay();
    const cnt = Object.values(st().vacancies).filter((v) => v.ownerId === u.id && (v.submittedAt || 0) >= since).length;
    if (cnt >= S().maxPerDay) return preview(u, s, `⚠️ Bugun ${S().maxPerDay} ta e’lon yubordingiz. Ertaga yana urinib ko‘ring.`);
  }
  if (s.replaceId) {
    const old = st().vacancies[s.replaceId];
    if (old && old.ownerId === u.id && ['draft', 'rejected'].includes(old.status)) delete st().vacancies[s.replaceId];
  }
  const id = db.nextId('vac');
  const v = {
    id,
    ownerId: u.id,
    status: 'draft',
    createdAt: clock.now(),
    title: d.title,
    category: d.category,
    region: d.region,
    company: d.company || '',
    salary: d.salary,
    schedule: d.schedule,
    req: d.req,
    contactMode: d.contactMode,
    contact: d.contact || '',
    verified: !!u.phoneVerified,
    apps: 0,
  };
  st().vacancies[id] = v;
  db.resetSess(u.id);
  await ui.clearPrompts(u.id);
  if (!S().payments) {
    v.tariff = 'oddiy';
    v.paidBy = 'free';
    await core.submitVacancy(v, { notifyUser: false });
    return ui.showMenu(u.id, `✅ «${esc(v.title)}» e’loningiz tekshiruvga yuborildi.\nTasdiqlangach kanalga chiqadi va sizga xabar beramiz.`);
  }
  return chooseTariff(u, v);
}

async function chooseTariff(u, v) {
  const P = S().prices;
  const H = S().hours;
  const tc = S().topCredits;
  const credits = u.credits || 0;
  const L = [
    `💼 <b>«${esc(v.title)}»</b> — tarifni tanlang:`,
    '',
    `• <b>Oddiy</b> — ${T.priceLabel(P.oddiy[0])} · kanalda ${T.dur(H.oddiy)}`,
    `• <b>🔥 TOP</b> — ${T.priceLabel(P.top[0])} · kanalda ${T.dur(H.top)}, tepada qadalib turadi`,
  ];
  if (credits) L.push('', `📦 Kreditingiz: <b>${credits}</b> ta (Oddiy = 1, TOP = ${tc})`);
  const rows = [
    [[`Oddiy — ${T.priceLabel(P.oddiy[0])}`, `t:o:${v.id}`]],
    [[`🔥 TOP — ${T.priceLabel(P.top[0])}`, `t:t:${v.id}`]],
  ];
  if (credits >= 1) rows.push([['📦 Kreditdan: Oddiy (1)', `t:co:${v.id}`]]);
  if (credits >= tc) rows.push([[`📦 Kreditdan: TOP (${tc})`, `t:ct:${v.id}`]]);
  rows.push([['❌ Keyinroq', `t:x:${v.id}`]]);
  return ui.ask(u.id, L.join('\n'), ikb(rows));
}

async function tariffCb(u, q, a, vid) {
  const v = st().vacancies[vid];
  if (!v || v.ownerId !== u.id || v.status !== 'draft') {
    await ui.answer(q, 'Bu e’lon allaqachon yuborilgan yoki topilmadi.');
    return ui.del(u.id, q.message && q.message.message_id);
  }
  if (a === 'x') {
    await ui.answer(q);
    return ui.showMenu(u.id, '📝 E’lon qoralama sifatida saqlandi. Uni «📂 Mening e’lonlarim» bo‘limidan yuborishingiz mumkin.');
  }
  if (!S().payments) {
    v.tariff = 'oddiy';
    v.paidBy = 'free';
    await ui.answer(q);
    await core.submitVacancy(v, { notifyUser: false });
    return ui.showMenu(u.id, '✅ E’lon tekshiruvga yuborildi.');
  }
  if (a === 'co' || a === 'ct') {
    const tariff = a === 'co' ? 'oddiy' : 'top';
    const cost = tariff === 'top' ? S().topCredits : 1;
    if ((u.credits || 0) < cost) return ui.answer(q, 'Kredit yetarli emas.', true);
    u.credits -= cost;
    v.tariff = tariff;
    v.paidBy = 'credit';
    v.creditCost = cost;
    db.markDirty();
    await ui.answer(q);
    await core.submitVacancy(v, { notifyUser: false });
    return ui.showMenu(u.id, `✅ E’lon tekshiruvga yuborildi (${cost} kredit ishlatildi, qoldi: ${u.credits}).`);
  }
  const tariff = a === 't' ? 'top' : 'oddiy';
  const [uzs, xtr] = S().prices[tariff];
  v.tariff = tariff;
  db.markDirty();
  await ui.answer(q);
  if (!uzs) {
    v.paidBy = 'free';
    await core.submitVacancy(v, { notifyUser: false });
    return ui.showMenu(u.id, '✅ E’lon tekshiruvga yuborildi.');
  }
  const p = core.createPayment(u, 'vac', v.id, uzs, xtr, `Vakansiya «${clip(v.title, 40)}» — ${tariff === 'top' ? 'TOP' : 'Oddiy'}`);
  return core.offerPayment(u, p);
}

async function submitResumeDraft(u, s) {
  const d = s.d;
  const id = db.nextId('res');
  const r = {
    id,
    ownerId: u.id,
    status: 'draft',
    createdAt: clock.now(),
    name: d.name,
    age: d.age || '',
    category: d.category,
    region: d.region,
    exp: d.exp,
    salary: d.salary,
    about: d.about,
    contactMode: d.contactMode,
    contact: d.contact || '',
  };
  st().resumes[id] = r;
  db.resetSess(u.id);
  await ui.clearPrompts(u.id);
  const [uzs, xtr] = S().prices.res;
  if (S().payments && uzs > 0 && !u.resCredit) {
    const p = core.createPayment(u, 'res', r.id, uzs, xtr, '«Ish qidiryapman» e’loni');
    return core.offerPayment(u, p);
  }
  if (u.resCredit) { u.resCredit = false; r.paidBy = 'credit'; r.creditCost = 1; } else r.paidBy = 'free';
  db.markDirty();
  await core.submitResume(r, { notifyUser: false });
  return ui.showMenu(u.id, '✅ Rezyumengiz tekshiruvga yuborildi. Tasdiqlangach xabar beramiz.');
}

// ================================================================ to‘lovlar
function cardText(p) {
  const L = ['💳 <b>Karta orqali to‘lov</b>', '', `Quyidagi kartaga <b>${som(p.uzs)}</b> o‘tkazing:`, `<code>${esc(S().card)}</code>`];
  if (S().cardOwner) L.push(`👤 ${esc(S().cardOwner)}`);
  L.push('', 'So‘ng to‘lov chekini (skrinshot) shu yerga <b>rasm</b> qilib yuboring.');
  return L.join('\n');
}

async function payCb(u, q, a, pid) {
  const p = st().payments[pid];
  if (!p || p.userId !== u.id) return ui.answer(q, 'To‘lov topilmadi.', true);
  if (!['new', 'await'].includes(p.status)) {
    await ui.answer(q, 'Bu to‘lov allaqachon ko‘rib chiqilgan.');
    return ui.del(u.id, q.message && q.message.message_id);
  }
  if (a === 'x') {
    p.status = 'cancelled';
    db.markDirty();
    db.resetSess(u.id);
    await ui.answer(q);
    const msg = p.product === 'vac'
      ? '📝 To‘lov bekor qilindi. E’lon «📂 Mening e’lonlarim» bo‘limida qoralama bo‘lib turibdi.'
      : 'To‘lov bekor qilindi.';
    return ui.showMenu(u.id, msg);
  }
  if (a === 'c') {
    if (!S().card) return ui.answer(q, 'Karta raqami sozlanmagan.', true);
    p.method = 'card';
    p.status = 'await';
    const s = db.resetSess(u.id);
    s.flow = 'chek';
    s.pid = p.id;
    db.markDirty();
    await ui.answer(q);
    return ui.ask(u.id, cardText(p), ikb([[['❌ Bekor qilish', `p:x:${p.id}`]]]));
  }
  if (a === 's') {
    if (!S().stars || !p.xtr) return ui.answer(q, 'Stars bilan to‘lov o‘chirilgan.', true);
    p.method = 'stars';
    p.status = 'await';
    db.markDirty();
    await ui.answer(q);
    await ui.clearPrompts(u.id);
    const m = await tg.call('sendInvoice', {
      chat_id: u.id,
      title: clip(p.title, 32),
      description: clip(`${p.title}. To‘lovdan so‘ng avtomatik faollashadi.`, 255),
      payload: `p${p.id}`,
      currency: 'XTR',
      prices: [{ label: clip(p.title, 32), amount: p.xtr }],
      reply_markup: { inline_keyboard: [[{ text: `⭐ ${p.xtr} Stars to‘lash`, pay: true }], [{ text: '❌ Bekor qilish', callback_data: `p:x:${p.id}` }]] },
    });
    db.sess(u.id).prompts.push(m.message_id);
    db.markDirty();
    return null;
  }
  return ui.answer(q);
}

async function chekInput(u, s, m) {
  const p = st().payments[s.pid];
  if (!p || p.status !== 'await') {
    db.resetSess(u.id);
    return ui.showMenu(u.id, 'To‘lov topilmadi yoki bekor qilingan.');
  }
  let fileId = null;
  let type = null;
  if (m.photo && m.photo.length) { fileId = m.photo[m.photo.length - 1].file_id; type = 'photo'; } else if (m.document) { fileId = m.document.file_id; type = 'document'; }
  if (!fileId) {
    await ui.delInput(m);
    return ui.ask(u.id, `${cardText(p)}\n\n⚠️ Iltimos, chekni <b>rasm</b> (skrinshot) qilib yuboring.`, ikb([[['❌ Bekor qilish', `p:x:${p.id}`]]]));
  }
  p.photo = fileId;
  p.photoType = type;
  p.status = 'pending';
  p.chekAt = clock.now();
  db.markDirty();
  db.resetSess(u.id);
  await ui.clearPrompts(u.id);
  if (p.product === 'vac') {
    const v = st().vacancies[p.ref];
    if (v) { v.payId = p.id; v.paidBy = 'pay'; await core.submitVacancy(v, { notifyUser: false }); }
  } else if (p.product === 'res') {
    const r = st().resumes[p.ref];
    if (r) { r.payId = p.id; r.paidBy = 'pay'; await core.submitResume(r, { notifyUser: false }); }
  } else {
    await core.cardsToAdmins(p, core.sendPayCard);
  }
  return ui.showMenu(u.id, '✅ Chek qabul qilindi. Admin tekshirgach sizga xabar beramiz.');
}

async function onPreCheckout(q) {
  const id = Number(String(q.invoice_payload || '').replace(/^p/, ''));
  const p = st().payments[id];
  const ok = !!p && p.userId === q.from.id && ['new', 'await'].includes(p.status) && q.currency === 'XTR' && q.total_amount === p.xtr;
  await tg.quiet('answerPreCheckoutQuery', ok
    ? { pre_checkout_query_id: q.id, ok: true }
    : { pre_checkout_query_id: q.id, ok: false, error_message: 'To‘lov eskirgan. Iltimos, qaytadan urinib ko‘ring.' });
}

async function onPaid(u, m) {
  const sp = m.successful_payment;
  const id = Number(String(sp.invoice_payload || '').replace(/^p/, ''));
  const p = st().payments[id];
  if (!p) {
    core.adminNotice(`pay:${sp.telegram_payment_charge_id}`, `⚠️ Noma’lum to‘lov keldi: ${sp.total_amount} ${esc(sp.currency)} · ${T.userLine(u)}`, 0);
    return null;
  }
  if (p.status === 'approved') return null;
  p.method = 'stars';
  p.status = 'approved';
  p.chargeId = sp.telegram_payment_charge_id;
  p.paidAt = clock.now();
  if (sp.currency === 'XTR') st().stats.xtr += sp.total_amount;
  db.markDirty();
  db.resetSess(u.id);
  await ui.clearPrompts(u.id);
  await ui.send(u.id, `✅ To‘lov qabul qilindi: ${sp.total_amount} ⭐`);
  return core.fulfill(p);
}

// ================================================================ ariza
async function startApply(u, vid) {
  const v = st().vacancies[vid];
  if (!v) return ui.showMenu(u.id, '⚠️ Vakansiya topilmadi.');
  if (v.status !== 'published') return ui.showMenu(u.id, `⚠️ «${esc(v.title)}» vakansiyasi yopilgan yoki muddati tugagan.`);
  if (v.ownerId === u.id) return ui.showMenu(u.id, 'ℹ️ Bu sizning e’loningiz. Arizalarni «📂 Mening e’lonlarim» bo‘limida ko‘rasiz.');
  const apps = Object.values(st().apps);
  if (apps.some((a) => a.vacancyId === v.id && a.userId === u.id)) return ui.showMenu(u.id, `ℹ️ Siz «${esc(v.title)}» vakansiyasiga allaqachon ariza yuborgansiz.`);
  const today = clock.startOfDay();
  if (!db.isAdmin(u.id) && apps.filter((a) => a.userId === u.id && a.createdAt >= today).length >= 30) {
    return ui.showMenu(u.id, '⚠️ Bugun juda ko‘p ariza yubordingiz. Ertaga davom eting.');
  }
  const s = db.resetSess(u.id);
  s.flow = 'apply';
  s.vid = v.id;
  s.a = { ...(u.profile || {}) };
  db.markDirty();
  return applyNext(u, s);
}

async function applyNext(u, s, note = '') {
  const v = st().vacancies[s.vid];
  const head = `${note ? `${note}\n\n` : ''}📩 <b>«${esc(v ? v.title : '')}»</b> vakansiyasiga ariza\n\n`;
  if (!s.a.name) {
    s.step = 'name';
    db.markDirty();
    return ui.ask(u.id, `${head}<b>1/3.</b> Ism va familiyangizni yozing:`, ikb([[['❌ Bekor qilish', 'ap:x']]]));
  }
  if (!s.a.phone) {
    if (u.phoneVerified && u.phone) { s.a.phone = u.phone; return applyNext(u, s, note); }
    s.step = 'phone';
    db.markDirty();
    return askPhone(u, s, 'apply', `${head}<b>2/3.</b> Telefon raqamingizni yuboring: tugmani bosing yoki raqamni yozing.`);
  }
  if (!s.a.about && !s.a.fileId) {
    s.step = 'about';
    db.markDirty();
    return ui.ask(u.id, `${head}<b>3/3.</b> O‘zingiz haqingizda qisqacha yozing (yosh, tajriba, ko‘nikmalar) yoki rezyume faylini (PDF, Word yoki rasm) yuboring:`, ikb([[['❌ Bekor qilish', 'ap:x']]]));
  }
  s.step = 'confirm';
  db.markDirty();
  const L = [`${note ? `${note}\n\n` : ''}📩 <b>Ariza</b> — «${esc(v.title)}»`, '', `👤 ${esc(s.a.name)}`, `📞 ${esc(fmtPhone(s.a.phone))}`];
  if (s.a.about) L.push(`📝 ${esc(s.a.about)}`);
  if (s.a.fileId) L.push('📎 Rezyume fayli biriktirilgan');
  L.push('', 'Ish beruvchiga yuboraymi?');
  return ui.ask(u.id, L.join('\n'), ikb([[['✅ Yuborish', 'ap:ok']], [['✏️ Qayta to‘ldirish', 'ap:re'], ['❌ Bekor qilish', 'ap:x']]]));
}

async function applyInput(u, s, m) {
  const t = (m.text || '').trim();
  switch (s.step) {
    case 'name':
      await ui.delInput(m);
      if (t.length < 2 || t.length > 60) return applyNext(u, s, '⚠️ Ismni to‘g‘ri yozing (2–60 belgi).');
      s.a.name = t;
      return applyNext(u, s);
    case 'phone': {
      await ui.delInput(m);
      const p = normPhone(t);
      if (!p) return askPhone(u, s, 'apply', '⚠️ Raqam noto‘g‘ri. Masalan: <i>+998 90 123 45 67</i>');
      s.a.phone = p;
      s.awaitPhone = null;
      await ui.showMenu(u.id, '✅ Raqam qabul qilindi.');
      return applyNext(u, s);
    }
    case 'about':
      if (m.document || (m.photo && m.photo.length)) {
        s.a.fileId = m.document ? m.document.file_id : m.photo[m.photo.length - 1].file_id;
        s.a.fileType = m.document ? 'document' : 'photo';
        if (m.caption) s.a.about = clip(m.caption.trim(), 800);
        return applyNext(u, s);
      }
      await ui.delInput(m);
      if (t.length < 3 || t.length > 800) return applyNext(u, s, '⚠️ 3–800 belgi yozing yoki fayl yuboring.');
      s.a.about = t;
      s.a.fileId = null;
      s.a.fileType = null;
      return applyNext(u, s);
    default:
      await ui.delInput(m);
      return applyNext(u, s);
  }
}

async function applyCb(u, q, a) {
  if (/^\d+$/.test(a)) {
    await ui.answer(q);
    if (!(await gate(u, `ap:${a}`))) return null;
    return startApply(u, Number(a));
  }
  const s = db.sess(u.id);
  if (s.flow !== 'apply') { await ui.answer(q, 'Bu tugma eskirgan.'); return ui.del(u.id, q.message && q.message.message_id); }
  if (a === 'x') {
    await ui.answer(q);
    await cancelFlow(u, false);
    return ui.showMenu(u.id, 'Ariza bekor qilindi.');
  }
  if (a === 're') { s.a = {}; db.markDirty(); return applyNext(u, s); }
  if (a === 'ok') { await ui.answer(q); return applySend(u, s); }
  return ui.answer(q);
}

async function applySend(u, s) {
  const v = st().vacancies[s.vid];
  db.resetSess(u.id);
  if (!v || v.status !== 'published') return ui.showMenu(u.id, '⚠️ Afsuski, bu vakansiya yopilgan.');
  if (Object.values(st().apps).some((x) => x.vacancyId === v.id && x.userId === u.id)) return ui.showMenu(u.id, 'ℹ️ Siz bu vakansiyaga ariza yuborgansiz.');
  const id = db.nextId('app');
  const a = {
    id,
    vacancyId: v.id,
    ownerId: v.ownerId,
    userId: u.id,
    name: s.a.name,
    phone: s.a.phone,
    about: s.a.about || '',
    fileId: s.a.fileId || null,
    fileType: s.a.fileType || null,
    createdAt: clock.now(),
  };
  st().apps[id] = a;
  v.apps = (v.apps || 0) + 1;
  u.profile = { name: a.name, phone: a.phone, about: a.about, fileId: a.fileId, fileType: a.fileType };
  db.markDirty();
  await core.safeSend(v.ownerId, T.appMsg(a, v, u), ikb([[['📂 E’lonni ochish', `my:v:${v.id}`]]]));
  if (a.fileId) {
    const isPhoto = a.fileType === 'photo';
    await tg.quiet(isPhoto ? 'sendPhoto' : 'sendDocument', { chat_id: v.ownerId, [isPhoto ? 'photo' : 'document']: a.fileId, caption: `📎 ${clip(a.name, 60)} — rezyume` });
  }
  return ui.showMenu(u.id, `✅ Arizangiz «${esc(v.title)}» e’loni egasiga yuborildi!\nIsh beruvchi siz bilan bog‘lanadi. Omad! 🍀`);
}

// ================================================================ vakansiyalarni ko‘rish
const activeVacancies = () => Object.values(st().vacancies).filter((v) => v.status === 'published');

async function browseStart(u, q) {
  const list = activeVacancies();
  if (!list.length) {
    return ui.screen(u.id, q, '🔎 Hozircha faol vakansiyalar yo‘q.\n\n🔔 «Obuna»ni yoqib qo‘ying: yangi ish chiqishi bilan xabar beramiz.', ikb([[['🔔 Obuna bo‘lish', 's:v']]]));
  }
  const counts = {};
  list.forEach((v) => { counts[v.category] = (counts[v.category] || 0) + 1; });
  const rows = [[[`📋 Barcha sohalar (${list.length})`, 'b:c:a']]];
  const cats = S().cats.map((c, i) => [c, i]).filter(([c]) => counts[c]);
  chunk(cats.map(([c, i]) => [`${c} (${counts[c]})`, `b:c:${i}`]), 2).forEach((r) => rows.push(r));
  return ui.screen(u.id, q, '🔎 <b>Vakansiyalar</b>\nSohani tanlang:', ikb(rows));
}

async function browseCb(u, q, a, b) {
  const s = db.sess(u.id);
  if (a === 's') return browseStart(u, q);
  if (a === 'c') {
    s.browse = { c: b === 'a' ? null : (S().cats[Number(b)] || null), r: null, page: 0 };
    db.markDirty();
    let list = activeVacancies();
    if (s.browse.c) list = list.filter((v) => v.category === s.browse.c);
    const counts = {};
    list.forEach((v) => { counts[v.region] = (counts[v.region] || 0) + 1; });
    const rows = [[[`📍 Barcha hududlar (${list.length})`, 'b:r:a']]];
    const regs = S().regions.map((r, i) => [r, i]).filter(([r]) => counts[r]);
    chunk(regs.map(([r, i]) => [`${r} (${counts[r]})`, `b:r:${i}`]), 2).forEach((x) => rows.push(x));
    rows.push([['⬅️ Orqaga', 'b:s']]);
    return ui.screen(u.id, q, `🔎 <b>${esc(s.browse.c || 'Barcha sohalar')}</b>\nHududni tanlang:`, ikb(rows));
  }
  if (a === 'r') {
    s.browse = s.browse || { c: null };
    s.browse.r = b === 'a' ? null : (S().regions[Number(b)] || null);
    db.markDirty();
    return browseList(u, q, 0);
  }
  if (a === 'p') return browseList(u, q, Number(b) || 0);
  return ui.answer(q);
}

async function browseList(u, q, page) {
  const s = db.sess(u.id);
  const b = s.browse || {};
  let list = activeVacancies();
  if (b.c) list = list.filter((v) => v.category === b.c);
  if (b.r) list = list.filter((v) => v.region === b.r);
  list.sort((x, y) => (y.tariff === 'top') - (x.tariff === 'top') || y.publishedAt - x.publishedAt);
  const per = 5;
  const pages = Math.max(1, Math.ceil(list.length / per));
  page = Math.min(Math.max(0, page), pages - 1);
  const items = list.slice(page * per, page * per + per);
  let text = `🔎 <b>${esc(b.c || 'Barcha sohalar')}</b> · ${esc(b.r || 'barcha hududlar')} — ${list.length} ta\n`;
  if (!items.length) text += '\nBu bo‘limda hozircha vakansiya yo‘q.';
  items.forEach((v, i) => {
    text += `\n<b>${page * per + i + 1}.</b> ${v.tariff === 'top' ? '🔥 ' : ''}${T.vShort(v)}\n🔗 <a href="${T.postLink(v)}">Kanalda batafsil</a>\n`;
  });
  const rows = [];
  if (items.length) rows.push(items.map((v, i) => [`📩 ${page * per + i + 1}`, `ap:${v.id}`]));
  if (pages > 1) rows.push([page > 0 ? ['⬅️', `b:p:${page - 1}`] : null, [`${page + 1}/${pages}`, 'noop'], page < pages - 1 ? ['➡️', `b:p:${page + 1}`] : null]);
  rows.push([['🔄 Boshqa qidiruv', 'b:s']]);
  if (items.length) text += '\n📩 raqamli tugma — shu vakansiyaga ariza yuborish.';
  return ui.screen(u.id, q, text, ikb(rows));
}

// ================================================================ obuna
async function subsView(u, q, note = '') {
  const sb = u.subs || (u.subs = { on: false, cats: [], regions: [] });
  const L = ['🔔 <b>Obuna</b>'];
  if (note) L.push(note);
  L.push(
    `Holati: ${sb.on ? '✅ yoqilgan' : '❌ o‘chirilgan'}`,
    `🗂 Sohalar: ${sb.cats.length ? esc(sb.cats.join(', ')) : 'barchasi'}`,
    `📍 Hududlar: ${sb.regions.length ? esc(sb.regions.join(', ')) : 'barchasi'}`,
    '',
    'Mos vakansiya kanalga chiqishi bilan bot sizga xabar yuboradi.',
  );
  const rows = [
    [['🗂 Sohalarni tanlash', 's:tc'], ['📍 Hududlarni tanlash', 's:tr']],
    [[sb.on ? '🔕 Obunani o‘chirish' : '🔔 Obunani yoqish', 's:on']],
  ];
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

async function subsPick(u, q, kind) {
  const sb = u.subs;
  const list = kind === 'c' ? S().cats : S().regions;
  const sel = kind === 'c' ? sb.cats : sb.regions;
  const rows = chunk(list.map((x, i) => [`${sel.includes(x) ? '✅' : '▫️'} ${x}`, `s:${kind}:${i}`]), 2);
  rows.push([[`${sel.length ? '▫️' : '✅'} Barchasi`, `s:${kind}a`]]);
  rows.push([['✔️ Tayyor', 's:v']]);
  const head = kind === 'c' ? '🗂 Qaysi sohalardagi ishlar qiziqtiradi? Bir nechtasini tanlash mumkin.' : '📍 Qaysi hududlar? Bir nechtasini tanlash mumkin.';
  return ui.screen(u.id, q, head, ikb(rows));
}

async function subsCb(u, q, a, b) {
  const sb = u.subs || (u.subs = { on: false, cats: [], regions: [] });
  if (a === 'v') return subsView(u, q);
  if (a === 'tc') return subsPick(u, q, 'c');
  if (a === 'tr') return subsPick(u, q, 'r');
  if (a === 'on') {
    sb.on = !sb.on;
    db.markDirty();
    await ui.answer(q, sb.on ? '🔔 Obuna yoqildi' : '🔕 Obuna o‘chirildi');
    return subsView(u, q);
  }
  if (a === 'ca' || a === 'ra') {
    if (a === 'ca') sb.cats = []; else sb.regions = [];
    sb.on = true;
    db.markDirty();
    return subsPick(u, q, a[0]);
  }
  if (a === 'c' || a === 'r') {
    const list = a === 'c' ? S().cats : S().regions;
    const sel = a === 'c' ? sb.cats : sb.regions;
    const val = list[Number(b)];
    if (!val) return ui.answer(q);
    const i = sel.indexOf(val);
    if (i >= 0) sel.splice(i, 1); else sel.push(val);
    sb.on = true;
    db.markDirty();
    return subsPick(u, q, a);
  }
  return ui.answer(q);
}

// ================================================================ mening e’lonlarim
async function myList(u, q, page = 0) {
  const list = Object.values(st().vacancies).filter((v) => v.ownerId === u.id && v.status !== 'cancelled').sort((a, b) => b.createdAt - a.createdAt);
  const per = 8;
  const pages = Math.max(1, Math.ceil(list.length / per));
  page = Math.min(Math.max(0, page), pages - 1);
  const L = ['📂 <b>Mening e’lonlarim</b>', `📦 Kreditlar: <b>${u.credits || 0}</b>`];
  if ((u.candUntil || 0) > clock.now()) L.push(`🗂 Nomzodlar bazasi: ${clock.fmtDate(u.candUntil)} gacha`);
  if (!list.length) L.push('', 'Hali vakansiya joylamagansiz.');
  const rows = list.slice(page * per, page * per + per).map((v) => [[`${T.VICON[v.status] || '•'} ${clip(v.title, 30)}${v.apps ? ` · ${v.apps} ariza` : ''}`, `my:v:${v.id}`]]);
  if (pages > 1) rows.push([page > 0 ? ['⬅️', `my:l:${page - 1}`] : null, [`${page + 1}/${pages}`, 'noop'], page < pages - 1 ? ['➡️', `my:l:${page + 1}`] : null]);
  rows.push([['➕ Yangi vakansiya', 'my:new']]);
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

async function myView(u, q, vid, note = '') {
  const v = st().vacancies[vid];
  if (!v || v.ownerId !== u.id) return ui.answer(q, 'E’lon topilmadi.', true);
  const L = [];
  if (note) L.push(note, '');
  L.push(`💼 <b>${esc(v.title)}</b>`, `Holati: ${T.VST[v.status] || v.status}`);
  if (v.tariff) L.push(`Tarif: ${v.tariff === 'top' ? '🔥 TOP' : 'Oddiy'}`);
  if (v.status === 'queued') L.push(core.queueText(v));
  if (v.publishedAt) L.push(`Chiqqan vaqti: ${clock.fmtDT(v.publishedAt)}`);
  if (v.status === 'published') L.push(`Muddati: ${clock.fmtDT(v.expiresAt)} gacha`);
  if (v.status === 'rejected' && v.rejectReason) L.push(`Sabab: ${esc(v.rejectReason)}`);
  L.push(`📩 Arizalar: ${v.apps || 0}`);
  const rows = [];
  const p = v.payId ? st().payments[v.payId] : null;
  switch (v.status) {
    case 'published':
      rows.push([['✅ Yopish (ishchi topildi)', `my:c:${v.id}`]], [['⏳ Muddatni uzaytirish', `ext:${v.id}`]]);
      break;
    case 'draft':
      rows.push([[S().payments ? '💳 To‘lash va yuborish' : '📤 Yuborish', `my:p:${v.id}`]], [['✏️ Tahrirlash', `my:re:${v.id}`], ['🗑 O‘chirish', `my:d:${v.id}`]]);
      break;
    case 'queued':
      rows.push([['🗑 Bekor qilish', `my:d:${v.id}`]]);
      break;
    case 'moderation':
      if (!(p && p.status === 'pending')) rows.push([['🗑 Bekor qilish', `my:d:${v.id}`]]);
      break;
    case 'rejected':
      rows.push([['✏️ Tuzatib qayta yuborish', `my:re:${v.id}`]], [['🗑 O‘chirish', `my:d:${v.id}`]]);
      break;
    case 'closed':
    case 'expired':
      rows.push([['🔁 Qayta joylash', `my:re:${v.id}`]]);
      break;
    default:
  }
  if (v.apps) rows.push([[`📋 Arizalar (${v.apps})`, `my:a:${v.id}`]]);
  if (v.msgId && !v.deleted && ['published', 'closed'].includes(v.status)) rows.push([['🔗 Kanalda ko‘rish', link(T.postLink(v))]]);
  rows.push([['⬅️ Orqaga', 'my:l:0']]);
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

const vacFields = (v) => ({
  title: v.title, category: v.category, region: v.region, company: v.company, salary: v.salary, schedule: v.schedule, req: v.req, contactMode: v.contactMode, contact: v.contact,
});

async function myCb(u, q, a, b) {
  const id = Number(b);
  const v = st().vacancies[id];
  const own = v && v.ownerId === u.id;
  switch (a) {
    case 'l': return myList(u, q, Number(b) || 0);
    case 'new': await ui.answer(q); return startPost(u);
    case 'v': return myView(u, q, id);
    case 'c':
      if (!own || v.status !== 'published') return ui.answer(q, 'E’lon kanalda emas.', true);
      return ui.screen(u.id, q, `«${esc(v.title)}» e’lonini yopasizmi?\nKanaldagi postda «✅ YOPILDI — ishchi topildi» deb yoziladi.`, ikb([[['✅ Ha, yopish', `my:cc:${v.id}`]], [['⬅️ Yo‘q', `my:v:${v.id}`]]]));
    case 'cc':
      if (!own) return ui.answer(q);
      if (await core.closeVacancy(v)) { await ui.answer(q, '✅ Yopildi'); return myView(u, q, id, '✅ E’lon yopildi. Omad!'); }
      return myView(u, q, id);
    case 'd':
      if (!own) return ui.answer(q);
      return ui.screen(u.id, q, `«${esc(v.title)}» e’lonini ${v.status === 'draft' || v.status === 'rejected' ? 'o‘chirasizmi' : 'bekor qilasizmi'}?`, ikb([[['🗑 Ha', `my:dd:${v.id}`]], [['⬅️ Yo‘q', `my:v:${v.id}`]]]));
    case 'dd': {
      if (!own) return ui.answer(q);
      const res = await cancelVacancy(u, v);
      if (res === 'pending') return ui.answer(q, 'Chekingiz tekshirilmoqda — hozir bekor qilib bo‘lmaydi.', true);
      await ui.answer(q, res && res.startsWith('refund') ? `📦 ${res.split(':')[1]} ta kredit qaytarildi` : '🗑 Bajarildi');
      return myList(u, q, 0);
    }
    case 'p':
      if (!own || v.status !== 'draft') return ui.answer(q, 'Bu e’lonni to‘lab bo‘lmaydi.', true);
      await ui.answer(q);
      if (!S().payments) {
        v.tariff = 'oddiy';
        v.paidBy = 'free';
        await core.submitVacancy(v, { notifyUser: false });
        return ui.showMenu(u.id, '✅ E’lon tekshiruvga yuborildi.');
      }
      return chooseTariff(u, v);
    case 're':
      if (!own) return ui.answer(q, 'E’lon topilmadi.', true);
      await ui.answer(q);
      return startForm(u, 'vac', vacFields(v), { atPreview: true, replaceId: ['draft', 'rejected'].includes(v.status) ? v.id : null });
    case 'a':
      if (!own) return ui.answer(q);
      return myApps(u, q, v);
    case 'af': {
      const ap = st().apps[id];
      if (!ap || ap.ownerId !== u.id || !ap.fileId) return ui.answer(q, 'Fayl topilmadi.', true);
      await ui.answer(q);
      const isPhoto = ap.fileType === 'photo';
      await tg.quiet(isPhoto ? 'sendPhoto' : 'sendDocument', { chat_id: u.id, [isPhoto ? 'photo' : 'document']: ap.fileId, caption: `📎 ${clip(ap.name, 60)} — rezyume` });
      return null;
    }
    default:
      return ui.answer(q);
  }
}

async function cancelVacancy(u, v) {
  if (v.status === 'draft' || v.status === 'rejected') {
    delete st().vacancies[v.id];
    db.markDirty();
    return 'deleted';
  }
  if (['queued', 'moderation'].includes(v.status)) {
    const p = v.payId ? st().payments[v.payId] : null;
    if (p && p.status === 'pending') return 'pending';
    let refund = 0;
    if (v.paidBy === 'credit') refund = v.creditCost || 1;
    else if (p && p.status === 'approved') refund = core.creditCost(v);
    if (refund) u.credits = (u.credits || 0) + refund;
    v.status = 'cancelled';
    db.markDirty();
    await core.closeCards(v, '🚫 Egasi bekor qildi', () => T.adminVacCard(v, u, p));
    return refund ? `refund:${refund}` : 'cancelled';
  }
  return 'none';
}

async function myApps(u, q, v) {
  const apps = Object.values(st().apps).filter((a) => a.vacancyId === v.id).sort((a, b) => b.createdAt - a.createdAt);
  const L = [`📋 <b>Arizalar</b> — ${esc(v.title)} (${apps.length})`];
  apps.slice(0, 10).forEach((a, i) => {
    const au = st().users[a.userId];
    L.push('', `<b>${i + 1}. ${esc(a.name)}</b> · ${esc(fmtPhone(a.phone))}${au && au.username ? ` · @${esc(au.username)}` : ''}`);
    if (a.about) L.push(`💬 ${esc(clip(a.about, 150))}`);
    if (a.fileId) L.push('📎 rezyume fayli bor');
    L.push(`🕒 ${clock.fmtShort(a.createdAt)}`);
  });
  if (apps.length > 10) L.push('', `…va yana ${apps.length - 10} ta (eng oxirgilari ko‘rsatildi)`);
  const rows = apps.slice(0, 10).filter((a) => a.fileId).slice(0, 6).map((a) => [[`📎 ${clip(a.name, 22)} — fayl`, `my:af:${a.id}`]]);
  rows.push([['⬅️ Orqaga', `my:v:${v.id}`]]);
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

async function extendCb(u, q, vid) {
  const v = st().vacancies[vid];
  if (!v || v.ownerId !== u.id) return ui.answer(q, 'E’lon topilmadi.', true);
  if (v.status !== 'published') return ui.answer(q, 'E’lon kanalda emas.', true);
  const [uzs, xtr] = S().prices.ext;
  await ui.answer(q);
  if (!S().payments || !uzs) {
    core.extendVacancy(v);
    return ui.screen(u.id, q, `✅ «${esc(v.title)}» muddati uzaytirildi: ${clock.fmtDT(v.expiresAt)} gacha.`, ikb([[['📂 Mening e’lonlarim', 'my:l:0']]]));
  }
  const p = core.createPayment(u, 'ext', v.id, uzs, xtr, `Muddatni uzaytirish (+${T.dur(S().hours.ext)}) — «${clip(v.title, 30)}»`);
  return core.offerPayment(u, p);
}

// ================================================================ ish qidiryapman (rezyume)
function myResume(u) {
  const t = clock.now();
  return Object.values(st().resumes)
    .filter((r) => r.ownerId === u.id && ['draft', 'moderation', 'queued', 'published', 'active', 'expired'].includes(r.status)
      && !(r.dbUntil && r.dbUntil < t))
    .sort((a, b) => b.createdAt - a.createdAt)[0] || null;
}

async function seekMenu(u, q) {
  const r = myResume(u);
  if (r) {
    const label = r.status === 'expired' ? '🟢 Nomzodlar bazasida (kanaldagi e’lon muddati tugagan)' : (T.VST[r.status] || r.status);
    const L = ['🙋 <b>Sizning rezyumengiz</b>', `Holati: ${label}`];
    if (r.dbUntil) L.push(`Nomzodlar bazasida: ${clock.fmtDate(r.dbUntil)} gacha`);
    L.push('', T.resumePost(r));
    const rows = [];
    if (r.status === 'draft') rows.push([[S().payments && S().prices.res[0] ? '💳 To‘lash va yuborish' : '📤 Yuborish', `rs:p:${r.id}`]]);
    rows.push([['🔁 Yangisini yozish', 'rs:n']], [['🗑 O‘chirish', 'rs:d']]);
    return ui.screen(u.id, q, L.join('\n'), ikb(rows));
  }
  return ui.screen(u.id, q, '🙋 <b>Ish qidiryapman</b>\n\nO‘zingiz haqingizda qisqa e’lon tayyorlaymiz. U kanalga chiqadi va nomzodlar bazasiga qo‘shiladi, ish beruvchilar ko‘rib siz bilan bog‘lanishadi.', ikb([[['✍️ Boshlash', 'rs:n']]]));
}

async function resumeCb(u, q, a, b) {
  if (a === 'n') {
    await ui.answer(q);
    if (!(await gate(u, 'm:seek'))) return null;
    return startForm(u, 'res', u.profile && u.profile.name ? { name: u.profile.name } : {}, {});
  }
  if (a === 'd') {
    const r = myResume(u);
    if (!r) return ui.answer(q, 'Rezyume topilmadi.', true);
    return ui.screen(u.id, q, 'Rezyumeni o‘chirasizmi? U nomzodlar bazasidan olib tashlanadi.', ikb([[['🗑 Ha, o‘chirish', 'rs:dd']], [['⬅️ Yo‘q', 'rs:v']]]));
  }
  if (a === 'dd') {
    const r = myResume(u);
    if (!r) return ui.answer(q);
    const p = r.payId ? st().payments[r.payId] : null;
    if (r.status === 'moderation' && p && p.status === 'pending') return ui.answer(q, 'Chekingiz tekshirilmoqda — hozir o‘chirib bo‘lmaydi.', true);
    if (r.status === 'published') await core.endPost(r, 'res');
    if (r.status === 'moderation') await core.closeCards(r, '🗑 Egasi o‘chirdi', () => T.adminResCard(r, u, p));
    r.status = 'removed';
    r.dbUntil = clock.now();
    db.markDirty();
    await ui.answer(q, '🗑 O‘chirildi');
    return seekMenu(u, q);
  }
  if (a === 'v') return seekMenu(u, q);
  if (a === 'p') {
    const r = st().resumes[Number(b)];
    if (!r || r.ownerId !== u.id || r.status !== 'draft') return ui.answer(q, 'Rezyume topilmadi.', true);
    await ui.answer(q);
    const [uzs, xtr] = S().prices.res;
    if (S().payments && uzs > 0) {
      const p = core.createPayment(u, 'res', r.id, uzs, xtr, '«Ish qidiryapman» e’loni');
      return core.offerPayment(u, p);
    }
    r.paidBy = 'free';
    await core.submitResume(r, { notifyUser: false });
    return ui.showMenu(u.id, '✅ Rezyumengiz tekshiruvga yuborildi.');
  }
  return ui.answer(q);
}

// ================================================================ nomzodlar bazasi
function activeCands() {
  const t = clock.now();
  return Object.values(st().resumes).filter((r) => r.approvedAt && r.dbUntil > t && ['active', 'queued', 'published', 'expired'].includes(r.status));
}
function candAccess(u) {
  const [uzs] = S().prices.cand;
  return !S().payments || !uzs || (u.candUntil || 0) > clock.now() || db.isAdmin(u.id);
}

async function candsStart(u, q) {
  const list = activeCands();
  const counts = {};
  list.forEach((r) => { counts[r.category] = (counts[r.category] || 0) + 1; });
  if (!candAccess(u)) {
    const [uzs, xtr] = S().prices.cand;
    const lines = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([c, n]) => `• ${esc(c)} — ${n} ta`);
    const text = [
      '🗂 <b>Nomzodlar bazasi</b>',
      `Bazada <b>${list.length}</b> ta nomzod bor.`,
      ...lines,
      '',
      `Ism, tajriba va aloqa ma’lumotlarini ko‘rish uchun kirish: <b>${S().candDays} kun — ${som(uzs)}</b>${xtr ? ` (yoki ${xtr} ⭐)` : ''}`,
    ].join('\n');
    return ui.screen(u.id, q, text, ikb([[['💳 Kirish sotib olish', 'cd:buy']]]));
  }
  if (!list.length) return ui.screen(u.id, q, '🗂 Nomzodlar bazasi hozircha bo‘sh. Tez orada to‘ladi!');
  const rows = [[[`📋 Barcha sohalar (${list.length})`, 'cd:c:a']]];
  const cats = S().cats.map((c, i) => [c, i]).filter(([c]) => counts[c]);
  chunk(cats.map(([c, i]) => [`${c} (${counts[c]})`, `cd:c:${i}`]), 2).forEach((r) => rows.push(r));
  const until = (u.candUntil || 0) > clock.now() ? `\nKirish: ${clock.fmtDate(u.candUntil)} gacha` : '';
  return ui.screen(u.id, q, `🗂 <b>Nomzodlar bazasi</b>${until}\nSohani tanlang:`, ikb(rows));
}

async function candCb(u, q, a, b) {
  const s = db.sess(u.id);
  if (a === 'buy') {
    const [uzs, xtr] = S().prices.cand;
    await ui.answer(q);
    if (candAccess(u)) return candsStart(u, q);
    const p = core.createPayment(u, 'cand', 0, uzs, xtr, `Nomzodlar bazasi — ${S().candDays} kun`);
    return core.offerPayment(u, p);
  }
  if (!candAccess(u)) return candsStart(u, q);
  if (a === 'o') return candsStart(u, q);
  if (a === 'c') {
    s.cand = { c: b === 'a' ? null : (S().cats[Number(b)] || null), r: null };
    db.markDirty();
    let list = activeCands();
    if (s.cand.c) list = list.filter((r) => r.category === s.cand.c);
    const counts = {};
    list.forEach((r) => { counts[r.region] = (counts[r.region] || 0) + 1; });
    const rows = [[[`📍 Barcha hududlar (${list.length})`, 'cd:r:a']]];
    const regs = S().regions.map((r, i) => [r, i]).filter(([r]) => counts[r]);
    chunk(regs.map(([r, i]) => [`${r} (${counts[r]})`, `cd:r:${i}`]), 2).forEach((x) => rows.push(x));
    rows.push([['⬅️ Orqaga', 'cd:o']]);
    return ui.screen(u.id, q, `🗂 <b>${esc(s.cand.c || 'Barcha sohalar')}</b>\nHududni tanlang:`, ikb(rows));
  }
  if (a === 'r') {
    s.cand = s.cand || { c: null };
    s.cand.r = b === 'a' ? null : (S().regions[Number(b)] || null);
    db.markDirty();
    return candList(u, q, 0);
  }
  if (a === 'p') return candList(u, q, Number(b) || 0);
  return ui.answer(q);
}

async function candList(u, q, page) {
  const s = db.sess(u.id);
  const c = s.cand || {};
  let list = activeCands();
  if (c.c) list = list.filter((r) => r.category === c.c);
  if (c.r) list = list.filter((r) => r.region === c.r);
  list.sort((a, b) => b.approvedAt - a.approvedAt);
  const per = 4;
  const pages = Math.max(1, Math.ceil(list.length / per));
  page = Math.min(Math.max(0, page), pages - 1);
  const items = list.slice(page * per, page * per + per);
  let text = `🗂 <b>${esc(c.c || 'Barcha sohalar')}</b> · ${esc(c.r || 'barcha hududlar')} — ${list.length} ta nomzod\n`;
  if (!items.length) text += '\nBu bo‘limda hozircha nomzod yo‘q.';
  items.forEach((r, i) => {
    text += `\n<b>${page * per + i + 1}. ${esc(r.name)}</b>${r.age ? `, ${esc(r.age)} yosh` : ''} — ${esc(r.region)}\n🗂 ${esc(r.category)} · 💰 ${esc(r.salary)}\n💼 ${esc(clip(r.exp, 150))}\n📝 ${esc(clip(r.about, 250))}\n📞 ${esc(T.contactLine(r))}\n`;
  });
  const rows = [];
  if (pages > 1) rows.push([page > 0 ? ['⬅️', `cd:p:${page - 1}`] : null, [`${page + 1}/${pages}`, 'noop'], page < pages - 1 ? ['➡️', `cd:p:${page + 1}`] : null]);
  rows.push([['🔄 Boshqa qidiruv', 'cd:o']]);
  return ui.screen(u.id, q, text, ikb(rows));
}

// ================================================================ paketlar va shablonlar
async function packsView(u, q) {
  const packs = S().packs || [];
  const L = ['📦 <b>Paketlar</b>', `Kreditingiz: <b>${u.credits || 0}</b> ta e’lon`, `Oddiy e’lon = 1 kredit, TOP = ${S().topCredits} kredit.`];
  const rows = [];
  if (!S().payments) {
    L.push('', '🎉 Hozir vakansiya joylash bepul, paket shart emas.');
  } else if (!packs.length) {
    L.push('', 'Hozircha paketlar yo‘q.');
  } else {
    L.push('');
    packs.forEach((pk, i) => {
      L.push(`• <b>${pk.n} ta e’lon</b> — ${som(pk.uzs)}${pk.xtr ? ` (${pk.xtr} ⭐)` : ''}`);
      rows.push([[`${pk.n} ta e’lon — ${som(pk.uzs)}`, `pk:${i}`]]);
    });
  }
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

async function packCb(u, q, i) {
  const pk = (S().packs || [])[i];
  if (!pk || !S().payments) return ui.answer(q, 'Paket topilmadi.', true);
  await ui.answer(q);
  const p = core.createPayment(u, 'pack', i, pk.uzs, pk.xtr, `Paket: ${pk.n} ta e’lon`, { n: pk.n });
  return core.offerPayment(u, p);
}

async function tplView(u, q) {
  const s = S();
  if (!s.tplFileId) return ui.screen(u.id, q, '📄 Rezyume shablonlari tez orada qo‘shiladi.');
  const [uzs, xtr] = s.prices.tpl;
  const free = !s.payments || !uzs || u.tpl;
  const L = ['📄 <b>Rezyume shablonlari</b>', esc(s.tplDesc), ''];
  if (u.tpl) L.push('Siz sotib olgansiz, istalgan vaqtda qayta yuklab olishingiz mumkin.');
  else if (free) L.push('Narxi: bepul 🎉');
  else L.push(`Narxi: <b>${som(uzs)}</b>${xtr ? ` yoki ${xtr} ⭐` : ''}`);
  return ui.screen(u.id, q, L.join('\n'), ikb([[free ? ['📥 Yuklab olish', 'tpl:get'] : ['💳 Sotib olish', 'tpl:buy']]]));
}

async function tplCb(u, q, a) {
  const s = S();
  const [uzs, xtr] = s.prices.tpl;
  const free = !s.payments || !uzs || u.tpl;
  if (a === 'get') {
    if (!free) return ui.answer(q, 'Avval sotib oling.', true);
    await ui.answer(q);
    return core.sendTemplate(u);
  }
  if (a === 'buy') {
    await ui.answer(q);
    if (free) return core.sendTemplate(u);
    const p = core.createPayment(u, 'tpl', 0, uzs, xtr, 'Rezyume shablonlari');
    return core.offerPayment(u, p);
  }
  return ui.answer(q);
}

// ================================================================ tugmalar
async function onCallback(q) {
  if (!q.from) return null;
  const u = db.touchUser(q.from);
  const data = q.data || '';
  try {
    if (u.blocked && !db.isAdmin(u.id)) return await ui.answer(q, '⛔ Siz cheklangansiz.', true);
    if (data.startsWith('a:')) return await admin.callback(u, q, data);
    const [p0, p1, p2] = data.split(':');
    switch (p0) {
      case 'f': return await formCb(u, q, p1, p2);
      case 'ap': return await applyCb(u, q, p1);
      case 'p': return await payCb(u, q, p1, Number(p2));
      case 't': return await tariffCb(u, q, p1, Number(p2));
      case 'my': return await myCb(u, q, p1, p2);
      case 'b': return await browseCb(u, q, p1, p2);
      case 's': return await subsCb(u, q, p1, p2);
      case 'rs': return await resumeCb(u, q, p1, p2);
      case 'cd': return await candCb(u, q, p1, p2);
      case 'pk': return await packCb(u, q, Number(p1));
      case 'tpl': return await tplCb(u, q, p1);
      case 'ext': return await extendCb(u, q, Number(p1));
      case 'extno':
        await ui.answer(q, 'Mayli 👌');
        return await ui.del(u.id, q.message && q.message.message_id);
      case 'chk': return await checkSubCb(u, q);
      default: return await ui.answer(q);
    }
  } finally {
    await ui.answer(q);
  }
}

// ================================================================ kanallar
async function onMyChatMember(up) {
  const chat = up.chat;
  const status = up.new_chat_member && up.new_chat_member.status;
  console.log(`[kanal] ${chat.type} «${chat.title || ''}» id=${chat.id} @${chat.username || '-'} → ${status}`);
  if (chat.type !== 'channel') return null;
  if (status !== 'administrator') {
    if (['left', 'kicked'].includes(status)) core.adminNotice(`left:${chat.id}`, `⚠️ Bot «${esc(chat.title || chat.id)}» kanalidan chiqarildi.`, 0);
    return null;
  }
  const S0 = S();
  if (chat.username) {
    if (!S0.channel) {
      S0.channel = `@${chat.username}`;
      db.markDirty();
      core.adminNotice(`ch:${chat.id}`, `📢 Vakansiyalar kanali ulandi: @${esc(chat.username)}`, 0);
    }
    return null;
  }
  const cfg = require('./config');
  if (String(chat.id) === String(cfg.BAZA_KANAL)) return null;
  await tg.quiet('sendMessage', {
    chat_id: chat.id,
    text: `✅ Bot ulandi.\nKanal ID raqami: <code>${chat.id}</code>\n\nAgar bu kanal baza uchun bo‘lsa, shu raqam serverdagi BAZA_KANAL sozlamasiga yoziladi.`,
    parse_mode: 'HTML',
  });
  core.adminNotice(`priv:${chat.id}`, `🔒 Bot yopiq kanalga admin qilindi: «${esc(chat.title || '')}» — ID <code>${chat.id}</code>`, 0);
  return null;
}

async function onChannelPost(p) {
  if (p.pinned_message) {
    // «... xabarni qadadi» degan xizmat xabarini o‘chiramiz
    await tg.quiet('deleteMessage', { chat_id: p.chat.id, message_id: p.message_id });
    return null;
  }
  const t = (p.text || '').trim();
  if (/^\/id(@\w+)?$/i.test(t)) {
    console.log(`[kanal] /id: «${p.chat.title || ''}» id=${p.chat.id}`);
    await tg.quiet('deleteMessage', { chat_id: p.chat.id, message_id: p.message_id });
    await tg.quiet('sendMessage', { chat_id: p.chat.id, text: `Kanal ID raqami: <code>${p.chat.id}</code>`, parse_mode: 'HTML' });
  }
  return null;
}

module.exports = { handleUpdate, STEPS };
