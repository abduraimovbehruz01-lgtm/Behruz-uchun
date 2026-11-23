'use strict';
// Xabar yuborish, tahrirlash, menyu va «chat toza tursin» yordamchilari.
const tg = require('./tg');
const db = require('./db');
const { rkb } = require('./util');

const BTN = {
  post: '➕ Vakansiya joylash',
  browse: '🔎 Vakansiyalar',
  seek: '🙋 Ish qidiryapman',
  subs: '🔔 Obuna',
  my: '📂 Mening e’lonlarim',
  cands: '🗂 Nomzodlar bazasi',
  packs: '📦 Paketlar',
  tpl: '📄 Rezyume shablonlari',
  help: 'ℹ️ Yordam',
  cancel: '❌ Bekor qilish',
  phone: '📱 Raqamni yuborish',
};

const MENU_ACTIONS = {
  [BTN.post]: 'post',
  [BTN.browse]: 'browse',
  [BTN.seek]: 'seek',
  [BTN.subs]: 'subs',
  [BTN.my]: 'my',
  [BTN.cands]: 'cands',
  [BTN.packs]: 'packs',
  [BTN.tpl]: 'tpl',
  [BTN.help]: 'help',
};

const mainKb = () => rkb(
  [[BTN.post, BTN.browse], [BTN.seek, BTN.subs], [BTN.my, BTN.cands], [BTN.packs, BTN.tpl], [BTN.help]],
  { is_persistent: true, input_field_placeholder: 'Menyudan tanlang' },
);
const phoneKb = () => rkb([[{ text: BTN.phone, request_contact: true }], [BTN.cancel]], { one_time_keyboard: true });

function send(chatId, text, markup, extra = {}) {
  return tg.call('sendMessage', {
    chat_id: chatId,
    text,
    parse_mode: 'HTML',
    link_preview_options: { is_disabled: true },
    reply_markup: markup,
    ...extra,
  });
}

async function edit(chatId, msgId, text, markup) {
  try {
    return await tg.call('editMessageText', {
      chat_id: chatId,
      message_id: msgId,
      text,
      parse_mode: 'HTML',
      link_preview_options: { is_disabled: true },
      reply_markup: markup || { inline_keyboard: [] },
    });
  } catch (e) {
    if (/not modified/i.test(e.message)) return null;
    throw e;
  }
}

const del = (chatId, msgId) => (msgId ? tg.quiet('deleteMessage', { chat_id: chatId, message_id: msgId }) : Promise.resolve(null));

async function answer(q, text, alert = false) {
  if (!q || q._answered) return;
  q._answered = true;
  await tg.quiet('answerCallbackQuery', { callback_query_id: q.id, text: text || undefined, show_alert: alert || undefined });
}

// Oqimdagi oldingi savollarni o‘chiradi (yoki tugmalarini olib tashlaydi)
async function clearPrompts(uid) {
  const s = db.sess(uid);
  const ids = s.prompts.splice(0);
  if (!ids.length) return;
  const clean = db.settings().cleanChat;
  for (const id of ids) {
    if (clean) await del(uid, id);
    else await tg.quiet('editMessageReplyMarkup', { chat_id: uid, message_id: id, reply_markup: { inline_keyboard: [] } });
  }
  db.markDirty();
}

// Yangi «ekran» yuborish: eskisi o‘chadi
async function ask(uid, text, markup) {
  await clearPrompts(uid);
  const m = await send(uid, text, markup);
  db.sess(uid).prompts.push(m.message_id);
  db.markDirty();
  return m;
}

// Tugma bosilganda shu xabarni tahrirlash, bo‘lmasa yangi ekran
async function screen(uid, q, text, markup) {
  if (q && q.message) {
    try {
      await edit(uid, q.message.message_id, text, markup);
      const s = db.sess(uid);
      if (!s.prompts.includes(q.message.message_id)) {
        await clearPrompts(uid);
        s.prompts.push(q.message.message_id);
        db.markDirty();
      }
      return q.message;
    } catch (e) { /* yangi xabar yuboramiz */ }
  }
  return ask(uid, text, markup);
}

async function delInput(msg) {
  if (msg && db.settings().cleanChat) await del(msg.chat.id, msg.message_id);
}

// Asosiy menyu (pastki tugmalar) bilan xabar
async function showMenu(uid, text) {
  await clearPrompts(uid);
  const u = db.state().users[uid];
  const m = await send(uid, text, mainKb());
  if (u) {
    const old = u.menuMsg;
    u.menuMsg = m.message_id;
    db.markDirty();
    if (old && old !== m.message_id && db.settings().cleanChat) await del(uid, old);
  }
  return m;
}

module.exports = { BTN, MENU_ACTIONS, mainKb, phoneKb, send, edit, del, answer, clearPrompts, ask, screen, delInput, showMenu };
