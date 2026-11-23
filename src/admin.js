'use strict';
// Admin panel: login-parol, tekshiruv, cheklar, sozlamalar, statistika,
// hammaga xabar, bloklash va zaxira.
const crypto = require('crypto');
const zlib = require('zlib');
const tg = require('./tg');
const db = require('./db');
const ui = require('./ui');
const T = require('./texts');
const core = require('./core');
const clock = require('./clock');
const cfg = require('./config');
const { esc, som, ikb, chunk, clip, nums, sleep } = require('./util');

const S = () => db.settings();
const st = () => db.state();
const sha = (s) => crypto.createHash('sha256').update(String(s)).digest();
const safeEq = (a, b) => crypto.timingSafeEqual(sha(a), sha(b));
const onoff = (b) => (b ? '✅' : '❌');

// iPhone birinchi harfni katta qilib yuborishi mumkin — shuni hisobga olamiz
function passOk(input, real) {
  if (!real) return false;
  if (safeEq(input, real)) return true;
  const flip = (x) => (x ? x[0].toLowerCase() + x.slice(1) : x);
  return safeEq(flip(input), flip(real));
}
const loginOk = (input, real) => !!real && safeEq(String(input).toLowerCase(), String(real).toLowerCase());

// Parol o‘zgarsa, barcha eski kirishlar bekor bo‘ladi
function init() {
  const h = crypto.createHash('sha256').update(`ish:${cfg.ADMIN_LOGIN}:${cfg.ADMIN_PASSWORD}`).digest('hex').slice(0, 16);
  const meta = st().meta;
  if (meta.passHash && meta.passHash !== h) st().admins = {};
  meta.passHash = h;
  db.markDirty();
}

// ================================================================ kirish
async function command(u) {
  if (db.isAdmin(u.id)) return panel(u, null);
  if (!cfg.ADMIN_LOGIN || !cfg.ADMIN_PASSWORD) return ui.ask(u.id, '⚙️ Admin login va paroli hali sozlanmagan.');
  const lock = (st().meta.locks || {})[u.id];
  if (lock && lock.until > clock.now()) return ui.ask(u.id, `⛔ Juda ko‘p noto‘g‘ri urinish. Soat ${clock.fmtTime(lock.until)} dan keyin qayta urinib ko‘ring.`);
  const s = db.resetSess(u.id);
  s.flow = 'adm:login';
  s.step = 'login';
  db.markDirty();
  return ui.ask(u.id, '🔐 <b>Admin panel</b>\nLoginni yozing:', ikb([[['❌ Bekor qilish', 'a:x']]]));
}

async function loginInput(u, s, m) {
  const t = (m.text || '').trim();
  await ui.del(u.id, m.message_id); // login va parol chatda qolmasin
  if (s.step === 'login') {
    s.login = t;
    s.step = 'pass';
    db.markDirty();
    return ui.ask(u.id, '🔑 Parolni yozing:', ikb([[['❌ Bekor qilish', 'a:x']]]));
  }
  const ok = loginOk(s.login || '', cfg.ADMIN_LOGIN) && passOk(t, cfg.ADMIN_PASSWORD);
  db.resetSess(u.id);
  const meta = st().meta;
  meta.fails = meta.fails || {};
  meta.locks = meta.locks || {};
  if (!ok) {
    const f = (meta.fails[u.id] || 0) + 1;
    meta.fails[u.id] = f;
    db.markDirty();
    console.warn(`[admin] noto‘g‘ri urinish: ${u.id} (${f})`);
    if (f >= 5) {
      meta.locks[u.id] = { until: clock.now() + 15 * 60e3 };
      meta.fails[u.id] = 0;
      return ui.ask(u.id, '⛔ 5 marta noto‘g‘ri kiritildi. 15 daqiqadan so‘ng qayta urinib ko‘ring.');
    }
    return ui.ask(u.id, `❌ Login yoki parol noto‘g‘ri. Yana ${5 - f} ta urinish qoldi.`, ikb([[['🔁 Qayta urinish', 'a:login']]]));
  }
  delete meta.fails[u.id];
  st().admins[u.id] = { since: clock.now(), until: clock.now() + 30 * clock.DAY, name: u.name };
  db.markDirty();
  console.log(`[admin] kirdi: ${u.id} ${u.name}`);
  return panel(u, null, '✅ Xush kelibsiz! Endi yangi e’lonlar va cheklar haqidagi xabarlar sizga keladi.');
}

// ================================================================ panel
function counts() {
  const V = Object.values(st().vacancies);
  const R = Object.values(st().resumes);
  const P = Object.values(st().payments);
  return {
    mod: V.filter((v) => v.status === 'moderation').length + R.filter((r) => r.status === 'moderation').length,
    pays: P.filter((p) => p.status === 'pending' && !['vac', 'res'].includes(p.product)).length,
    queued: V.filter((v) => v.status === 'queued').length + R.filter((r) => r.status === 'queued').length,
    live: V.filter((v) => v.status === 'published').length,
  };
}

function bazaLine() {
  const ds = db.status;
  if (!cfg.BAZA_KANAL) return '⚠️ ulanmagan — server qayta yonganda ma’lumotlar yo‘qoladi!';
  if (ds.error) return `⚠️ xato: ${esc(clip(ds.error, 150))}`;
  return `✅ ulangan${ds.lastOk ? ` · oxirgi saqlash ${clock.fmtTime(ds.lastOk)}` : ''}`;
}

async function panel(u, q, note = '') {
  const c = counts();
  const users = Object.values(st().users);
  const today = clock.startOfDay();
  const L = [];
  if (note) L.push(note, '');
  L.push(
    '⚙️ <b>Admin panel</b>',
    '',
    `👥 Foydalanuvchilar: <b>${users.length}</b> · bugun +${users.filter((x) => x.joinedAt >= today).length}`,
    `📥 Tekshiruvda: <b>${c.mod}</b> · 💳 Cheklar: <b>${c.pays}</b>`,
    `🕒 Navbatda: ${c.queued} · 🟢 Kanalda: ${c.live}`,
    `📢 Kanal: ${esc(S().channel || 'ulanmagan')}`,
    `💳 To‘lov: ${S().payments ? '✅ yoqilgan' : '🎉 bepul rejim'}`,
    `🔒 Majburiy obuna: ${onoff(S().forceSub)}`,
    `💾 Baza: ${bazaLine()}`,
  );
  const rows = [
    [[`📥 Tekshiruv (${c.mod})`, 'a:mod'], [`💳 Cheklar (${c.pays})`, 'a:pays']],
    [['📊 Statistika', 'a:stats'], ['📢 Hammaga xabar', 'a:bc']],
    [['⚙️ Sozlamalar', 'a:set'], ['🚫 Bloklash', 'a:blkp']],
    [['💾 Zaxira', 'a:bak'], ['🚪 Chiqish', 'a:out']],
  ];
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

async function modList(u, q) {
  const V = Object.values(st().vacancies).filter((v) => v.status === 'moderation').sort((a, b) => a.submittedAt - b.submittedAt);
  const R = Object.values(st().resumes).filter((r) => r.status === 'moderation').sort((a, b) => a.submittedAt - b.submittedAt);
  if (!V.length && !R.length) return ui.screen(u.id, q, '📥 Tekshiruvda hech narsa yo‘q ✅', ikb([[['⬅️ Orqaga', 'a:menu']]]));
  const rows = [];
  V.slice(0, 25).forEach((v) => {
    const p = v.payId ? st().payments[v.payId] : null;
    const icon = v.flags && v.flags.length ? '⚠️' : '💼';
    rows.push([[`${icon} #${v.id} ${clip(v.title, 22)} · ${v.tariff === 'top' ? 'TOP' : 'oddiy'}${p && p.method === 'card' ? ' · chek' : ''}`, `a:vv:${v.id}`]]);
  });
  R.slice(0, 10).forEach((r) => rows.push([[`🙋 #${r.id} ${clip(r.name, 26)}`, `a:rv:${r.id}`]]));
  rows.push([['⬅️ Orqaga', 'a:menu']]);
  return ui.screen(u.id, q, `📥 <b>Tekshiruv</b> — ${V.length + R.length} ta\nKo‘rish uchun tanlang:`, ikb(rows));
}

async function payList(u, q) {
  const P = Object.values(st().payments).filter((p) => p.status === 'pending' && !['vac', 'res'].includes(p.product)).sort((a, b) => a.chekAt - b.chekAt);
  if (!P.length) return ui.screen(u.id, q, '💳 Tekshirilmagan chek yo‘q ✅\n(Vakansiya cheklari «Tekshiruv» bo‘limida e’lon bilan birga keladi.)', ikb([[['⬅️ Orqaga', 'a:menu']]]));
  const rows = P.slice(0, 25).map((p) => [[`#${p.id} ${clip(p.title, 26)} · ${som(p.uzs)}`, `a:pv:${p.id}`]]);
  rows.push([['⬅️ Orqaga', 'a:menu']]);
  return ui.screen(u.id, q, `💳 <b>Cheklar</b> — ${P.length} ta`, ikb(rows));
}

async function stats(u, q) {
  const t = clock.now();
  const today = clock.startOfDay();
  const month = t - 30 * clock.DAY;
  const U = Object.values(st().users);
  const V = Object.values(st().vacancies);
  const A = Object.values(st().apps);
  const P = Object.values(st().payments).filter((p) => p.status === 'approved');
  const R = Object.values(st().resumes).filter((r) => r.approvedAt && r.dbUntil > t && ['active', 'queued', 'published', 'expired'].includes(r.status));
  const sum = (arr, f) => arr.reduce((acc, x) => acc + f(x), 0);
  const L = [
    '📊 <b>Statistika</b>',
    '',
    `👥 Foydalanuvchilar: <b>${U.length}</b> (bugun +${U.filter((x) => x.joinedAt >= today).length}, 30 kunda +${U.filter((x) => x.joinedAt >= month).length})`,
    `🔔 Bot obunachilari: ${U.filter((x) => x.subs && x.subs.on).length}`,
    `🚫 Bloklangan: ${U.filter((x) => x.blocked).length} · botni o‘chirganlar: ${U.filter((x) => x.inactive).length}`,
    '',
    `💼 Vakansiyalar: jami ${V.filter((v) => v.submittedAt).length}, bugun ${V.filter((v) => (v.submittedAt || 0) >= today).length}`,
    `🟢 Kanalda: ${V.filter((v) => v.status === 'published').length} · ✅ yopilgan: ${V.filter((v) => v.status === 'closed').length}`,
    `📩 Arizalar: jami ${A.length}, bugun ${A.filter((a) => a.createdAt >= today).length}`,
    `🙋 Nomzodlar bazada: ${R.length}`,
    '',
    `💰 Daromad (jami): ${som(st().stats.uzs)} · ${st().stats.xtr} ⭐`,
    `💰 Oxirgi 30 kun: ${som(sum(P.filter((p) => p.method === 'card' && (p.decidedAt || 0) >= month), (p) => p.uzs))} · ${sum(P.filter((p) => p.method === 'stars' && (p.paidAt || 0) >= month), (p) => p.xtr)} ⭐`,
  ];
  return ui.screen(u.id, q, L.join('\n'), ikb([[['⬅️ Orqaga', 'a:menu']]]));
}

// ================================================================ sozlamalar
const PRICE_LABELS = {
  oddiy: 'Oddiy e’lon', top: '🔥 TOP e’lon', ext: '⏳ Uzaytirish', res: '🙋 Rezyume e’loni', cand: '🗂 Nomzodlar bazasi', tpl: '📄 Shablonlar',
};
const HOUR_LABELS = { oddiy: 'Oddiy e’lon kanalda', top: 'TOP e’lon kanalda', ext: 'Uzaytirish', res: 'Rezyume e’loni kanalda' };

async function settingsView(u, q, note = '') {
  const s = S();
  const L = [];
  if (note) L.push(note, '');
  L.push(
    '⚙️ <b>Sozlamalar</b>',
    '',
    `📢 Kanal: ${esc(s.channel || '—')}`,
    `💳 To‘lov: ${s.payments ? '✅ yoqilgan' : '❌ o‘chirilgan (bepul)'} · ⭐ Stars: ${onoff(s.stars)}`,
    `🏦 Karta: ${s.card ? `${esc(s.card)}${s.cardOwner ? ` · ${esc(s.cardOwner)}` : ''}` : '—'}`,
    `🕘 Navbat: ${s.queue.interval ? `har ${s.queue.interval} daqiqada` : 'darhol'}, ${s.queue.from}:00–${s.queue.to}:00`,
    `📋 Kechki ro‘yxat: ${s.digest.on ? `✅ soat ${s.digest.hour}:00` : '❌'}`,
    `🗑 Avto o‘chirish: ${onoff(s.autoDelete)} · 🧹 Chatni tozalash: ${onoff(s.cleanChat)}`,
    `🔒 Majburiy obuna: ${onoff(s.forceSub)} · 📱 Telefon majburiy: ${onoff(s.phoneRequired)}`,
    `🙋 Rezyume kanalga: ${onoff(s.resToChannel)} · 🔢 Kunlik limit: ${s.maxPerDay}`,
    `📄 Shablon fayli: ${s.tplFileId ? esc(s.tplName || 'yuklangan') : '—'}`,
    `💬 Yordam uchun: ${esc(s.support || '—')}`,
  );
  const rows = [
    [['💰 Narxlar', 'a:sp'], ['⏳ Muddatlar', 'a:sh']],
    [[`💳 To‘lov ${onoff(s.payments)}`, 'a:t:payments'], [`⭐ Stars ${onoff(s.stars)}`, 'a:t:stars']],
    [['🏦 Karta raqami', 'a:e:card'], ['📢 Kanal', 'a:e:channel']],
    [['🗂 Sohalar', 'a:e:cats'], ['📍 Hududlar', 'a:e:regions']],
    [['🕘 Navbat va soatlar', 'a:e:queue'], ['📦 Paketlar', 'a:e:packs']],
    [[`📋 Kechki ro‘yxat ${onoff(s.digest.on)}`, 'a:t:digest'], ['🕗 Ro‘yxat soati', 'a:e:digestHour']],
    [[`🗑 Avto o‘chirish ${onoff(s.autoDelete)}`, 'a:t:autoDelete'], [`🧹 Chat tozalash ${onoff(s.cleanChat)}`, 'a:t:cleanChat']],
    [[`🔒 Majburiy obuna ${onoff(s.forceSub)}`, 'a:t:forceSub'], [`📱 Telefon majburiy ${onoff(s.phoneRequired)}`, 'a:t:phoneRequired']],
    [[`🙋 Rezyume kanalga ${onoff(s.resToChannel)}`, 'a:t:resToChannel'], ['⚠️ Shubhali so‘zlar', 'a:e:badWords']],
    [['📁 Shablon fayli', 'a:e:tpl'], ['📝 Shablon tavsifi', 'a:e:tplDesc']],
    [['💬 Yordam username', 'a:e:support'], ['🔢 Kunlik limit', 'a:e:maxPerDay']],
    [['⬅️ Orqaga', 'a:menu']],
  ];
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

async function pricesView(u, q, note = '') {
  const P = S().prices;
  const L = [];
  if (note) L.push(note, '');
  L.push('💰 <b>Narxlar</b> (so‘m / ⭐ Stars)', '');
  for (const [k, lbl] of Object.entries(PRICE_LABELS)) L.push(`${lbl}: ${P[k][0] ? som(P[k][0]) : 'bepul'} / ${P[k][1] || 0} ⭐`);
  L.push('', `🔥 TOP e’lon paketdan: ${S().topCredits} kredit`);
  if (!S().payments) L.push('', 'ℹ️ Hozir bepul rejim yoqilgan: narxlar to‘lov yoqilganda ishlaydi.');
  const rows = chunk(Object.entries(PRICE_LABELS).map(([k, lbl]) => [lbl, `a:e:price:${k}`]), 2);
  rows.push([['🔥 TOP krediti', 'a:e:topCredits']]);
  rows.push([['⬅️ Orqaga', 'a:set']]);
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

async function hoursView(u, q, note = '') {
  const s = S();
  const L = [];
  if (note) L.push(note, '');
  L.push(
    '⏳ <b>Muddatlar</b>',
    '',
    `Oddiy e’lon kanalda: ${T.dur(s.hours.oddiy)}`,
    `TOP e’lon kanalda: ${T.dur(s.hours.top)}`,
    `Uzaytirish: +${T.dur(s.hours.ext)}`,
    `Rezyume e’loni kanalda: ${T.dur(s.hours.res)}`,
    `Rezyume bazada: ${s.resDbDays} kun`,
    `Nomzodlar bazasiga kirish: ${s.candDays} kun`,
    `Uzaytirish eslatmasi: ${s.remindHours ? `tugashidan ${s.remindHours} soat oldin` : 'o‘chirilgan'}`,
    '',
    'ℹ️ Muddat 2 kungacha bo‘lsa, tugaganda e’lon kanaldan butunlay o‘chadi. Undan uzun bo‘lsa, Telegram cheklovi sabab o‘chmaydi: «⌛ Muddati tugagan» deb belgilanadi.',
  );
  const rows = [
    [['Oddiy', 'a:e:h:oddiy'], ['TOP', 'a:e:h:top']],
    [['Uzaytirish', 'a:e:h:ext'], ['Rezyume kanalda', 'a:e:h:res']],
    [['Rezyume bazada', 'a:e:resDbDays'], ['Bazaga kirish', 'a:e:candDays']],
    [['Eslatma', 'a:e:remindHours']],
    [['⬅️ Orqaga', 'a:set']],
  ];
  return ui.screen(u.id, q, L.join('\n'), ikb(rows));
}

const lines = (t) => String(t).split(/\n|;/).map((x) => x.trim()).filter(Boolean);
const uniq = (arr) => [...new Set(arr)];
const intIn = (t, lo, hi, msg) => {
  const n = Math.round(nums(t)[0]);
  if (!(n >= lo && n <= hi)) throw new Error(msg || `${lo} dan ${hi} gacha son yozing.`);
  return n;
};

function getEdit(key) {
  const s = S();
  if (key.startsWith('price:')) {
    const k = key.slice(6);
    if (!PRICE_LABELS[k]) return null;
    return {
      back: 'sp',
      q: `💰 <b>${PRICE_LABELS[k]}</b> narxini yozing: so‘m va Stars.\nMasalan: <code>20000 100</code>\nFaqat so‘m yozsangiz, Stars o‘zi hisoblanadi. <code>0</code> — bepul.`,
      cur: () => `${s.prices[k][0] ? som(s.prices[k][0]) : 'bepul'} / ${s.prices[k][1]} ⭐`,
      set: (t) => {
        const n = nums(t);
        if (!n.length) throw new Error('Son yozing. Masalan: 20000 100');
        const uzs = Math.round(n[0]);
        if (uzs !== 0 && uzs < 500) throw new Error('Narx kamida 500 so‘m yoki 0 (bepul) bo‘lsin. Sonni probelsiz yozing: 20000');
        if (uzs > 100000000) throw new Error('Juda katta narx.');
        const xtr = n.length > 1 ? Math.round(n[1]) : (uzs ? Math.max(1, Math.round(uzs / 200)) : 0);
        if (xtr < 0 || xtr > 100000) throw new Error('Stars 0 dan 100000 gacha bo‘lsin.');
        s.prices[k] = [uzs, xtr];
      },
    };
  }
  if (key.startsWith('h:')) {
    const k = key.slice(2);
    if (!HOUR_LABELS[k]) return null;
    return {
      back: 'sh',
      q: `⏳ <b>${HOUR_LABELS[k]}</b> — necha kun? Masalan: <code>2</code> yoki <code>1.5</code>\nℹ️ 2 kungacha bo‘lsa, muddat tugaganda e’lon kanaldan butunlay o‘chadi.`,
      cur: () => T.dur(s.hours[k]),
      set: (t) => {
        const n = nums(t)[0];
        if (!(n > 0 && n <= 60)) throw new Error('0.1 dan 60 gacha kun yozing.');
        s.hours[k] = Math.max(1, Math.round(n * 24));
      },
    };
  }
  const E = {
    card: {
      back: 'set',
      q: '🏦 Karta raqami va egasini yozing.\nMasalan: <code>8600 1234 5678 9012 Ali Valiyev</code>\nO‘chirish uchun: <code>-</code>',
      cur: () => (s.card ? `${s.card} · ${s.cardOwner}` : '—'),
      set: (t) => {
        if (t === '-') { s.card = ''; s.cardOwner = ''; return ''; }
        const m = t.match(/^\s*([\d\s-]{16,30})/);
        const digits = m ? m[1].replace(/\D/g, '') : '';
        if (digits.length < 16 || digits.length > 19) throw new Error('Karta raqami 16 xonali bo‘lishi kerak.');
        s.card = digits.replace(/(\d{4})(?=\d)/g, '$1 ');
        s.cardOwner = t.slice(m[0].length).trim().slice(0, 60);
        return s.payments ? '' : 'ℹ️ To‘lovni qabul qilish uchun «💳 To‘lov» tugmasini ham yoqing.';
      },
    },
    channel: {
      back: 'set',
      q: '📢 Vakansiyalar kanali manzilini yozing.\nMasalan: <code>@samarqand_vakansiya</code>\nAvval botni kanalga admin qiling.',
      cur: () => s.channel || '—',
      set: async (t) => {
        const ch = db.normChannel(t);
        if (!ch) throw new Error('Kanal manzilini @kanal ko‘rinishida yozing.');
        const chat = await tg.call('getChat', { chat_id: ch }).catch(() => null);
        if (!chat) throw new Error('Kanal topilmadi. Manzilni tekshiring va botni kanalga admin qiling.');
        const mem = await tg.call('getChatMember', { chat_id: chat.id, user_id: core.me.id }).catch(() => null);
        if (!mem || mem.status !== 'administrator') throw new Error('Bot bu kanalda admin emas. Avval botni kanalga admin qiling.');
        s.channel = chat.username ? `@${chat.username}` : String(chat.id);
        st().meta.invite = null;
        const miss = [];
        if (mem.can_post_messages === false) miss.push('xabar joylash');
        if (mem.can_edit_messages === false) miss.push('tahrirlash');
        if (mem.can_delete_messages === false) miss.push('o‘chirish');
        return miss.length ? `⚠️ Botga kanalda quyidagi ruxsatlar ham kerak: ${miss.join(', ')}.` : '';
      },
    },
    support: {
      back: 'set',
      q: '💬 Savollar uchun Telegram username yozing.\nMasalan: <code>@behruz</code>\nO‘chirish uchun: <code>-</code>',
      cur: () => s.support || '—',
      set: (t) => {
        if (t === '-') { s.support = ''; return ''; }
        const un = t.replace(/^https?:\/\/t\.me\//, '').replace(/^@/, '');
        if (!/^[A-Za-z0-9_]{4,32}$/.test(un)) throw new Error('Username noto‘g‘ri. Masalan: @behruz');
        s.support = `@${un}`;
        return '';
      },
    },
    cats: {
      back: 'set',
      q: '🗂 Sohalar ro‘yxatini yozing, har birini yangi qatordan.',
      cur: () => `\n${s.cats.join('\n')}`,
      set: (t) => {
        const l = uniq(lines(t).map((x) => x.slice(0, 40)));
        if (l.length < 2 || l.length > 40) throw new Error('2 tadan 40 tagacha soha yozing.');
        s.cats = l;
        return '';
      },
    },
    regions: {
      back: 'set',
      q: '📍 Hududlar ro‘yxatini yozing, har birini yangi qatordan.\nMasalan, faqat bitta shahar tumanlarini yozishingiz mumkin.',
      cur: () => `\n${s.regions.join('\n')}`,
      set: (t) => {
        const l = uniq(lines(t).map((x) => x.slice(0, 40)));
        if (l.length < 1 || l.length > 60) throw new Error('1 tadan 60 tagacha hudud yozing.');
        s.regions = l;
        return '';
      },
    },
    queue: {
      back: 'set',
      q: '🕘 Navbat sozlamasi: <b>oraliq (daqiqa)</b>, <b>boshlanish soati</b>, <b>tugash soati</b>.\nMasalan: <code>10 8 22</code> — har 10 daqiqada bitta e’lon, 08:00 dan 22:00 gacha.\n<code>0 0 24</code> — navbatsiz, istalgan vaqtda.',
      cur: () => `${s.queue.interval} ${s.queue.from} ${s.queue.to}`,
      set: (t) => {
        const [i, f, to] = nums(t).map(Math.round);
        if ([i, f, to].some((x) => x === undefined || Number.isNaN(x))) throw new Error('Uchta son yozing. Masalan: 10 8 22');
        if (i < 0 || i > 240 || f < 0 || f > 23 || to < 1 || to > 24 || f >= to) throw new Error('Masalan: 10 8 22 (boshlanish soati tugashdan kichik bo‘lsin).');
        s.queue = { interval: i, from: f, to };
        return '';
      },
    },
    digestHour: {
      back: 'set',
      q: '📋 Kechki «Bugungi vakansiyalar» ro‘yxati soat nechada chiqsin? (0–23)\nMasalan: <code>20</code>',
      cur: () => String(s.digest.hour),
      set: (t) => { s.digest.hour = intIn(t, 0, 23); return ''; },
    },
    packs: {
      back: 'set',
      q: '📦 Paketlar: har qatorga bittasi — <b>e’lonlar soni</b>, <b>narx (so‘m)</b>, <b>Stars</b>.\nMasalan:\n<code>5 80000 400\n10 150000 750</code>\nO‘chirish uchun: <code>-</code>',
      cur: () => `\n${s.packs.map((p) => `${p.n} ${p.uzs} ${p.xtr}`).join('\n') || '—'}`,
      set: (t) => {
        if (t === '-') { s.packs = []; return ''; }
        const out = [];
        for (const ln of lines(t)) {
          const [n, uzs, xtr] = nums(ln).map(Math.round);
          if (!(n >= 1 && n <= 1000) || !(uzs >= 500)) throw new Error(`Qator noto‘g‘ri: «${ln}». Masalan: 5 80000 400`);
          out.push({ n, uzs, xtr: xtr >= 0 ? xtr : Math.max(1, Math.round(uzs / 200)) });
        }
        if (!out.length || out.length > 8) throw new Error('1 tadan 8 tagacha paket yozing.');
        s.packs = out;
        return '';
      },
    },
    badWords: {
      back: 'set',
      q: '⚠️ Shubhali so‘zlar ro‘yxati: yangi qatordan yoki vergul bilan yozing. Bunday so‘zli e’lonlar sizga «⚠️» belgisi bilan keladi.',
      cur: () => `\n${s.badWords.join(', ')}`,
      set: (t) => {
        s.badWords = uniq(t.split(/[\n,;]/).map((x) => x.trim().toLowerCase()).filter(Boolean)).slice(0, 200);
        return '';
      },
    },
    maxPerDay: {
      back: 'set',
      q: '🔢 Bir kishi kuniga nechta vakansiya yubora oladi? (1–100)',
      cur: () => String(s.maxPerDay),
      set: (t) => { s.maxPerDay = intIn(t, 1, 100); return ''; },
    },
    topCredits: {
      back: 'sp',
      q: '🔥 TOP e’lon paketdan necha kredit turadi? (1–10)',
      cur: () => String(s.topCredits),
      set: (t) => { s.topCredits = intIn(t, 1, 10); return ''; },
    },
    tplDesc: {
      back: 'set',
      q: '📝 Rezyume shablonlari tavsifini yozing (300 belgigacha):',
      cur: () => s.tplDesc,
      set: (t) => {
        if (t.length < 5) throw new Error('Kamida 5 belgi yozing.');
        s.tplDesc = t.slice(0, 300);
        return '';
      },
    },
    tpl: {
      back: 'set',
      file: true,
      q: '📁 Rezyume shablonlari faylini yuboring (ZIP, Word yoki PDF). Sotib olganlarga aynan shu fayl yuboriladi.\nO‘chirish uchun: <code>-</code>',
      cur: () => (s.tplFileId ? (s.tplName || 'yuklangan') : 'yuklanmagan'),
    },
    resDbDays: {
      back: 'sh',
      q: '🗂 Rezyume nomzodlar bazasida necha kun tursin? (1–365)',
      cur: () => `${s.resDbDays} kun`,
      set: (t) => { s.resDbDays = intIn(t, 1, 365); return ''; },
    },
    candDays: {
      back: 'sh',
      q: '🗂 Nomzodlar bazasiga kirish necha kunga beriladi? (1–365)',
      cur: () => `${s.candDays} kun`,
      set: (t) => { s.candDays = intIn(t, 1, 365); return ''; },
    },
    remindHours: {
      back: 'sh',
      q: '⏰ Muddat tugashidan necha soat oldin uzaytirish eslatmasi yuborilsin? (0 — yubormaslik, 47 gacha)',
      cur: () => `${s.remindHours} soat`,
      set: (t) => { s.remindHours = intIn(t, 0, 47); return ''; },
    },
  };
  return E[key] || null;
}

async function editStart(u, q, key, note = '') {
  const e = getEdit(key);
  if (!e) return ui.answer(q);
  const s = db.resetSess(u.id);
  s.flow = 'adm:set';
  s.key = key;
  db.markDirty();
  const cur = e.cur ? e.cur() : '';
  return ui.screen(u.id, q, `${note ? `${note}\n\n` : ''}${e.q}${cur ? `\n\nHozirgi: ${esc(cur)}` : ''}`, ikb([[['⬅️ Bekor qilish', `a:${e.back}`]]]));
}

async function backTo(u, back, note) {
  if (back === 'sp') return pricesView(u, null, note);
  if (back === 'sh') return hoursView(u, null, note);
  return settingsView(u, null, note);
}

async function setInput(u, s, m) {
  const key = s.key;
  const e = getEdit(key || '');
  if (!e) { db.resetSess(u.id); return panel(u, null); }
  if (e.file) {
    const t = (m.text || '').trim();
    let fileId = null;
    let name = '';
    let type = 'document';
    if (m.document) { fileId = m.document.file_id; name = m.document.file_name || 'fayl'; } else if (m.photo && m.photo.length) { fileId = m.photo[m.photo.length - 1].file_id; name = 'rasm'; type = 'photo'; }
    if (t === '-') {
      await ui.delInput(m);
      S().tplFileId = '';
      S().tplName = '';
      db.markDirty();
      db.resetSess(u.id);
      return settingsView(u, null, '✅ Shablon fayli o‘chirildi.');
    }
    if (!fileId) { await ui.delInput(m); return editStart(u, null, key, '⚠️ Faylni hujjat sifatida yuboring.'); }
    S().tplFileId = fileId;
    S().tplName = name;
    S().tplType = type;
    db.markDirty();
    db.resetSess(u.id);
    return settingsView(u, null, `✅ Shablon fayli saqlandi: ${esc(name)}`);
  }
  const t = (m.text || '').trim();
  await ui.delInput(m);
  if (!t) return editStart(u, null, key, '⚠️ Matn yozing.');
  try {
    const note = await e.set(t, u);
    db.markDirty();
    db.resetSess(u.id);
    return backTo(u, e.back, `✅ Saqlandi.${note ? `\n${note}` : ''}`);
  } catch (err) {
    return editStart(u, null, key, `⚠️ ${esc(err.message)}`);
  }
}

async function checkChannelAdmin() {
  const s = S();
  if (!s.channel) return '⚠️ Majburiy obuna ishlashi uchun avval kanalni ulang.';
  const mem = await tg.call('getChatMember', { chat_id: s.channel, user_id: core.me.id }).catch(() => null);
  if (!mem || mem.status !== 'administrator') return '⚠️ Bot kanalda admin emas, obunani tekshira olmaydi. Botni kanalga admin qiling.';
  return '';
}

async function toggle(u, q, key) {
  const s = S();
  if (key === 'digest') s.digest.on = !s.digest.on;
  else if (typeof s[key] === 'boolean') s[key] = !s[key];
  else return ui.answer(q);
  db.markDirty();
  let note = '';
  if (key === 'payments' && s.payments) {
    if (!s.card && !s.stars) note = '⚠️ To‘lov yoqildi, lekin karta ham, Stars ham sozlanmagan. «🏦 Karta raqami»ni kiriting.';
    else if (!s.card) note = 'ℹ️ Hozircha faqat Stars orqali to‘lov qabul qilinadi. Karta orqali ham qabul qilish uchun «🏦 Karta raqami»ni kiriting.';
  }
  if (key === 'forceSub' && s.forceSub) note = await checkChannelAdmin();
  await ui.answer(q, '✅ Saqlandi');
  return settingsView(u, q, note);
}

// ================================================================ hammaga xabar
async function bcStart(u, q) {
  const s = db.resetSess(u.id);
  s.flow = 'adm:bc';
  db.markDirty();
  return ui.screen(u.id, q, '📢 Hammaga yuboriladigan xabarni yuboring: matn, rasm yoki video.\nXabar aynan shu ko‘rinishda yuboriladi.', ikb([[['⬅️ Bekor qilish', 'a:menu']]]));
}

async function bcConfirm(u, s, m) {
  s.bc = { from: u.id, msg: m.message_id };
  s.flow = 'adm:bc2';
  db.markDirty();
  const n = Object.values(st().users).filter((x) => !x.blocked && !x.inactive).length;
  return ui.ask(u.id, `📢 Yuqoridagi xabar <b>${n}</b> ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?`, ikb([[['✅ Yuborish', 'a:bcgo'], ['❌ Bekor qilish', 'a:menu']]]));
}

async function bcGo(u, q) {
  const s = db.sess(u.id);
  const bc = s.bc;
  if (!bc) return ui.answer(q, 'Xabar topilmadi.', true);
  db.resetSess(u.id);
  const targets = Object.values(st().users).filter((x) => !x.blocked && !x.inactive).map((x) => x.id);
  await ui.answer(q, '⏳ Yuborilmoqda...');
  await ui.screen(u.id, q, `⏳ Yuborilmoqda: ${targets.length} ta foydalanuvchi...`, null);
  const run = (async () => {
    let ok = 0;
    let fail = 0;
    for (const id of targets) {
      try {
        await tg.call('copyMessage', { chat_id: id, from_chat_id: bc.from, message_id: bc.msg });
        ok += 1;
      } catch (e) {
        fail += 1;
        if (e.code === 403) { const x = st().users[id]; if (x) x.inactive = true; }
      }
      await sleep(45);
    }
    db.markDirty();
    await core.safeSend(u.id, `✅ Xabar yuborildi: ${ok} ta.${fail ? ` Yetib bormadi: ${fail} ta.` : ''}`, ikb([[['⚙️ Admin panel', 'a:menu']]]));
  })().catch((e) => console.error('[hammaga]', e));
  if (cfg.TEST) await run;
  return null;
}

// ================================================================ bloklash
async function blkStart(u, q, note = '') {
  const s = db.resetSess(u.id);
  s.flow = 'adm:blk';
  db.markDirty();
  const blocked = Object.values(st().users).filter((x) => x.blocked).slice(0, 10);
  const L = [];
  if (note) L.push(note, '');
  L.push('🚫 Bloklash yoki blokdan chiqarish uchun foydalanuvchi ID raqamini yoki @username yuboring.');
  if (blocked.length) {
    L.push('', 'Bloklanganlar:');
    blocked.forEach((x) => L.push(`• ${esc(x.name)}${x.username ? ` @${esc(x.username)}` : ''} — <code>${x.id}</code>`));
  }
  return ui.screen(u.id, q, L.join('\n'), ikb([[['⬅️ Orqaga', 'a:menu']]]));
}

async function blkInput(u, s, m) {
  const t = (m.text || '').trim();
  await ui.delInput(m);
  const target = db.findUser(t);
  if (!target) return blkStart(u, null, '⚠️ Foydalanuvchi topilmadi. U botdan foydalangan bo‘lishi kerak.');
  db.resetSess(u.id);
  if (db.isAdmin(target.id)) return panel(u, null, '⚠️ Adminni bloklab bo‘lmaydi.');
  if (target.blocked) {
    target.blocked = false;
    db.markDirty();
    return panel(u, null, `✅ ${esc(target.name)} blokdan chiqarildi.`);
  }
  await core.blockUser(target, u);
  return panel(u, null, `🚫 ${esc(target.name)} bloklandi.`);
}

async function blockToggle(u, q, uid) {
  const target = st().users[uid];
  if (!target) return ui.answer(q, 'Foydalanuvchi topilmadi.', true);
  if (db.isAdmin(target.id)) return ui.answer(q, 'Adminni bloklab bo‘lmaydi.', true);
  if (target.blocked) {
    target.blocked = false;
    db.markDirty();
    return ui.answer(q, '✅ Blokdan chiqarildi', true);
  }
  await core.blockUser(target, u);
  return ui.answer(q, '🚫 Bloklandi. Tekshiruvdagi e’lonlari rad etildi.', true);
}

// ================================================================ zaxira
async function bakView(u, q, note = '') {
  const ds = db.status;
  const L = [];
  if (note) L.push(note, '');
  L.push('💾 <b>Zaxira</b>', '', `Baza: ${bazaLine()}`);
  if (ds.size) L.push(`Hajmi: ${Math.max(1, Math.ceil(ds.size / 1024))} KB`);
  L.push('', 'Bot barcha ma’lumotlarni yopiq baza kanaliga o‘zi saqlab boradi, har kuni alohida nusxa ham qoldiradi.');
  return ui.screen(u.id, q, L.join('\n'), ikb([
    [['💾 Hozir saqlash', 'a:bks']],
    [['📤 Faylni olish', 'a:bkd']],
    [['📥 Fayldan tiklash', 'a:bkr']],
    [['⬅️ Orqaga', 'a:menu']],
  ]));
}

async function restoreInput(u, s, m) {
  if (s.flow === 'adm:rs2') { await ui.delInput(m); return null; }
  if (!m.document) {
    await ui.delInput(m);
    return ui.ask(u.id, '⚠️ Zaxira faylini hujjat sifatida yuboring (.json.gz).', ikb([[['⬅️ Bekor qilish', 'a:bak']]]));
  }
  try {
    const buf = await tg.download(m.document.file_id);
    const json = buf[0] === 0x1f && buf[1] === 0x8b ? zlib.gunzipSync(buf).toString('utf8') : buf.toString('utf8');
    const o = JSON.parse(json);
    if (!o || !o.users || !o.settings) throw new Error('bu fayl baza zaxirasi emas');
    s.rsFile = m.document.file_id;
    s.flow = 'adm:rs2';
    db.markDirty();
    return ui.ask(u.id, `📥 Faylda: ${Object.keys(o.users).length} ta foydalanuvchi, ${Object.keys(o.vacancies || {}).length} ta vakansiya.\n⚠️ Hozirgi ma’lumotlar shu fayldagilar bilan almashtiriladi. Davom etamizmi?`, ikb([[['✅ Ha, tiklash', 'a:bkrok']], [['⬅️ Bekor qilish', 'a:bak']]]));
  } catch (e) {
    return ui.ask(u.id, `⚠️ Faylni o‘qib bo‘lmadi: ${esc(e.message)}`, ikb([[['⬅️ Orqaga', 'a:bak']]]));
  }
}

// ================================================================ yozma javoblar
async function input(u, s, m) {
  if (s.flow === 'adm:login') return loginInput(u, s, m);
  if (!db.isAdmin(u.id)) {
    db.resetSess(u.id);
    return ui.showMenu(u.id, 'Menyudan tanlang 👇');
  }
  switch (s.flow) {
    case 'adm:set': return setInput(u, s, m);
    case 'adm:rej': {
      const t = (m.text || '').trim();
      await ui.delInput(m);
      if (t.length < 2) return askReason(u, s.rk, s.rid, '⚠️ Sababni yozing.');
      return doReject(u, clip(t, 300));
    }
    case 'adm:bc': return bcConfirm(u, s, m);
    case 'adm:bc2': await ui.delInput(m); return null;
    case 'adm:blk': return blkInput(u, s, m);
    case 'adm:rs':
    case 'adm:rs2': return restoreInput(u, s, m);
    default:
      db.resetSess(u.id);
      return panel(u, null);
  }
}

const REASONS = ['Ma’lumot to‘liq emas', 'Qoidalarga zid', 'Shubhali e’lon', 'Takroriy e’lon', 'Noto‘g‘ri soha yoki hudud'];

async function askReason(u, kind, id, note = '') {
  const s = db.resetSess(u.id);
  s.flow = 'adm:rej';
  s.rk = kind;
  s.rid = id;
  db.markDirty();
  const rows = REASONS.map((r, i) => [[r, `a:rs:${i}`]]);
  rows.push([['⬅️ Bekor qilish', 'a:x']]);
  return ui.ask(u.id, `${note ? `${note}\n\n` : ''}❌ ${kind === 'vac' ? 'Vakansiya' : 'Rezyume'} #${id} — rad etish sababini tanlang yoki o‘zingiz yozing:`, ikb(rows));
}

async function doReject(u, reason) {
  const s = db.sess(u.id);
  const kind = s.rk;
  const id = s.rid;
  db.resetSess(u.id);
  let ok = false;
  if (kind === 'vac') { const v = st().vacancies[id]; if (v) ok = await core.rejectVacancy(v, reason, u); } else { const r = st().resumes[id]; if (r) ok = await core.rejectResume(r, reason, u); }
  return ui.ask(u.id, ok ? `❌ #${id} rad etildi. Sabab egasiga yuborildi.` : `ℹ️ #${id} allaqachon ko‘rib chiqilgan.`, ikb([[['📥 Tekshiruv', 'a:mod'], ['⚙️ Panel', 'a:menu']]]));
}

// ================================================================ tugmalar
async function callback(u, q, data) {
  const parts = data.split(':');
  const a = parts[1];
  const b = parts[2];
  const c = parts.slice(2).join(':');
  if (a === 'x') {
    await ui.answer(q);
    db.resetSess(u.id);
    if (db.isAdmin(u.id)) return panel(u, q);
    await ui.clearPrompts(u.id);
    return ui.showMenu(u.id, 'Menyudan tanlang 👇');
  }
  if (a === 'login') { await ui.answer(q); return command(u); }
  if (!db.isAdmin(u.id)) return ui.answer(q, '🔐 Avval /admin orqali kiring.', true);
  const id = Number(b);
  switch (a) {
    case 'menu': db.resetSess(u.id); return panel(u, q);
    case 'mod': return modList(u, q);
    case 'pays': return payList(u, q);
    case 'stats': return stats(u, q);
    case 'vv': { const v = st().vacancies[id]; if (!v) return ui.answer(q, 'Topilmadi.', true); await ui.answer(q); return core.sendVacCard(u.id, v); }
    case 'rv': { const r = st().resumes[id]; if (!r) return ui.answer(q, 'Topilmadi.', true); await ui.answer(q); return core.sendResCard(u.id, r); }
    case 'pv': { const p = st().payments[id]; if (!p) return ui.answer(q, 'Topilmadi.', true); await ui.answer(q); return core.sendPayCard(u.id, p); }
    case 'vok': {
      const v = st().vacancies[id];
      if (!v) return ui.answer(q, 'Topilmadi.', true);
      const ok = await core.approveVacancy(v, u);
      return ui.answer(q, ok ? '✅ Tasdiqlandi' : 'Allaqachon ko‘rib chiqilgan', !ok);
    }
    case 'vrj': {
      const v = st().vacancies[id];
      if (!v || v.status !== 'moderation') return ui.answer(q, 'Allaqachon ko‘rib chiqilgan.', true);
      await ui.answer(q);
      return askReason(u, 'vac', id);
    }
    case 'vbp': {
      const v = st().vacancies[id];
      const ok = v ? await core.badChekVacancy(v, u) : false;
      return ui.answer(q, ok ? '🧾 Chek rad etildi' : 'Allaqachon ko‘rib chiqilgan', !ok);
    }
    case 'rok': {
      const r = st().resumes[id];
      const ok = r ? await core.approveResume(r, u) : false;
      return ui.answer(q, ok ? '✅ Tasdiqlandi' : 'Allaqachon ko‘rib chiqilgan', !ok);
    }
    case 'rrj': {
      const r = st().resumes[id];
      if (!r || r.status !== 'moderation') return ui.answer(q, 'Allaqachon ko‘rib chiqilgan.', true);
      await ui.answer(q);
      return askReason(u, 'res', id);
    }
    case 'rbp': {
      const r = st().resumes[id];
      const ok = r ? await core.badChekResume(r, u) : false;
      return ui.answer(q, ok ? '🧾 Chek rad etildi' : 'Allaqachon ko‘rib chiqilgan', !ok);
    }
    case 'pok': {
      const p = st().payments[id];
      const ok = p ? await core.approvePayment(p, u) : false;
      return ui.answer(q, ok ? '✅ To‘lov tasdiqlandi' : 'Allaqachon ko‘rib chiqilgan', !ok);
    }
    case 'prj': {
      const p = st().payments[id];
      const ok = p ? await core.rejectPayment(p, u) : false;
      return ui.answer(q, ok ? '❌ Rad etildi' : 'Allaqachon ko‘rib chiqilgan', !ok);
    }
    case 'rs': {
      const s = db.sess(u.id);
      if (s.flow !== 'adm:rej') return ui.answer(q, 'Eskirgan tugma.');
      await ui.answer(q);
      return doReject(u, REASONS[id] || 'Qoidalarga zid');
    }
    case 'blk': return blockToggle(u, q, id);
    case 'blkp': return blkStart(u, q);
    case 'bc': return bcStart(u, q);
    case 'bcgo': return bcGo(u, q);
    case 'set': db.resetSess(u.id); return settingsView(u, q);
    case 'sp': db.resetSess(u.id); return pricesView(u, q);
    case 'sh': db.resetSess(u.id); return hoursView(u, q);
    case 't': return toggle(u, q, b);
    case 'e': return editStart(u, q, c);
    case 'bak': db.resetSess(u.id); return bakView(u, q);
    case 'bks':
      db.markDirty();
      await db.flush();
      await ui.answer(q, db.status.error ? '⚠️ Saqlashda xato' : '✅ Saqlandi');
      return bakView(u, q, db.status.error ? `⚠️ ${esc(db.status.error)}` : '✅ Saqlandi.');
    case 'bkd': {
      await ui.answer(q);
      const gz = db.exportGz();
      await tg.call('sendDocument', { chat_id: u.id, caption: `💾 Baza nusxasi · ${clock.fmtDT()}` }, { document: { data: gz, name: `baza_${clock.dayKey()}.json.gz`, type: 'application/gzip' } });
      return null;
    }
    case 'bkr': {
      const s = db.resetSess(u.id);
      s.flow = 'adm:rs';
      db.markDirty();
      return ui.screen(u.id, q, '📥 Zaxira faylini (.json.gz) yuboring.\n⚠️ Hozirgi ma’lumotlar shu fayldagilar bilan almashtiriladi.', ikb([[['⬅️ Bekor qilish', 'a:bak']]]));
    }
    case 'bkrok': {
      const s = db.sess(u.id);
      if (!s.rsFile) return ui.answer(q, 'Fayl topilmadi.', true);
      await ui.answer(q, '⏳ Tiklanmoqda...');
      const buf = await tg.download(s.rsFile);
      db.importData(buf);
      await db.flush();
      return panel(u, q, '✅ Ma’lumotlar zaxiradan tiklandi.');
    }
    case 'out':
      delete st().admins[u.id];
      db.resetSess(u.id);
      db.markDirty();
      await ui.answer(q, '🚪 Chiqdingiz');
      await ui.clearPrompts(u.id);
      return ui.showMenu(u.id, 'Admin paneldan chiqdingiz.');
    default:
      return ui.answer(q);
  }
}

module.exports = { init, command, input, callback };
