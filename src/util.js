'use strict';

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const money = (n) => String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
const som = (n) => `${money(n)} so‘m`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Inline klaviatura: [[['Matn', 'data'], ['Havola', {url: '...'}]], ...]
function ikb(rows) {
  const out = [];
  for (const row of rows || []) {
    if (!row) continue;
    const r = [];
    for (const b of row) {
      if (!b) continue;
      const [text, d] = b;
      if (d && typeof d === 'object') r.push({ text, ...d });
      else r.push({ text, callback_data: String(d) });
    }
    if (r.length) out.push(r);
  }
  return { inline_keyboard: out };
}
const link = (u) => ({ url: u });

// Pastki (reply) klaviatura
function rkb(rows, opts = {}) {
  return {
    keyboard: rows.map((r) => r.map((t) => (typeof t === 'string' ? { text: t } : t))),
    resize_keyboard: true,
    ...opts,
  };
}

function chunk(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
}

// Heshteg: "Toshkent shahri" -> #Toshkent_shahri
function tag(s) {
  const t = String(s || '')
    .replace(/[‘’ʼ'`´]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, '_')
    .replace(/^_+|_+$/g, '');
  return t ? `#${t}` : '';
}

// Matnni solishtirish uchun soddalashtirish
function norm(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[‘’ʼ`´]/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function clip(s, n) {
  s = String(s || '');
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

// "20000 100" -> [20000, 100]
function nums(s) {
  return (String(s || '').match(/\d+(?:[.,]\d+)?/g) || []).map((x) => parseFloat(x.replace(',', '.')));
}

// Telefon raqamini tekshirish va chiroyli ko‘rinishga keltirish
function normPhone(s) {
  const d = String(s || '').replace(/\D/g, '');
  if (d.length === 9) return `+998${d}`;
  if (d.length === 12 && d.startsWith('998')) return `+${d}`;
  if (d.length >= 10 && d.length <= 15) return `+${d}`;
  return null;
}
function fmtPhone(p) {
  const d = String(p || '').replace(/\D/g, '');
  if (d.length === 12 && d.startsWith('998')) return `+998 ${d.slice(3, 5)} ${d.slice(5, 8)} ${d.slice(8, 10)} ${d.slice(10)}`;
  return p ? (String(p).startsWith('+') ? String(p) : `+${p}`) : '';
}

function plural(n, word) {
  return `${n} ta ${word}`;
}

module.exports = { esc, money, som, sleep, ikb, link, rkb, chunk, tag, norm, clip, nums, normPhone, fmtPhone, plural };
