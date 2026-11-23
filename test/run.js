'use strict';
// To‘liq sinov: soxta Telegram server bilan botning barcha asosiy oqimlari.
const MockTG = require('./mock-tg');
const clock = require('../src/clock');

let passed = 0;
function check(cond, msg, extra) {
  if (!cond) {
    console.error(`❌ ${msg}`);
    if (extra) console.error(String(extra).slice(0, 3000));
    process.exit(1);
  }
  passed += 1;
}

(async () => {
  // 2026-11-23 10:00 Toshkent vaqti
  clock.set(Date.UTC(2026, 10, 23, 5, 0, 0));
  const mock = new MockTG(clock);
  const base = await mock.start();
  Object.assign(process.env, {
    NODE_ENV: 'test',
    BOT_TOKEN: '123:TEST',
    TG_API: base,
    BAZA_KANAL: '-100500',
    KANAL: '@ish_kanal',
    ADMIN_LOGIN: 'testadmin',
    ADMIN_PASSWORD: 'testparol1',
    GEMINI_API_KEY: 'test-key',
    GEMINI_BASE: `${base}/gemini`,
  });

  const db = require('../src/db');
  const core = require('../src/core');
  const bot = require('../src/bot');
  const admin = require('../src/admin');
  const scheduler = require('../src/scheduler');
  const { BTN } = require('../src/ui');

  core.me.id = mock.bot.id;
  core.me.username = mock.bot.username;
  await db.init();
  admin.init();
  check(db.settings().channel === '@ish_kanal', 'kanal KANAL dan olinishi kerak');
  check(db.settings().forceSub === true, 'majburiy obuna standart yoqilgan bo‘lishi kerak');

  const E = { id: 1001, is_bot: false, first_name: 'Ali', last_name: 'Valiyev', username: 'ali_hr' };
  const K = { id: 1002, is_bot: false, first_name: 'Bobur', username: 'bobur' };
  const A = { id: 1003, is_bot: false, first_name: 'Behruz', username: 'behruz' };
  const Z = { id: 1004, is_bot: false, first_name: 'Zarina' };
  let upd = 1;
  let cbn = 1;

  const say = (u, text, extra = {}) => {
    const chat = { id: u.id, type: 'private', first_name: u.first_name };
    const m = mock.addMsg(chat, { from: u, text, ...extra });
    if (text === undefined) delete m.text;
    return bot.handleUpdate({ update_id: upd++, message: m });
  };
  const press = async (u, data) => {
    const msg = mock.findButtonMsg(u.id, data);
    check(!!msg, `Tugma topilmadi: ${data}`, `Oxirgi xabarlar:\n${mock.allText(u.id).slice(-2500)}`);
    await bot.handleUpdate({ update_id: upd++, callback_query: { id: String(cbn++), from: u, chat_instance: 'x', message: msg, data } });
    return msg;
  };
  const screenOf = (u, msg) => (mock.getMsg(u.id, msg.message_id) || {}).text || '';
  const last = (u) => mock.lastText(u.id);
  const vac = (id) => db.state().vacancies[id];
  const kanal = () => mock.messages('@ish_kanal');
  const tick = () => scheduler.tick();

  // ------------------------------------------------ 1. Majburiy obuna
  await say(E, '/start');
  check(/obuna bo‘ling/.test(last(E)), 'obuna bo‘lmaganga obuna so‘ralishi kerak', last(E));
  await press(E, 'chk');
  check(mock.callsOf('answerCallbackQuery').some((c) => c.p.show_alert && /obuna bo‘lmadingiz/.test(c.p.text)), 'obuna bo‘lmasa ogohlantirish chiqishi kerak');
  mock.setMember('@ish_kanal', E.id, 'member');
  await press(E, 'chk');
  check(/Assalomu alaykum/.test(mock.allText(E.id)), 'obunadan keyin salom xabari chiqishi kerak', mock.allText(E.id));
  check(mock.lastBot(E.id).reply_markup.keyboard, 'asosiy menyu tugmalari bo‘lishi kerak');

  // ------------------------------------------------ 2. Vakansiya joylash (bepul rejim)
  await say(E, BTN.post);
  check(/1\/8/.test(last(E)), 'birinchi savol chiqishi kerak', last(E));
  await say(E, 'Sotuvchi');
  check(/2\/8/.test(last(E)), 'soha savoli', last(E));
  await press(E, 'f:o:0');
  await press(E, 'f:o:10'); // Samarqand
  check(/4\/8/.test(last(E)), 'ish joyi savoli', last(E));
  await say(E, 'Makro');
  await press(E, 'f:q:0'); // Kelishiladi
  await say(E, '09:00–18:00');
  await say(E, 'Tajriba 1 yil, viza kerak emas, rus tili');
  check(/8\/8/.test(last(E)), 'aloqa savoli', last(E));
  await press(E, 'f:c:text');
  await say(E, '+998 90 123 45 67');
  check(/kanalda shunday ko‘rinadi/.test(last(E)) && /Makro/.test(last(E)) && /Kelishiladi/.test(last(E)) && /#Samarqand/.test(last(E)), 'ko‘rinish to‘g‘ri bo‘lishi kerak', last(E));
  // Chat tozalanishi: savollar o‘chgan bo‘lishi kerak
  const eVisible = mock.botMessages(E.id).filter((m) => /\/8/.test(m.text || ''));
  check(eVisible.length === 0, 'eski savollar o‘chirilishi kerak', eVisible.map((m) => m.text).join('\n'));
  check(mock.messages(E.id).filter((m) => m.from && m.from.id === E.id && m.text === 'Makro').length === 0, 'foydalanuvchi javoblari o‘chirilishi kerak');
  // AI
  await press(E, 'f:ai');
  check(/AI «Talablar va vazifalar»/.test(last(E)) && /• 1 yil tajriba/.test(last(E)), 'AI matnni yaxshilashi kerak', last(E));
  await press(E, 'f:aiu');
  check(/viza kerak emas/.test(last(E)), 'eski matnga qaytish ishlashi kerak', last(E));
  // Tahrirlash
  await press(E, 'f:e');
  await press(E, 'f:ef:4');
  await say(E, '5 mln so‘m');
  check(/5 mln so‘m/.test(last(E)) && /kanalda shunday/.test(last(E)), 'tahrirlashdan keyin ko‘rinishga qaytishi kerak', last(E));
  await press(E, 'f:ok');
  check(vac(1) && vac(1).status === 'moderation', 'vakansiya tekshiruvga tushishi kerak', JSON.stringify(vac(1)));
  check(vac(1).flags.includes('viza'), 'shubhali so‘z topilishi kerak');
  check(/tekshiruvga yuborildi/.test(last(E)), 'egaga xabar', last(E));

  // ------------------------------------------------ 3. Admin kirish (iPhone katta harf bilan)
  mock.setMember('@ish_kanal', A.id, 'member');
  await say(A, '/admin');
  check(/Loginni yozing/.test(last(A)), 'login so‘ralishi kerak', last(A));
  await say(A, 'Testadmin');
  await say(A, 'Testparol1');
  check(/Admin panel/.test(last(A)) && /Tekshiruvda: <b>1<\/b>/.test(last(A)), 'admin panel ochilishi kerak', last(A));
  check(mock.messages(A.id).every((m) => !(m.from && m.from.id === A.id && /Testadmin|Testparol1/.test(m.text || ''))), 'login/parol xabarlari o‘chirilishi kerak');
  await press(A, 'a:mod');
  await press(A, 'a:vv:1');
  check(/Shubhali so‘zlar:<\/b> viza/.test(last(A)), 'admin kartasida shubhali so‘z ko‘rinishi kerak', last(A));
  await press(A, 'a:vok:1');
  check(vac(1).status === 'queued', 'tasdiqlangach navbatga tushishi kerak');
  check(/tasdiqlandi/.test(mock.allText(E.id)), 'egaga tasdiq xabari');

  // ------------------------------------------------ 4. Obuna (ish qidiruvchi)
  mock.setMember('@ish_kanal', K.id, 'member');
  await say(K, '/start');
  await say(K, BTN.subs);
  await press(K, 's:tc');
  await press(K, 's:c:0');
  await press(K, 's:v');
  check(db.state().users[K.id].subs.on && db.state().users[K.id].subs.cats[0] === 'Savdo va sotuv', 'obuna saqlanishi kerak');

  // ------------------------------------------------ 5. Kanalga chiqish
  await tick();
  check(vac(1).status === 'published' && vac(1).msgId, 'kanalga chiqishi kerak', JSON.stringify(vac(1)));
  const post1 = mock.getMsg('@ish_kanal', vac(1).msgId);
  check(/💼 <b>Sotuvchi<\/b>/.test(post1.text) && post1.reply_markup.inline_keyboard[0][0].url === 'https://t.me/ish_test_bot?start=a_1', 'kanal posti va ariza tugmasi', JSON.stringify(post1));
  check(/kanalga chiqdi/.test(mock.allText(E.id)), 'egaga «kanalga chiqdi» xabari');
  await core.outboxIdle();
  check(/Yangi vakansiya/.test(mock.allText(K.id)), 'obunachiga xabar borishi kerak', mock.allText(K.id));

  // ------------------------------------------------ 6. Ariza
  await say(K, '/start a_1');
  check(/1\/3/.test(last(K)), 'ariza: ism savoli', last(K));
  await say(K, 'Bobur Karimov');
  check(mock.lastBot(K.id).reply_markup.keyboard && mock.lastBot(K.id).reply_markup.keyboard[0][0].request_contact, 'telefon tugmasi chiqishi kerak');
  await say(K, undefined, { contact: { user_id: K.id, phone_number: '998901112233', first_name: 'Bobur' } });
  check(db.state().users[K.id].phoneVerified, 'telefon tasdiqlanishi kerak');
  check(/3\/3/.test(last(K)), 'ariza: ma’lumot savoli', last(K));
  await say(K, '2 yil sotuvchi bo‘lib ishlaganman');
  await press(K, 'ap:ok');
  check(Object.values(db.state().apps).length === 1 && vac(1).apps === 1, 'ariza saqlanishi kerak');
  check(/Yangi ariza/.test(mock.allText(E.id)) && /Bobur Karimov/.test(mock.allText(E.id)), 'ish beruvchiga ariza borishi kerak');
  await say(K, '/start a_1');
  check(/allaqachon ariza/.test(last(K)), 'takroriy ariza bloklanishi kerak', last(K));
  await say(E, '/start a_1');
  check(/sizning e’loningiz/.test(last(E)), 'o‘z e’loniga ariza bermasligi kerak', last(E));

  // ------------------------------------------------ 7. Mening e’lonlarim
  await say(E, BTN.my);
  await press(E, 'my:v:1');
  check(/Arizalar: 1/.test(last(E)), 'arizalar soni', last(E));
  await press(E, 'my:a:1');
  check(/Bobur Karimov/.test(last(E)), 'arizalar ro‘yxati', last(E));

  // ------------------------------------------------ 8. Eslatma va uzaytirish, keyin muddati tugashi
  clock.set(vac(1).expiresAt - 2 * clock.HOUR);
  await tick();
  check(/muddati ~2 soatdan keyin tugaydi/.test(mock.allText(E.id)), 'uzaytirish eslatmasi', mock.allText(E.id).slice(-800));
  const before = vac(1).expiresAt;
  await press(E, 'ext:1');
  check(vac(1).expiresAt === before + 48 * clock.HOUR, 'bepul uzaytirish 48 soat qo‘shishi kerak');
  clock.set(vac(1).expiresAt + 1000);
  await tick();
  check(vac(1).status === 'expired' && !vac(1).deleted, '48 soatdan keyin o‘chirilmaydi, belgilanadi', JSON.stringify(vac(1)));
  check(/Muddati tugagan/i.test(mock.getMsg('@ish_kanal', vac(1).msgId).text), 'kanal posti «muddati tugagan» bo‘lishi kerak');

  // ------------------------------------------------ 9. Navbat oralig‘i va 48 soat ichida o‘chirish
  clock.set(Date.UTC(2026, 10, 26, 5, 0, 0)); // 26-noyabr 10:00
  const quickPost = async (u, title, catIdx = 1) => {
    await say(u, BTN.post);
    await say(u, title);
    await press(u, `f:o:${catIdx}`);
    await press(u, 'f:o:0');
    await press(u, 'f:skip');
    await say(u, '3 mln');
    await press(u, 'f:q:0');
    await say(u, 'Kompyuterni bilish, mas’uliyat');
    await press(u, u.username ? 'f:c:user' : 'f:c:bot');
    await press(u, 'f:ok');
    return Object.values(db.state().vacancies).sort((a, b) => b.id - a.id)[0];
  };
  const v2 = await quickPost(E, 'Administrator');
  check(v2.status === 'moderation' && v2.contactMode === 'user', 'ikkinchi vakansiya', JSON.stringify(v2));
  check(!!mock.findButtonMsg(A.id, `a:vok:${v2.id}`), 'admin kartasi avtomatik kelishi kerak');
  await press(A, `a:vok:${v2.id}`);
  const v3 = await quickPost(E, 'Operator');
  await press(A, `a:vok:${v3.id}`);
  db.state().meta.lastPostAt = clock.now() - 60e3; // 1 daqiqa oldin post chiqqan
  await tick();
  check(v2.status === 'queued', 'oraliq tugamaguncha chiqmasligi kerak');
  clock.advance(10 * clock.MIN);
  await tick();
  check(v2.status === 'published' && v3.status === 'queued', 'navbat bilan bittadan chiqishi kerak');
  clock.advance(10 * clock.MIN);
  await tick();
  check(v3.status === 'published', 'keyingisi 10 daqiqadan keyin chiqishi kerak');
  clock.set(v2.expiresAt + 1000);
  await tick();
  check(v2.status === 'expired' && v2.deleted === true, '48 soatgacha bo‘lsa o‘chirilishi kerak', JSON.stringify(v2));
  check(mock.getMsg('@ish_kanal', v2.msgId).deleted, 'kanal posti o‘chgan bo‘lishi kerak');

  // ------------------------------------------------ 10. Kechasi chiqmaydi
  clock.set(Date.UTC(2026, 10, 28, 18, 0, 0)); // 23:00
  const v4 = await quickPost(E, 'Qorovul');
  await press(A, `a:vok:${v4.id}`);
  check(/ertalab soat 8:00/.test(mock.allText(E.id)), 'kechasi tasdiqlansa ertalab chiqishi aytilishi kerak');
  await tick();
  check(v4.status === 'queued', 'kechasi kanalga chiqmasligi kerak');
  clock.set(Date.UTC(2026, 10, 29, 3, 5, 0)); // 08:05
  await tick();
  check(v4.status === 'published', 'ertalab chiqishi kerak');

  // ------------------------------------------------ 11. Yopish
  await say(E, BTN.my);
  await press(E, `my:v:${v4.id}`);
  await press(E, `my:c:${v4.id}`);
  await press(E, `my:cc:${v4.id}`);
  check(v4.status === 'closed' && /YOPILDI/.test(mock.getMsg('@ish_kanal', v4.msgId).text), 'yopilgan e’lon kanalda belgilanishi kerak');

  // ------------------------------------------------ 12. To‘lovni yoqish va karta
  await say(A, '/admin');
  await press(A, 'a:set');
  await press(A, 'a:t:payments');
  check(db.settings().payments === true, 'to‘lov yoqilishi kerak');
  await press(A, 'a:e:card');
  await say(A, '8600 1234 5678 9012 Ali Valiyev');
  check(db.settings().card === '8600 1234 5678 9012' && db.settings().cardOwner === 'Ali Valiyev', 'karta saqlanishi kerak', JSON.stringify([db.settings().card, db.settings().cardOwner]));
  await press(A, 'a:e:queue');
  await say(A, '0 8 22');
  check(db.settings().queue.interval === 0, 'navbat sozlamasi saqlanishi kerak');

  // TOP vakansiya karta orqali
  clock.set(Date.UTC(2026, 10, 30, 5, 0, 0));
  await say(E, BTN.post);
  await say(E, 'Bosh oshpaz');
  await press(E, 'f:o:7');
  await press(E, 'f:o:0');
  await say(E, 'Rayhon restorani');
  await say(E, '8 mln');
  await press(E, 'f:q:0');
  await say(E, 'Milliy taomlar, 3 yil tajriba');
  await press(E, 'f:c:bot');
  await press(E, 'f:ok');
  const v5 = Object.values(db.state().vacancies).sort((a, b) => b.id - a.id)[0];
  check(v5.status === 'draft' && /tarifni tanlang/.test(last(E)), 'tarif tanlash chiqishi kerak', last(E));
  await press(E, `t:t:${v5.id}`);
  const pay5 = Object.values(db.state().payments).sort((a, b) => b.id - a.id)[0];
  check(pay5.uzs === 50000, 'TOP narxi 50 000');
  await press(E, `p:c:${pay5.id}`);
  check(/8600 1234 5678 9012/.test(last(E)), 'karta raqami ko‘rsatilishi kerak', last(E));
  await say(E, undefined, { photo: [{ file_id: 'small' }, { file_id: 'CHEK1' }] });
  check(pay5.status === 'pending' && v5.status === 'moderation', 'chekdan keyin tekshiruvga tushishi kerak');
  check(mock.messages(A.id).some((m) => m.photo && m.photo[0].file_id === 'CHEK1'), 'adminga chek rasmi borishi kerak');
  check(!!mock.findButtonMsg(A.id, `a:vbp:${v5.id}`), 'adminda «Chek noto‘g‘ri» tugmasi bo‘lishi kerak');
  await press(A, `a:vok:${v5.id}`);
  check(pay5.status === 'approved' && db.state().stats.uzs === 50000, 'to‘lov tasdiqlanib daromadga qo‘shilishi kerak');
  await tick();
  check(v5.status === 'published' && v5.pinned, 'TOP e’lon chiqib qadalishi kerak', JSON.stringify(v5));
  check(/TOP VAKANSIYA/.test(mock.getMsg('@ish_kanal', v5.msgId).text), 'TOP belgisi');
  const pinsrv = mock.addMsg(mock.resolveChat('@ish_kanal'), { pinned_message: { message_id: v5.msgId } });
  await bot.handleUpdate({ update_id: upd++, channel_post: pinsrv });
  check(mock.getMsg('@ish_kanal', pinsrv.message_id).deleted, '«qadadi» xizmat xabari o‘chirilishi kerak');

  // ------------------------------------------------ 13. Stars bilan paket
  await say(E, BTN.packs);
  await press(E, 'pk:0');
  const payPk = Object.values(db.state().payments).sort((a, b) => b.id - a.id)[0];
  await press(E, `p:s:${payPk.id}`);
  check(mock.callsOf('sendInvoice').length === 1 && mock.callsOf('sendInvoice')[0].p.currency === 'XTR' && mock.callsOf('sendInvoice')[0].p.prices[0].amount === 400, 'Stars hisob-fakturasi');
  await bot.handleUpdate({ update_id: upd++, pre_checkout_query: { id: 'pc1', from: E, currency: 'XTR', total_amount: 400, invoice_payload: `p${payPk.id}` } });
  check(mock.callsOf('answerPreCheckoutQuery').slice(-1)[0].p.ok === true, 'pre-checkout tasdiqlanishi kerak');
  await say(E, undefined, { successful_payment: { currency: 'XTR', total_amount: 400, invoice_payload: `p${payPk.id}`, telegram_payment_charge_id: 'CH1' } });
  check(db.state().users[E.id].credits === 5 && db.state().stats.xtr === 400, 'paket kreditlari qo‘shilishi kerak', JSON.stringify(db.state().users[E.id]));

  // Kreditdan foydalanish va rad etilganda qaytishi
  const v6 = await quickPost(E, 'Yordamchi');
  check(/tarifni tanlang/.test(last(E)) && /Kreditingiz: <b>5<\/b>/.test(last(E)), 'kredit ko‘rinishi kerak', last(E));
  await press(E, `t:co:${v6.id}`);
  check(db.state().users[E.id].credits === 4 && v6.status === 'moderation', 'kredit yechilishi kerak');
  await press(A, `a:vrj:${v6.id}`);
  await press(A, 'a:rs:0');
  check(v6.status === 'rejected' && db.state().users[E.id].credits === 5, 'rad etilganda kredit qaytishi kerak');
  check(/rad etildi/.test(mock.allText(E.id)) && /Ma’lumot to‘liq emas/.test(mock.allText(E.id)), 'sabab egaga borishi kerak');

  // Noto‘g‘ri chek
  const v7 = await quickPost(E, 'Kuryer');
  await press(E, `t:o:${v7.id}`);
  const pay7 = Object.values(db.state().payments).sort((a, b) => b.id - a.id)[0];
  await press(E, `p:c:${pay7.id}`);
  await say(E, undefined, { photo: [{ file_id: 'CHEK2' }] });
  await press(A, `a:vbp:${v7.id}`);
  check(pay7.status === 'rejected' && v7.status === 'draft', 'noto‘g‘ri chekdan keyin qoralama bo‘lishi kerak');
  check(!!mock.findButtonMsg(E.id, `my:p:${v7.id}`), 'qayta to‘lash tugmasi');

  // Kartadan to‘langan e’lon rad etilsa — kredit
  await press(E, `my:p:${v7.id}`);
  await press(E, `t:o:${v7.id}`);
  const pay7b = Object.values(db.state().payments).sort((a, b) => b.id - a.id)[0];
  await press(E, `p:c:${pay7b.id}`);
  await say(E, undefined, { photo: [{ file_id: 'CHEK3' }] });
  const credBefore = db.state().users[E.id].credits;
  await press(A, `a:vrj:${v7.id}`);
  await say(A, 'Telefon raqam noto‘g‘ri');
  check(db.state().users[E.id].credits === credBefore + 1 && pay7b.status === 'approved', 'karta to‘lovi kreditga aylanishi kerak');

  // ------------------------------------------------ 14. Rezyume va nomzodlar bazasi
  await say(K, BTN.seek);
  await press(K, 'rs:n');
  await say(K, 'Bobur Karimov');
  await say(K, '25');
  await press(K, 'f:o:0');
  await press(K, 'f:o:10');
  await say(K, 'Korzinkada 2 yil');
  await press(K, 'f:q:0');
  await say(K, 'Rus va ingliz tili, kassa dasturlari');
  await press(K, 'f:c:phone');
  check(/ISH QIDIRYAPMAN/.test(last(K)), 'rezyume ko‘rinishi', last(K));
  await press(K, 'f:ok');
  const r1 = db.state().resumes[1];
  check(r1 && r1.status === 'moderation', 'rezyume tekshiruvga tushishi kerak', JSON.stringify(r1));
  await press(A, 'a:rok:1');
  await tick();
  check(r1.status === 'published', 'rezyume kanalga chiqishi kerak', JSON.stringify(r1));
  await say(E, BTN.cands);
  check(/Bazada <b>1<\/b>/.test(last(E)), 'nomzodlar soni ko‘rinishi kerak', last(E));
  await press(E, 'cd:buy');
  const payC = Object.values(db.state().payments).sort((a, b) => b.id - a.id)[0];
  await press(E, `p:s:${payC.id}`);
  await bot.handleUpdate({ update_id: upd++, pre_checkout_query: { id: 'pc2', from: E, currency: 'XTR', total_amount: payC.xtr, invoice_payload: `p${payC.id}` } });
  await say(E, undefined, { successful_payment: { currency: 'XTR', total_amount: payC.xtr, invoice_payload: `p${payC.id}`, telegram_payment_charge_id: 'CH2' } });
  check(db.state().users[E.id].candUntil > clock.now(), 'nomzodlar bazasiga kirish ochilishi kerak');
  await press(E, 'cd:o');
  await press(E, 'cd:c:a');
  await press(E, 'cd:r:a');
  check(/Bobur Karimov/.test(last(E)) && /\+998 90 111 22 33/.test(last(E)), 'nomzod ma’lumoti va aloqasi ko‘rinishi kerak', last(E));

  // ------------------------------------------------ 15. Shablonlar
  await say(A, '/admin');
  await press(A, 'a:set');
  await press(A, 'a:e:tpl');
  await say(A, undefined, { document: { file_id: 'TPLZIP', file_name: 'rezyume_shablonlar.zip' } });
  check(db.settings().tplFileId === 'TPLZIP', 'shablon fayli saqlanishi kerak');
  await say(Z, '/start');
  mock.setMember('@ish_kanal', Z.id, 'member');
  await press(Z, 'chk');
  await say(Z, BTN.tpl);
  await press(Z, 'tpl:buy');
  const payT = Object.values(db.state().payments).sort((a, b) => b.id - a.id)[0];
  await press(Z, `p:s:${payT.id}`);
  await say(Z, undefined, { successful_payment: { currency: 'XTR', total_amount: payT.xtr, invoice_payload: `p${payT.id}`, telegram_payment_charge_id: 'CH3' } });
  check(mock.messages(Z.id).some((m) => m.document && m.document.file_id === 'TPLZIP'), 'shablon fayli yuborilishi kerak');

  // ------------------------------------------------ 16. Kechki ro‘yxat
  clock.set(Date.UTC(2026, 10, 30, 15, 5, 0)); // 20:05
  await tick();
  check(kanal().some((m) => /Bugungi vakansiyalar/.test(m.text || '') && /Bosh oshpaz/.test(m.text)), 'kechki ro‘yxat chiqishi kerak', kanal().map((m) => m.text).join('\n---\n').slice(-1500));

  // ------------------------------------------------ 17. Hammaga xabar
  await say(A, '/admin');
  await press(A, 'a:bc');
  await say(A, 'Yangi kanalimizga xush kelibsiz!');
  await press(A, 'a:bcgo');
  check(mock.callsOf('copyMessage').length >= 4, 'hammaga xabar yuborilishi kerak');

  // ------------------------------------------------ 18. Bloklash
  await press(A, 'a:menu');
  await press(A, 'a:blkp');
  await say(A, String(Z.id));
  check(db.state().users[Z.id].blocked, 'bloklanishi kerak');
  await say(Z, BTN.browse);
  check(/cheklangansiz/.test(last(Z)), 'bloklangan foydalanuvchi to‘xtatilishi kerak', last(Z));

  // ------------------------------------------------ 19. Vakansiyalarni ko‘rish
  await say(K, BTN.browse);
  await press(K, 'b:c:a');
  await press(K, 'b:r:a');
  check(/Bosh oshpaz/.test(last(K)), 'qidiruvda faol vakansiya ko‘rinishi kerak', last(K));

  // ------------------------------------------------ 20. Kunlik limit
  db.settings().maxPerDay = 1;
  await say(K, BTN.post);
  await say(K, 'Test');
  await press(K, 'f:o:0');
  await press(K, 'f:o:0');
  await press(K, 'f:skip');
  await say(K, '1 mln');
  await press(K, 'f:q:0');
  await say(K, 'Test matni');
  await press(K, 'f:c:bot');
  await press(K, 'f:ok');
  await press(K, `t:o:${Object.values(db.state().vacancies).sort((a, b) => b.id - a.id)[0].id}`);
  const cntK = Object.values(db.state().vacancies).filter((v) => v.ownerId === K.id).length;
  check(cntK === 1, 'birinchi e’lon yaratilishi kerak');
  db.settings().maxPerDay = 5;

  // ------------------------------------------------ 21. Zaxira va tiklash
  db.markDirty();
  await db.flush();
  check(!db.status.error && db.status.lastOk, 'zaxira xatosiz saqlanishi kerak', db.status.error);
  const pinned1 = mock.pins.get('-100500');
  check(pinned1 && pinned1.length === 1, 'baza fayli qadalgan bo‘lishi kerak');
  db.state().meta.testMarker = 'salom';
  db.markDirty();
  await db.flush();
  check(mock.callsOf('editMessageMedia').length >= 1, 'keyingi saqlashda xabar tahrirlanishi kerak');
  const snapshot = JSON.stringify(db.state());
  await db.init(); // server qayta yongandek
  check(JSON.stringify(db.state()) === snapshot, 'bazadan to‘liq tiklanishi kerak');
  check(db.state().meta.testMarker === 'salom', 'oxirgi o‘zgarish ham saqlangan bo‘lishi kerak');

  // ------------------------------------------------ 22. Admin noto‘g‘ri parol va qulf
  const X = { id: 1005, is_bot: false, first_name: 'Hacker' };
  for (let i = 0; i < 5; i += 1) {
    await say(X, '/admin');
    await say(X, 'testadmin');
    await say(X, 'xato');
  }
  await say(X, '/admin');
  check(/Juda ko‘p noto‘g‘ri urinish/.test(last(X)), '5 xatodan keyin qulflanishi kerak', last(X));

  // ------------------------------------------------ 23. Yangi kanal ulanishi (my_chat_member)
  await bot.handleUpdate({ update_id: upd++, my_chat_member: { chat: { id: -100777, type: 'channel', title: 'Yangi baza' }, from: E, date: 1, old_chat_member: { status: 'left' }, new_chat_member: { status: 'administrator', user: mock.bot } } });
  check(mock.messages(-100777).some((m) => /-100777/.test(m.text || '')), 'yopiq kanalga ID yozilishi kerak');

  // ------------------------------------------------ 24. Statistika va panel ekranlari xatosiz
  await press(A, 'a:menu');
  const stMsg = await press(A, 'a:stats');
  check(/Daromad \(jami\)/.test(screenOf(A, stMsg)) && /70 000 so‘m/.test(screenOf(A, stMsg)), 'statistika', screenOf(A, stMsg));
  await press(A, 'a:menu');
  await press(A, 'a:set');
  await press(A, 'a:sp');
  await press(A, 'a:e:price:oddiy');
  await say(A, '30000');
  check(db.settings().prices.oddiy[0] === 30000 && db.settings().prices.oddiy[1] === 150, 'narx va Stars avtomatik hisoblanishi kerak', JSON.stringify(db.settings().prices.oddiy));
  await press(A, 'a:set');
  await press(A, 'a:sh');
  await press(A, 'a:e:h:oddiy');
  await say(A, '1.5');
  check(db.settings().hours.oddiy === 36, 'muddat soatda saqlanishi kerak');
  await press(A, 'a:set');
  await press(A, 'a:e:cats');
  await say(A, 'Savdo\nIT\nTa’lim');
  check(db.settings().cats.length === 3, 'sohalar yangilanishi kerak');
  await press(A, 'a:menu');
  await press(A, 'a:bak');
  await press(A, 'a:bkd');
  check(mock.messages(A.id).some((m) => m.document && /baza_/.test(m.document.file_name)), 'zaxira faylini yuklab olish');

  console.log(`✅ Barcha sinovlar o‘tdi: ${passed} ta tekshiruv`);
  process.exit(0);
})().catch((e) => {
  console.error('❌ Sinovda xato:', e);
  process.exit(1);
});
