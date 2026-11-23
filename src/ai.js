'use strict';
// Gemini yordamida vakansiya matnini yaxshilash.
const cfg = require('./config');

const enabled = () => !!cfg.GEMINI_API_KEY;
let workingModel = null;

function models() {
  const list = [workingModel, cfg.GEMINI_MODEL, 'gemini-flash-latest', 'gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash', 'gemini-2.5-flash', 'gemini-3.5-flash-lite', 'gemini-flash-lite-latest'];
  return list.filter((m, i) => m && list.indexOf(m) === i);
}

async function generate(prompt) {
  let lastErr = null;
  for (const model of models()) {
    try {
      const r = await fetch(`${cfg.GEMINI_BASE}/v1beta/models/${model}:generateContent`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-goog-api-key': cfg.GEMINI_API_KEY },
        body: JSON.stringify({
          contents: [{ role: 'user', parts: [{ text: prompt }] }],
          generationConfig: { temperature: 0.4, maxOutputTokens: 4096 },
        }),
        signal: AbortSignal.timeout(35000),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        const msg = (j.error && j.error.message) || `HTTP ${r.status}`;
        // Kalitning o‘zi ishlamasa, boshqa modellarni sinash befoyda
        if (r.status === 401 || (r.status === 403 && /API_KEY|SERVICE_DISABLED/i.test(msg))) throw Object.assign(new Error(`Gemini kaliti ishlamadi: ${msg}`), { fatal: true });
        throw new Error(`${model}: ${msg}`);
      }
      const parts = (j.candidates && j.candidates[0] && j.candidates[0].content && j.candidates[0].content.parts) || [];
      const text = parts.filter((p) => !p.thought).map((p) => p.text || '').join('').trim();
      if (text) { workingModel = model; return text; }
      lastErr = new Error(`${model}: bo‘sh javob`);
    } catch (e) {
      lastErr = e;
      if (e.fatal) break;
    }
  }
  throw lastErr || new Error('AI javob bermadi');
}

async function improveVacancy(d) {
  const prompt = [
    'Sen O‘zbekistondagi ish e’lonlari Telegram kanalining muharririsan.',
    'Quyidagi ma’lumotlar asosida «Talablar va vazifalar» bo‘limini o‘zbek tilida (lotin yozuvida) aniq, qisqa va chiroyli qilib qayta yoz.',
    'Qoidalar: faqat berilgan faktlardan foydalan; maosh, telefon, manzil yoki yangi talab o‘ylab topma; 3–7 ta qisqa punkt yoz, har biri «• » bilan boshlansin; kerak bo‘lsa «Talablar:» va «Vazifalar:» kichik sarlavhalarini ishlat; emoji, HTML va markdown ishlatma; faqat tayyor matnni qaytar, 700 belgidan oshmasin.',
    '',
    `Lavozim: ${d.title}`,
    `Soha: ${d.category}`,
    d.company ? `Ish joyi: ${d.company}` : '',
    `Ish vaqti: ${d.schedule}`,
    `Ish beruvchi yozgan matn: ${d.req}`,
  ].filter(Boolean).join('\n');
  let out = await generate(prompt);
  out = out
    .replace(/\*\*/g, '')
    .replace(/^#+\s*/gm, '')
    .replace(/^\s*[-*]\s+/gm, '• ')
    .replace(/<[^>]+>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  if (out.length > 800) out = `${out.slice(0, 797).replace(/\s+\S*$/, '')}…`;
  if (out.length < 10) throw new Error('AI matni juda qisqa');
  return out;
}

// Ishga tushganda kalitni tekshirish (loglar uchun)
async function check() {
  if (!enabled()) return 'kalit yo‘q';
  try {
    const r = await fetch(`${cfg.GEMINI_BASE}/v1beta/models?pageSize=100`, { headers: { 'x-goog-api-key': cfg.GEMINI_API_KEY }, signal: AbortSignal.timeout(15000) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return `xato: ${(j.error && j.error.message) || r.status}`;
    const names = (j.models || []).map((m) => String(m.name || '').replace('models/', ''));
    const ok = models().filter((m) => names.includes(m));
    return `ishlayapti (${ok.slice(0, 3).join(', ') || 'mos model topilmadi'})`;
  } catch (e) {
    return `tekshirib bo‘lmadi: ${e.message}`;
  }
}

module.exports = { enabled, improveVacancy, check };
