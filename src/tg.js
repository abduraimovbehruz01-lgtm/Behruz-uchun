'use strict';
// Telegram Bot API bilan ishlash (tashqi kutubxonasiz).
// Ulanish faqat IPv4 orqali: ba’zi serverlarda IPv6 ishlamay, so‘rovlar osilib qoladi.
const http = require('http');
const https = require('https');
const dns = require('dns');
const crypto = require('crypto');
const cfg = require('./config');
const { sleep } = require('./util');

try { dns.setDefaultResultOrder('ipv4first'); } catch (e) { /* eski Node */ }

const agentHttps = new https.Agent({ keepAlive: true, maxSockets: 25, family: 4 });
const agentHttp = new http.Agent({ keepAlive: true, maxSockets: 25 });

function request(urlStr, { method = 'GET', headers = {}, body = null, timeout = 30000 } = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const isHttps = url.protocol === 'https:';
    const opts = {
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port || (isHttps ? 443 : 80),
      path: url.pathname + url.search,
      method,
      headers,
      agent: isHttps ? agentHttps : agentHttp,
    };
    if (isHttps) opts.family = 4;
    const req = (isHttps ? https : http).request(opts, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
      res.on('error', reject);
    });
    req.setTimeout(timeout, () => req.destroy(new Error(`javob ${Math.round(timeout / 1000)} soniyada kelmadi`)));
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

function multipart(params, files) {
  const boundary = `----ishbot${crypto.randomBytes(12).toString('hex')}`;
  const parts = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${k}"\r\n\r\n${val}\r\n`));
  }
  for (const [k, f] of Object.entries(files)) {
    const name = String(f.name || k).replace(/["\r\n]/g, '');
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${k}"; filename="${name}"\r\nContent-Type: ${f.type || 'application/octet-stream'}\r\n\r\n`));
    parts.push(Buffer.isBuffer(f.data) ? f.data : Buffer.from(f.data));
    parts.push(Buffer.from('\r\n'));
  }
  parts.push(Buffer.from(`--${boundary}--\r\n`));
  return { body: Buffer.concat(parts), type: `multipart/form-data; boundary=${boundary}` };
}

const base = () => `${cfg.TG_API}/bot${cfg.BOT_TOKEN}`;

async function call(method, params = {}, files = null) {
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      let body;
      let type;
      if (files) {
        const mp = multipart(params, files);
        body = mp.body;
        type = mp.type;
      } else {
        body = Buffer.from(JSON.stringify(params));
        type = 'application/json';
      }
      res = await request(`${base()}/${method}`, {
        method: 'POST',
        headers: { 'content-type': type, 'content-length': body.length },
        body,
        timeout: method === 'getUpdates' ? 75000 : (files ? 60000 : 25000),
      });
    } catch (e) {
      if (attempt < 2) { await sleep(800 * (attempt + 1)); continue; }
      const err = new Error(`${method}: ${e.message}`);
      err.network = true;
      throw err;
    }
    let j;
    try { j = JSON.parse(res.body.toString('utf8')); } catch (e) { j = { ok: false, error_code: res.status, description: `HTTP ${res.status}` }; }
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
  const r = await request(`${cfg.TG_API}/file/bot${cfg.BOT_TOKEN}/${f.file_path}`, { timeout: 60000 });
  if (r.status !== 200) throw new Error(`Faylni yuklab bo‘lmadi: ${r.status}`);
  return r.body;
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

module.exports = { call, download, quiet, request };
