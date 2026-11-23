'use strict';
// Ish e’lonlari boti — ishga tushirish fayli.
// Render: Build Command — (bo‘sh yoki «npm install»), Start Command — «node server.js»
const http = require('http');
const crypto = require('crypto');
const cfg = require('./src/config');
const tg = require('./src/tg');
const db = require('./src/db');
const core = require('./src/core');
const bot = require('./src/bot');
const admin = require('./src/admin');
const ai = require('./src/ai');
const scheduler = require('./src/scheduler');
const { sleep, esc } = require('./src/util');

const hash = (s) => crypto.createHash('sha256').update(s).digest('hex');
const WEBHOOK_PATH = cfg.BOT_TOKEN ? `/tg/${hash(`path:${cfg.BOT_TOKEN}`).slice(0, 24)}` : '/tg/none';
const SECRET = cfg.BOT_TOKEN ? hash(`secret:${cfg.BOT_TOKEN}`).slice(0, 48) : '';
const ALLOWED = ['message', 'callback_query', 'pre_checkout_query', 'my_chat_member', 'channel_post'];
let startError = '';

function statusPage() {
  const ok = !startError && db.isReady();
  const title = ok ? '✅ Ish e’lonlari boti ishlayapti' : (startError ? `⚠️ ${esc(startError)}` : '⏳ Bot ishga tushmoqda...');
  const botLine = core.me.username ? `<p>Bot: <a href="https://t.me/${core.me.username}">@${core.me.username}</a></p>` : '';
  return `<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ish e’lonlari boti</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 20px;color:#1f2937}h1{font-size:22px}p{color:#4b5563}</style></head>
<body><h1>${title}</h1>${botLine}<p>Baza: ${esc(db.status.mode)}${db.status.error ? ` — ${esc(db.status.error)}` : ''}</p></body></html>`;
}

const server = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === WEBHOOK_PATH) {
    if (req.headers['x-telegram-bot-api-secret-token'] !== SECRET) { res.writeHead(403); res.end(); return; }
    let body = '';
    req.on('data', (c) => { body += c; if (body.length > 5e6) req.destroy(); });
    req.on('end', async () => {
      try { await db.whenReady(25000); } catch (e) { res.writeHead(503); res.end(); return; }
      res.writeHead(200);
      res.end('ok');
      let upd;
      try { upd = JSON.parse(body); } catch (e) { return; }
      bot.handleUpdate(upd);
    });
    return;
  }
  if (req.url === '/health') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: !startError, ready: db.isReady(), bot: core.me.username || null, error: startError || db.status.error || null }));
    return;
  }
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(statusPage());
});

async function polling() {
  await tg.quiet('deleteWebhook', { drop_pending_updates: false });
  console.log('[bot] polling rejimi (PUBLIC_URL yo‘q)');
  let offset = 0;
  for (;;) {
    try {
      const ups = await tg.call('getUpdates', { offset, timeout: 50, allowed_updates: ALLOWED });
      for (const u of ups) { offset = u.update_id + 1; bot.handleUpdate(u); }
    } catch (e) {
      console.error(`[polling] ${e.message}`);
      await sleep(3000);
    }
  }
}

async function main() {
  server.listen(cfg.PORT, () => console.log(`[server] ${cfg.PORT}-portda ishlayapti`));
  if (!cfg.BOT_TOKEN) {
    startError = 'BOT_TOKEN kiritilmagan';
    console.error('[server] BOT_TOKEN yo‘q — Render → Environment bo‘limiga BOT_TOKEN qo‘shing.');
    return;
  }
  console.log(`[server] Node ${process.version}`);
  // Telegram bilan aloqa tiklanmaguncha qayta urinamiz
  for (let i = 0; ; i += 1) {
    const t0 = Date.now();
    try {
      // Yangi tokenda Telegram birinchi so‘rovga sekin javob berishi mumkin — uzoq kutamiz
      const me = await tg.call('getMe', {}, null, { retry: false, timeout: 150000 });
      core.me.id = me.id;
      core.me.username = me.username;
      startError = '';
      console.log(`[bot] @${me.username} ulandi (${Date.now() - t0} ms)`);
      break;
    } catch (e) {
      if (e.code === 401 || e.code === 404) {
        startError = 'BOT_TOKEN noto‘g‘ri';
        console.error(`[bot] ${startError}: ${e.message}`);
        return;
      }
      startError = `Telegram bilan aloqa yo‘q: ${e.message}`;
      // Telegram cheklov qo‘ygan bo‘lsa, so‘rovlar bilan uni uzaytirmaslik uchun uzoqroq kutamiz
      const wait = e.retryAfter ? (e.retryAfter + 10) * 1000 : 45000;
      console.error(`[bot] ${startError} — ${Math.round(wait / 1000)} soniyadan keyin qayta urinamiz`);
      await sleep(wait);
    }
  }
  if (!cfg.BAZA_KANAL) console.warn('[baza] BAZA_KANAL yo‘q — ma’lumotlar faqat xotirada (server qayta yonganda yo‘qoladi)');
  await db.init();
  admin.init();
  console.log(`[sozlama] kanal: ${db.settings().channel || 'yo‘q'}, to‘lov: ${db.settings().payments ? 'yoqilgan' : 'bepul'}, majburiy obuna: ${db.settings().forceSub ? 'ha' : 'yo‘q'}, admin login: ${cfg.ADMIN_LOGIN ? 'bor' : 'YO‘Q'}`);
  ai.check().then((r) => console.log(`[ai] Gemini: ${r}`));
  await tg.quiet('setMyCommands', {
    commands: [
      { command: 'start', description: 'Bosh menyu' },
      { command: 'cancel', description: 'Bekor qilish' },
      { command: 'admin', description: 'Admin panel' },
    ],
  });
  if (cfg.PUBLIC_URL) {
    try {
      await tg.call('setWebhook', { url: cfg.PUBLIC_URL + WEBHOOK_PATH, secret_token: SECRET, allowed_updates: ALLOWED, max_connections: 20 });
      console.log('[bot] webhook o‘rnatildi');
    } catch (e) {
      console.error(`[bot] webhook xatosi: ${e.message}`);
    }
  } else {
    polling();
  }
  scheduler.start();
}

let stopping = false;
async function shutdown(sig) {
  if (stopping) return;
  stopping = true;
  console.log(`[server] ${sig}: ma’lumotlar saqlanmoqda...`);
  try { await db.flush(); } catch (e) { console.error(e.message); }
  process.exit(0);
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('unhandledRejection', (e) => console.error('[xato]', e));
process.on('uncaughtException', (e) => console.error('[xato]', e));

if (require.main === module) main();

module.exports = { server, WEBHOOK_PATH, SECRET };
