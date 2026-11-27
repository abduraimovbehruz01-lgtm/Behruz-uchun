// Manhora rasmlar xizmati — Cloudflare Worker (bepul tarif).
// Bot ilovaga imzolangan vaqtinchalik havolalar beradi: /i/<file_id>?e=<muddat>&s=<imzo>.
// Worker imzoni tekshiradi, rasmni Telegram'dan olib, foydalanuvchiga beradi. Render trafigi sarflanmaydi.
// Kerakli sozlama: BOT_TOKEN (Worker secret) — imzo kaliti ham shu tokendan hosil qilinadi.
// Bot bu faylni /cloudflare buyrug'i bilan o'zi joylaydi va kod o'zgarsa qayta joylaydi.

const enc = new TextEncoder();
let KEY = null;                 // imzo kaliti (HMAC-SHA256(BOT_TOKEN, "img-w"))
let KEY_FOR = "";
const PATHS = new Map();        // file_id → {path, t} (Telegram fayl yo'li ~1 soat yaroqli)
const CORS = {"Access-Control-Allow-Origin": "*"};

async function hmac(keyBytes, msg) {
  const k = await crypto.subtle.importKey("raw", keyBytes, {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, enc.encode(msg)));
}

function b64u(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function signature(token, fid, exp) {
  if (!KEY || KEY_FOR !== token) {
    KEY = await hmac(enc.encode(token), "img-w");
    KEY_FOR = token;
  }
  return b64u(await hmac(KEY, `${fid}:${exp}`)).slice(0, 22);
}

function same(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function filePath(env, fid) {
  const hit = PATHS.get(fid);
  if (hit && Date.now() - hit.t < 50 * 60 * 1000) return hit.path;
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/getFile?file_id=${encodeURIComponent(fid)}`);
  const j = await r.json();
  if (!j.ok || !j.result || !j.result.file_path) throw new Error(j.description || "getFile");
  if (PATHS.size > 20000) PATHS.clear();
  PATHS.set(fid, {path: j.result.file_path, t: Date.now()});
  return j.result.file_path;
}

function text(body, status) {
  return new Response(body, {status: status || 200, headers: {"Content-Type": "text/plain; charset=utf-8", ...CORS}});
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, {status: 204, headers: {...CORS, "Access-Control-Allow-Methods": "GET"}});
    if (url.pathname === "/" || url.pathname === "/health") return text("Manhora rasmlar xizmati ishlayapti ✅");
    const m = url.pathname.match(/^\/i\/([A-Za-z0-9_-]{10,250})$/);
    if (!m || req.method !== "GET") return text("topilmadi", 404);
    if (!env.BOT_TOKEN) return text("BOT_TOKEN sozlanmagan", 500);
    const fid = m[1];
    const exp = Number(url.searchParams.get("e") || 0);
    const sig = url.searchParams.get("s") || "";
    if (!exp || exp < Date.now() / 1000) return text("havola muddati o‘tgan", 403);
    if (!same(await signature(env.BOT_TOKEN, fid, exp), sig)) return text("imzo noto‘g‘ri", 403);

    const cache = caches.default;
    const key = new Request(`https://manhora-cache.local/i/${fid}`);
    let res = await cache.match(key);
    if (!res) {
      let path;
      try {
        path = await filePath(env, fid);
      } catch (e) {
        return text("rasm topilmadi", 404);
      }
      const r = await fetch(`https://api.telegram.org/file/bot${env.BOT_TOKEN}/${path}`);
      if (!r.ok) {
        PATHS.delete(fid);
        return text("Telegram javob bermadi", 502);
      }
      const type = (r.headers.get("Content-Type") || "").startsWith("image/") ? r.headers.get("Content-Type") : "image/jpeg";
      res = new Response(r.body, {headers: {"Content-Type": type, "Cache-Control": "public, max-age=2592000, immutable"}});
      ctx.waitUntil(cache.put(key, res.clone()).catch(() => {}));
    }
    return new Response(res.body, {
      status: 200,
      headers: {"Content-Type": res.headers.get("Content-Type") || "image/jpeg",
                "Cache-Control": "private, max-age=604800, immutable", ...CORS},
    });
  },
};
