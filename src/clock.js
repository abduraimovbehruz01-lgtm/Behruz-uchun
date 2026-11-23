'use strict';
// Vaqt bilan ishlash. O‘zbekiston vaqti: UTC+5 (yozgi vaqt yo‘q).
const TZ = 5 * 3600e3;
const MIN = 60e3;
const HOUR = 3600e3;
const DAY = 24 * HOUR;

let fixed = null; // sinov uchun

const now = () => (fixed !== null ? fixed : Date.now());
const set = (ts) => { fixed = ts; };
const advance = (ms) => { if (fixed === null) fixed = Date.now(); fixed += ms; };

const pad = (n) => String(n).padStart(2, '0');

function parts(ts = now()) {
  const d = new Date(ts + TZ);
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth() + 1, d: d.getUTCDate(), h: d.getUTCHours(), mi: d.getUTCMinutes() };
}

const dayKey = (ts = now()) => { const p = parts(ts); return `${p.y}-${pad(p.mo)}-${pad(p.d)}`; };
const fmtDate = (ts = now()) => { const p = parts(ts); return `${pad(p.d)}.${pad(p.mo)}.${p.y}`; };
const fmtTime = (ts = now()) => { const p = parts(ts); return `${pad(p.h)}:${pad(p.mi)}`; };
const fmtDT = (ts = now()) => `${fmtDate(ts)} ${fmtTime(ts)}`;
const fmtShort = (ts = now()) => { const p = parts(ts); return `${pad(p.d)}.${pad(p.mo)} ${pad(p.h)}:${pad(p.mi)}`; };
const hourFloat = (ts = now()) => { const p = parts(ts); return p.h + p.mi / 60; };

function startOfDay(ts = now()) {
  const d = new Date(ts + TZ);
  d.setUTCHours(0, 0, 0, 0);
  return d.getTime() - TZ;
}

module.exports = { TZ, MIN, HOUR, DAY, now, set, advance, parts, dayKey, fmtDate, fmtTime, fmtDT, fmtShort, hourFloat, startOfDay };
