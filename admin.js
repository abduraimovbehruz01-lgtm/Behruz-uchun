// admin.js — sayt egasi uchun boshqaruv paneli (login va parol bilan kirilgandan keyin ochiladi).
let H = null;      // app.js dan yordamchilar: api, toast, esc, fmtMoney, fmtDate, refreshMe
let root = null;
let tab = "stats";
let payFilter = "pending";

const ST = { pending: "Tekshirish kerak", approved: "Tasdiqlangan", rejected: "Rad etilgan", awaiting_receipt: "Chek kutilmoqda", cancelled: "Bekor qilingan" };
const LANG = { uz: "O‘zb", ru: "Рус", en: "Eng" };

export function mountAdmin(el, helpers) {
  H = helpers;
  root = el;
  if (!el.querySelector("#admTabs")) {
    el.innerHTML = `
      <div class="admin-head"><h1>Admin panel</h1>
        <div style="display:flex;gap:6px"><a class="icon-btn" href="#" data-home>← Orqaga</a>
        <button class="icon-btn" type="button" data-logout>Chiqish</button></div></div>
      <div class="tabs" role="tablist" id="admTabs">
        <button type="button" role="tab" data-tab="stats">📊 Statistika</button>
        <button type="button" role="tab" data-tab="payments">💳 To‘lovlar<span class="badge" id="admBadge" hidden></span></button>
        <button type="button" role="tab" data-tab="users">👥 Foydalanuvchilar</button>
        <button type="button" role="tab" data-tab="broadcast">📣 Xabar</button>
        <button type="button" role="tab" data-tab="settings">⚙️ Sozlamalar</button>
        <button type="button" role="tab" data-tab="history">🕘 Tarix</button>
      </div>
      <div id="admBody" role="tabpanel"></div>`;
    el.querySelector("#admTabs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) show(b.dataset.tab);
    });
  }
  show(tab);
}

function show(name) {
  tab = name;
  root.querySelectorAll("#admTabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  // har safar yangi quti: eski (sekin kelgan) javob yangi bo'limning ustiga yozilib qolmasin
  const panel = root.querySelector("#admBody");
  const body = document.createElement("div");
  body.innerHTML = `<p class="empty">Yuklanmoqda…</p>`;
  panel.replaceChildren(body);
  const fn = { stats, payments, users, settings, history, broadcast }[name];
  fn(body).catch((e) => { body.innerHTML = `<p class="empty">${H.esc(e.message)}</p>`; });
}

function setBadge(n) {
  const b = root.querySelector("#admBadge");
  b.hidden = !n;
  b.textContent = n;
}

// ------------------------------------------------------------ statistika
async function stats(body) {
  const s = await H.api("/api/admin/stats");
  setBadge(s.pending);
  const card = (v, l, act) => `<div class="stat"${act ? ` role="button" tabindex="0" data-go="${act}" style="cursor:pointer"` : ""}>
    <div class="v">${H.esc(v)}</div><div class="l">${H.esc(l)}</div></div>`;
  body.innerHTML = `
    ${s.warnings && s.warnings.length ? `<p class="notice" style="margin:0 0 12px">⚠️ ${s.warnings.map(H.esc).join("<br>⚠️ ")}</p>` : ""}
    <div class="stats">
      ${card(s.users, "Foydalanuvchilar")}
      ${card(s.generations, "Jami taqdimotlar")}
      ${card(s.today == null ? "—" : s.today, "Bugun yasalgan")}
      ${card(H.fmtMoney(s.revenue), "Daromad (karta)")}
      ${card("⭐ " + (s.stars || 0), "Stars bilan tushgan")}
      ${card(s.approved, "Tasdiqlangan to‘lovlar")}
      ${card(s.pending, "Kutilayotgan to‘lovlar", "payments")}
    </div>
    <div class="panel" style="margin-top:14px">
      <b>Qanday ishlaydi</b>
      <ol class="steps" style="margin-top:8px">
        <li>Foydalanuvchi ilovada paket tanlaydi — bot unga karta raqamini yuboradi.</li>
        <li>U kartaga o‘tkazib, chek rasmini botga yuboradi — chek sizga Telegram’da ✅/❌ tugmalari bilan keladi.</li>
        <li><b>✅ Tasdiqlash</b>ni bossangiz — jetonlar avtomatik tushadi va foydalanuvchiga xabar boradi.</li>
        <li>⭐ Stars bilan to‘lovlar tekshiruvsiz, darhol tushadi.</li>
      </ol>
      <p class="fine" style="margin-top:10px">AI modellari: ${H.esc((s.models || []).join(", "))}</p>
      ${s.telegram ? `<p class="fine">Telegram bot: ${H.esc(s.telegram)} — ilova bot ichida ochiladi. Admin panel: botga /admin yozing.</p>` : ""}
    </div>`;
  body.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => show(el.dataset.go)));
}

// ------------------------------------------------------------ to'lovlar
async function payments(body) {
  const r = await H.api("/api/admin/payments?status=" + payFilter);
  if (payFilter === "pending") setBadge(r.items.length);
  const SEG = { pending: "Yangi", awaiting_receipt: "Chek kutilmoqda", approved: "Tasdiqlangan", rejected: "Rad etilgan", all: "Hammasi" };
  const seg = Object.keys(SEG).map((k) => `<label><input type="radio" name="pf" value="${k}"${k === payFilter ? " checked" : ""}>
    <span>${SEG[k]}</span></label>`).join("");
  body.innerHTML = `<div class="seg seg-sm" style="margin-bottom:12px">${seg}</div>
    <div class="items">${r.items.length ? r.items.map(payItem).join("") : `<p class="empty">${payFilter === "pending" ? "Yangi so‘rovlar yo‘q 🎉" : "Hech narsa yo‘q"}</p>`}</div>`;
  body.querySelector(".seg").addEventListener("change", (e) => { payFilter = e.target.value; show("payments"); });
  body.querySelector(".items").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const item = b.closest(".item");
    const id = item.dataset.id;
    if (b.dataset.act === "approve") {
      if (b.dataset.sure !== "1") {
        b.dataset.sure = "1";
        b.textContent = "Rostdan tasdiqlaysizmi? Yana bosing";
        setTimeout(() => { if (b.isConnected) { b.dataset.sure = ""; b.textContent = b.dataset.label; } }, 4000);
        return;
      }
      b.disabled = true;
      try {
        await H.api("/api/admin/payments/approve", { body: { id } });
        H.toast("✓ Tasdiqlandi — jetonlar qo‘shildi");
        show("payments");
      } catch (err) { H.toast(err.message); b.disabled = false; }
    } else if (b.dataset.act === "receipt") {
      b.disabled = true;
      b.textContent = "Yuklanmoqda…";
      try {
        const r = await H.api("/api/admin/receipt?id=" + encodeURIComponent(id));
        const box = item.querySelector(".rc");
        box.hidden = false;
        box.innerHTML = r.mime.startsWith("image/") ? `<img src="${r.dataUrl}" alt="Chek" style="max-width:100%;border-radius:10px">`
          : `<a class="btn btn-ghost btn-sm" href="${r.dataUrl}" download="chek.pdf">📄 Chekni ochish</a>`;
        b.remove();
      } catch (err) { H.toast(err.message); b.disabled = false; b.textContent = "🧾 Chekni ko‘rish"; }
    } else if (b.dataset.act === "reject") {
      item.querySelector(".rej").hidden = false;
      item.querySelector(".rej input").focus();
    } else if (b.dataset.act === "rejectGo") {
      b.disabled = true;
      try {
        await H.api("/api/admin/payments/reject", { body: { id, reason: item.querySelector(".rej input").value } });
        H.toast("So‘rov rad etildi");
        show("payments");
      } catch (err) { H.toast(err.message); b.disabled = false; }
    }
  });
}

function payItem(p) {
  const e = H.esc;
  const label = `✓ Tasdiqlash (+${p.credits} jeton)`;
  return `<div class="item" data-id="${e(p.id)}">
    <div class="top"><div><div class="who">${e(p.email)}</div><div class="sub">${e(p.name || "")}${p.name ? " · " : ""}${e(H.fmtDate(p.createdAt))}</div></div>
      <span class="st ${e(p.status)}">${e(ST[p.status] || p.status)}</span></div>
    <div><b>${e(p.title)}</b> · ${p.method === "stars" ? "⭐ " + e(p.stars) + " Stars" : e(H.fmtMoney(p.price))} · +${e(p.credits)} jeton</div>
    ${p.note && p.note !== "Chek bot orqali" ? `<div class="note">💬 ${e(p.note)}</div>` : ""}
    ${p.receipt ? `<div class="acts"><button class="btn btn-ghost btn-sm" type="button" data-act="receipt">🧾 Chekni ko‘rish</button></div><div class="rc" hidden></div>` : ""}
    ${p.reason ? `<div class="sub">Sabab: ${e(p.reason)}</div>` : ""}
    ${["pending", "awaiting_receipt"].includes(p.status) ? `<div class="acts">
        <button class="btn btn-ok btn-sm" type="button" data-act="approve" data-label="${e(label)}">${e(label)}</button>
        <button class="btn btn-danger btn-sm" type="button" data-act="reject">✕ Rad etish</button></div>
      <div class="acts rej" hidden><input type="text" maxlength="200" placeholder="Sabab (masalan: chek topilmadi)">
        <button class="btn btn-danger btn-sm" type="button" data-act="rejectGo">Rad etish</button></div>`
      : p.decidedAt ? `<div class="sub">Ko‘rib chiqildi: ${e(H.fmtDate(p.decidedAt))}</div>` : ""}
  </div>`;
}

// ------------------------------------------------------------ foydalanuvchilar
let userQ = "";
async function users(body) {
  body.innerHTML = `<input type="search" id="uq" placeholder="Qidirish: @username yoki ID (boshini yozing)" value="${H.esc(userQ)}"
      autocapitalize="off" autocorrect="off" spellcheck="false" style="margin-bottom:12px">
    <div class="items" id="ulist"><p class="empty">Yuklanmoqda…</p></div>`;
  let t;
  body.querySelector("#uq").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => {
      if (!body.isConnected) return;   // bo'lim yopilgan bo'lsa — hech narsa qilmaymiz
      userQ = e.target.value.trim().toLowerCase();
      loadUsers(body).catch((err) => H.toast(err.message));
    }, 400);
  });
  body.querySelector("#ulist").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const item = b.closest(".item");
    const uid = item.dataset.uid;
    b.disabled = true;
    try {
      if (b.dataset.act === "add") {
        const delta = Number(b.dataset.n || item.querySelector("input[type=number]").value);
        if (!delta) { H.toast("Sonni yozing"); return; }
        const r = await H.api("/api/admin/users/credits", { body: { uid, delta } });
        item.querySelector(".cr").textContent = r.credits;
        H.toast(`✓ Endi ${r.credits} ta`);
        H.refreshMe().catch(() => {});
      } else if (b.dataset.act === "block") {
        const blocked = item.dataset.blocked !== "1";
        await H.api("/api/admin/users/block", { body: { uid, blocked } });
        H.toast(blocked ? "Bloklandi" : "Blokdan chiqarildi");
        await loadUsers(body);
      }
    } catch (err) { H.toast(err.message); }
    finally { b.disabled = false; }
  });
  await loadUsers(body);
}

async function loadUsers(body) {
  const list = body.querySelector("#ulist");
  const r = await H.api("/api/admin/users?q=" + encodeURIComponent(userQ));
  const e = H.esc;
  list.innerHTML = r.items.length ? r.items.map((u) => `<div class="item" data-uid="${e(u.uid)}" data-blocked="${u.blocked ? 1 : 0}">
      <div class="top"><div><div class="who">${e(u.name || u.email)}${u.blocked ? ' <span class="st rejected">Bloklangan</span>' : ""}</div>
        <div class="sub">${u.name ? e(u.email) + " · " : ""}${u.refCount ? "👥 " + e(u.refCount) + " do‘st · " : ""}ro‘yxatdan: ${e(H.fmtDate(u.createdAt))} · oxirgi kirish: ${e(H.fmtDate(u.lastSeen))}</div></div>
        <div style="text-align:right"><div class="stat-v" style="font-size:22px;font-weight:700">🎟 <span class="cr">${e(u.credits)}</span></div>
        <div class="sub">yasagan: ${e(u.used)} ta</div></div></div>
      <div class="acts">
        <button class="btn btn-ghost btn-sm" type="button" data-act="add" data-n="1">+1</button>
        <button class="btn btn-ghost btn-sm" type="button" data-act="add" data-n="5">+5</button>
        <button class="btn btn-ghost btn-sm" type="button" data-act="add" data-n="-1">−1</button>
        <input type="number" inputmode="numeric" placeholder="son" style="max-width:90px">
        <button class="btn btn-ghost btn-sm" type="button" data-act="add">Qo‘shish</button>
        <button class="btn btn-danger btn-sm" type="button" data-act="block">${u.blocked ? "Blokdan chiqarish" : "Bloklash"}</button>
      </div></div>`).join("")
    : `<p class="empty">${userQ ? "Bunday pochta topilmadi" : "Hali foydalanuvchilar yo‘q"}</p>`;
}

// ------------------------------------------------------------ sozlamalar
async function settings(body) {
  const { settings: s } = await H.api("/api/admin/settings");
  const e = H.esc;
  const row = (p) => `<div class="pkg-row">
      <label class="pk-title"><span>Nomi</span><input type="text" data-k="title" value="${e(p.title)}" placeholder="Masalan: 10 ta taqdimot" maxlength="60" data-id="${e(p.id || "")}"></label>
      <label><span>Jeton</span><input type="number" data-k="credits" value="${e(p.credits)}" inputmode="numeric" min="1"></label>
      <label><span>Narxi, so‘m</span><input type="number" data-k="price" value="${e(p.price)}" inputmode="numeric" min="0"></label>
      <label><span>⭐ Stars</span><input type="number" data-k="stars" value="${e(p.stars || 0)}" inputmode="numeric" min="0"></label>
      <button class="x" type="button" data-del aria-label="Paketni o‘chirish">✕</button></div>`;
  body.innerHTML = `<form class="panel" id="setForm" style="display:flex;flex-direction:column;gap:16px" novalidate>
    <div class="field"><span class="lbl">Cheklar keladigan Telegram akkauntlar</span>
      ${(s.adminChats || []).length ? `<ul class="list" id="admChats">${s.adminChats.map((a) => `<li><div class="main"><div class="t">${e(a.name || a.id)}</div>
        <div class="s">ID ${e(a.id)}</div></div><button class="btn btn-ghost btn-sm" type="button" data-unlink="${e(a.id)}">Olib tashlash</button></li>`).join("")}</ul>`
        : `<p class="notice" style="margin:0">Hali yo‘q. Botga <b>/admin</b> yozib, panelga shu yerdan kiring — akkauntingiz avtomatik qo‘shiladi va cheklar sizga keladi.</p>`}</div>
    <div class="field"><label for="sFree">Yangi foydalanuvchiga bepul jetonlar</label>
      <input id="sFree" type="number" inputmode="numeric" min="0" max="100" value="${e(s.freeCredits)}"></div>
    <div class="field"><span class="lbl">Do‘st taklif qilish bonusi</span>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">Har
        <input id="sRefEvery" type="number" inputmode="numeric" min="1" max="100" value="${e(s.refEvery || 5)}" style="max-width:80px"> ta do‘st uchun +
        <input id="sRefBonus" type="number" inputmode="numeric" min="0" max="100" value="${e(s.refBonus == null ? 2 : s.refBonus)}" style="max-width:80px"> jeton</div>
      <p class="hint">0 jeton — bonus o‘chiriladi.</p></div>
    <div class="field"><label for="sChan">Majburiy kanal obunasi <span class="hint">— @kanal, bo‘sh qoldirsangiz o‘chiq</span></label>
      <input id="sChan" type="text" maxlength="64" value="${e(s.requiredChannel || "")}" placeholder="@kanalingiz" autocapitalize="off">
      <p class="hint">Bot kanalga admin qilib qo‘shilgan bo‘lishi kerak. Obuna bo‘lmaganlar taqdimot yasay olmaydi.</p></div>
    <div class="field"><label for="sCard">Karta raqami <span class="hint">(Uzcard / Humo)</span></label>
      <input id="sCard" type="text" inputmode="numeric" maxlength="40" value="${e(s.cardNumber)}" placeholder="8600 0000 0000 0000"></div>
    <div class="field"><label for="sHolder">Karta egasi</label>
      <input id="sHolder" type="text" maxlength="60" value="${e(s.cardHolder)}" placeholder="ISM FAMILIYA"></div>
    <div class="field"><label for="sTg">Yordam uchun Telegram <span class="hint">— @siz, faqat username</span></label>
      <input id="sTg" type="text" maxlength="80" value="${e(s.telegram)}" placeholder="username" autocapitalize="off"></div>
    <div class="field"><label for="sNote">To‘lov oynasidagi izoh</label>
      <textarea id="sNote" rows="3" maxlength="500">${e(s.note)}</textarea></div>
    <div class="field"><span class="lbl">Paketlar</span>
      <div id="pkgRows" style="display:flex;flex-direction:column;gap:8px">${(s.packages || []).map(row).join("")}</div>
      <button class="btn btn-ghost btn-sm" type="button" id="addPkg">＋ Paket qo‘shish</button></div>
    <button class="btn btn-primary" type="submit">Saqlash</button>
  </form>`;
  const rows = body.querySelector("#pkgRows");
  body.querySelector("#addPkg").addEventListener("click", () => {
    if (rows.children.length >= 8) return H.toast("Ko‘pi bilan 8 ta paket");
    rows.insertAdjacentHTML("beforeend", row({ title: "", credits: 10, price: 20000, stars: 100 }));
    rows.lastElementChild.querySelector("input").focus();
  });
  rows.addEventListener("click", (ev) => { const d = ev.target.closest("[data-del]"); if (d) d.parentElement.remove(); });
  body.querySelectorAll("[data-unlink]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await H.api("/api/admin/tg-unlink", { body: { id: b.dataset.unlink } }); H.toast("Olib tashlandi"); show("settings"); }
    catch (err) { H.toast(err.message); b.disabled = false; }
  }));
  body.querySelector("#setForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = ev.submitter || body.querySelector("button[type=submit]");
    const packages = [...rows.children].map((r) => ({
      id: r.querySelector('[data-k="title"]').dataset.id,
      title: r.querySelector('[data-k="title"]').value,
      credits: Number(r.querySelector('[data-k="credits"]').value),
      price: Number(r.querySelector('[data-k="price"]').value),
      stars: Number(r.querySelector('[data-k="stars"]').value),
    })).filter((p) => p.title.trim());
    btn.disabled = true;
    try {
      await H.api("/api/admin/settings", { body: {
        freeCredits: Number(body.querySelector("#sFree").value),
        cardNumber: body.querySelector("#sCard").value, cardHolder: body.querySelector("#sHolder").value,
        telegram: body.querySelector("#sTg").value, note: body.querySelector("#sNote").value, packages,
        refEvery: Number(body.querySelector("#sRefEvery").value), refBonus: Number(body.querySelector("#sRefBonus").value),
        requiredChannel: body.querySelector("#sChan").value,
      } });
      H.toast("✓ Saqlandi");
      show("settings");
    } catch (err) { H.toast(err.message); }
    finally { btn.disabled = false; }
  });
}

// ------------------------------------------------------------ tarix
async function history(body) {
  const r = await H.api("/api/admin/generations");
  const e = H.esc;
  body.innerHTML = r.items.length ? `<ul class="list">${r.items.map((g) => `<li><div class="main">
      <div class="t">${e(g.title || g.topic)}</div>
      <div class="s">${g.mode === "text" ? "📄 matndan · " : g.mode === "sample" ? "🎯 namunadek · " : ""}${e(g.email)} · ${e(H.fmtDate(g.createdAt))} · ${e(LANG[g.lang] || g.lang)} · ${e(g.n)} slayd</div></div></li>`).join("")}</ul>`
    : `<p class="empty">Hali taqdimot yasalmagan</p>`;
}

// ------------------------------------------------------------ hammaga xabar
async function broadcast(body) {
  const r = await H.api("/api/admin/broadcast");
  body.innerHTML = `<div class="panel" style="display:flex;flex-direction:column;gap:12px">
    <div class="field"><label for="bText">Barcha foydalanuvchilarga xabar</label>
      <textarea id="bText" rows="6" maxlength="3500" placeholder="Masalan: Yangi usul qo‘shildi — namunadek taqdimot! 🎯"></textarea>
      <p class="hint">Xabar bot orqali boradi, ostida «Ilovani ochish» tugmasi bo‘ladi. Botni bloklaganlarga yetib bormaydi.</p></div>
    <button class="btn btn-primary" type="button" id="bSend">📣 Yuborish</button>
    <p class="fine" id="bStat"></p></div>`;
  const stat = body.querySelector("#bStat");
  const showJob = (j) => {
    if (!j) { stat.textContent = ""; return; }
    stat.textContent = j.done ? `✓ Yuborildi: ${j.sent} ta${j.failed ? `, yetib bormadi: ${j.failed}` : ""} (jami ${j.total}).`
      : `Yuborilmoqda… ${j.sent + j.failed} / ${j.total}`;
  };
  let timer = null;
  const poll = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!body.isConnected) return;
      try { const x = await H.api("/api/admin/broadcast"); showJob(x.job); if (x.job && !x.job.done) poll(); } catch (e) { /* */ }
    }, 1500);
  };
  showJob(r.job);
  if (r.job && !r.job.done) poll();
  body.querySelector("#bSend").addEventListener("click", async (ev) => {
    const b = ev.currentTarget;
    const text = body.querySelector("#bText").value.trim();
    if (text.length < 2) return H.toast("Xabar matnini yozing");
    if (b.dataset.sure !== "1") {
      b.dataset.sure = "1";
      b.textContent = "Hammaga yuborilsinmi? Yana bosing";
      setTimeout(() => { if (b.isConnected) { b.dataset.sure = ""; b.textContent = "📣 Yuborish"; } }, 4000);
      return;
    }
    b.disabled = true;
    try {
      const x = await H.api("/api/admin/broadcast", { body: { text } });
      body.querySelector("#bText").value = "";
      showJob(x.job);
      poll();
    } catch (err) { H.toast(err.message); }
    finally { b.disabled = false; b.dataset.sure = ""; b.textContent = "📣 Yuborish"; }
  });
}
