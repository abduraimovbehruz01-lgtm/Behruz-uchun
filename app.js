// app.js — saytning asosiy qismi: Google orqali kirish, taqdimot yasash, yuklab olish, to'lov so'rovi.
const FB = "https://www.gstatic.com/firebasejs/10.12.2/";
const PPTX_URL = "https://cdn.jsdelivr.net/npm/pptxgenjs@4.0.1/dist/pptxgen.bundle.js";
const STORE = "taqdimot:v2";
const ADMIN_STORE = "taqdimot:admin";

const $ = (id) => document.getElementById(id);
// Sayt Telegram bot ichida (Web App) ochilganmi? Unda Google o'rniga Telegram akkaunti ishlatiladi.
const TG = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData ? window.Telegram.WebApp : null;
const Deck = window.Deck;
const X = window.SlideExport;

const state = {
  cfg: null, A: null, auth: null, user: null, me: null, authReady: false,
  data: null, deckId: null, lang: "uz", isSample: true, model: null, busy: false,
  adminToken: loadAdminToken(),
};

// ------------------------------------------------------------ kichik yordamchilar
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtMoney = (n) => Number(n || 0).toLocaleString("ru-RU").replace(/[  ]/g, " ") + " so‘m";
const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (x) => String(x).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}, ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const LANG_NAME = { uz: "O‘zbekcha", ru: "Русский", en: "English" };

let toastT;
function toast(msg, ms) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { t.hidden = true; }, ms || 3200);
}

async function copyText(text, fallbackEl) {
  try {
    await navigator.clipboard.writeText(text);
    toast("✓ Nusxa olindi");
  } catch (e) {
    if (fallbackEl) {
      fallbackEl.hidden = false;
      fallbackEl.focus();
      fallbackEl.select();
      try { document.execCommand("copy"); toast("✓ Nusxa olindi"); return; } catch (e2) { /* pastga */ }
    }
    toast("Nusxa olinmadi — matnni bosib turib, qo‘lda nusxa oling.");
  }
}

const scripts = {};
function loadScript(src) {
  if (!scripts[src]) {
    scripts[src] = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => { delete scripts[src]; reject(new Error("script")); };
      document.head.appendChild(s);
    });
  }
  return scripts[src];
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 120000);
}

class ApiError extends Error {
  constructor(message, code, status) { super(message); this.code = code; this.status = status; }
}

async function api(path, opts) {
  opts = opts || {};
  const headers = {};
  if (state.user) headers.Authorization = TG ? "tma " + TG.initData : "Bearer " + (await state.user.getIdToken());
  if (opts.body) headers["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch(path, { method: opts.method || (opts.body ? "POST" : "GET"), headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  } catch (e) {
    throw new ApiError("Internet aloqasi uzildi yoki server javob bermadi.", "network", 0);
  }
  let j = null;
  try { j = await r.json(); } catch (e) { /* JSON emas */ }
  if (!r.ok || !j) {
    const msg = j && j.error ? j.error
      : r.status >= 502 ? "Server uyg‘onmoqda. 30–60 soniyadan keyin qayta urinib ko‘ring." : "Xatolik yuz berdi. Qayta urinib ko‘ring.";
    throw new ApiError(msg, j && j.code, r.status);
  }
  return j;
}

// ------------------------------------------------------------ admin kirishi (login + parol)
function loadAdminToken() {
  try {
    const t = JSON.parse(localStorage.getItem(ADMIN_STORE) || "null");
    if (t && t.token && Date.parse(t.expiresAt) > Date.now()) return t.token;
  } catch (e) { /* saqlanmagan */ }
  return null;
}
function setAdminToken(t) {
  state.adminToken = t ? t.token : null;
  try { if (t) localStorage.setItem(ADMIN_STORE, JSON.stringify(t)); else localStorage.removeItem(ADMIN_STORE); } catch (e) { /* xotirada qoladi */ }
}

async function adminApi(path, opts) {
  opts = opts || {};
  const headers = { "X-Admin-Token": state.adminToken || "" };
  if (opts.body) headers["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch(path, { method: opts.body ? "POST" : "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  } catch (e) {
    throw new ApiError("Internet aloqasi uzildi yoki server javob bermadi.", "network", 0);
  }
  let j = null;
  try { j = await r.json(); } catch (e) { /* JSON emas */ }
  if (r.status === 401 && j && j.code === "admin_auth") {
    setAdminToken(null);
    toast("Sessiya tugadi. Login va parolni qayta kiriting.", 4000);
    route();
  }
  if (!r.ok || !j) throw new ApiError(j && j.error ? j.error : "Xatolik yuz berdi. Qayta urinib ko‘ring.", j && j.code, r.status);
  return j;
}

const isAdminPage = () => location.hash === "#admin" || location.pathname === "/admin";

function goHome() {
  if (location.pathname === "/admin") location.href = "/";
  else location.hash = "";
}

function adminLogout() {
  setAdminToken(null);
  toast("Admin paneldan chiqdingiz");
  route();
}

function showAdminLogin() {
  const box = $("adminView");
  box.innerHTML = `<form class="panel admin-login" id="admLoginForm" novalidate>
      <h1 class="brand" style="font-size:22px">Admin panel</h1>
      <p class="fine">Faqat sayt egasi uchun.</p>
      <div class="field"><label for="admLogin">Login</label>
        <input id="admLogin" type="text" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="100"></div>
      <div class="field"><label for="admPass">Parol</label>
        <div class="pass"><input id="admPass" type="password" autocomplete="current-password" maxlength="200">
        <button type="button" class="icon-btn" id="admEye" aria-controls="admPass">Ko‘rsatish</button></div></div>
      <button class="btn btn-primary btn-block" type="submit" id="admGo">Kirish</button>
      <p class="fine" id="admMsg" role="status"></p>
      <a class="icon-btn" href="#" data-home style="align-self:flex-start">← Orqaga</a>
    </form>`;
  const pass = $("admPass");
  $("admEye").addEventListener("click", () => {
    const show = pass.type === "password";
    pass.type = show ? "text" : "password";
    $("admEye").textContent = show ? "Yashirish" : "Ko‘rsatish";
  });
  $("admLoginForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const login = $("admLogin").value.trim();
    const password = pass.value;
    if (!login || !password) { $("admMsg").textContent = "Login va parolni kiriting."; return; }
    $("admGo").disabled = true;
    $("admMsg").textContent = "Tekshirilmoqda…";
    try {
      const r = await fetch("/api/admin/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ login, password }) });
      let j = null;
      try { j = await r.json(); } catch (e) { /* JSON emas */ }
      if (!r.ok || !j || !j.token) throw new Error(j && j.error ? j.error : "Server javob bermadi. Birozdan keyin urinib ko‘ring.");
      setAdminToken(j);
      toast("✓ Admin panelga kirdingiz");
      route();
    } catch (e) {
      $("admMsg").textContent = e.message === "Failed to fetch" ? "Internet aloqasini tekshiring." : e.message;
      $("admGo").disabled = false;
      pass.select();
    }
  });
  setTimeout(() => $("admLogin").focus(), 50);
}

// ------------------------------------------------------------ ishga tushish
async function boot() {
  let cfg;
  try {
    const r = await fetch("/api/config");
    cfg = await r.json();
  } catch (e) {
    $("bootView").innerHTML = `<p>Server javob bermadi. Bepul server uxlab qolgan bo‘lishi mumkin — 1 daqiqadan keyin qayta urining.</p>
      <button class="btn btn-primary" type="button" onclick="location.reload()">Qayta urinish</button>`;
    return;
  }
  state.cfg = cfg;
  $("bootView").hidden = true;
  if (cfg.setup) return showSetup(cfg);
  document.querySelectorAll(".freeN").forEach((el) => { el.textContent = cfg.settings.freeCredits; });
  initMain();
  window.addEventListener("hashchange", route);
  route();
  if (TG) startTelegram();
  else if (!cfg.firebase) { state.authReady = true; renderAuth(); }   // faqat Telegram rejimi: brauzerda "Telegram'da ochish"
  else try {
    const [appMod, A] = await Promise.all([import(FB + "firebase-app.js"), import(FB + "firebase-auth.js")]);
    state.A = A;
    state.auth = A.getAuth(appMod.initializeApp(cfg.firebase));
    A.onAuthStateChanged(state.auth, onUser);
  } catch (e) {
    console.error(e);
    state.authReady = true;
    renderAuth();
    setStatus("Google kirish moduli yuklanmadi. Internetni tekshirib, sahifani yangilang.", "err");
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.user) refreshMe().catch(() => {});
  });
}

// Telegram ichida: kirish avtomatik, Telegram tasdiqlagan ma'lumot serverda tekshiriladi
async function startTelegram() {
  try { TG.ready(); TG.expand(); } catch (e) { /* eski Telegram versiyasi */ }
  try { if (TG.BackButton) TG.BackButton.onClick(goHome); } catch (e) { /* eski versiya */ }
  document.documentElement.classList.add("in-tg");
  const tu = (TG.initDataUnsafe && TG.initDataUnsafe.user) || {};
  state.user = {
    tg: true, email: "", photoURL: tu.photo_url || null,
    displayName: [tu.first_name, tu.last_name].filter(Boolean).join(" "), getIdToken: async () => "",
  };
  state.authReady = true;
  renderAuth();
  try { await refreshMe(); } catch (e) { setStatus(e.message, "err"); }
  loadHistory();
  renderAuth();
  route();
}

async function sendToTelegram(blob, name) {
  let r;
  try {
    r = await fetch("/api/tg/send?name=" + encodeURIComponent(name), {
      method: "POST", headers: { Authorization: "tma " + TG.initData, "Content-Type": "application/octet-stream" }, body: blob,
    });
  } catch (e) {
    const x = new Error("net");
    x.userMessage = "Internet aloqasi uzildi. Qayta bosing.";
    throw x;
  }
  let j = null;
  try { j = await r.json(); } catch (e) { /* JSON emas */ }
  if (!r.ok) {
    const x = new Error("tg");
    x.userMessage = (j && j.error) || "Faylni Telegram’ga yuborib bo‘lmadi. Qayta urinib ko‘ring.";
    throw x;
  }
}

// Faylni foydalanuvchiga berish: brauzerda — yuklab olish, Telegram ichida — bot chatiga yuborish
async function deliver(blob, name) {
  if (TG) {
    setStatus("Fayl Telegram chatga yuborilmoqda…", "busy", 95);
    await sendToTelegram(blob, name);
    return "tg";
  }
  saveBlob(blob, name); return "file";
}

async function onUser(u) {
  state.user = u;
  state.authReady = true;
  if (u) {
    try { await refreshMe(); } catch (e) { toast(e.message, 5000); }
    loadHistory();
  } else {
    state.me = null;
    $("histBox").hidden = true;
  }
  renderAuth();
  route();
}

async function refreshMe() {
  state.me = await api("/api/me");
  renderAuth();
  return state.me;
}

async function login() {
  if (TG) return !!state.me;
  if (!state.auth) { toast("Kirish moduli hali yuklanmadi. Birozdan keyin bosing."); return false; }
  const A = state.A;
  const provider = new A.GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  try {
    await A.signInWithPopup(state.auth, provider);
    state.user = state.auth.currentUser;
    await refreshMe();
    return true;
  } catch (e) {
    const c = (e && e.code) || "";
    const MSG = {
      "auth/popup-closed-by-user": "",
      "auth/cancelled-popup-request": "",
      "auth/popup-blocked": "Brauzer kirish oynasini to‘sib qo‘ydi. Yana bir marta bosing.",
      "auth/unauthorized-domain": "Bu sayt manzili Firebase’da ruxsat etilmagan. Admin: Firebase → Authentication → Settings → Authorized domains ga sayt manzilini qo‘shing.",
      "auth/operation-not-allowed": "Google orqali kirish Firebase’da yoqilmagan (Authentication → Sign-in method → Google).",
      "auth/network-request-failed": "Internet aloqasini tekshirib, qayta urinib ko‘ring.",
      "auth/web-storage-unsupported": "Saytni Safari yoki Chrome’da oching (ilova ichidagi brauzerda Google kirish ishlamaydi).",
      "auth/internal-error": "Saytni Safari yoki Chrome’da oching (Telegram/Instagram ichidagi brauzerda Google kirish ishlamaydi).",
    };
    const m = c in MSG ? MSG[c] : (e instanceof ApiError ? e.message : `Kirib bo‘lmadi (${c || e.message}).`);
    if (m) setStatus(m, "err");
    return false;
  }
}

async function logout() {
  if (!state.auth) return;
  await state.A.signOut(state.auth);
  toast("Hisobdan chiqdingiz");
}

// ------------------------------------------------------------ sahifalar
function route() {
  $("adminLink").hidden = !state.adminToken;
  if (TG && TG.BackButton) { try { if (isAdminPage()) TG.BackButton.show(); else TG.BackButton.hide(); } catch (e) { /* eski versiya */ } }
  if (isAdminPage()) {
    $("mainView").hidden = true;
    $("tgOnlyView").hidden = true;
    $("adminView").hidden = false;
    if (!state.adminToken) { state.adminMounted = false; showAdminLogin(); return; }
    if (state.adminMounted) return;   // panel allaqachon ochiq — qayta yuklamaymiz
    state.adminMounted = true;
    const helpers = { api: adminApi, toast, esc, fmtMoney, fmtDate, refreshMe: () => (state.user ? refreshMe() : Promise.resolve()) };
    import("./admin.js").then((m) => m.mountAdmin($("adminView"), helpers))
      .catch((e) => { console.error(e); state.adminMounted = false; toast("Admin panel yuklanmadi."); });
    return;
  }
  $("adminView").hidden = true;
  state.adminMounted = false;
  const tgOnly = !TG && state.cfg && !state.cfg.firebase;
  $("mainView").hidden = tgOnly;
  $("tgOnlyView").hidden = !tgOnly;
  if (tgOnly) {
    const bot = state.cfg.telegram && state.cfg.telegram.bot;
    $("tgOpen").hidden = !bot;
    if (bot) $("tgOpen").href = "https://t.me/" + encodeURIComponent(bot);
    $("tgWait").hidden = !!bot;
  }
}

function showSetup(cfg) {
  $("setupView").hidden = false;
  const NAMES = {
    FIREBASE_SERVICE_ACCOUNT: "Firebase xizmat kaliti (JSON fayl matni)",
    TELEGRAM_BOT_TOKEN: "Telegram bot tokeni (@BotFather beradi)",
  };
  $("setupList").innerHTML = cfg.setup.concat(cfg.warnings || []).map((s) => {
    const name = NAMES[s] ? `<div class="t mono">${esc(s)}</div><div class="s">${esc(NAMES[s])}</div>` : `<div class="t">${esc(s)}</div>`;
    return `<li><span aria-hidden="true">⚠️</span><div class="main">${name}</div></li>`;
  }).join("");
  let minified = "";
  $("saFile").addEventListener("change", () => {
    const f = $("saFile").files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      try {
        const j = JSON.parse(String(rd.result));
        if (!j.private_key || !j.client_email || !j.project_id) throw new Error("fields");
        minified = JSON.stringify(j);
        $("saInfo").textContent = `✓ To‘g‘ri fayl. Loyiha: ${j.project_id}. Endi «Nusxa olish»ni bosing.`;
        $("saText").value = minified;
        $("saCopy").hidden = false;
      } catch (e) {
        $("saInfo").textContent = "Bu fayl Firebase xizmat kaliti emas. Firebase → Project settings → Service accounts → «Generate new private key» bilan olingan faylni tanlang.";
        $("saCopy").hidden = true;
      }
    };
    rd.readAsText(f);
  });
  $("saCopy").addEventListener("click", () => copyText(minified, $("saText")));
}

// ------------------------------------------------------------ asosiy sahifa
const form = () => $("form");
const val = (name) => (document.querySelector(`input[name="${name}"]:checked`) || {}).value;
const opts = () => ({ author: $("author").value, subject: $("subject").value, theme: val("theme"), lang: state.lang });

function initMain() {
  const themesBox = $("themes");
  for (const [key, th] of Object.entries(Deck.THEMES)) {
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="radio" name="theme" value="${key}" id="theme-${key}"${key === "klassik" ? " checked" : ""}>` +
      `<span class="sw"><span class="chips"><i style="background:#${th.dark}"></i><i style="background:#${th.accent}"></i>` +
      `<i style="background:#${th.light}"></i></span><span class="nm">${esc(th.name)}</span></span>`;
    themesBox.appendChild(lab);
  }

  state.data = JSON.parse($("namuna").textContent);
  $("subject").value = "Falsafa";
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "null");
    if (saved && saved.data && saved.data.slides) applySaved(saved);
  } catch (e) { /* saqlangan narsa yo'q */ }

  let t;
  const rerender = () => { clearTimeout(t); t = setTimeout(() => { render(); persist(); }, 300); };
  $("author").addEventListener("input", rerender);
  $("subject").addEventListener("input", rerender);
  themesBox.addEventListener("change", () => { render(); persist(); });
  $("fmtSeg").addEventListener("change", () => { try { localStorage.setItem(STORE + ":fmt", val("fmt")); } catch (e) { /* */ } });
  try {
    const f = localStorage.getItem(STORE + ":fmt");
    const el = f && document.querySelector(`input[name="fmt"][value="${f}"]`);
    if (el) el.checked = true;
  } catch (e) { /* */ }

  form().addEventListener("submit", onGenerate);
  initModes();
  $("dlBtn").addEventListener("click", () => download(false));
  $("cwDlBtn").addEventListener("click", () => download(true));
  $("cwBtn").addEventListener("click", onCrossword);
  $("logoutBtn").addEventListener("click", logout);
  $("topLogin").addEventListener("click", login);
  $("creditsBtn").addEventListener("click", openPay);
  $("authBox").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.act === "login") login();
    if (b.dataset.act === "buy") openPay();
  });
  $("histList").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-id]");
    if (b) openDeck(b.dataset.id);
  });
  $("adminView").addEventListener("click", (e) => {
    if (e.target.closest("[data-home]")) { e.preventDefault(); goHome(); }
    if (e.target.closest("[data-logout]")) adminLogout();
  });
  // Telegram ichida t.me havolalari Telegram'ning o'zida ochilsin
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="https://t.me/"]');
    if (a && TG && TG.openTelegramLink) { e.preventDefault(); TG.openTelegramLink(a.href); }
  });
  const bot = state.cfg.telegram && state.cfg.telegram.bot;
  if (bot && !TG) {
    $("tgHint").hidden = false;
    $("tgHint").innerHTML = `📱 Telegram’da ham ishlaydi: <a href="https://t.me/${encodeURIComponent(bot)}" target="_blank" rel="noopener">@${esc(bot)}</a>`;
  }
  initPay();
  renderAuth();
  render();
}

function applySaved(saved) {
  state.data = saved.data;
  state.deckId = saved.deckId || null;
  state.lang = saved.lang || "uz";
  state.isSample = false;
  $("topic").value = saved.topic || "";
  $("subject").value = saved.subject || "";
  $("author").value = saved.author || "";
  if (saved.theme && $("theme-" + saved.theme)) $("theme-" + saved.theme).checked = true;
  if ($("lang-" + state.lang)) $("lang-" + state.lang).checked = true;
}

function persist() {
  if (state.isSample) return;
  try {
    localStorage.setItem(STORE, JSON.stringify({
      data: state.data, deckId: state.deckId, lang: state.lang, topic: $("topic").value,
      subject: $("subject").value, author: $("author").value, theme: val("theme"),
    }));
  } catch (e) { /* brauzer saqlashga ruxsat bermasa — muhim emas */ }
}

function render() {
  state.model = Deck.buildDeck(state.data, opts());
  $("resTitle").textContent = state.model.title;
  const pill = $("resPill");
  pill.textContent = state.isSample ? "NAMUNA" : "TAYYOR";
  pill.className = "pill " + (state.isSample ? "sample" : "ready");
  $("resMeta").textContent = `${state.model.slides.length} ta slayd · ${Deck.THEMES[val("theme")].name} dizayn`;
  $("how").hidden = !state.isSample;
  const hasCw = !!(state.data.crossword && state.data.crossword.words);
  $("cwDlBtn").hidden = !hasCw;
  $("cwBtn").textContent = hasCw ? "🧩 Krossvordni yangilash" : "🧩 Krossvord tuzish";
  Deck.renderPreview(state.model, $("preview"));
}

function renderAuth() {
  const box = $("authBox");
  const free = state.cfg && state.cfg.settings ? state.cfg.settings.freeCredits : 3;
  const me = state.me;
  $("topLogin").hidden = !state.authReady || !!state.user || !(state.cfg && state.cfg.firebase);
  $("logoutBtn").hidden = !!TG;
  $("userBox").hidden = !state.user;
  $("adminLink").hidden = !state.adminToken;
  box.classList.remove("zero");
  if (!state.authReady) {
    box.innerHTML = `<p>Tekshirilmoqda…</p>`;
    return;
  }
  if (!state.user) {
    box.innerHTML = `<p><b>Google bilan kiring</b> — har bir yangi foydalanuvchiga ${esc(free)} ta taqdimot bepul.</p>
      <button type="button" class="btn btn-google btn-block" data-act="login">${GOOGLE_SVG} Google bilan kirish</button>
      <p class="fine">Sayt Telegram ichida ochilgan bo‘lsa, ⋯ tugmasi → «Safari’da ochish»ni tanlang.</p>`;
    return;
  }
  const u = state.user;
  const av = $("avatar");
  av.innerHTML = u.photoURL ? `<img src="${esc(u.photoURL)}" alt="" referrerpolicy="no-referrer">` : esc((u.displayName || u.email || "?").charAt(0).toUpperCase());
  if (!me) { box.innerHTML = `<p>Hisob ma’lumotlari yuklanmoqda…</p>`; return; }
  $("creditsN").textContent = me.credits;
  $("creditsBtn").classList.toggle("zero", me.credits <= 0);
  const name = esc((me.name || me.email || "").split(" ")[0]);
  if (me.blocked) {
    box.classList.add("zero");
    box.innerHTML = `<p><b>Hisobingiz bloklangan.</b> Savollar bo‘lsa, admin bilan bog‘laning.</p>`;
  } else if (me.credits > 0) {
    box.innerHTML = `<p>Salom, ${name}! Sizda yana <b>${me.credits} ta</b> taqdimot yasash imkoni bor.</p>
      <p class="fine"><a href="#" data-buy>Ko‘proq kerakmi? Paket sotib olish</a></p>`;
    box.querySelector("[data-buy]").addEventListener("click", (e) => { e.preventDefault(); openPay(); });
  } else {
    box.classList.add("zero");
    box.innerHTML = `<p>Bepul taqdimotlar tugadi. <b>Paket sotib oling</b> — kartaga to‘lab, chekni yuborasiz.</p>
      <button type="button" class="btn btn-primary btn-block" data-act="buy">💳 Paket sotib olish</button>`;
  }
}

const GOOGLE_SVG = `<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>`;

function setStatus(text, kind, progress) {
  const st = $("status");
  st.classList.add("show");
  st.classList.toggle("busy", kind === "busy");
  st.classList.toggle("err", kind === "err");
  $("stText").textContent = text;
  if (progress != null) $("barFill").style.width = Math.max(4, Math.min(100, progress)) + "%";
}

// ------------------------------------------------------------ taqdimot yasash
async function onGenerate(ev) {
  ev.preventDefault();
  if (state.busy) return;
  const mode = val("mode") === "text" ? "text" : "topic";
  const topic = $("topic").value.replace(/\s+/g, " ").trim();
  const source = mode === "text" ? $("source").value.trim() : "";
  if (mode === "topic" && topic.length < 3) {
    setStatus("Avval taqdimot mavzusini yozing.", "err");
    $("topic").focus();
    return;
  }
  if (mode === "text" && source.length < 200) {
    setStatus("Matn juda qisqa. Mustaqil ish yoki konspektdan kamida bir-ikki xatboshi joylang.", "err");
    $("source").focus();
    return;
  }
  if (!state.user && !(await login())) return;
  if (!state.me) { try { await refreshMe(); } catch (e) { setStatus(e.message, "err"); return; } }
  if (state.me.blocked) { setStatus("Hisobingiz bloklangan.", "err"); return; }
  if (state.me.credits <= 0) { openPay(); return; }

  const n = Number(val("n"));
  const lang = val("lang");
  state.busy = true;
  $("goBtn").disabled = true;
  $("preview").classList.add("dim");
  const started = Date.now();
  const tick = () => {
    const s = (Date.now() - started) / 1000;
    const msg = s < 8 ? (mode === "text" ? "Matningiz o‘qilmoqda…" : "Mavzu o‘rganilmoqda…")
      : s < 45 ? (mode === "text" ? "Matn slaydlarga bo‘linmoqda…" : "Slaydlar matni yozilmoqda…") : s < 90 ? "Deyarli tayyor…" : "Biroz ko‘proq vaqt ketyapti, kuting…";
    setStatus(`${msg} ${Math.round(s)} soniya. Sahifani yopmang.`, "busy", 96 * (1 - Math.exp(-s / 35)));
  };
  tick();
  const timer = setInterval(tick, 1000);
  try {
    const r = await api("/api/generate", { body: { mode, topic, source, subject: $("subject").value.trim(), n, lang } });
    state.data = r.data;
    state.deckId = r.id;
    state.lang = lang;
    state.isSample = false;
    if (state.me) state.me.credits = r.credits;
    renderAuth();
    render();
    persist();
    loadHistory();
    setStatus(`✓ Tayyor! Fayl turini tanlab, «Yuklab olish»ni bosing. Qolgan taqdimotlar: ${r.credits} ta.`, "ok", 100);
    $("resTitle").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    if (e.code === "no_credits") { if (state.me) state.me.credits = 0; renderAuth(); openPay(); }
    if (e.code === "network") {
      setStatus("Aloqa uzildi. Taqdimot baribir tayyor bo‘lgan bo‘lishi mumkin — 1 daqiqadan so‘ng «Mening taqdimotlarim» ro‘yxatini oching.", "err");
      setTimeout(() => { loadHistory(); refreshMe().catch(() => {}); }, 30000);
    } else setStatus(e.message, "err");
  } finally {
    clearInterval(timer);
    state.busy = false;
    $("goBtn").disabled = false;
    $("preview").classList.remove("dim");
  }
}

async function onCrossword() {
  if (state.busy) return;
  if (!state.user && !(await login())) return;
  const topic = $("topic").value.replace(/\s+/g, " ").trim() || state.model.title;
  state.busy = true;
  $("cwBtn").disabled = $("goBtn").disabled = true;
  const started = Date.now();
  const tick = () => {
    const s = (Date.now() - started) / 1000;
    setStatus(`Krossvord uchun so‘zlar tanlanmoqda… ${Math.round(s)} soniya`, "busy", 96 * (1 - Math.exp(-s / 20)));
  };
  tick();
  const timer = setInterval(tick, 1000);
  try {
    const r = await api("/api/crossword", { body: { topic, lang: state.lang, deckId: state.isSample ? null : state.deckId } });
    state.data.crossword = r.crossword;
    render();
    persist();
    const cw = Deck.layoutCrossword(state.data.crossword.words, 15);
    setStatus(`✓ Krossvord tayyor: ${cw.placed.length} ta so‘z. Taqdimotga 3 ta slayd qo‘shildi — katak, savollar va javoblar.`, "ok", 100);
    const idx = state.model.slides.findIndex((s) => s.items.some((it) => it.k === "rect" && it.fill === "FFFFFF" && it.lineW === 1));
    const figs = $("preview").querySelectorAll(".pv-slide");
    if (idx >= 0 && figs[idx]) figs[idx].scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) {
    setStatus(e.message, "err");
  } finally {
    clearInterval(timer);
    state.busy = false;
    $("cwBtn").disabled = $("goBtn").disabled = false;
  }
}

// ------------------------------------------------------------ 2-usul: o'z matnidan
const MODE_HINT = {
  topic: "AI mavzu bo‘yicha matnni o‘zi yozadi.",
  text: "Matningizni joylang — AI uni slaydlarga bo‘lib, reja va xulosa qo‘shadi. Yangi fakt qo‘shmaydi.",
};

function initModes() {
  const apply = () => {
    const text = val("mode") === "text";
    $("srcBox").hidden = !text;
    $("modeHint").textContent = MODE_HINT[text ? "text" : "topic"];
    $("topicLbl").innerHTML = text ? 'Mavzu <span class="hint">(ixtiyoriy — bo‘lmasa matndan olinadi)</span>' : "Mavzu";
    $("topic").rows = text ? 1 : 2;
    $("topic").placeholder = text ? "Masalan: Amir Temur davlati" : "Masalan: Amir Temur davlatining boshqaruv tizimi";
  };
  $("modeSeg").addEventListener("change", apply);
  const count = () => { $("srcCount").textContent = `${$("source").value.length.toLocaleString("ru-RU").replace(/[\u00a0\u202f]/g, " ")} / 30 000`; };
  $("source").addEventListener("input", count);
  $("srcFile").addEventListener("change", async () => {
    const f = $("srcFile").files[0];
    $("srcFile").value = "";
    if (!f) return;
    const name = f.name.toLowerCase();
    try {
      let text;
      if (name.endsWith(".txt")) text = await f.text();
      else if (name.endsWith(".docx")) text = await readDocx(f);
      else { setStatus("Faqat Word (.docx) yoki .txt fayl. PDF bo‘lsa, matnni nusxalab joylang.", "err"); return; }
      text = text.trim();
      if (!text) { setStatus("Faylda matn topilmadi.", "err"); return; }
      const cut = text.length > 30000;
      $("source").value = text.slice(0, 30000);
      count();
      if (!$("topic").value.trim()) $("topic").value = text.split("\n").map((l) => l.trim()).find((l) => l.length > 3 && l.length < 150) || "";
      setStatus(cut ? "✓ Fayl o‘qildi. Matn juda uzun edi — dastlabki 30 000 belgisi olindi." : "✓ Fayl o‘qildi. Endi «Taqdimot yasash»ni bosing.", "ok");
    } catch (e) {
      console.error(e);
      setStatus("Faylni o‘qib bo‘lmadi. Matnni nusxalab, shu yerga joylang.", "err");
    }
  });
  apply();
  count();
}

// Word (.docx) fayldan matnni olish: .docx — bu ZIP arxiv, matn word/document.xml ichida
async function readDocx(file) {
  const buf = new Uint8Array(await file.arrayBuffer());
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("zip emas");
  const total = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const dec = new TextDecoder();
  for (let k = 0; k < total; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true);
    const csize = dv.getUint32(p + 20, true);
    const nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
    const off = dv.getUint32(p + 42, true);
    const name = dec.decode(buf.subarray(p + 46, p + 46 + nlen));
    if (name === "word/document.xml") {
      const start = off + 30 + dv.getUint16(off + 26, true) + dv.getUint16(off + 28, true);
      const data = buf.subarray(start, start + csize);
      let xml;
      if (method === 0) xml = dec.decode(data);
      else if (method === 8) {
        if (typeof DecompressionStream === "undefined") throw new Error("DecompressionStream yo‘q");
        xml = await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).text();
      } else throw new Error("siqish usuli " + method);
      return xml.replace(/<w:tab\/>/g, "\t").replace(/<w:br[^>]*\/>/g, "\n").replace(/<\/w:p>/g, "\n").replace(/<[^>]+>/g, "")
        .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&")
        .replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    }
    p += 46 + nlen + xlen + clen;
  }
  throw new Error("word/document.xml topilmadi");
}

// ------------------------------------------------------------ yuklab olish
const HINT = {
  pptx: "PowerPoint, Keynote yoki Google Slides’da oching.",
  pdf: "Istalgan telefonda ochiladi va chop etish uchun qulay.",
  docx: "Word yoki Pages’da oching — matnni o‘zgartirish mumkin.",
  jpg: "ZIP faylni bosing — slayd rasmlari papkasi ochiladi.",
};

async function download(onlyCw) {
  if (state.busy) return;
  const fmt = val("fmt") || "pptx";
  const o = opts();
  render();
  const model = onlyCw ? Deck.buildDeck(state.data, Object.assign({}, o, { only: "crossword" })) : state.model;
  const base = Deck.safeFilename(model.title).replace(/\.pptx$/i, "");
  const btn = onlyCw ? $("cwDlBtn") : $("dlBtn");
  state.busy = true;
  btn.disabled = true;
  try {
    let blob, name;
    if (fmt === "pptx") {
      setStatus("PowerPoint fayl tayyorlanmoqda…", "busy", 30);
      await loadScript(PPTX_URL);
      blob = await Deck.renderPptx(model, window.PptxGenJS);
      name = base + ".pptx";
    } else if (fmt === "pdf") {
      blob = await X.toPdf(model, (i, n) => setStatus(`PDF tayyorlanmoqda: ${i} / ${n} slayd`, "busy", (i / n) * 100));
      name = base + ".pdf";
    } else if (fmt === "docx") {
      setStatus("Word fayl tayyorlanmoqda…", "busy", 50);
      blob = onlyCw ? X.crosswordDocx(state.data, o, Deck) : X.toDocx(state.data, o, Deck);
      name = base + ".docx";
    } else {
      blob = await X.toImagesZip(model, base, (i, n) => setStatus(`Rasmlar tayyorlanmoqda: ${i} / ${n}`, "busy", (i / n) * 100));
      name = base + " (rasmlar).zip";
    }
    const how = await deliver(blob, name);
    if (!how) { setStatus("Saqlash bekor qilindi.", "ok", 100); return; }
    setStatus(how === "tg"
      ? `✓ «${name}» bot chatiga yuborildi. Oynani yopsangiz, fayl chatda turibdi.`
      : `✓ «${name}» yuklab olindi. ${HINT[fmt]} iPhone’da: Fayllar → Yuklanganlar.`, "ok", 100);
  } catch (e) {
    console.error(e);
    setStatus(e.userMessage ? e.userMessage : fmt === "pptx" && String(e.message) === "script"
      ? "PowerPoint moduli yuklanmadi. Internetni tekshirib, qayta bosing."
      : "Faylni tayyorlab bo‘lmadi. Sahifani yangilab, qayta urinib ko‘ring.", "err");
  } finally {
    state.busy = false;
    btn.disabled = false;
  }
}

// ------------------------------------------------------------ mening taqdimotlarim
async function loadHistory() {
  if (!state.user) return;
  try {
    const r = await api("/api/history");
    const box = $("histBox");
    box.hidden = !r.items.length;
    $("histList").innerHTML = r.items.map((it) => `<li><button class="open" type="button" data-id="${esc(it.id)}">
      <div class="t">${esc(it.title || it.topic)}</div>
      <div class="s">${esc(fmtDate(it.createdAt))} · ${esc(LANG_NAME[it.lang] || "")} · ${esc(it.n)} slayd</div></button></li>`).join("");
  } catch (e) { /* muhim emas */ }
}

async function openDeck(id) {
  if (state.busy) return;
  try {
    setStatus("Taqdimot ochilmoqda…", "busy", 50);
    const r = await api("/api/deck?id=" + encodeURIComponent(id));
    state.data = r.data;
    state.deckId = r.id;
    state.lang = r.lang || "uz";
    state.isSample = false;
    $("topic").value = r.data.title || "";
    if (r.subject) $("subject").value = r.subject;
    if ($("lang-" + state.lang)) $("lang-" + state.lang).checked = true;
    render();
    persist();
    setStatus("✓ Taqdimot ochildi. Uni istalgan formatda qayta yuklab olishingiz mumkin.", "ok", 100);
    $("resTitle").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    setStatus(e.message, "err");
  }
}

// ------------------------------------------------------------ to'lov
function initPay() {
  const dlg = $("payDlg");
  $("paySend").addEventListener("click", sendPayment);
  $("payInfo").addEventListener("click", (e) => {
    if (e.target.closest("[data-copy]")) copyText(String(state.cfg.settings.cardNumber || "").replace(/\s+/g, ""));
  });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
}

async function openPay() {
  if (!state.user && !(await login())) return;
  try {
    const cfg = await (await fetch("/api/config")).json();
    if (cfg.settings) state.cfg.settings = cfg.settings;
  } catch (e) { /* eski sozlamalar bilan davom etamiz */ }
  const s = state.cfg.settings;
  const me = state.me || {};
  $("payLead").textContent = me.credits > 0 ? `Hozir sizda ${me.credits} ta taqdimot bor.` : "Bepul taqdimotlar tugadi. Paketni tanlang:";
  const pk = s.packages || [];
  $("pkgs").innerHTML = pk.length ? pk.map((p, i) => `<label><input type="radio" name="pkg" value="${esc(p.id)}"${i === 0 ? " checked" : ""}>
    <span class="pk"><b>${esc(p.title)}</b><span>${esc(fmtMoney(p.price))}</span>
    <small>${p.credits > 0 ? esc(fmtMoney(Math.round(p.price / p.credits))) + " / dona" : ""}</small></span></label>`).join("")
    : `<p class="notice">Paketlar hali qo‘shilmagan.</p>`;
  const tg = String(s.telegram || "").trim();
  const card = String(s.cardNumber || "").trim();
  $("payInfo").innerHTML = !card ? `<p class="notice">To‘lov ma’lumotlari hali kiritilmagan. Keyinroq urinib ko‘ring.</p>` : `
    <ol class="steps"><li>Tanlangan paket narxini shu kartaga o‘tkazing:</li></ol>
    <div class="cardbox" style="margin:8px 0">
      <div><div class="cardnum">${esc(card.replace(/\s+/g, "").replace(/(\d{4})(?=\d)/g, "$1 "))}</div>
      ${s.cardHolder ? `<div class="cardholder">${esc(s.cardHolder)}</div>` : ""}</div>
      <button class="btn btn-ghost btn-sm" type="button" data-copy>📋 Nusxa</button>
    </div>
    <ol class="steps" start="2">
      ${tg ? `<li>Chekni (skrinshot) Telegram’da yuboring: <a href="https://t.me/${encodeURIComponent(tg)}" target="_blank" rel="noopener">@${esc(tg)}</a>. ${TG ? "Chekni shu Telegram akkauntingizdan yuboring." : `Xabarga pochtangizni yozing: <b>${esc(me.email || "")}</b>`}</li>` : `<li>Chekni saqlab qo‘ying — admin so‘rashi mumkin.</li>`}
      <li>Pastdagi izohni yozib, «So‘rov yuborish»ni bosing.</li>
      <li>Admin tasdiqlagach, taqdimotlar hisobingizga qo‘shiladi.</li>
    </ol>
    ${s.note ? `<p class="fine" style="margin-top:8px">${esc(s.note)}</p>` : ""}`;
  $("paySend").disabled = !card || !pk.length;
  $("payMsg").textContent = "";
  $("payNote").value = "";
  loadMyPays();
  if (!$("payDlg").open) $("payDlg").showModal();
}

async function sendPayment() {
  const pkg = (document.querySelector('input[name="pkg"]:checked') || {}).value;
  const note = $("payNote").value.trim();
  if (!pkg) { $("payMsg").textContent = "Paketni tanlang."; return; }
  if (note.length < 3) { $("payMsg").textContent = "Izohga to‘lov vaqti yoki kartangizning oxirgi 4 raqamini yozing."; $("payNote").focus(); return; }
  $("paySend").disabled = true;
  try {
    await api("/api/payments", { body: { packageId: pkg, note } });
    $("payMsg").textContent = "✓ So‘rov yuborildi. Admin chekni tekshirib tasdiqlaydi — shundan keyin taqdimotlar qo‘shiladi.";
    $("payNote").value = "";
    loadMyPays();
  } catch (e) {
    $("payMsg").textContent = e.message;
  } finally {
    $("paySend").disabled = false;
  }
}

const ST = { pending: "Kutilmoqda", approved: "Tasdiqlandi", rejected: "Rad etildi" };
async function loadMyPays() {
  try {
    const r = await api("/api/payments");
    $("myPays").hidden = !r.items.length;
    $("myPaysList").innerHTML = r.items.map((p) => `<li><div class="main"><div class="t">${esc(p.title)} · ${esc(fmtMoney(p.price))}</div>
      <div class="s">${esc(fmtDate(p.createdAt))}${p.reason ? " · " + esc(p.reason) : ""}</div></div>
      <span class="st ${esc(p.status)}">${esc(ST[p.status] || p.status)}</span></li>`).join("");
    if (r.items.some((p) => p.status === "approved")) refreshMe().catch(() => {});
  } catch (e) { /* muhim emas */ }
}

boot();
