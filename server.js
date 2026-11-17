/*
 * server.js — Taqdimot sayti serveri.
 * - Google orqali kirish (Firebase Authentication tokenini tekshiradi)
 * - Har bir foydalanuvchiga bepul limit (standart 3 ta), keyin pullik
 * - Gemini orqali taqdimot va krossvord matni
 * - Qo'lda to'lov so'rovlari (karta + chek) va admin panel API
 * Ishga tushirish: node server.js   (sozlash — README.md)
 */
"use strict";

const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const admin = require("firebase-admin");
const Deck = require("./deck.js");

// ------------------------------------------------------------------ sozlamalar (Render → Environment)
const PORT = Number(process.env.PORT) || 8080;
const GEMINI_KEY = (process.env.GEMINI_API_KEY || "").trim();
const GEMINI_BASE = (process.env.GEMINI_BASE || "https://generativelanguage.googleapis.com/v1beta").replace(/\/$/, "");
const MODELS = (process.env.GEMINI_MODEL || "gemini-flash-latest,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-2.5-flash")
  .split(",").map((s) => s.trim()).filter(Boolean);
const ADMIN_LOGIN = (process.env.ADMIN_LOGIN || "").trim();
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const ADMIN_SESSION_DAYS = 7;
// Telegram bot (ixtiyoriy): sayt bot ichida Web App bo'lib ochiladi
const TG_TOKEN = (process.env.TELEGRAM_BOT_TOKEN || "").trim();
const TG_API = (process.env.TELEGRAM_API_BASE || "https://api.telegram.org").replace(/\/$/, "");
const SITE_URL = (process.env.SITE_URL || process.env.RENDER_EXTERNAL_URL || "").trim().replace(/\/$/, "");
const TG_WEBHOOK = (process.env.TELEGRAM_WEBHOOK || "on").trim().toLowerCase() !== "off";
let TG_BOT = null;
const CROSSWORD_DAILY = Number(process.env.CROSSWORD_DAILY) || 5;
const ROOT = __dirname;

// Firebase sahifasidagi config JSON yoki JS ko'rinishida bo'lishi mumkin — ikkalasini ham tushunamiz
function parseWebConfig(raw) {
  raw = (raw || "").trim();
  try { const j = JSON.parse(raw); if (j && typeof j === "object") return j; } catch (e) { /* JS ko'rinishi */ }
  const out = {};
  for (const m of raw.matchAll(/["']?(\w+)["']?\s*:\s*["']([^"']*)["']/g)) out[m[1]] = m[2];
  return out;
}

function parseServiceAccount(raw) {
  raw = (raw || "").trim();
  if (!raw) return null;
  if (!raw.startsWith("{")) raw = Buffer.from(raw, "base64").toString("utf8");
  const sa = JSON.parse(raw);
  if (sa.private_key) sa.private_key = sa.private_key.replace(/\\n/g, "\n");
  return sa;
}

const WEB_RAW = (process.env.FIREBASE_WEB_CONFIG || "").trim();
const WEB_CONFIG = parseWebConfig(WEB_RAW);
// Google orqali kirish ixtiyoriy: FIREBASE_WEB_CONFIG berilmasa, ilova faqat Telegram ichida ishlaydi
const GOOGLE = !!(WEB_CONFIG.apiKey && WEB_CONFIG.authDomain);
const SETUP = [];    // sayt ishlashi uchun yetishmayotgan narsalar
const WARN = [];     // ishlaydi, lekin to'liq emas
let db = null;
let FV = null;

try {
  const sa = parseServiceAccount(process.env.FIREBASE_SERVICE_ACCOUNT);
  if (!sa) SETUP.push("FIREBASE_SERVICE_ACCOUNT");
  else if (!sa.private_key || !sa.client_email || !sa.project_id) SETUP.push("FIREBASE_SERVICE_ACCOUNT (fayl to‘liq emas)");
  else {
    admin.initializeApp({ credential: admin.credential.cert(sa) });
    db = admin.firestore();
    FV = admin.firestore.FieldValue;
    if (GOOGLE && WEB_CONFIG.projectId && WEB_CONFIG.projectId !== sa.project_id)
      SETUP.push("FIREBASE_WEB_CONFIG va FIREBASE_SERVICE_ACCOUNT har xil Firebase loyihasidan olingan");
  }
} catch (e) {
  SETUP.push("FIREBASE_SERVICE_ACCOUNT (noto‘g‘ri nusxalangan)");
  console.error("Service account xatosi:", e.message);
}
if (WEB_RAW && !GOOGLE) SETUP.push("FIREBASE_WEB_CONFIG (noto‘g‘ri nusxalangan — kerak bo‘lmasa, o‘chirib tashlang)");
if (!GOOGLE && !TG_TOKEN) SETUP.push("TELEGRAM_BOT_TOKEN");
if (!GEMINI_KEY) WARN.push("GEMINI_API_KEY yo‘q — taqdimot yasab bo‘lmaydi");
if (!ADMIN_LOGIN || !ADMIN_PASSWORD) WARN.push("ADMIN_LOGIN yoki ADMIN_PASSWORD yo‘q — admin panelga kirib bo‘lmaydi");
else if (ADMIN_PASSWORD.length < 8) WARN.push("ADMIN_PASSWORD juda qisqa — kamida 8 belgi qiling");

// ------------------------------------------------------------------ admin kirishi (login + parol)
// Sessiya tokeni login/parol va maxfiy kalitdan yasalgan imzo bilan himoyalanadi:
// parol o'zgarsa, barcha eski sessiyalar o'z-o'zidan bekor bo'ladi.
const ADMIN_KEY = crypto.createHash("sha256")
  .update(["taqdimot-admin", ADMIN_LOGIN, ADMIN_PASSWORD, process.env.FIREBASE_SERVICE_ACCOUNT || ""].join("\u0000")).digest();
const sha = (x) => crypto.createHash("sha256").update(String(x)).digest();
const sameText = (a, b) => crypto.timingSafeEqual(sha(a), sha(b));
const sign = (payload) => crypto.createHmac("sha256", ADMIN_KEY).update(payload).digest("base64url");

function makeAdminToken() {
  const exp = Date.now() + ADMIN_SESSION_DAYS * 86400000;
  const payload = `admin.${exp}`;
  return { token: `${payload}.${sign(payload)}`, expiresAt: new Date(exp).toISOString() };
}
function isAdminToken(token) {
  const m = /^admin\.(\d{10,16})\.([\w-]{43})$/.exec(String(token || ""));
  if (!m || !ADMIN_LOGIN || !ADMIN_PASSWORD) return false;
  if (Number(m[1]) < Date.now()) return false;
  return crypto.timingSafeEqual(Buffer.from(sign(`admin.${m[1]}`)), Buffer.from(m[2]));
}

// ------------------------------------------------------------------ Telegram
const TG_SECRET = TG_TOKEN ? crypto.createHash("sha256").update("tg-webhook|" + TG_TOKEN).digest("hex").slice(0, 48) : "";

async function tg(method, body) {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  try {
    const r = await fetch(`${TG_API}/bot${TG_TOKEN}/${method}`, {
      method: "POST", headers: isForm ? undefined : { "Content-Type": "application/json" },
      body: isForm ? body : JSON.stringify(body || {}), signal: AbortSignal.timeout(60000),
    });
    const j = await r.json().catch(() => ({ ok: false, description: "javob o‘qilmadi" }));
    if (!j.ok) log("telegram", method, "xato:", j.error_code || r.status, j.description || "");
    return j;
  } catch (e) {
    log("telegram", method, "ulanmadi:", e.message);
    return { ok: false, description: e.message };
  }
}

// Telegram Web App ma'lumotini (initData) bot tokeni bilan tekshiramiz — soxtalashtirib bo'lmaydi
function verifyTelegram(raw) {
  if (!TG_TOKEN) throw new HttpError(401, "Telegram orqali kirish hali ulanmagan.", "auth");
  const p = new URLSearchParams(raw);
  const hash = (p.get("hash") || "").toLowerCase();
  p.delete("hash");
  const check = [...p.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([k, v]) => `${k}=${v}`).join("\n");
  const secret = crypto.createHmac("sha256", "WebAppData").update(TG_TOKEN).digest();
  const calc = crypto.createHmac("sha256", secret).update(check).digest("hex");
  if (hash.length !== 64 || !crypto.timingSafeEqual(Buffer.from(calc), Buffer.from(hash)))
    throw new HttpError(401, "Telegram ma’lumoti tasdiqlanmadi. Botni yopib, qayta oching.", "auth");
  const age = Date.now() / 1000 - Number(p.get("auth_date") || 0);
  if (!(age < 86400)) throw new HttpError(401, "Sessiya eskirgan. Botdagi oynani yopib, qayta oching.", "auth");
  let user = null;
  try { user = JSON.parse(p.get("user") || "null"); } catch (e) { /* pastda */ }
  if (!user || !user.id) throw new HttpError(401, "Telegram foydalanuvchisi aniqlanmadi.", "auth");
  return user;
}

function readRaw(req, limit) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > limit) { reject(new HttpError(413, "Fayl juda katta.")); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}
const sends = new Map();   // Telegram'ga fayl yuborish chegarasi: soatiga 40 ta

async function setupTelegram(attempt) {
  if (!TG_TOKEN) return;
  attempt = attempt || 1;
  const me = await tg("getMe");
  if (!me.ok) {
    // Telegram vaqtincha javob bermasa — keyinroq qayta urinamiz; token noto'g'ri bo'lsa — ogohlantiramiz
    if (me.error_code !== 401 && me.error_code !== 404 && attempt < 10) {
      setTimeout(() => setupTelegram(attempt + 1).catch(() => {}), Math.min(60000, 5000 * attempt));
      return;
    }
    WARN.push("TELEGRAM_BOT_TOKEN noto‘g‘ri yoki Telegram javob bermadi — bot ulanmadi");
    return;
  }
  TG_BOT = { username: me.result.username, name: me.result.first_name };
  if (!SITE_URL) { WARN.push("Sayt manzili noma’lum — SITE_URL ni kiriting, bot tugmasi sozlanmadi"); return; }
  await tg("setChatMenuButton", { menu_button: { type: "web_app", text: "Taqdimot", web_app: { url: SITE_URL } } });
  await tg("setMyCommands", { commands: [{ command: "start", description: "Taqdimot yasash" }] });
  if (TG_WEBHOOK) {
    await tg("setWebhook", { url: SITE_URL + "/api/tg/webhook", secret_token: TG_SECRET, allowed_updates: ["message"], drop_pending_updates: true });
  }
  log(`Telegram bot ulandi: @${TG_BOT.username} → ${SITE_URL}`);
}

// Parolni taxmin qilib topishga urinishlarni cheklash: bitta manzildan 15 daqiqada 5 ta, hammasidan 50 ta xato
const FAIL_WINDOW = 15 * 60000;
const fails = new Map();
let globalFails = [];
const clientIp = (req) => String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || req.socket.remoteAddress || "?";
function loginBlocked(ip) {
  const t = Date.now() - FAIL_WINDOW;
  globalFails = globalFails.filter((x) => x > t);
  const mine = (fails.get(ip) || []).filter((x) => x > t);
  fails.set(ip, mine);
  return mine.length >= 5 || globalFails.length >= 50;
}
function noteFail(ip) {
  const now = Date.now();
  fails.set(ip, (fails.get(ip) || []).concat(now));
  globalFails.push(now);
  if (fails.size > 5000) fails.clear();
}

const DEFAULT_SETTINGS = {
  freeCredits: 3,
  cardNumber: "",
  cardHolder: "",
  telegram: "",
  note: "To‘lov qilgach, chekni (skrinshot) Telegram orqali yuboring va pastdagi formani to‘ldiring. Odatda bir necha soat ichida tasdiqlanadi.",
  packages: [
    { id: "p5", title: "5 ta taqdimot", credits: 5, price: 10000 },
    { id: "p15", title: "15 ta taqdimot", credits: 15, price: 25000 },
    { id: "p40", title: "40 ta taqdimot", credits: 40, price: 50000 },
  ],
};

// ------------------------------------------------------------------ yordamchilar
class HttpError extends Error {
  constructor(status, message, code) {
    super(message);
    this.status = status;
    this.code = code || "error";
  }
}

const clean = (s, n) => String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, n || 500);
const tashkentDay = () => new Date(Date.now() + 5 * 3600 * 1000).toISOString().slice(0, 10);
const tashkentDayStart = () => new Date(Date.parse(tashkentDay() + "T00:00:00Z") - 5 * 3600 * 1000);
const iso = (v) => (v && typeof v.toDate === "function" ? v.toDate().toISOString() : v || null);
const byNewest = (a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || ""));
const log = (...a) => console.log(new Date().toISOString(), ...a);

function send(res, status, obj) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(obj));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > 200 * 1024) { reject(new HttpError(413, "So‘rov juda katta.")); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => {
      if (!chunks.length) return resolve({});
      try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
      catch (e) { reject(new HttpError(400, "Noto‘g‘ri so‘rov.")); }
    });
    req.on("error", reject);
  });
}

async function auth(req) {
  const h = req.headers.authorization || "";
  if (h.startsWith("tma ")) {
    const t = verifyTelegram(h.slice(4));
    const uname = clean(t.username, 64).toLowerCase();
    return {
      uid: "tg_" + t.id, tgId: t.id, tg: true, email: uname ? "@" + uname : "tg:" + t.id,
      name: clean([t.first_name, t.last_name].filter(Boolean).join(" "), 80), picture: clean(t.photo_url, 500),
    };
  }
  const token = h.startsWith("Bearer ") ? h.slice(7) : "";
  if (!token || !GOOGLE) throw new HttpError(401, GOOGLE ? "Avval Google orqali kiring." : "Ilovani Telegram bot ichida oching.", "auth");
  let u;
  try {
    u = await admin.auth().verifyIdToken(token);
  } catch (e) {
    log("token xatosi:", e.code || e.message);
    throw new HttpError(401, "Kirish muddati tugagan. Qaytadan kiring.", "auth");
  }
  const email = (u.email || "").toLowerCase();
  return { uid: u.uid, email, name: clean(u.name, 80), picture: u.picture || "" };
}

let settingsCache = null;
let settingsAt = 0;
async function getSettings(force) {
  if (!force && settingsCache && Date.now() - settingsAt < 30000) return settingsCache;
  const snap = await db.doc("settings/main").get();
  settingsCache = Object.assign({}, DEFAULT_SETTINGS, snap.exists ? snap.data() : {});
  settingsAt = Date.now();
  return settingsCache;
}

const publicSettings = (s) => ({
  freeCredits: s.freeCredits, cardNumber: s.cardNumber, cardHolder: s.cardHolder,
  telegram: s.telegram, note: s.note, packages: s.packages,
});

// Foydalanuvchi yozuvi: birinchi kirishda bepul limit beriladi
async function ensureUser(u) {
  const ref = db.collection("users").doc(u.uid);
  const s = await getSettings();
  return db.runTransaction(async (tx) => {
    const snap = await tx.get(ref);
    if (snap.exists) {
      tx.update(ref, { lastSeen: FV.serverTimestamp(), email: u.email, name: u.name, photo: u.picture });
      return snap.data();
    }
    const d = {
      email: u.email, name: u.name, photo: u.picture, credits: Number(s.freeCredits) || 0, used: 0,
      blocked: false, createdAt: FV.serverTimestamp(), lastSeen: FV.serverTimestamp(),
    };
    tx.set(ref, d);
    tx.set(db.doc("stats/main"), { users: FV.increment(1) }, { merge: true });
    return d;
  });
}

// ------------------------------------------------------------------ Gemini
function extractJson(text) {
  try { return JSON.parse(text); } catch (e) { /* davom */ }
  const a = text.indexOf("{");
  const b = text.lastIndexOf("}");
  if (a >= 0 && b > a) { try { return JSON.parse(text.slice(a, b + 1)); } catch (e) { /* davom */ } }
  const err = new Error("javob JSON emas");
  err.retryNext = true;
  throw err;
}

async function callModel(model, prompt, withThinking) {
  const gen = { responseMimeType: "application/json", maxOutputTokens: 30000 };
  if (withThinking && /^gemini-[3-9]/.test(model)) gen.thinkingConfig = { thinkingLevel: "low" };
  const r = await fetch(`${GEMINI_BASE}/models/${encodeURIComponent(model)}:generateContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY },
    body: JSON.stringify({ contents: [{ role: "user", parts: [{ text: prompt }] }], generationConfig: gen }),
    signal: AbortSignal.timeout(170000),
  });
  const txt = await r.text();
  if (r.status === 400 && /API key not valid|API_KEY_INVALID/i.test(txt))
    throw new HttpError(500, "Gemini kaliti noto‘g‘ri. Admin GEMINI_API_KEY ni tekshirishi kerak.", "ai_key");
  // Kalitning o'zi ishlamasa (401 yoki API o'chirilgan) — boshqa modellarni sinash befoyda
  if (r.status === 401 || (r.status === 403 && /SERVICE_DISABLED|API_KEY_INVALID|API_KEY_SERVICE_BLOCKED/i.test(txt))) {
    log("gemini kalit xatosi:", r.status, txt.slice(0, 300).replace(/\s+/g, " "));
    throw new HttpError(500, "Gemini kaliti ishlamadi. Admin kalitni tekshirishi kerak.", "ai_key");
  }
  if (r.status === 400 && /location is not supported/i.test(txt))
    throw new HttpError(500, "Gemini bu server joylashgan mamlakatda ishlamaydi. Admin serverni boshqa hududga ko‘chirishi kerak.", "ai_key");
  if (r.status === 400 && gen.thinkingConfig) return callModel(model, prompt, false);
  if (!r.ok) throw new Error(`${model} ${r.status}: ${txt.slice(0, 200).replace(/\s+/g, " ")}`);
  let j = {};
  try { j = JSON.parse(txt); } catch (e) { throw new Error(`${model}: javob o‘qilmadi`); }
  const cand = j.candidates && j.candidates[0];
  if (!cand) {
    if (j.promptFeedback && j.promptFeedback.blockReason)
      throw new HttpError(400, "AI bu mavzuda yozmadi. Mavzuni boshqacha so‘zlar bilan yozib ko‘ring.", "refused");
    throw new Error(`${model}: javob bo‘sh`);
  }
  const text = ((cand.content && cand.content.parts) || []).filter((p) => !p.thought).map((p) => p.text || "").join("");
  if (!text.trim()) throw new Error(`${model}: matn bo‘sh (${cand.finishReason || "?"})`);
  return text;
}

// Modellarni navbat bilan sinaymiz: biri band (429) yoki topilmasa (404) — keyingisi
async function askGemini(prompt) {
  if (!GEMINI_KEY) throw new HttpError(500, "Server hali to‘liq sozlanmagan (GEMINI_API_KEY yo‘q).", "ai_key");
  for (const model of MODELS) {
    try {
      const out = extractJson(await callModel(model, prompt, true));
      log("gemini ok:", model);
      return out;
    } catch (e) {
      if (e instanceof HttpError) throw e;
      log("gemini xato:", e.message);
    }
  }
  throw new HttpError(503, "AI hozir band (bepul limit tugagan bo‘lishi mumkin). Bir necha daqiqadan keyin qayta urinib ko‘ring.", "ai_busy");
}

// ------------------------------------------------------------------ API
const routes = {};
const route = (method, p, fn) => { routes[method + " " + p] = fn; };
function needAdmin(req) {
  if (!isAdminToken(req.headers["x-admin-token"])) throw new HttpError(401, "Admin sifatida qaytadan kiring.", "admin_auth");
}
const langOf = (x) => (["uz", "ru", "en"].includes(x) ? x : "uz");

route("GET", "/api/config", async () => {
  if (SETUP.length) return { setup: SETUP, warnings: WARN };
  const s = await getSettings();
  return { firebase: GOOGLE ? WEB_CONFIG : null, settings: publicSettings(s), telegram: TG_BOT ? { bot: TG_BOT.username } : null, warnings: [] };
});

route("GET", "/api/me", async (req) => {
  const u = await auth(req);
  const d = await ensureUser(u);
  return {
    uid: u.uid, email: u.email, name: u.name, photo: u.picture, credits: d.credits || 0, used: d.used || 0,
    blocked: !!d.blocked, tg: !!u.tg,
  };
});

// Telegram ichida brauzer faylni saqlay olmaydi — tayyor faylni bot chatga yuboradi
route("POST", "/api/tg/send", async (req, url) => {
  const u = await auth(req);
  if (!u.tg) throw new HttpError(400, "Bu imkoniyat faqat Telegram ichida ishlaydi.");
  const name = clean(url.searchParams.get("name"), 120).replace(/[\\/:*?"<>|]/g, "") || "taqdimot.pdf";
  if (!/\.(pptx|pdf|docx|zip)$/i.test(name)) throw new HttpError(400, "Fayl turi noto‘g‘ri.");
  const hour = Date.now() - 3600000;
  const mine = (sends.get(u.uid) || []).filter((t) => t > hour);
  if (mine.length >= 40) throw new HttpError(429, "Bir soatda juda ko‘p fayl yubordingiz. Birozdan keyin urinib ko‘ring.");
  const buf = await readRaw(req, 25 * 1024 * 1024);
  if (buf.length < 100) throw new HttpError(400, "Fayl bo‘sh.");
  sends.set(u.uid, mine.concat(Date.now()));
  if (sends.size > 10000) sends.clear();
  const fd = new FormData();
  fd.append("chat_id", String(u.tgId));
  fd.append("caption", "✅ Tayyor!");
  fd.append("document", new Blob([buf]), name);
  const r = await tg("sendDocument", fd);
  if (!r.ok) {
    if (r.error_code === 403 || /chat not found|bot can't initiate/i.test(r.description || ""))
      throw new HttpError(400, "Bot sizga fayl yubora olmadi. Avval botga /start yozing, keyin qayta bosing.", "tg_start");
    throw new HttpError(502, "Faylni Telegram’ga yuborib bo‘lmadi. Qayta urinib ko‘ring.");
  }
  return { ok: true };
});

// Bot xabarlari: /start (yoki istalgan xabar) ga "Taqdimot yasash" tugmasi bilan javob
route("POST", "/api/tg/webhook", async (req) => {
  if (!TG_TOKEN || !sameText(req.headers["x-telegram-bot-api-secret-token"] || "", TG_SECRET)) throw new HttpError(403, "forbidden");
  const upd = await readBody(req);
  const msg = upd.message;
  if (msg && msg.chat && msg.chat.type === "private" && SITE_URL && /^\/admin(@\w+)?\s*$/i.test(String(msg.text || "").trim())) {
    // Admin panel Telegram ichida ochiladi; kirish baribir login va parol bilan
    tg("sendMessage", {
      chat_id: msg.chat.id,
      text: "⚙️ Admin panel. Ochilgan oynada login va parolni kiriting — Telegram 7 kun eslab qoladi.",
      reply_markup: { inline_keyboard: [[{ text: "⚙️ Admin panelni ochish", web_app: { url: SITE_URL + "/admin" } }]] },
    });
  } else if (msg && msg.chat && msg.chat.type === "private" && SITE_URL) {
    const s = await getSettings();
    const first = clean(msg.from && msg.from.first_name, 40);
    tg("sendMessage", {
      chat_id: msg.chat.id,
      text: `Salom${first ? ", " + first : ""}! 👋\n\nMavzuni yozing — tayyor taqdimot (PowerPoint, PDF, Word) va krossvord olasiz. ` +
        `Dastlabki ${s.freeCredits} ta taqdimot bepul.\n\nPastdagi tugmani bosing 👇`,
      reply_markup: { inline_keyboard: [[{ text: "📊 Taqdimot yasash", web_app: { url: SITE_URL } }]] },
    });
  }
  return { ok: true };
});

route("POST", "/api/generate", async (req) => {
  const u = await auth(req);
  const body = await readBody(req);
  // Ikki usul: "topic" — mavzu bo'yicha, "text" — foydalanuvchining o'z matnidan
  const mode = body.mode === "text" ? "text" : "topic";
  const topic = clean(body.topic, 200);
  const source = mode === "text"
    ? String(body.source || "").replace(/\r\n?/g, "\n").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim().slice(0, 30000) : "";
  if (mode === "topic" && topic.length < 3) throw new HttpError(400, "Mavzuni to‘liqroq yozing.");
  if (mode === "text" && source.length < 200) throw new HttpError(400, "Matn juda qisqa. Kamida bir-ikki xatboshi joylang.");
  const subject = clean(body.subject, 50);
  const n = [10, 12, 15, 20].includes(Number(body.n)) ? Number(body.n) : 12;
  const lang = langOf(body.lang);
  await ensureUser(u);
  const ref = db.collection("users").doc(u.uid);
  await db.runTransaction(async (tx) => {
    const d = (await tx.get(ref)).data();
    if (d.blocked) throw new HttpError(403, "Hisobingiz bloklangan. Admin bilan bog‘laning.", "blocked");
    if ((d.credits || 0) <= 0) throw new HttpError(402, "Taqdimotlar limiti tugadi.", "no_credits");
    tx.update(ref, { credits: FV.increment(-1), used: FV.increment(1) });
  });
  let data;
  try {
    const prompt = mode === "text" ? Deck.buildTextPrompt(source, topic, subject, n, lang) : Deck.buildPrompt(topic, subject, n, lang);
    data = Deck.normalize(await askGemini(prompt), topic || "Taqdimot");
  } catch (e) {
    await ref.update({ credits: FV.increment(1), used: FV.increment(-1) });   // xato bo'lsa limit qaytariladi
    if (e instanceof HttpError) {
      if (e.status !== 401 && e.status !== 403) e.message += " Limitingiz qaytarildi.";
      throw e;
    }
    log("normalize xato:", e.message);
    throw new HttpError(500, "Taqdimot matnini olib bo‘lmadi. Qayta urinib ko‘ring — limitingiz qaytarildi.");
  }
  const gen = db.collection("generations").doc();
  const batch = db.batch();
  batch.set(gen, { uid: u.uid, email: u.email, topic: topic || clean(data.title, 120), title: clean(data.title, 120), subject, lang, n, mode,
    createdAt: FV.serverTimestamp() });
  batch.set(db.collection("decks").doc(gen.id), { uid: u.uid, data, lang, subject, createdAt: FV.serverTimestamp() });
  batch.set(db.doc("stats/main"), { generations: FV.increment(1) }, { merge: true });
  await batch.commit();
  const after = (await ref.get()).data();
  return { id: gen.id, data, credits: after.credits || 0 };
});

route("POST", "/api/crossword", async (req) => {
  const u = await auth(req);
  const body = await readBody(req);
  const topic = clean(body.topic, 200);
  if (topic.length < 3) throw new HttpError(400, "Mavzuni yozing.");
  const lang = langOf(body.lang);
  await ensureUser(u);
  const ref = db.collection("users").doc(u.uid);
  const day = tashkentDay();
  await db.runTransaction(async (tx) => {
    const d = (await tx.get(ref)).data();
    if (d.blocked) throw new HttpError(403, "Hisobingiz bloklangan.", "blocked");
    if ((d.used || 0) === 0) throw new HttpError(400, "Avval taqdimot yasang, keyin unga krossvord qo‘shasiz.");
    const count = d.cwDay === day ? d.cwCount || 0 : 0;
    if (count >= CROSSWORD_DAILY)
      throw new HttpError(429, `Bugun ${CROSSWORD_DAILY} ta krossvord tuzdingiz. Ertaga yana urinib ko‘ring.`);
    tx.update(ref, { cwDay: day, cwCount: count + 1 });
  });
  const raw = await askGemini(Deck.buildCrosswordPrompt(topic, lang));
  let crossword;
  try { crossword = Deck.normalizeCrossword(raw); }
  catch (e) { throw new HttpError(500, "So‘zlardan krossvord tuzib bo‘lmadi. Yana bir marta bosing.", "cw_failed"); }
  if (body.deckId) {
    const dref = db.collection("decks").doc(String(body.deckId));
    const ds = await dref.get();
    if (ds.exists && ds.data().uid === u.uid) await dref.update({ "data.crossword": crossword });
  }
  return { crossword };
});

route("GET", "/api/history", async (req) => {
  const u = await auth(req);
  const snap = await db.collection("generations").where("uid", "==", u.uid).get();
  const items = snap.docs.map((d) => ({ id: d.id, title: d.data().title || d.data().topic, topic: d.data().topic,
    lang: d.data().lang, n: d.data().n, createdAt: iso(d.data().createdAt) })).sort(byNewest).slice(0, 30);
  return { items };
});

route("GET", "/api/deck", async (req, url) => {
  const u = await auth(req);
  const ds = await db.collection("decks").doc(String(url.searchParams.get("id") || "x")).get();
  if (!ds.exists || ds.data().uid !== u.uid) throw new HttpError(404, "Taqdimot topilmadi.");
  const d = ds.data();
  return { id: ds.id, data: d.data, lang: d.lang || "uz", subject: d.subject || "" };
});

route("POST", "/api/payments", async (req) => {
  const u = await auth(req);
  const body = await readBody(req);
  const s = await getSettings();
  const pkg = (s.packages || []).find((p) => p.id === body.packageId);
  if (!pkg) throw new HttpError(400, "Paketni tanlang.");
  const note = clean(body.note, 300);
  if (note.length < 3) throw new HttpError(400, "Izohga to‘lov vaqti yoki kartangizning oxirgi 4 raqamini yozing.");
  await ensureUser(u);
  const mine = await db.collection("payments").where("uid", "==", u.uid).get();
  if (mine.docs.filter((d) => d.data().status === "pending").length >= 3)
    throw new HttpError(429, "Sizda tekshirilmagan so‘rovlar bor. Admin tasdiqlashini kuting.");
  await db.collection("payments").add({
    uid: u.uid, email: u.email, name: u.name, packageId: pkg.id, title: pkg.title,
    credits: Number(pkg.credits) || 0, price: Number(pkg.price) || 0, note, status: "pending",
    createdAt: FV.serverTimestamp(),
  });
  return { ok: true };
});

route("GET", "/api/payments", async (req) => {
  const u = await auth(req);
  const snap = await db.collection("payments").where("uid", "==", u.uid).get();
  const items = snap.docs.map((d) => {
    const p = d.data();
    return { id: d.id, title: p.title, credits: p.credits, price: p.price, status: p.status, reason: p.reason || "",
      createdAt: iso(p.createdAt) };
  }).sort(byNewest).slice(0, 10);
  return { items };
});

// ---- admin
route("POST", "/api/admin/login", async (req) => {
  if (!ADMIN_LOGIN || !ADMIN_PASSWORD) throw new HttpError(503, "Admin login va paroli serverda (Render → Environment) kiritilmagan.", "admin_off");
  const ip = clientIp(req);
  if (loginBlocked(ip)) throw new HttpError(429, "Juda ko‘p noto‘g‘ri urinish. 15 daqiqadan keyin qayta urinib ko‘ring.", "locked");
  const { login, password } = await readBody(req);
  const okLogin = sameText(String(login || "").trim(), ADMIN_LOGIN);
  const okPass = sameText(String(password || ""), ADMIN_PASSWORD);
  if (!okLogin || !okPass) {
    noteFail(ip);
    log("admin: noto‘g‘ri urinish, manzil", ip);
    await new Promise((r) => setTimeout(r, 700));
    throw new HttpError(401, "Login yoki parol noto‘g‘ri.", "bad_login");
  }
  fails.delete(ip);
  log("admin: kirdi, manzil", ip);
  return makeAdminToken();
});

route("GET", "/api/admin/check", async (req) => { needAdmin(req); return { ok: true }; });

route("GET", "/api/admin/stats", async (req) => {
  needAdmin(req);
  const st = (await db.doc("stats/main").get()).data() || {};
  const pending = (await db.collection("payments").where("status", "==", "pending").get()).size;
  let today = null;
  try {
    today = (await db.collection("generations").where("createdAt", ">=", tashkentDayStart()).count().get()).data().count;
  } catch (e) { log("bugungi son:", e.message); }
  return { users: st.users || 0, generations: st.generations || 0, revenue: st.revenue || 0,
    approved: st.approvedPayments || 0, pending, today, warnings: WARN, models: MODELS,
    telegram: TG_BOT ? "@" + TG_BOT.username : null, site: SITE_URL || null };
});

route("GET", "/api/admin/payments", async (req, url) => {
  needAdmin(req);
  const status = url.searchParams.get("status") || "pending";
  const q = status === "all"
    ? db.collection("payments").orderBy("createdAt", "desc").limit(100)
    : db.collection("payments").where("status", "==", status);
  const items = (await q.get()).docs.map((d) => {
    const p = d.data();
    return { id: d.id, uid: p.uid, email: p.email, name: p.name, title: p.title, credits: p.credits, price: p.price,
      note: p.note, status: p.status, reason: p.reason || "", createdAt: iso(p.createdAt), decidedAt: iso(p.decidedAt) };
  }).sort(byNewest).slice(0, 100);
  return { items };
});

route("POST", "/api/admin/payments/approve", async (req) => {
  needAdmin(req);
  const { id } = await readBody(req);
  const pref = db.collection("payments").doc(String(id || "x"));
  await db.runTransaction(async (tx) => {
    const ps = await tx.get(pref);
    if (!ps.exists) throw new HttpError(404, "So‘rov topilmadi.");
    const p = ps.data();
    if (p.status !== "pending") throw new HttpError(400, "Bu so‘rov allaqachon ko‘rib chiqilgan.");
    tx.update(db.collection("users").doc(p.uid), { credits: FV.increment(p.credits) });
    tx.update(pref, { status: "approved", decidedAt: FV.serverTimestamp(), decidedBy: "admin" });
    tx.set(db.doc("stats/main"), { revenue: FV.increment(p.price || 0), approvedPayments: FV.increment(1) }, { merge: true });
  });
  return { ok: true };
});

route("POST", "/api/admin/payments/reject", async (req) => {
  needAdmin(req);
  const { id, reason } = await readBody(req);
  const pref = db.collection("payments").doc(String(id || "x"));
  await db.runTransaction(async (tx) => {
    const ps = await tx.get(pref);
    if (!ps.exists) throw new HttpError(404, "So‘rov topilmadi.");
    if (ps.data().status !== "pending") throw new HttpError(400, "Bu so‘rov allaqachon ko‘rib chiqilgan.");
    tx.update(pref, { status: "rejected", reason: clean(reason, 200), decidedAt: FV.serverTimestamp(), decidedBy: "admin" });
  });
  return { ok: true };
});

route("GET", "/api/admin/users", async (req, url) => {
  needAdmin(req);
  const q = clean(url.searchParams.get("q"), 100).toLowerCase();
  const query = q
    ? db.collection("users").where("email", ">=", q).where("email", "<=", q + "").limit(50)
    : db.collection("users").orderBy("createdAt", "desc").limit(50);
  const items = (await query.get()).docs.map((d) => {
    const u = d.data();
    return { uid: d.id, email: u.email, name: u.name, credits: u.credits || 0, used: u.used || 0, blocked: !!u.blocked,
      createdAt: iso(u.createdAt), lastSeen: iso(u.lastSeen) };
  });
  return { items };
});

route("POST", "/api/admin/users/credits", async (req) => {
  needAdmin(req);
  const { uid, delta } = await readBody(req);
  const n = Math.trunc(Number(delta));
  if (!n || Math.abs(n) > 1000) throw new HttpError(400, "Noto‘g‘ri son.");
  const ref = db.collection("users").doc(String(uid || "x"));
  let credits = 0;
  await db.runTransaction(async (tx) => {
    const s = await tx.get(ref);
    if (!s.exists) throw new HttpError(404, "Foydalanuvchi topilmadi.");
    credits = Math.max(0, (s.data().credits || 0) + n);
    tx.update(ref, { credits });
  });
  return { ok: true, credits };
});

route("POST", "/api/admin/users/block", async (req) => {
  needAdmin(req);
  const { uid, blocked } = await readBody(req);
  const ref = db.collection("users").doc(String(uid || "x"));
  if (!(await ref.get()).exists) throw new HttpError(404, "Foydalanuvchi topilmadi.");
  await ref.update({ blocked: !!blocked });
  return { ok: true };
});

route("GET", "/api/admin/settings", async (req) => {
  needAdmin(req);
  return { settings: await getSettings(true) };
});

route("POST", "/api/admin/settings", async (req) => {
  needAdmin(req);
  const b = await readBody(req);
  const seen = new Set();
  const packages = (Array.isArray(b.packages) ? b.packages : []).slice(0, 8).map((p, i) => {
    let id = clean(p.id, 20).replace(/[^\w-]/g, "") || `p${Date.now().toString(36)}${i}`;
    if (seen.has(id)) id += "_" + i;
    seen.add(id);
    return { id, title: clean(p.title, 60), credits: Math.max(1, Math.trunc(Number(p.credits)) || 1),
      price: Math.max(0, Math.trunc(Number(p.price)) || 0) };
  }).filter((p) => p.title);
  const s = {
    freeCredits: Math.min(100, Math.max(0, Math.trunc(Number(b.freeCredits)) || 0)),
    cardNumber: clean(b.cardNumber, 40), cardHolder: clean(b.cardHolder, 60),
    telegram: clean(b.telegram, 80).replace(/^https?:\/\/(t\.me|telegram\.me)\//i, "").replace(/^@/, ""),
    note: clean(b.note, 500), packages,
  };
  await db.doc("settings/main").set(s);
  return { settings: await getSettings(true) };
});

route("GET", "/api/admin/generations", async (req) => {
  needAdmin(req);
  const snap = await db.collection("generations").orderBy("createdAt", "desc").limit(60).get();
  return { items: snap.docs.map((d) => {
    const g = d.data();
    return { id: d.id, email: g.email, topic: g.topic, title: g.title, lang: g.lang, n: g.n, mode: g.mode || "topic", createdAt: iso(g.createdAt) };
  }) };
});

// ------------------------------------------------------------------ fayllar va server
const STATIC = {
  "/": "index.html", "/index.html": "index.html", "/admin": "index.html",
  "/app.js": "app.js", "/admin.js": "admin.js", "/deck.js": "deck.js", "/export.js": "export.js",
};
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8" };

function serveStatic(res, pathname) {
  if (pathname === "/healthz") { res.writeHead(200, { "Content-Type": "text/plain" }); return res.end("ok"); }
  const name = STATIC[pathname];
  if (!name) { res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" }); return res.end("Topilmadi"); }
  fs.readFile(path.join(ROOT, name), (err, buf) => {
    if (err) { res.writeHead(500); return res.end("Fayl yo‘q: " + name); }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(name)], "Cache-Control": "no-cache" });
    res.end(buf);
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (!url.pathname.startsWith("/api/")) return serveStatic(res, url.pathname);
  const fn = routes[req.method + " " + url.pathname];
  if (!fn) return send(res, 404, { error: "Topilmadi", code: "not_found" });
  if (SETUP.length && url.pathname !== "/api/config")
    return send(res, 503, { error: "Sayt hali sozlanmagan.", code: "setup" });
  try {
    send(res, 200, await fn(req, url));
  } catch (e) {
    if (e instanceof HttpError) return send(res, e.status, { error: e.message, code: e.code });
    log("server xatosi:", e && e.stack ? e.stack : e);
    const hint = /FAILED_PRECONDITION|NOT_FOUND.*database|database.*does not exist/i.test(String(e && e.message))
      ? " (Firestore bazasi yaratilmagan bo‘lishi mumkin)" : "";
    send(res, 500, { error: "Serverda xatolik. Birozdan keyin qayta urinib ko‘ring." + hint, code: "server" });
  }
});

server.listen(PORT, () => {
  log(`Sayt ishga tushdi: port ${PORT}`);
  log(`Modellar: ${MODELS.join(", ")} | admin login: ${ADMIN_LOGIN ? "bor" : "YO‘Q"} | kirish: ${GOOGLE ? "Google + " : ""}${TG_TOKEN ? "Telegram" : ""}`);
  if (SETUP.length) log("SOZLASH KERAK:", SETUP.join("; "));
  if (WARN.length) log("Ogohlantirish:", WARN.join("; "));
  if (!SETUP.length) setupTelegram().catch((e) => log("telegram sozlash xatosi:", e.message));
});
