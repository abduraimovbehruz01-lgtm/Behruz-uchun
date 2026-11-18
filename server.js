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
const zlib = require("zlib");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const admin = require("firebase-admin");
const Deck = require("./deck.js");
const Exact = require("./exact.js");
const JSZip = require("jszip");

// ------------------------------------------------------------------ sozlamalar (Render → Environment)
const PORT = Number(process.env.PORT) || 8080;
const GEMINI_KEY = (process.env.GEMINI_API_KEY || "").trim();
const GEMINI_BASE = (process.env.GEMINI_BASE || "https://generativelanguage.googleapis.com/v1beta").replace(/\/$/, "");
const MODELS = (process.env.GEMINI_MODEL || "gemini-3.8-flash,gemini-flash-latest,gemini-3.5-flash,gemini-4-hydrogen,gemini-3.5-flash-lite,gemini-flash-lite-latest")
  .split(",").map((s) => s.trim()).filter(Boolean);
const ADMIN_LOGIN = (process.env.ADMIN_LOGIN || "").trim();
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const ADMIN_SESSION_DAYS = 7;
// Telegram bot (ixtiyoriy): sayt bot ichida Web App bo'lib ochiladi
const TG_TOKEN = (process.env.TELEGRAM_BOT_TOKEN || "").trim();
const TG_API = (process.env.TELEGRAM_API_BASE || "https://api.telegram.org").replace(/\/$/, "");
const IMG_API = (process.env.IMAGE_API_BASE || "https://commons.wikimedia.org").replace(/\/$/, "");
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
  user.start_param = p.get("start_param") || "";
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
  await tg("setMyCommands", { commands: [{ command: "start", description: "Taqdimot yasash" },
    { command: "paysupport", description: "To‘lov bo‘yicha yordam" }, { command: "terms", description: "Shartlar" }] });
  if (TG_WEBHOOK) {
    await tg("setWebhook", { url: SITE_URL + "/api/tg/webhook", secret_token: TG_SECRET, allowed_updates: ["message", "callback_query", "pre_checkout_query"], drop_pending_updates: true });
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
  note: "To‘lov qilgach, chek rasmini (skrinshot) botga yuboring. Admin tasdiqlashi bilan jetonlar hisobingizga tushadi.",
  packages: [
    { id: "p5", title: "5 jeton", credits: 5, price: 10000, stars: 50 },
    { id: "p15", title: "15 jeton", credits: 15, price: 25000, stars: 125 },
    { id: "p40", title: "40 jeton", credits: 40, price: 50000, stars: 250 },
  ],
  refEvery: 5,          // har nechta taklif qilingan do'st uchun
  refBonus: 2,          // necha jeton beriladi
  requiredChannel: "",  // masalan @kanal — bo'sh bo'lsa, obuna talab qilinmaydi
  adminChats: [],       // cheklar keladigan Telegram akkauntlar [{id, name}]
};
// Har bir usul necha jeton turadi
const COST = { topic: 1, text: 1, sample: 2 };

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

function readBody(req, limit) {
  limit = limit || 200 * 1024;
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > limit) { reject(new HttpError(413, "Fayl yoki so‘rov juda katta.")); req.destroy(); return; }
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
      startParam: clean(t.start_param, 64),
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
  telegram: s.telegram, note: s.note, packages: s.packages, refEvery: s.refEvery, refBonus: s.refBonus,
  requiredChannel: s.requiredChannel || "", cost: COST,
});

// Foydalanuvchi yozuvi: birinchi kirishda bepul jetonlar beriladi.
// Tezlik uchun: mavjud foydalanuvchi oddiy o'qiladi, "oxirgi kirish" esa 30 daqiqada bir yangilanadi.
async function ensureUser(u) {
  const ref = db.collection("users").doc(u.uid);
  const snap = await ref.get();
  if (snap.exists) {
    const d = snap.data();
    const seen = d.lastSeen && typeof d.lastSeen.toDate === "function" ? d.lastSeen.toDate().getTime() : 0;
    if (Date.now() - seen > 30 * 60000 || d.name !== u.name || d.email !== u.email)
      ref.update({ lastSeen: FV.serverTimestamp(), email: u.email, name: u.name, photo: u.picture }).catch((e) => log("lastSeen:", e.message));
    return d;
  }
  const s = await getSettings();
  // Kim taklif qilgan: Mini App havolasidagi start_param yoki botdagi /start ref_... (pendingRefs)
  let refUid = /^ref_(tg_\d+|[\w-]{6,40})$/.exec(u.startParam || "") ? u.startParam.slice(4) : "";
  if (!refUid && u.tg) {
    const pr = await db.collection("pendingRefs").doc(String(u.tgId)).get().catch(() => null);
    if (pr && pr.exists) refUid = pr.data().ref || "";
  }
  if (refUid === u.uid) refUid = "";
  const created = await db.runTransaction(async (tx) => {
    const again = await tx.get(ref);
    if (again.exists) return { d: again.data(), isNew: false };
    const rref = refUid ? db.collection("users").doc(refUid) : null;
    const rs = rref ? await tx.get(rref) : null;
    const d = {
      email: u.email, name: u.name, photo: u.picture, credits: Number(s.freeCredits) || 0, used: 0,
      blocked: false, createdAt: FV.serverTimestamp(), lastSeen: FV.serverTimestamp(),
    };
    let bonus = 0;
    if (rs && rs.exists) {
      d.referredBy = refUid;
      const count = (rs.data().refCount || 0) + 1;
      const every = Math.max(1, Number(s.refEvery) || 5);
      bonus = count % every === 0 ? Math.max(0, Number(s.refBonus) || 0) : 0;
      tx.update(rref, { refCount: count, credits: FV.increment(bonus), refEarned: FV.increment(bonus) });
    }
    tx.set(ref, d);
    tx.set(db.doc("stats/main"), { users: FV.increment(1) }, { merge: true });
    return { d, isNew: true, bonus };
  });
  if (created.isNew && created.d.referredBy && created.d.referredBy.startsWith("tg_")) {
    const rid = created.d.referredBy.slice(3);
    tg("sendMessage", { chat_id: rid, text: created.bonus
      ? `🎉 Siz taklif qilgan do‘stlardan yana biri qo‘shildi! Hisobingizga +${created.bonus} jeton tushdi.`
      : `👥 Siz taklif qilgan do‘stingiz (${u.name || "yangi foydalanuvchi"}) ilovaga qo‘shildi.` });
  }
  return created.d;
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

async function callModel(model, prompt, withThinking, signal) {
  const gen = { responseMimeType: "application/json", maxOutputTokens: 30000 };
  // Tezlik uchun: Flash modellarda "minimal" fikrlash, boshqalarida "low". Model qo'llamasa — 400 → fikrlashsiz qayta.
  if (withThinking && /^gemini-([3-9]|flash)/.test(model)) gen.thinkingConfig = { thinkingLevel: /flash/.test(model) ? "minimal" : "low" };
  const parts = Array.isArray(prompt) ? prompt : [{ text: prompt }];
  const r = await fetch(`${GEMINI_BASE}/models/${encodeURIComponent(model)}:generateContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY },
    body: JSON.stringify({ contents: [{ role: "user", parts }], generationConfig: gen }),
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(150000)]) : AbortSignal.timeout(150000),
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
  if (r.status === 400 && gen.thinkingConfig) return callModel(model, prompt, false, signal);
  if (!r.ok) {
    const err = new Error(`${model} ${r.status}: ${txt.slice(0, 200).replace(/\s+/g, " ")}`);
    err.status = r.status;
    throw err;
  }
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

// Tezlik va ishonchlilik: modellar "poyga" usulida so'raladi.
// Birinchi model javob bermay turgan bo'lsa (Google yuklangan paytda 503 javobi 30–60 soniyada keladi),
// GEMINI_HEDGE_MS dan keyin keyingi model ham parallel so'raladi (bir vaqtda ko'pi bilan 3 ta). Birinchi to'g'ri javob olinadi,
// qolganlari bekor qilinadi. Biri band (503/429) desa — darhol keyingisi. Topilmagan (404) modellar 6 soat chetlab o'tiladi.
const HEDGE_MS = Math.max(1000, Number(process.env.GEMINI_HEDGE_MS) || 20000);
let lastGood = null;   // { model, ms }
const deadModels = new Map();
function raceModels(order, prompt) {
  return new Promise((resolve) => {
    let idx = 0, running = 0, done = false, busy = false, timer = null;
    const ctrls = new Set();
    const finish = (r) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      ctrls.forEach((c) => c.abort());
      resolve(r);
    };
    const launch = () => {
      clearTimeout(timer);
      if (done) return;
      if (idx >= order.length) { if (!running) finish({ ok: false, busy }); return; }
      if (running >= 3) return;
      const model = order[idx++];
      const ctrl = new AbortController();
      const t0 = Date.now();
      running++;
      ctrls.add(ctrl);
      callModel(model, prompt, true, ctrl.signal)
        .then((txt) => {
          const out = extractJson(txt);
          lastGood = { model, ms: Date.now() - t0 };
          log("gemini ok:", model, Math.round((Date.now() - t0) / 1000) + "s");
          finish({ ok: true, out });
        })
        .catch((e) => {
          if (done) return;
          if (e instanceof HttpError) return finish({ ok: false, fatal: e });
          if (e.status === 404) deadModels.set(model, Date.now() + 6 * 3600000);
          if (e.status === 503 || e.status === 429 || e.status === 500 || e.name === "TimeoutError") busy = true;
          log("gemini xato:", e.message);
        })
        .finally(() => { running--; ctrls.delete(ctrl); if (!done) launch(); });
      timer = setTimeout(launch, HEDGE_MS);
    };
    launch();
  });
}
async function askGemini(prompt) {
  if (!GEMINI_KEY) throw new HttpError(500, "Server hali to‘liq sozlanmagan (GEMINI_API_KEY yo‘q).", "ai_key");
  for (let round = 0; round < 2; round++) {
    if (round) await new Promise((r) => setTimeout(r, 4000));
    // oxirgi tez ishlagan model birinchi; sekin ishlagani (1 daqiqadan ko'p) oddiy navbatda qoladi
    const first = lastGood && lastGood.ms < 60000 ? lastGood.model : null;
    let order = (first ? [first, ...MODELS.filter((m) => m !== first)] : MODELS.slice()).filter((m) => (deadModels.get(m) || 0) <= Date.now());
    if (!order.length) order = MODELS.slice();
    const r = await raceModels(order, prompt);
    if (r.ok) return r.out;
    if (r.fatal) throw r.fatal;
    if (!r.busy) break;   // band bo'lmasa (masalan, javob buzuq) — qayta aylanish foydasiz
  }
  throw new HttpError(503, "AI hozir juda band (Google serverlari yuklangan). 1–2 daqiqadan keyin qayta urinib ko‘ring.", "ai_busy");
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

// eski usulda saqlangan .pptx namuna (faqat tahlil) — qayta yuklash kerak
const sampleInfo = (sm) => (sm && (sm.exact || (sm.profile && sm.kind !== "pptx")) ? {
  name: sm.name, kind: sm.kind, exact: !!sm.exact,
  summary: (sm.profile && sm.profile.summary_uz) || (sm.slides ? `${sm.slides} ta slayd` : ""),
  slideCount: sm.slides || (sm.profile && sm.profile.slide_count) || 0,
  contentSlides: sm.profile ? sm.profile.content_slides : 0, colors: sm.colors || null,
} : null);

// ------------------------------------------------------------------ 3-usul (.pptx): namunaning o'zidan yasash
const sampleCache = new Map();   // fileId -> Buffer (oxirgi 3 ta)
const builtCache = new Map();    // deckId -> {buf, name} (oxirgi 5 ta)
const BUILT_MAX = process.env.BUILT_CACHE == null ? 5 : Math.max(0, Number(process.env.BUILT_CACHE) || 0);
const imgCache = new Map();      // qidiruv natijalari
const cachePut = (m, k, v, max) => { m.delete(k); m.set(k, v); while (m.size > max) m.delete(m.keys().next().value); };
const UA = "TaqdimotBot/1.0 (+https://t.me/mustaqil_taqdimot_bot)";

async function loadSample(fileId) {
  if (sampleCache.has(fileId)) { const b = sampleCache.get(fileId); cachePut(sampleCache, fileId, b, 3); return b; }
  const f = await tg("getFile", { file_id: fileId });
  if (!f.ok) throw new HttpError(502, "Namuna faylini Telegram’dan olib bo‘lmadi. Namunani qayta yuklang.");
  const r = await fetch(`${TG_API}/file/bot${TG_TOKEN}/${f.result.file_path}`, { signal: AbortSignal.timeout(60000) });
  if (!r.ok) throw new HttpError(502, "Namuna faylini Telegram’dan olib bo‘lmadi. Qayta urinib ko‘ring.");
  const buf = Buffer.from(await r.arrayBuffer());
  cachePut(sampleCache, fileId, buf, 3);
  return buf;
}

async function zipSafe(buf) {
  const zip = await JSZip.loadAsync(buf);
  let total = 0;
  zip.forEach((p, f) => { total += (f._data && f._data.uncompressedSize) || 0; });
  if (total > 300 * 1024 * 1024) throw new Error("too big");
  return zip;
}

// Wikimedia Commons: bepul, kalitsiz rasm qidiruv
async function commons(q) {
  const key = q.toLowerCase();
  const c = imgCache.get(key);
  if (c && Date.now() - c.t < 86400000) return c.list;
  const url = `${IMG_API}/w/api.php?action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=15`
    + `&gsrsearch=${encodeURIComponent(q + " filetype:bitmap")}&prop=imageinfo&iiprop=url|size|mime&iiurlwidth=1280`;
  let list = [];
  try {
    const r = await fetch(url, { headers: { "User-Agent": UA }, signal: AbortSignal.timeout(8000) });
    const j = await r.json();
    list = Object.values((j.query && j.query.pages) || {}).sort((a, b) => (a.index || 0) - (b.index || 0))
      .filter((pg) => !/\b(logo|icon|flag of|coat of arms|signature|locator|map of|diagram|chart|graph)\b/i.test(pg.title || ""))
      .map((pg) => (pg.imageinfo || [])[0])
      .filter((ii) => ii && /^image\/(jpeg|png)$/.test(ii.mime) && ii.width >= 400 && ii.height >= 250 && ii.thumburl)
      .map((ii) => ({ url: ii.thumburl, w: ii.thumbwidth || ii.width, h: ii.thumbheight || ii.height, mime: ii.mime }));
  } catch (e) { log("rasm qidiruv xato:", q, e.message); }
  cachePut(imgCache, key, { t: Date.now(), list }, 500);
  return list;
}
async function download(img) {
  const r = await fetch(img.url, { headers: { "User-Agent": UA }, signal: AbortSignal.timeout(12000) });
  if (!r.ok) throw new Error("http " + r.status);
  const data = Buffer.from(await r.arrayBuffer());
  if (data.length < 500 || data.length > 6 * 1024 * 1024) throw new Error("size");
  const ext = data[0] === 0x89 && data[1] === 0x50 ? "png" : data[0] === 0xff && data[1] === 0xd8 ? "jpg" : null;
  if (!ext) throw new Error("not image");
  return { data, ext, w: img.w, h: img.h };
}
// har bir rasm joyi uchun mos rasm: avval slayd so'rovi, keyin umumiy mavzu
async function findImages(jobs) {
  const map = new Map(), list = [], usedUrl = new Set();
  let i = 0;
  const worker = async () => {
    while (i < jobs.length) {
      const job = jobs[i++];
      for (const q of [...new Set([job.query, job.fallback, job.fallback2].filter(Boolean))]) {
        const cands = (await commons(q)).filter((c) => !usedUrl.has(c.url));
        // ramkaga shakli yaqin rasm afzal (juda cho'ziq rasmlar tashlanadi)
        const fit = (c) => Math.abs(Math.log((c.w / c.h) / (job.ar || 1.33)));
        const pick = cands.filter((c) => fit(c) < 0.8).slice(0, 3).concat(cands.slice(0, 2));
        let got = null;
        for (const c of pick) {
          if (usedUrl.has(c.url)) continue;
          usedUrl.add(c.url);
          try { got = await download(c); got.url = c.url; break; } catch (e) { /* keyingisi */ }
        }
        if (got) { map.set(job.slide + ":" + job.pic, got); list.push({ k: job.slide + ":" + job.pic, url: got.url, w: got.w, h: got.h }); break; }
      }
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  return { map, list };
}
async function refetchImages(list) {
  const map = new Map();
  await Promise.all((list || []).map(async (it) => {
    try { const g = await download(it); map.set(it.k, g); } catch (e) { /* rasm topilmasa eski joy o'zgarmaydi */ }
  }));
  return map;
}

async function sendFileToChat(u, buf, name, caption) {
  const fd = new FormData();
  fd.append("chat_id", String(u.tgId));
  if (caption) fd.append("caption", caption);
  fd.append("document", new Blob([buf]), name);
  const r = await tg("sendDocument", fd);
  if (r.ok) return r;
  if (r.error_code === 403 || /chat not found|bot can't initiate/i.test(r.description || ""))
    throw new HttpError(400, "Bot sizga fayl yubora olmadi. Avval botga /start yozing, keyin «Yuklab olish»ni bosing.", "tg_start");
  throw new HttpError(502, "Faylni Telegram’ga yuborib bo‘lmadi. «Yuklab olish»ni qayta bosing.");
}

async function exactFile(deckId, dd) {
  const c = builtCache.get(deckId);
  if (c) return c;
  const sm = (await db.collection("samples").doc(dd.sid).get()).data();
  if (!sm) throw new HttpError(410, "Namuna fayli topilmadi. Namunani qayta yuklab, taqdimotni qayta yasang.");
  const buf = await loadSample(sm.fileId);
  const images = await refetchImages(dd.deck.imgs);
  const out = { buf: await Exact.build(JSZip, buf, sm.sk, dd.deck, images), name: Deck.safeFilename(dd.deck.title || "taqdimot") };
  cachePut(builtCache, deckId, out, BUILT_MAX);
  return out;
}

route("GET", "/api/me", async (req) => {
  const u = await auth(req);
  const d = await ensureUser(u);
  return {
    uid: u.uid, email: u.email, name: u.name, photo: u.picture, credits: d.credits || 0, used: d.used || 0,
    blocked: !!d.blocked, tg: !!u.tg, refCount: d.refCount || 0, refEarned: d.refEarned || 0,
    refLink: TG_BOT ? `https://t.me/${TG_BOT.username}?start=ref_${u.uid}` : null, sample: sampleInfo(d.sample),
  };
});

// Majburiy kanal obunasi (sozlamada kanal ko'rsatilgan bo'lsa). Natija 30 daqiqa eslab qolinadi.
const subCache = new Map();
async function checkChannel(u) {
  const s = await getSettings();
  const ch = String(s.requiredChannel || "").trim();
  if (!ch || !u.tg || !TG_TOKEN) return;
  const key = u.uid + "|" + ch;
  if ((subCache.get(key) || 0) > Date.now()) return;
  const r = await tg("getChatMember", { chat_id: ch, user_id: u.tgId });
  if (!r.ok) { log("kanal tekshiruvi:", r.description); return; }   // bot kanalga admin qilinmagan — foydalanuvchini to'xtatmaymiz
  const st = r.result && r.result.status;
  if (["creator", "administrator", "member"].includes(st) || (st === "restricted" && r.result.is_member)) {
    subCache.set(key, Date.now() + 30 * 60000);
    if (subCache.size > 20000) subCache.clear();
    return;
  }
  throw new HttpError(403, `Davom etish uchun ${ch} kanaliga obuna bo‘ling, keyin qayta bosing.`, "need_sub");
}

// ---- 3-usul uchun namuna: yuklash, tahlil qilish, saqlash
route("POST", "/api/sample", async (req) => {
  const u = await auth(req);
  const body = await readBody(req, 12 * 1024 * 1024);
  const kind = ["docx", "pdf"].includes(body.kind) ? body.kind : null;
  if (body.kind === "pptx") throw new HttpError(400, "Ilovani yopib, qayta oching — PowerPoint namuna yangi usulda yuklanadi.");
  if (!kind) throw new HttpError(400, "Namuna PowerPoint (.pptx), PDF yoki Word (.docx) bo‘lishi kerak.");
  const name = clean(body.name, 120) || "namuna";
  const text = kind === "pdf" ? "" : String(body.text || "").replace(/\r\n?/g, "\n").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim().slice(0, 40000);
  const pdfRaw = kind === "pdf" ? String(body.pdf || "") : "";
  if (kind === "pdf" && /^data:/.test(pdfRaw) && !/^data:application\/pdf[;,]/.test(pdfRaw)) throw new HttpError(400, "Bu PDF fayl emas.");
  const pdf = pdfRaw.replace(/^data:[^,]*,/, "");
  if (kind !== "pdf" && text.length < 80) throw new HttpError(400, "Namunada matn juda kam. Boshqa fayl tanlang.");
  if (kind === "pdf" && (pdf.length < 200 || pdf.length > 11.5 * 1024 * 1024)) throw new HttpError(400, "PDF fayl bo‘sh yoki juda katta (ko‘pi bilan 8 MB).");
  const d = await ensureUser(u);
  if (d.blocked) throw new HttpError(403, "Hisobingiz bloklangan.", "blocked");
  const ref = db.collection("users").doc(u.uid);
  const day = tashkentDay();
  const cnt = d.smDay === day ? d.smCount || 0 : 0;
  if (cnt >= 10) throw new HttpError(429, "Bugun 10 ta namuna yukladingiz. Ertaga yana urinib ko‘ring.");
  await ref.update({ smDay: day, smCount: cnt + 1 });
  const prompt = Deck.buildSampleAnalysisPrompt(text);
  const parts = kind === "pdf" ? [{ inlineData: { mimeType: "application/pdf", data: pdf } }, { text: prompt }] : prompt;
  let profile;
  try { profile = Deck.normalizeProfile(await askGemini(parts)); }
  catch (e) {
    if (e instanceof HttpError) throw e;
    throw new HttpError(500, "Namunani tahlil qilib bo‘lmadi. Boshqa fayl bilan urinib ko‘ring.");
  }
  const hex = (x) => (typeof x === "string" && /^[0-9a-f]{6}$/i.test(x) ? x.toUpperCase() : null);
  const c = body.colors || {};
  const colors = hex(c.dark) && hex(c.accent) ? { dark: hex(c.dark), light: hex(c.light) || "F6F7FB", accent: hex(c.accent), accent2: hex(c.accent2) } : null;
  const sample = { name, kind, profile, colors, at: FV.serverTimestamp() };
  await ref.update({ sample });
  return { sample: sampleInfo(sample) };
});

// .pptx namuna: fayl Telegram'da saqlanadi (foydalanuvchining bot chatiga), tuzilishi — bazada
route("POST", "/api/sample/pptx", async (req, url) => {
  const u = await auth(req);
  if (!u.tg) throw new HttpError(400, "Bu imkoniyat faqat Telegram ichida ishlaydi.");
  let name = clean(url.searchParams.get("name"), 120).replace(/[\\/:*?"<>|]/g, "") || "namuna.pptx";
  if (!/\.pptx$/i.test(name)) name += ".pptx";
  const d = await ensureUser(u);
  if (d.blocked) throw new HttpError(403, "Hisobingiz bloklangan.", "blocked");
  const day = tashkentDay();
  const cnt = d.smDay === day ? d.smCount || 0 : 0;
  if (cnt >= 10) throw new HttpError(429, "Bugun 10 ta namuna yukladingiz. Ertaga yana urinib ko‘ring.");
  const buf = await readRaw(req, 20 * 1024 * 1024).catch((e) => {
    if (e instanceof HttpError && e.status === 413) throw new HttpError(413, "Namuna juda katta (ko‘pi bilan 20 MB). Rasmlarni siqib yoki kamroq slayd bilan saqlang.");
    throw e;
  });
  if (buf.length < 1000) throw new HttpError(400, "Fayl bo‘sh.");
  let sk;
  try { sk = await Exact.parse(await zipSafe(buf)); }
  catch (e) { throw new HttpError(400, "Faylni o‘qib bo‘lmadi. PowerPoint’da «.pptx» qilib qayta saqlab, yana yuklang."); }
  if (sk.slides.length > 60) throw new HttpError(400, "Namunada slayd juda ko‘p (ko‘pi bilan 60).");
  if (!sk.slides.some((x) => x.slots.length)) throw new HttpError(400, "Namunada matn topilmadi. Matnli taqdimot yuklang.");
  const ref = db.collection("users").doc(u.uid);
  await ref.update({ smDay: day, smCount: cnt + 1 });
  const outline = sk.outline;
  delete sk.outline;
  const fd = new FormData();
  fd.append("chat_id", String(u.tgId));
  fd.append("caption", "🎯 Namuna saqlandi. Endi ilovada yangi mavzuni yozing — taqdimot shu namunaning o‘zidek yasaladi.");
  fd.append("disable_notification", "true");
  fd.append("document", new Blob([buf]), name);
  const [r, profile] = await Promise.all([
    tg("sendDocument", fd),
    askGemini(Deck.buildSampleAnalysisPrompt(outline)).then((x) => Deck.normalizeProfile(x)).catch((e) => { log("namuna tahlili:", e.message); return null; }),
  ]);
  if (!r.ok || !r.result || !r.result.document) {
    if (r.error_code === 403 || /chat not found|bot can't initiate/i.test(r.description || ""))
      throw new HttpError(400, "Bot sizga yoza olmadi. Avval botga /start yozing, keyin namunani qayta yuklang.", "tg_start");
    throw new HttpError(502, "Namunani saqlab bo‘lmadi. Qayta urinib ko‘ring.");
  }
  const doc = r.result.document;
  const sid = "s_" + crypto.createHash("sha256").update(doc.file_unique_id || doc.file_id).digest("hex").slice(0, 24);
  cachePut(sampleCache, doc.file_id, buf, 3);
  await db.collection("samples").doc(sid).set({ uid: u.uid, fileId: doc.file_id, name, sk: JSON.parse(JSON.stringify(sk)), at: FV.serverTimestamp() });
  const sample = { name, kind: "pptx", exact: true, sid, slides: sk.slides.length, profile: profile || null, at: FV.serverTimestamp() };
  await ref.update({ sample });
  return { sample: sampleInfo(sample) };
});

route("POST", "/api/sample/clear", async (req) => {
  const u = await auth(req);
  await ensureUser(u);
  await db.collection("users").doc(u.uid).update({ sample: FV.delete() });
  return { ok: true };
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

// ------------------------------------------------------------------ Telegram bot: xabarlar, cheklar, Stars
const appButton = (text) => ({ inline_keyboard: [[{ text: text || "📊 Taqdimot yasash", web_app: { url: SITE_URL } }]] });
const fmtSum = (n) => Number(n || 0).toLocaleString("ru-RU").replace(/[\u00a0\u202f]/g, " ") + " so‘m";
const isAdminChat = (s, id) => (s.adminChats || []).some((a) => String(a.id) === String(id));

route("POST", "/api/tg/webhook", async (req) => {
  if (!TG_TOKEN || !sameText(req.headers["x-telegram-bot-api-secret-token"] || "", TG_SECRET)) throw new HttpError(403, "forbidden");
  const upd = await readBody(req);
  await handleUpdate(upd).catch((e) => log("bot xatosi:", e && e.stack ? e.stack : e));
  return { ok: true };
});

async function handleUpdate(upd) {
  const s = await getSettings();
  if (upd.pre_checkout_query) return onPreCheckout(upd.pre_checkout_query, s);
  if (upd.callback_query) return onCallback(upd.callback_query, s);
  const msg = upd.message;
  if (!msg || !msg.chat || msg.chat.type !== "private" || !msg.from) return;
  if (msg.successful_payment) return onStarsPaid(msg, s);
  if (msg.photo || msg.document) return onReceipt(msg, s);
  const text = String(msg.text || "").trim();
  const cmd = (/^\/(\w+)(?:@\w+)?\s*(.*)$/.exec(text) || []);
  if (cmd[1] === "admin" && SITE_URL) {
    return tg("sendMessage", { chat_id: msg.chat.id,
      text: "⚙️ Admin panel. Ochilgan oynada login va parolni kiriting — Telegram 7 kun eslab qoladi.",
      reply_markup: { inline_keyboard: [[{ text: "⚙️ Admin panelni ochish", web_app: { url: SITE_URL + "/admin" } }]] } });
  }
  if (cmd[1] === "paysupport") {
    return tg("sendMessage", { chat_id: msg.chat.id, text: `💬 To‘lov bo‘yicha savollar uchun yozing: ${s.telegram ? "@" + s.telegram : "admin"}.\n` +
      "Jetonlar tushmagan bo‘lsa, to‘lov vaqti va chekni yuboring." });
  }
  if (cmd[1] === "terms") {
    return tg("sendMessage", { chat_id: msg.chat.id, text: "📄 Shartlar: jetonlar taqdimot yasash uchun ishlatiladi. To‘langan jetonlar " +
      "qaytarilmaydi, lekin texnik xato bo‘lsa, jeton avtomatik qaytariladi. AI yozgan matnni topshirishdan oldin tekshiring." });
  }
  if (cmd[1] === "start" && /^ref_[\w-]{6,40}$/.test(cmd[2] || "")) {
    const exists = await db.collection("users").doc("tg_" + msg.from.id).get();
    if (!exists.exists && cmd[2].slice(4) !== "tg_" + msg.from.id)
      await db.collection("pendingRefs").doc(String(msg.from.id)).set({ ref: cmd[2].slice(4), at: FV.serverTimestamp() });
  }
  if (!SITE_URL) return;
  const first = clean(msg.from.first_name, 40);
  return tg("sendMessage", { chat_id: msg.chat.id,
    text: `Salom${first ? ", " + first : ""}! 👋\n\nMavzuni yozing — tayyor taqdimot (PowerPoint, PDF, Word) va krossvord olasiz. ` +
      `Yangi foydalanuvchiga ${s.freeCredits} ta jeton bepul.\n\nPastdagi tugmani bosing 👇`, reply_markup: appButton() });
}

// Chek rasmi: foydalanuvchining "chek kutilmoqda" holatidagi so'roviga biriktiriladi va adminga yuboriladi
async function onReceipt(msg, s) {
  const uid = "tg_" + msg.from.id;
  const doc = msg.document;
  if (doc && (/presentation|wordprocessing|powerpoint/.test(doc.mime_type || "") || /\.(pptx?|docx?)$/i.test(doc.file_name || "")))
    return tg("sendMessage", { chat_id: msg.chat.id, text: "🎯 Namunani ilovadan yuklang: «Taqdimot yasash» → «🎯 Namuna» → «📎 Namuna faylini tanlash».",
      reply_markup: appButton("🎯 Namunadek yasash") });
  if (doc && !/^image\/|application\/pdf/.test(doc.mime_type || ""))
    return tg("sendMessage", { chat_id: msg.chat.id, text: "Chekni rasm (skrinshot) yoki PDF ko‘rinishida yuboring." });
  const mine = await db.collection("payments").where("uid", "==", uid).get();
  const since = Date.now() - 48 * 3600000;
  const wait = mine.docs.map((d) => ({ id: d.id, ...d.data() }))
    .filter((p) => p.status === "awaiting_receipt" && (!p.createdAt || !p.createdAt.toDate || p.createdAt.toDate().getTime() > since))
    .sort((a, b) => String(iso(b.createdAt)).localeCompare(String(iso(a.createdAt))))[0];
  if (!wait) {
    return tg("sendMessage", { chat_id: msg.chat.id,
      text: "Chekni qabul qilish uchun avval ilovada paketni tanlang: 🎟 tugmasi → paket → «Chekni botga yuborish».",
      reply_markup: appButton("🎟 Paket tanlash") });
  }
  const receipt = msg.photo ? { type: "photo", fileId: msg.photo[msg.photo.length - 1].file_id } : { type: "document", fileId: doc.file_id };
  await db.collection("payments").doc(wait.id).update({ status: "pending", receipt, receivedAt: FV.serverTimestamp() });
  await tg("sendMessage", { chat_id: msg.chat.id,
    text: `✅ Chek qabul qilindi: «${wait.title}» — ${fmtSum(wait.price)}.\nAdmin tekshirgach, jetonlar hisobingizga avtomatik tushadi va sizga xabar keladi.` });
  const who = `${clean(msg.from.first_name, 40)}${msg.from.username ? " (@" + msg.from.username + ")" : ""} · ID ${msg.from.id}`;
  const caption = `💳 Yangi to‘lov\n👤 ${who}\n📦 ${wait.title} — ${fmtSum(wait.price)}\n➕ ${wait.credits} jeton`;
  const markup = { inline_keyboard: [[{ text: `✅ Tasdiqlash (+${wait.credits})`, callback_data: "pay:ok:" + wait.id },
    { text: "❌ Rad etish", callback_data: "pay:no:" + wait.id }]] };
  for (const a of s.adminChats || []) {
    if (receipt.type === "photo") await tg("sendPhoto", { chat_id: a.id, photo: receipt.fileId, caption, reply_markup: markup });
    else await tg("sendDocument", { chat_id: a.id, document: receipt.fileId, caption, reply_markup: markup });
  }
}

// Admin Telegram'dagi tugmani bosdi
async function onCallback(cb, s) {
  const m = /^pay:(ok|no):([\w-]{1,60})$/.exec(cb.data || "");
  if (!m) return tg("answerCallbackQuery", { callback_query_id: cb.id });
  if (!isAdminChat(s, cb.from.id)) return tg("answerCallbackQuery", { callback_query_id: cb.id, text: "Ruxsat yo‘q.", show_alert: true });
  let note;
  try {
    if (m[1] === "ok") { const p = await approvePayment(m[2], "telegram"); note = `✅ Tasdiqlandi: +${p.credits} jeton qo‘shildi`; }
    else { await rejectPayment(m[2], "Chek tasdiqlanmadi", "telegram"); note = "❌ Rad etildi"; }
  } catch (e) {
    return tg("answerCallbackQuery", { callback_query_id: cb.id, text: e.message, show_alert: true });
  }
  await tg("answerCallbackQuery", { callback_query_id: cb.id, text: note });
  const msg = cb.message;
  if (msg) {
    const base = msg.caption != null ? msg.caption : msg.text || "";
    if (msg.caption != null) await tg("editMessageCaption", { chat_id: msg.chat.id, message_id: msg.message_id, caption: `${base}\n\n${note}` });
    else await tg("editMessageText", { chat_id: msg.chat.id, message_id: msg.message_id, text: `${base}\n\n${note}` });
  }
}

// Stars to'lovi: Telegram to'lovdan oldin tekshiradi
async function onPreCheckout(q, s) {
  const [tag, uid, pkgId] = String(q.invoice_payload || "").split("|");
  const pkg = (s.packages || []).find((p) => p.id === pkgId);
  const ok = tag === "st" && uid === "tg_" + q.from.id && pkg && q.currency === "XTR" && Number(pkg.stars) === Number(q.total_amount);
  return tg("answerPreCheckoutQuery", ok ? { pre_checkout_query_id: q.id, ok: true }
    : { pre_checkout_query_id: q.id, ok: false, error_message: "Narx o‘zgargan. Ilovani yopib, qayta oching." });
}

// Stars to'landi — jeton darhol qo'shiladi (bir to'lov ikki marta hisoblanmaydi)
async function onStarsPaid(msg, s) {
  const sp = msg.successful_payment;
  const [tag, uid, pkgId] = String(sp.invoice_payload || "").split("|");
  if (tag !== "st" || uid !== "tg_" + msg.from.id) return;
  const pkg = (s.packages || []).find((p) => p.id === pkgId) || {};
  const credits = Number(pkg.credits) || 0;
  const pref = db.collection("payments").doc("stars_" + String(sp.telegram_payment_charge_id).replace(/[^\w-]/g, "").slice(0, 80));
  const added = await db.runTransaction(async (tx) => {
    if ((await tx.get(pref)).exists) return false;
    const uref = db.collection("users").doc(uid);
    tx.set(pref, { uid, email: msg.from.username ? "@" + msg.from.username.toLowerCase() : "tg:" + msg.from.id, name: clean(msg.from.first_name, 80),
      packageId: pkgId, title: pkg.title || pkgId, credits, price: 0, stars: sp.total_amount, method: "stars", status: "approved",
      chargeId: sp.telegram_payment_charge_id, createdAt: FV.serverTimestamp(), decidedAt: FV.serverTimestamp(), decidedBy: "stars" });
    tx.set(uref, { credits: FV.increment(credits) }, { merge: true });
    tx.set(db.doc("stats/main"), { stars: FV.increment(sp.total_amount), approvedPayments: FV.increment(1) }, { merge: true });
    return true;
  });
  if (!added) return;
  await tg("sendMessage", { chat_id: msg.chat.id, text: `✅ To‘lov qabul qilindi! Hisobingizga +${credits} jeton qo‘shildi.`, reply_markup: appButton() });
  for (const a of s.adminChats || [])
    await tg("sendMessage", { chat_id: a.id, text: `⭐ Stars to‘lovi: ${clean(msg.from.first_name, 40)} — ${pkg.title || pkgId}, ${sp.total_amount} ⭐` });
}

async function notifyPayment(p, approved, reason) {
  if (!p || !String(p.uid || "").startsWith("tg_")) return;
  const s = await getSettings();
  await tg("sendMessage", { chat_id: p.uid.slice(3), text: approved
    ? `✅ To‘lovingiz tasdiqlandi! Hisobingizga +${p.credits} jeton qo‘shildi.`
    : `❌ To‘lov tasdiqlanmadi${reason ? ": " + reason : ""}.\nSavol bo‘lsa: ${s.telegram ? "@" + s.telegram : "admin"}.`, reply_markup: appButton() });
}

async function approvePayment(id, by) {
  const pref = db.collection("payments").doc(String(id || "x"));
  const p = await db.runTransaction(async (tx) => {
    const ps = await tx.get(pref);
    if (!ps.exists) throw new HttpError(404, "So‘rov topilmadi.");
    const p = ps.data();
    if (!["pending", "awaiting_receipt"].includes(p.status)) throw new HttpError(400, "Bu so‘rov allaqachon ko‘rib chiqilgan.");
    tx.set(db.collection("users").doc(p.uid), { credits: FV.increment(p.credits) }, { merge: true });
    tx.update(pref, { status: "approved", decidedAt: FV.serverTimestamp(), decidedBy: by });
    tx.set(db.doc("stats/main"), { revenue: FV.increment(p.price || 0), approvedPayments: FV.increment(1) }, { merge: true });
    return p;
  });
  notifyPayment(p, true).catch(() => {});
  return p;
}

async function rejectPayment(id, reason, by) {
  const pref = db.collection("payments").doc(String(id || "x"));
  const p = await db.runTransaction(async (tx) => {
    const ps = await tx.get(pref);
    if (!ps.exists) throw new HttpError(404, "So‘rov topilmadi.");
    if (!["pending", "awaiting_receipt"].includes(ps.data().status)) throw new HttpError(400, "Bu so‘rov allaqachon ko‘rib chiqilgan.");
    tx.update(pref, { status: "rejected", reason: clean(reason, 200), decidedAt: FV.serverTimestamp(), decidedBy: by });
    return ps.data();
  });
  notifyPayment(p, false, clean(reason, 200)).catch(() => {});
  return p;
}

// Ilovada paket tanlandi → bot foydalanuvchidan chek rasmini so'raydi
route("POST", "/api/payments/start", async (req) => {
  const u = await auth(req);
  if (!u.tg) throw new HttpError(400, "Bu usul faqat Telegram ichida ishlaydi.");
  const body = await readBody(req);
  const s = await getSettings();
  const pkg = (s.packages || []).find((p) => p.id === body.packageId);
  if (!pkg) throw new HttpError(400, "Paketni tanlang.");
  if (!s.cardNumber) throw new HttpError(400, "To‘lov ma’lumotlari hali kiritilmagan.");
  await ensureUser(u);
  const mine = await db.collection("payments").where("uid", "==", u.uid).get();
  const open = mine.docs.filter((d) => d.data().status === "pending").length;
  if (open >= 3) throw new HttpError(429, "Sizda tekshirilmagan cheklar bor. Admin tasdiqlashini kuting.");
  for (const d of mine.docs) if (d.data().status === "awaiting_receipt") await d.ref.update({ status: "cancelled" });
  await db.collection("payments").add({
    uid: u.uid, email: u.email, name: u.name, packageId: pkg.id, title: pkg.title, credits: Number(pkg.credits) || 0,
    price: Number(pkg.price) || 0, method: "card", status: "awaiting_receipt", note: "Chek bot orqali", createdAt: FV.serverTimestamp(),
  });
  const card = String(s.cardNumber).replace(/\s+/g, "").replace(/(\d{4})(?=\d)/g, "$1 ");
  const r = await tg("sendMessage", { chat_id: u.tgId, text:
    `💳 «${pkg.title}» — ${fmtSum(pkg.price)}\n\n1) Shu kartaga o‘tkazing:\n${card}${s.cardHolder ? "\n" + s.cardHolder : ""}\n\n` +
    "2) To‘lov chekining rasmini (skrinshot) shu chatga yuboring 📸\n\nAdmin tasdiqlashi bilan jetonlar hisobingizga avtomatik tushadi." });
  if (!r.ok) throw new HttpError(400, "Bot sizga yoza olmadi. Avval botga /start yozing, keyin qayta urining.", "tg_start");
  return { ok: true };
});

// Stars: Telegram to'lov oynasi uchun havola
route("POST", "/api/stars/invoice", async (req) => {
  const u = await auth(req);
  if (!u.tg) throw new HttpError(400, "Stars faqat Telegram ichida ishlaydi.");
  const body = await readBody(req);
  const s = await getSettings();
  const pkg = (s.packages || []).find((p) => p.id === body.packageId);
  if (!pkg || !(Number(pkg.stars) > 0)) throw new HttpError(400, "Bu paketni Stars bilan sotib bo‘lmaydi.");
  const r = await tg("createInvoiceLink", {
    title: String(pkg.title).slice(0, 32), description: `${pkg.credits} jeton — taqdimot yasash uchun`.slice(0, 255),
    payload: `st|${u.uid}|${pkg.id}|${Date.now()}`, currency: "XTR", prices: [{ label: String(pkg.title).slice(0, 32), amount: Number(pkg.stars) }],
  });
  if (!r.ok) throw new HttpError(502, "To‘lov oynasini ochib bo‘lmadi. Birozdan keyin urinib ko‘ring.");
  return { link: r.result };
});

route("POST", "/api/generate", async (req) => {
  const u = await auth(req);
  const body = await readBody(req);
  // Uch usul: "topic" — mavzu bo'yicha, "text" — o'z matnidan, "sample" — namunaga o'xshatib
  const mode = ["text", "sample"].includes(body.mode) ? body.mode : "topic";
  const cost = COST[mode];
  const topic = clean(body.topic, 200);
  const source = mode === "text"
    ? String(body.source || "").replace(/\r\n?/g, "\n").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim().slice(0, 30000) : "";
  if (mode !== "text" && topic.length < 3) throw new HttpError(400, "Mavzuni to‘liqroq yozing.");
  if (mode === "text" && source.length < 200) throw new HttpError(400, "Matn juda qisqa. Kamida bir-ikki xatboshi joylang.");
  const subject = clean(body.subject, 50);
  const n = [10, 12, 15, 20].includes(Number(body.n)) ? Number(body.n) : 12;
  const lang = langOf(body.lang);
  const u0 = await ensureUser(u);
  if (mode === "sample" && !sampleInfo(u0.sample)) throw new HttpError(400, "Avval namuna faylini yuklang.", "no_sample");
  const exact = mode === "sample" && !!u0.sample.exact;
  let smDoc = null;
  if (exact) {
    smDoc = (await db.collection("samples").doc(u0.sample.sid).get()).data();
    if (!smDoc || !smDoc.sk) throw new HttpError(400, "Namunani qayta yuklang.", "no_sample");
  }
  await checkChannel(u);
  const ref = db.collection("users").doc(u.uid);
  let profile = null;
  await db.runTransaction(async (tx) => {
    const d = (await tx.get(ref)).data();
    if (d.blocked) throw new HttpError(403, "Hisobingiz bloklangan. Admin bilan bog‘laning.", "blocked");
    if ((d.credits || 0) < cost)
      throw new HttpError(402, (d.credits || 0) > 0 ? `Jeton yetarli emas: bu usul ${cost} jeton turadi.` : "Jetonlar tugadi.", "no_credits");
    if (mode === "sample") profile = d.sample && d.sample.profile;
    tx.update(ref, { credits: FV.increment(-cost), used: FV.increment(1) });
  });
  // krossvord (ixtiyoriy): taqdimot bilan parallel tuziladi — kutish vaqti qo'shilmaydi
  const wantCw = body.crossword === true || body.crossword === 1 || body.crossword === "1";
  const cwTopic = topic || clean((source.split("\n").find((l) => l.trim().length > 3) || ""), 150);
  const cwJob = wantCw && cwTopic
    ? askGemini(Deck.buildCrosswordPrompt(cwTopic, lang)).then((raw) => Deck.normalizeCrossword(raw)).catch((e) => { log("krossvord xato:", e.message); return null; })
    : null;
  let data, exactDeck = null, exactBuf = null, nOut = n;
  try {
    if (exact) {
      const sk = smDoc.sk;
      const nReq = Math.trunc(Number(body.n)) || 0;
      const designs = Exact.plan(sk, nReq ? Math.min(30, Math.max(3, nReq)) : sk.slides.length);
      const prompt = Deck.buildExactPrompt(sk, designs, { topic, subject, author: clean(body.author, 80), lang, profile });
      const [raw, sampleBuf] = await Promise.all([askGemini(prompt), loadSample(smDoc.fileId)]);
      exactDeck = Exact.normalize(raw, sk, designs);
      if (!exactDeck.title) exactDeck.title = topic;
      const imgs = await findImages(Exact.imageJobs(sk, exactDeck, topic));
      exactDeck.imgs = imgs.list;
      exactBuf = await Exact.build(JSZip, sampleBuf, sk, exactDeck, imgs.map);
      data = { exact: true, title: exactDeck.title, outline: Exact.outline(exactDeck) };
      nOut = exactDeck.slides.length;
    } else {
    const prompt = mode === "text" ? Deck.buildTextPrompt(source, topic, subject, n, lang)
      : mode === "sample" ? Deck.buildSamplePrompt(topic, subject, profile, lang) : Deck.buildPrompt(topic, subject, n, lang);
    data = Deck.normalize(await askGemini(prompt), topic || "Taqdimot");
    if (mode === "sample") {
      const skip = [];
      if (profile.has_plan === false) skip.push("plan");
      if (profile.has_references === false) skip.push("references");
      if (profile.has_thanks === false) skip.push("thanks");
      if (skip.length) data.skip = skip;
    }
    try { await attachImages(data, topic || data.title); } catch (e) { log("rasm tanlash xato:", e.message); }
    }
  } catch (e) {
    await ref.update({ credits: FV.increment(cost), used: FV.increment(-1) });   // xato bo'lsa jeton qaytariladi
    if (e instanceof HttpError) {
      if (e.status !== 401 && e.status !== 403) e.message += " Jetoningiz qaytarildi.";
      throw e;
    }
    log(exact ? "namunadek yasash xato:" : "normalize xato:", e.message);
    throw new HttpError(500, "Taqdimot matnini olib bo‘lmadi. Qayta urinib ko‘ring — jetoningiz qaytarildi.");
  }
  let cwError = null;
  if (cwJob) {
    const cw = await cwJob;
    if (cw) data.crossword = cw;
    else cwError = "Krossvordni tuzib bo‘lmadi.";
  }
  const gen = db.collection("generations").doc();
  const batch = db.batch();
  batch.set(gen, { uid: u.uid, email: u.email, topic: topic || clean(data.title, 120), title: clean(data.title, 120), subject, lang, n: nOut, mode, cost,
    createdAt: FV.serverTimestamp() });
  batch.set(db.collection("decks").doc(gen.id), exact
    ? { uid: u.uid, data, lang, subject, exact: true, sid: u0.sample.sid, deck: JSON.parse(JSON.stringify(exactDeck)), createdAt: FV.serverTimestamp() }
    : { uid: u.uid, data, lang, subject, createdAt: FV.serverTimestamp() });
  batch.set(db.doc("stats/main"), { generations: FV.increment(1) }, { merge: true });
  await batch.commit();
  const after = (await ref.get()).data();
  let sent = null;
  if (exact) {
    const name = Deck.safeFilename(exactDeck.title);
    cachePut(builtCache, gen.id, { buf: exactBuf, name }, BUILT_MAX);
    try {
      await sendFileToChat(u, exactBuf, name, "✅ Tayyor! Dizayn — namunadagidek." + (exactDeck.imgs.length ? "\n🖼 Rasmlar: Wikimedia Commons" : ""));
      sent = { ok: true, name };
    } catch (e) { sent = { ok: false, error: e.message }; }
  }
  return { id: gen.id, data, credits: after.credits || 0, mode, sent, crosswordError: cwError };
});

// namunadek taqdimotni qayta yuborish (tarixdan yoki «Yuklab olish»)
route("POST", "/api/deck/send", async (req) => {
  const u = await auth(req);
  if (!u.tg) throw new HttpError(400, "Bu imkoniyat faqat Telegram ichida ishlaydi.");
  const body = await readBody(req);
  const id = clean(body.id, 80);
  const ds = await db.collection("decks").doc(id || "x").get();
  if (!ds.exists || ds.data().uid !== u.uid || !ds.data().exact) throw new HttpError(404, "Taqdimot topilmadi.");
  const hour = Date.now() - 3600000;
  const mine = (sends.get(u.uid) || []).filter((t) => t > hour);
  if (mine.length >= 40) throw new HttpError(429, "Bir soatda juda ko‘p fayl yubordingiz. Birozdan keyin urinib ko‘ring.");
  sends.set(u.uid, mine.concat(Date.now()));
  const f = await exactFile(id, ds.data());
  await sendFileToChat(u, f.buf, f.name, "✅ Tayyor! Dizayn — namunadagidek.");
  return { ok: true, name: f.name };
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
  return { id: ds.id, data: d.data, lang: d.lang || "uz", subject: d.subject || "", exact: !!d.exact };
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
      method: p.method || "card", stars: p.stars || 0, createdAt: iso(p.createdAt) };
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
    approved: st.approvedPayments || 0, stars: st.stars || 0, pending, today, warnings: WARN, models: MODELS,
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
      note: p.note, status: p.status, reason: p.reason || "", method: p.method || "card", stars: p.stars || 0, receipt: !!p.receipt,
      createdAt: iso(p.createdAt), decidedAt: iso(p.decidedAt) };
  }).sort(byNewest).slice(0, 100);
  return { items };
});

// Chek rasmini admin panelda ko'rsatish (Telegram serveridan olib beramiz)
route("GET", "/api/admin/receipt", async (req, url) => {
  needAdmin(req);
  const ps = await db.collection("payments").doc(clean(url.searchParams.get("id"), 120)).get();
  const rc = ps.exists && ps.data().receipt;
  if (!rc || !TG_TOKEN) throw new HttpError(404, "Chek topilmadi.");
  const f = await tg("getFile", { file_id: rc.fileId });
  if (!f.ok) throw new HttpError(502, "Chekni Telegram’dan olib bo‘lmadi.");
  if (f.result.file_size > 15 * 1024 * 1024) throw new HttpError(413, "Chek fayli juda katta — uni bot chatida ko‘ring.");
  const r = await fetch(`${TG_API}/file/bot${TG_TOKEN}/${f.result.file_path}`, { signal: AbortSignal.timeout(30000) });
  if (!r.ok) throw new HttpError(502, "Chekni Telegram’dan olib bo‘lmadi.");
  const buf = Buffer.from(await r.arrayBuffer());
  const ext = String(f.result.file_path).split(".").pop().toLowerCase();
  const mime = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", pdf: "application/pdf" }[ext] || "application/octet-stream";
  return { mime, dataUrl: `data:${mime};base64,${buf.toString("base64")}` };
});

route("POST", "/api/admin/payments/approve", async (req) => {
  needAdmin(req);
  const { id } = await readBody(req);
  await approvePayment(id, "admin");
  return { ok: true };
});

route("POST", "/api/admin/payments/reject", async (req) => {
  needAdmin(req);
  const { id, reason } = await readBody(req);
  await rejectPayment(id, reason, "admin");
  return { ok: true };
});

// Admin panel Telegram ichida ochilganda — shu Telegram akkaunt cheklarni qabul qiladigan bo'ladi
route("POST", "/api/admin/tg-link", async (req) => {
  needAdmin(req);
  const { initData } = await readBody(req);
  const t = verifyTelegram(String(initData || ""));
  const s = await getSettings(true);
  const list = (s.adminChats || []).filter((a) => String(a.id) !== String(t.id));
  const isNew = list.length === (s.adminChats || []).length;
  list.unshift({ id: t.id, name: clean([t.first_name, t.username ? "@" + t.username : ""].filter(Boolean).join(" "), 80) });
  await db.doc("settings/main").set({ adminChats: list.slice(0, 5) }, { merge: true });
  await getSettings(true);
  if (isNew) tg("sendMessage", { chat_id: t.id, text: "✅ Bu Telegram akkaunt admin sifatida ulandi. Yangi to‘lov cheklari shu yerga «Tasdiqlash / Rad etish» tugmalari bilan keladi." });
  return { adminChats: list.slice(0, 5) };
});

route("POST", "/api/admin/tg-unlink", async (req) => {
  needAdmin(req);
  const { id } = await readBody(req);
  const s = await getSettings(true);
  const list = (s.adminChats || []).filter((a) => String(a.id) !== String(id));
  await db.doc("settings/main").set({ adminChats: list }, { merge: true });
  await getSettings(true);
  return { adminChats: list };
});

// Hammaga xabar (Telegram foydalanuvchilariga), soniyasiga ~20 ta
let broadcastJob = null;
route("POST", "/api/admin/broadcast", async (req) => {
  needAdmin(req);
  if (!TG_TOKEN) throw new HttpError(400, "Telegram bot ulanmagan.");
  if (broadcastJob && !broadcastJob.done) throw new HttpError(409, "Oldingi xabar hali yuborilmoqda.");
  const { text } = await readBody(req);
  const msg = String(text || "").trim().slice(0, 3500);
  if (msg.length < 2) throw new HttpError(400, "Xabar matnini yozing.");
  const snap = await db.collection("users").get();
  const ids = snap.docs.filter((d) => d.id.startsWith("tg_") && !d.data().blocked).map((d) => d.id.slice(3));
  broadcastJob = { total: ids.length, sent: 0, failed: 0, done: false, startedAt: new Date().toISOString() };
  const job = broadcastJob;
  (async () => {
    for (const id of ids) {
      let r = await tg("sendMessage", { chat_id: id, text: msg, reply_markup: SITE_URL ? appButton("📊 Ilovani ochish") : undefined });
      if (!r.ok && r.error_code === 429) {
        await new Promise((x) => setTimeout(x, ((r.parameters && r.parameters.retry_after) || 2) * 1000));
        r = await tg("sendMessage", { chat_id: id, text: msg });
      }
      if (r.ok) job.sent++; else job.failed++;
      await new Promise((x) => setTimeout(x, 50));
    }
    job.done = true;
    log(`xabar yuborildi: ${job.sent}/${job.total}, xato ${job.failed}`);
  })().catch((e) => { job.done = true; log("xabar xatosi:", e.message); });
  return { job };
});

route("GET", "/api/admin/broadcast", async (req) => {
  needAdmin(req);
  return { job: broadcastJob };
});

route("GET", "/api/admin/users", async (req, url) => {
  needAdmin(req);
  let q = clean(url.searchParams.get("q"), 100).toLowerCase().replace(/^https?:\/\/t\.me\//, "");
  let docs;
  if (/^\d{3,}$/.test(q)) {           // Telegram ID bo'yicha
    const d = await db.collection("users").doc("tg_" + q).get();
    docs = d.exists ? [d] : [];
  } else {
    if (q && !q.startsWith("@") && !q.startsWith("tg:") && !q.includes("@")) q = "@" + q;
    const query = q
      ? db.collection("users").where("email", ">=", q).where("email", "<=", q + "").limit(50)
      : db.collection("users").orderBy("createdAt", "desc").limit(50);
    docs = (await query.get()).docs;
  }
  const items = docs.map((d) => {
    const u = d.data();
    return { uid: d.id, email: u.email, name: u.name, credits: u.credits || 0, used: u.used || 0, blocked: !!u.blocked,
      refCount: u.refCount || 0, createdAt: iso(u.createdAt), lastSeen: iso(u.lastSeen) };
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
      price: Math.max(0, Math.trunc(Number(p.price)) || 0), stars: Math.min(100000, Math.max(0, Math.trunc(Number(p.stars)) || 0)) };
  }).filter((p) => p.title);
  const cur = await getSettings(true);
  const ch = clean(b.requiredChannel, 64).replace(/^https?:\/\/(t\.me|telegram\.me)\//i, "").replace(/^@?/, "");
  const s = {
    freeCredits: Math.min(100, Math.max(0, Math.trunc(Number(b.freeCredits)) || 0)),
    cardNumber: clean(b.cardNumber, 40), cardHolder: clean(b.cardHolder, 60),
    telegram: clean(b.telegram, 80).replace(/^https?:\/\/(t\.me|telegram\.me)\//i, "").replace(/^@/, ""),
    note: clean(b.note, 500), packages,
    refEvery: b.refEvery == null ? cur.refEvery || 5 : Math.min(100, Math.max(1, Math.trunc(Number(b.refEvery)) || 5)),
    refBonus: b.refBonus == null ? (cur.refBonus == null ? 2 : cur.refBonus) : Math.min(100, Math.max(0, Math.trunc(Number(b.refBonus)) || 0)),
    requiredChannel: ch ? "@" + ch : "", adminChats: cur.adminChats || [],
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

// Fayllar xotirada siqilgan holda saqlanadi; ETag bilan telefon o'zgarmagan faylni qayta yuklamaydi
const staticCache = new Map();
function loadStatic(name) {
  if (staticCache.has(name)) return staticCache.get(name);
  const buf = fs.readFileSync(path.join(ROOT, name));
  const item = { buf, gz: zlib.gzipSync(buf, { level: 9 }), etag: '"' + crypto.createHash("sha1").update(buf).digest("hex").slice(0, 16) + '"' };
  staticCache.set(name, item);
  return item;
}

function serveStatic(req, res, pathname) {
  if (pathname === "/healthz") { res.writeHead(200, { "Content-Type": "text/plain" }); return res.end("ok"); }
  const name = STATIC[pathname];
  if (!name) { res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" }); return res.end("Topilmadi"); }
  let item;
  try { item = loadStatic(name); } catch (e) { res.writeHead(500); return res.end("Fayl yo‘q: " + name); }
  const headers = { "Content-Type": TYPES[path.extname(name)], "Cache-Control": "no-cache", ETag: item.etag, Vary: "Accept-Encoding" };
  if (req.headers["if-none-match"] === item.etag) { res.writeHead(304, headers); return res.end(); }
  if (/\bgzip\b/.test(req.headers["accept-encoding"] || "")) {
    res.writeHead(200, Object.assign(headers, { "Content-Encoding": "gzip" }));
    return res.end(item.gz);
  }
  res.writeHead(200, headers);
  res.end(item.buf);
}

// Wikimedia rasmlari ilovaga shu server orqali beriladi (Telegram ichida boshqa saytdan rasm olish cheklanishi mumkin)
const imgProxyCache = new Map();
const IMG_HOSTS = new Set(["upload.wikimedia.org", (() => { try { return new URL(IMG_API).host; } catch (e) { return ""; } })()]);
async function proxyImage(req, res, url) {
  let target;
  try { target = new URL(url.searchParams.get("u") || ""); } catch (e) { res.writeHead(400); return res.end(); }
  if (!IMG_HOSTS.has(target.host) || !/^https?:$/.test(target.protocol)) { res.writeHead(403); return res.end(); }
  const key = target.href;
  let hit = imgProxyCache.get(key);
  if (!hit) {
    try {
      const r = await fetch(target.href, { headers: { "User-Agent": UA }, signal: AbortSignal.timeout(15000) });
      const ct = r.headers.get("content-type") || "";
      if (!r.ok || !/^image\/(jpeg|png|webp|gif)/.test(ct)) throw new Error("http " + r.status + " " + ct);
      const buf = Buffer.from(await r.arrayBuffer());
      if (buf.length > 8 * 1024 * 1024) throw new Error("too big");
      hit = { buf, ct };
      cachePut(imgProxyCache, key, hit, 40);
    } catch (e) {
      log("rasm olinmadi:", target.pathname.slice(-60), e.message);
      res.writeHead(502); return res.end();
    }
  }
  res.writeHead(200, { "Content-Type": hit.ct, "Content-Length": hit.buf.length, "Cache-Control": "public, max-age=604800, immutable" });
  res.end(hit.buf);
}

// Mavzu/matn usuli: muqova va 2-3 slayd uchun rasm tanlanadi (manzil saqlanadi, rasmni ilova yuklaydi)
async function attachImages(data, topic) {
  const COVER_AR = (13.333 - 7.75) / 7.5, SLIDE_AR = 4.55 / 4.97;
  const jobs = [{ k: "cover", q: data.image_topic || topic, ar: COVER_AR }];
  data.slides.forEach((sd, i) => { if (sd.image) jobs.push({ k: i, q: sd.image, ar: SLIDE_AR }); });
  for (let i = 0; i < data.slides.length && jobs.length < 3; i++) {
    const sd = data.slides[i];
    if (sd.type === "bullets" && !sd.image) { sd.image = data.image_topic || topic; jobs.push({ k: i, q: sd.image, ar: SLIDE_AR }); }
  }
  const used = new Set();
  const lists = await Promise.all(jobs.map(async (j) => {
    for (const q of [...new Set([j.q, data.image_topic, topic].filter(Boolean))]) {
      const l = await commons(q);
      if (l.length) return l;
    }
    return [];
  }));
  jobs.forEach((j, n) => {
    const fit = (c) => Math.abs(Math.log((c.w / c.h) / j.ar));
    const cands = lists[n].filter((c) => !used.has(c.url)).slice(0, 4).sort((a, b) => fit(a) - fit(b));
    const c = cands[0];
    if (!c) return;
    used.add(c.url);
    const ref = { u: "/api/img?u=" + encodeURIComponent(c.url), w: c.w, h: c.h };
    if (j.k === "cover") data.cover_image = ref;
    else data.slides[j.k].img = ref;
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/api/img" && req.method === "GET") return proxyImage(req, res, url);
  if (!url.pathname.startsWith("/api/")) return serveStatic(req, res, url.pathname);
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
  // Render bepul serveri 15 daqiqa so'rovsiz qolsa uxlaydi. O'zimizni har 10 daqiqada uyg'otib turamiz.
  if (SITE_URL && (process.env.KEEP_AWAKE || "on").toLowerCase() !== "off") {
    setInterval(() => { fetch(SITE_URL + "/healthz", { signal: AbortSignal.timeout(20000) }).catch(() => {}); }, 10 * 60000).unref();
  }
});
