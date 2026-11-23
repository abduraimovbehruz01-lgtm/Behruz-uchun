'use strict';
// Telegram Bot API bilan ishlash (tashqi kutubxonasiz).
const cfg = require('./config');
const { sleep } = require('./util');

const base = () => `${cfg.TG_API}/bot${cfg.BOT_TOKEN}`;

async function call(method, params = {}, files = null) {
  for (let attempt = 0; ; attempt++) {
    let j;
    try {
      let body;
      const headers = {};
      if (files) {
        const fd = new FormData();
        for (const [k, v] of Object.entries(params)) {
          if (v === undefined || v === null) continue;
          fd.append(k, typeof v === 'object' ? JSON.stringify(v) : String(v));
        }
        for (const [k, f] of Object.entries(files)) {
          fd.append(k, new Blob([f.data], { type: f.type || 'application/octet-stream' }), f.name || k);
        }
        body = fd;
      } else {
        body = JSON.stringify(params);
        headers['content-type'] = 'application/json';
      }
      const res = await fetch(`${base()}/${method}`, {
        method: 'POST',
        headers,
        body,
        signal: AbortSignal.timeout(method === 'getUpdates' ? 75000 : 30000),
      });
      j = await res.json();
    } catch (e) {
      if (attempt < 2) { await sleep(700 * (attempt + 1)); continue; }
      const err = new Error(`${method}: ${e.message}`);
      err.network = true;
      throw err;
    }
    if (j.ok) return j.result;
    const ra = j.parameters && j.parameters.retry_after;
    if (j.error_code === 429 && ra && ra <= 35 && attempt < 3) { await sleep(ra * 1000 + 300); continue; }
    if (j.error_code >= 500 && attempt < 2) { await sleep(1000); continue; }
    const err = new Error(`${method}: ${j.description}`);
    err.code = j.error_code;
    err.desc = j.description || '';
    err.retryAfter = ra;
    throw err;
  }
}

async function download(fileId) {
  const f = await call('getFile', { file_id: fileId });
  const r = await fetch(`${cfg.TG_API}/file/bot${cfg.BOT_TOKEN}/${f.file_path}`, { signal: AbortSignal.timeout(60000) });
  if (!r.ok) throw new Error(`Faylni yuklab bo‘lmadi: ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
}

// Xatoni yutib yuboradigan chaqiruv (muhim bo‘lmagan amallar uchun)
async function quiet(method, params, files) {
  try { return await call(method, params, files); } catch (e) {
    if (!/not modified|message to delete not found|message can't be deleted|MESSAGE_ID_INVALID/i.test(e.message)) {
      console.warn(`[tg] ${e.message}`);
    }
    return null;
  }
}

module.exports = { call, download, quiet };
