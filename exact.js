/* exact.js — "Namunadek" usuli: yangi taqdimot namuna .pptx faylining O'ZIDAN yasaladi.
 * Dizayn (fon, shrift, shakllar, joylashuv) o'zgarmaydi — faqat matnlar yangi mavzuga, rasmlar esa mavzuga mos rasmlarga almashadi.
 * Server (Node) va brauzerda bir xil ishlaydi; ZIP uchun JSZip tashqaridan beriladi. */
(function (root) {
  "use strict";

  // ------------------------------------------------------------ yordamchilar
  const RE_ELEM = /<p:sp(?:\s[^>]*)?>[\s\S]*?<\/p:sp>|<p:graphicFrame(?:\s[^>]*)?>[\s\S]*?<\/p:graphicFrame>/g;
  const RE_PICEL = /<p:pic(?:\s[^>]*)?>[\s\S]*?<\/p:pic>|<p:sp(?:\s[^>]*)?>[\s\S]*?<\/p:sp>/g;
  const RE_PARA = /<a:p>[\s\S]*?<\/a:p>|<a:p\/>/g;
  const RE_TC = /<a:tc\b([^>]*)>([\s\S]*?)<\/a:tc>|<a:tc\b([^>]*)\/>/g;
  const RE_PT = /<dgm:pt\b([^>]*?)(?:\/>|>([\s\S]*?)<\/dgm:pt>)/g;
  const IMG_EXT = /\.(jpe?g|png|gif|bmp|tiff?|webp)$/i;
  const REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image";
  const CT_SLIDE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml";
  const EMPTY_RELS = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>';

  const re = (r) => new RegExp(r.source, r.flags);
  const dec = (t) => t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (m, n) => String.fromCodePoint(Number(n))).replace(/&#x([0-9a-f]+);/gi, (m, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/&amp;/g, "&");
  const enc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const attrs = (s) => { const o = {}; for (const m of String(s || "").matchAll(/([\w:]+)="([^"]*)"/g)) o[m[1]] = m[2]; return o; };
  const paraText = (p) => dec([...p.matchAll(/<a:t(?:\s[^>]*)?>([^<]*)<\/a:t>/g)].map((m) => m[1]).join("")).replace(/\s+/g, " ").trim();
  const bodyParas = (xml) => (String(xml || "").match(re(RE_PARA)) || []).map(paraText).filter(Boolean);
  const cut = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const clean = (s, n) => String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, n || 400);

  function parseRels(xml) {
    const m = new Map();
    for (const r of String(xml || "").matchAll(/<Relationship\b([^>]*?)\/?>/g)) { const a = attrs(r[1]); if (a.Id) m.set(a.Id, a); }
    return m;
  }
  function resolve(base, target) {
    if (target.startsWith("/")) return target.slice(1);
    const parts = base.split("/");
    parts.pop();
    for (const seg of target.split("/")) { if (seg === "..") parts.pop(); else if (seg && seg !== ".") parts.push(seg); }
    return parts.join("/");
  }
  function relative(fromPart, toPart) {   // ppt/slides/slide1.xml -> ppt/charts/chart1.xml => ../charts/chart1.xml
    const a = fromPart.split("/"); a.pop();
    const b = toPart.split("/");
    let i = 0;
    while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
    return "../".repeat(a.length - i) + b.slice(i).join("/");
  }
  const relsPath = (p) => { const i = p.lastIndexOf("/"); return p.slice(0, i) + "/_rels/" + p.slice(i + 1) + ".rels"; };
  const readText = async (zip, p) => { const f = zip.file(p); return f ? f.async("string") : null; };

  async function slideOrder(zip) {
    const pres = await readText(zip, "ppt/presentation.xml");
    if (!pres) throw new Error("not a pptx");
    const rels = parseRels(await readText(zip, "ppt/_rels/presentation.xml.rels"));
    const lst = (/<p:sldIdLst>([\s\S]*?)<\/p:sldIdLst>/.exec(pres) || [])[1] || "";
    const slides = [];
    for (const m of lst.matchAll(/<p:sldId\b([^>]*?)\/?>/g)) {
      const r = rels.get(attrs(m[1])["r:id"]);
      if (r) { const p = resolve("ppt/presentation.xml", r.Target); if (zip.file(p)) slides.push(p); }
    }
    const sz = attrs((/<p:sldSz\b([^>]*?)\/?>/.exec(pres) || [])[1]);
    return { slides, w: Number(sz.cx) || 12192000, h: Number(sz.cy) || 6858000, pres };
  }

  // ------------------------------------------------------------ matn joylari (slot) — tahlil va yasashda bir xil tartib
  function spSlot(el) {
    const ph = /<p:ph\b([^>]*?)\/?>/.exec(el);
    const type = ph ? attrs(ph[1]).type || "body" : null;
    if (type && /^(sldNum|dt|ftr|hdr)$/.test(type)) return null;
    const tb = /<p:txBody>[\s\S]*?<\/p:txBody>/.exec(el);
    if (!tb) return null;
    const p = bodyParas(tb[0]);
    if (!p.length) return null;
    const k = type === "title" || type === "ctrTitle" ? "title" : type === "subTitle" ? "sub" : type ? "body" : "text";
    return { k, p };
  }
  function gfSlot(el) {
    if (/<a:tbl>/.test(el)) {
      const c = [];
      let any = false;
      for (const m of el.matchAll(re(RE_TC))) {
        const a = attrs(m[1] || m[3]);
        if (a.hMerge === "1" || a.vMerge === "1") { c.push(null); continue; }
        const t = bodyParas(m[2]).join(" ");
        if (t) any = true;
        c.push(t);
      }
      const first = /<a:tr\b[^>]*>([\s\S]*?)<\/a:tr>/.exec(el);
      const cols = first ? (first[1].match(re(RE_TC)) || []).length : 1;
      return any ? { k: "table", cols: Math.max(1, cols), c } : null;
    }
    const dm = /<dgm:relIds\b([^>]*?)\/?>/.exec(el);
    if (dm) return { k: "smart", dm: attrs(dm[1])["r:dm"] };
    const chm = /<c:chart\b([^>]*?)\/?>/.exec(el);
    if (chm && attrs(chm[1])["r:id"]) return { k: "chart", rid: attrs(chm[1])["r:id"] };
    return null;
  }

  // ------------------------------------------------------------ grafiklar (chart): raqamlar yangi mavzuga almashadi
  const ptVals = (blk) => [...String(blk || "").matchAll(/<c:pt idx="(\d+)"[^>]*>\s*<c:v>([^<]*)<\/c:v>/g)]
    .sort((a, b) => Number(a[1]) - Number(b[1])).map((m) => dec(m[2]));
  function chartInfo(xml) {
    const tm = /<c:(bar|bar3D|line|line3D|pie|pie3D|doughnut|area|area3D|radar|ofPie)Chart>/.exec(xml || "");
    if (!tm) return null;
    const ser = [];
    let cats = [];
    for (const m of xml.matchAll(/<c:ser>([\s\S]*?)<\/c:ser>/g)) {
      const b = m[1];
      const tx = /<c:tx>[\s\S]*?<\/c:tx>/.exec(b);
      const name = tx ? (ptVals(tx[0])[0] || dec((/<c:v>([^<]*)<\/c:v>/.exec(tx[0]) || [])[1] || "")) : "";
      const c = ptVals((/<c:cat>[\s\S]*?<\/c:cat>/.exec(b) || [])[0]);
      const v = ptVals((/<c:val>[\s\S]*?<\/c:val>/.exec(b) || [])[0]).map(Number);
      if (!v.length) continue;
      if (c.length > cats.length) cats = c;
      ser.push({ n: cut(name, 60), v: v.slice(0, 24).map((x) => (Number.isFinite(x) ? x : 0)) });
      if (ser.length >= 6) break;
    }
    if (!ser.length) return null;
    const title = (/<c:title>([\s\S]*?)<\/c:title>/.exec(xml) || [])[1];
    return { t: tm[1].replace(/3D$/, ""), cats: cats.slice(0, 24).map((x) => cut(x, 40)), ser, title: title ? cut(bodyParas(title).join(" "), 80) : "" };
  }
  function setPts(blk, values) {
    let i = 0;
    return blk.replace(/(<c:pt idx="\d+"[^>]*>\s*<c:v>)([^<]*)(<\/c:v>)/g, (m, a, v, b) => { const nv = values[i++]; return nv == null || nv === "" ? m : a + enc(String(nv)) + b; });
  }
  function fillChartXml(xml, ch) {
    let si = 0;
    xml = xml.replace(/<c:ser>([\s\S]*?)<\/c:ser>/g, (m, b) => {
      const sr = ch.ser[si++];
      if (!sr) return m;
      b = b.replace(/<c:tx>[\s\S]*?<\/c:tx>/, (tx) => (/<c:pt /.test(tx) ? setPts(tx, [sr.n]) : tx.replace(/<c:v>[^<]*<\/c:v>/, `<c:v>${enc(sr.n)}</c:v>`)));
      b = b.replace(/<c:cat>[\s\S]*?<\/c:cat>/, (blk) => setPts(blk, ch.cats));
      b = b.replace(/<c:val>[\s\S]*?<\/c:val>/, (blk) => setPts(blk, sr.v.map((x) => String(x))));
      return "<c:ser>" + b + "</c:ser>";
    });
    if (ch.title) xml = xml.replace(/<c:title>[\s\S]*?<\/c:title>/, (tt) => { let first = true; return tt.replace(/<a:t>[^<]*<\/a:t>/g, () => { if (first) { first = false; return `<a:t>${enc(ch.title)}</a:t>`; } return "<a:t></a:t>"; }); });
    // ichki Excel jadvali eski raqamlarda qoladi — ko'rinadigan raqamlar bilan chalkashmasligi uchun bog'lanish olib tashlanadi
    return xml.replace(/<c:externalData\b[^>]*\/>|<c:externalData\b[^>]*>[\s\S]*?<\/c:externalData>/g, "");
  }
  const slotOf = (el) => (el.startsWith("<p:sp") ? spSlot(el) : gfSlot(el));

  function smartNodes(data) {
    const out = [];
    for (const m of String(data || "").matchAll(re(RE_PT))) {
      const a = attrs(m[1]);
      if ((a.type || "node") !== "node") continue;
      const t = /<dgm:t>([\s\S]*?)<\/dgm:t>/.exec(m[2] || "");
      const txt = t ? bodyParas(t[1]).join(" ") : "";
      if (txt) out.push({ id: a.modelId, t: txt });
    }
    return out;
  }

  // rasm: <p:pic> yoki rasm bilan to'ldirilgan shakl
  function picInfo(el) {
    let fill;
    if (el.startsWith("<p:pic")) fill = /<p:blipFill\b[^>]*>[\s\S]*?<\/p:blipFill>/.exec(el);
    else {
      const spPr = /<p:spPr\b[^>]*>[\s\S]*?<\/p:spPr>/.exec(el);
      fill = spPr && /<a:blipFill\b[^>]*>[\s\S]*?<\/a:blipFill>/.exec(spPr[0]);
    }
    if (!fill) return null;
    const blip = /<a:blip\b([^>]*?)(?:\/>|>)/.exec(fill[0]);
    const embed = blip && attrs(blip[1])["r:embed"];
    if (!embed) return null;
    const spPr = /<p:spPr\b[^>]*>[\s\S]*?<\/p:spPr>/.exec(el);
    const ext = spPr && /<a:xfrm\b[^>]*>[\s\S]*?<a:ext\b([^>]*?)\/>/.exec(spPr[0]);
    const e = attrs(ext && ext[1]);
    return { embed, cx: Number(e.cx) || 0, cy: Number(e.cy) || 0 };
  }

  function roleOf(slots, i) {
    const first = ((slots[0] && slots[0].p && slots[0].p[0]) || "").toLowerCase();
    const all = slots.map((s) => (s.p || []).join(" ")).join(" ").toLowerCase();
    if (i === 0) return "title";
    if (/rahmat|e[’'`ʼ‘]?tibor|thank|спасибо|внимани|diqqat/.test(all) && all.length < 160) return "thanks";
    if (/^(reja|режа|план|plan|mundarija|содержание|contents?|agenda|outline)\b/.test(first)) return "plan";
    if (/^(xulosa|хулоса|вывод|заключение|conclusion|summary|итог)/.test(first)) return "conclusion";
    if (/(adabiyot|manba|литератур|источник|reference|bibliograph|sources)/.test(first)) return "references";
    return "content";
  }

  // ------------------------------------------------------------ 1) namunani o'qish
  async function parse(zip) {
    const ord = await slideOrder(zip);
    if (!ord.slides.length) throw new Error("no slides");
    const raw = [];
    const mediaUse = new Map();
    for (const p of ord.slides) {
      const xml = await zip.file(p).async("string");
      const relsXml = (await readText(zip, relsPath(p))) || EMPTY_RELS;
      const rels = parseRels(relsXml);
      const slots = [];
      for (const m of xml.matchAll(re(RE_ELEM))) {
        const sl = slotOf(m[0]);
        if (!sl) continue;
        if (sl.k === "smart") {
          const r = rels.get(sl.dm);
          const data = r ? await readText(zip, resolve(p, r.Target)) : null;
          slots.push({ k: "smart", p: smartNodes(data).map((n) => n.t) });
        } else if (sl.k === "chart") {
          const r = rels.get(sl.rid);
          const info = r ? chartInfo(await readText(zip, resolve(p, r.Target))) : null;
          slots.push(info ? Object.assign({ k: "chart" }, info) : { k: "chart", t: "", cats: [], ser: [] });
        } else slots.push(sl);
      }
      const pics = [];
      const seen = new Set();
      for (const m of xml.matchAll(re(RE_PICEL))) {
        const info = picInfo(m[0]);
        if (!info) continue;
        const r = rels.get(info.embed);
        const media = r && !/External/i.test(r.TargetMode || "") ? resolve(p, r.Target) : "";
        pics.push({ media, cx: info.cx, cy: info.cy });
        if (media && !seen.has(media)) { seen.add(media); mediaUse.set(media, (mediaUse.get(media) || 0) + 1); }
      }
      raw.push({ p, slots, pics });
    }
    const area = ord.w * ord.h;
    const slides = raw.map((s, i) => ({
      role: roleOf(s.slots, i),
      slots: s.slots.map((sl) => (sl.k === "table" ? { k: "table", cols: sl.cols, c: sl.c.map((x) => (x == null ? null : cut(x, 120))) }
        : sl.k === "chart" ? sl : { k: sl.k, p: sl.p.slice(0, 30).map((x) => cut(x, 220)) })),
      // mazmunli rasm: faqat shu slaydda ishlatilgan, o'rtacha kattalikdagi foto (fon, logo va ikonkalar emas)
      pics: s.pics.map((pc) => {
        const fr = pc.cx && pc.cy ? (pc.cx * pc.cy) / area : 0.25;
        const c = !!pc.media && IMG_EXT.test(pc.media) && (mediaUse.get(pc.media) || 0) <= 1 && fr >= 0.035 && fr <= 0.9;
        return { c: c ? 1 : 0, ar: pc.cx && pc.cy ? Math.round((pc.cx / pc.cy) * 100) / 100 : 1.33 };
      }),
    }));
    let outline = "";
    slides.forEach((s, i) => {
      const np = s.pics.filter((x) => x.c).length;
      outline += `--- Slide ${i + 1}${np ? ` [${np} picture]` : ""}${s.slots.some((x) => x.k === "table") ? " [table]" : ""}${s.slots.some((x) => x.k === "smart") ? " [diagram]" : ""}${s.slots.some((x) => x.k === "chart") ? " [chart]" : ""}\n`;
      s.slots.forEach((sl, k) => {
        const lines = sl.k === "table" ? [sl.c.filter((x) => x).join(" | ")] : sl.k === "chart" ? [`[chart: ${sl.cats.join(", ")}]`] : sl.p;
        lines.forEach((t, j) => { outline += (k === 0 && j === 0 ? "" : "• ") + t + "\n"; });
      });
    });
    return { w: ord.w, h: ord.h, slides, outline: outline.slice(0, 40000) };
  }

  // ------------------------------------------------------------ 2) qaysi slayd qaysi namuna dizaynida bo'ladi
  function plan(sk, n) {
    const S = sk.slides.length;
    const idx = (role) => sk.slides.findIndex((s, i) => i > 0 && s.role === role);
    const lastIdx = (role) => { for (let i = S - 1; i > 0; i--) if (sk.slides[i].role === role) return i; return -1; };
    const title = 0;
    const pl = idx("plan");
    const tail = [lastIdx("conclusion"), lastIdx("references"), lastIdx("thanks")].filter((i) => i > 0);
    let content = sk.slides.map((s, i) => i).filter((i) => i > 0 && i !== pl && !tail.includes(i));
    if (!content.length) content = sk.slides.map((s, i) => i).filter((i) => i > 0);
    if (!content.length) content = [0];
    if (!n || n === S) return sk.slides.map((s, i) => i + 1);   // namunadagidek
    const fixed = [title].concat(pl > 0 ? [pl] : []);
    let tl = tail.slice();
    // juda kam slayd so'ralsa: avval manbalar, keyin rahmat, keyin reja, keyin xulosa tashlanadi
    const drop = () => {
      for (const r of ["references", "thanks"]) { const k = tl.findIndex((i) => sk.slides[i].role === r); if (k >= 0) { tl.splice(k, 1); return true; } }
      if (fixed.length > 1) { fixed.pop(); return true; }
      if (tl.length) { tl.pop(); return true; }
      return false;
    };
    while (n - fixed.length - tl.length < 1 && drop()) { /* */ }
    const m = Math.max(1, n - fixed.length - tl.length);
    const k = content.length;
    const mid = [];
    if (m <= k) for (let j = 0; j < m; j++) mid.push(content[m === 1 ? 0 : Math.round((j * (k - 1)) / (m - 1))]);
    else for (let j = 0; j < m; j++) mid.push(content[j % k]);
    return fixed.concat(mid, tl).map((i) => i + 1);
  }

  // ------------------------------------------------------------ 3) AI javobini tekshirish
  function normalize(raw, sk, designs) {
    if (!raw || typeof raw !== "object") throw new Error("bad json");
    const arr = Array.isArray(raw.slides) ? raw.slides : [];
    if (!arr.length) throw new Error("no slides");
    const line = (t) => clean(typeof t === "string" || typeof t === "number" ? t : "", 400);
    const slides = designs.map((d, i) => {
      const r = arr[i] || {};
      const skS = sk.slides[d - 1];
      const rs = Array.isArray(r.slots) ? r.slots : [];
      const s = skS.slots.map((sl, j) => {
        const v = rs[j];
        if (sl.k === "chart") {
          if (!sl.ser.length || !v || typeof v !== "object" || Array.isArray(v)) return { ch: null };
          const num = (x) => { const n = typeof x === "number" ? x : Number(String(x).replace(/\s+/g, "").replace(",", ".")); return Number.isFinite(n) ? n : null; };
          const cats = sl.cats.map((o, i) => line((Array.isArray(v.categories) ? v.categories : [])[i]) || o);
          const ser = sl.ser.map((o, k) => {
            const sv = (Array.isArray(v.series) ? v.series : [])[k] || {};
            const vals = Array.isArray(sv.values) ? sv.values : [];
            return { n: line(sv.name) || o.n, v: o.v.map((ov, i) => { const nv = num(vals[i]); return nv == null ? ov : nv; }) };
          });
          return { ch: { title: line(v.title).slice(0, 80), cats, ser } };
        }
        if (sl.k === "table") {
          const rows = Array.isArray(v) ? v : [];
          const grid = rows.every(Array.isArray) ? rows : [];
          const flat = grid.length ? null : rows.map(line);
          const c = sl.c.map((orig, idx) => {
            if (orig == null) return null;
            const t = flat ? flat[idx] : (grid[Math.floor(idx / sl.cols)] || [])[idx % sl.cols];
            return t == null ? null : line(t);
          });
          return { c };
        }
        const list = Array.isArray(v) ? v : typeof v === "string" ? v.split(/\n+/) : [];
        const lines = list.map(line).filter(Boolean).slice(0, Math.max(sl.p.length + 3, 4));
        return { p: lines.length ? lines : null };
      });
      const im = (Array.isArray(r.images) ? r.images : []).map((q) => clean(q, 90)).filter(Boolean).slice(0, 12);
      return { d, s, im };
    });
    const got = Math.min(arr.length, designs.length);
    if (got < Math.ceil(designs.length * 0.6)) throw new Error("too few slides");
    let out = slides.slice(0, got);
    // AI kamroq slayd qaytarsa: oxirgi "rahmat" slaydi (umumiy matnli) baribir qoladi
    const lastD = designs[designs.length - 1];
    if (got < designs.length && sk.slides[lastD - 1].role === "thanks" && out[out.length - 1].d !== lastD) out = out.concat([slides[slides.length - 1]]);
    return { title: clean(raw.title, 120), imgTopic: clean(raw.image_topic, 90), slides: out };
  }

  // ------------------------------------------------------------ 4) matnni almashtirish (formatlash saqlanadi)
  function makePara(p, text) {
    const pPr = (/<a:pPr\b[^>]*\/>|<a:pPr\b[^>]*>[\s\S]*?<\/a:pPr>/.exec(p) || [""])[0];
    const r = /<a:r>([\s\S]*?)<\/a:r>/.exec(p);
    let rPr = r ? (/<a:rPr\b[^>]*\/>|<a:rPr\b[^>]*>[\s\S]*?<\/a:rPr>/.exec(r[1]) || [""])[0] : "";
    const end = (/<a:endParaRPr\b[^>]*\/>|<a:endParaRPr\b[^>]*>[\s\S]*?<\/a:endParaRPr>/.exec(p) || [""])[0];
    if (!rPr && end) rPr = end.replace(/^<a:endParaRPr/, "<a:rPr").replace(/<\/a:endParaRPr>$/, "</a:rPr>");
    rPr = rPr.replace(/<a:hlinkClick\b[^>]*\/>|<a:hlinkClick\b[^>]*>[\s\S]*?<\/a:hlinkClick>/g, "");
    return `<a:p>${pPr}<a:r>${rPr}<a:t>${enc(text)}</a:t></a:r>${end}</a:p>`;
  }
  function fillBody(tb, lines, allowEmpty) {
    lines = (lines || []).map((t) => clean(t, 400)).filter(Boolean);
    if (!lines.length) return tb;
    const items = [];
    let last = 0;
    tb.replace(re(RE_PARA), (p, off) => { items.push({ s: tb.slice(last, off), p }); last = off + p.length; return p; });
    if (!items.length) return tb;
    const tail = tb.slice(last);
    let nonEmpty = items.map((it, i) => (paraText(it.p) ? i : -1)).filter((i) => i >= 0);
    if (!nonEmpty.length) { if (!allowEmpty) return tb; nonEmpty = [0]; }
    const out = items.map((it) => ({ s: it.s, p: it.p }));
    nonEmpty.forEach((idx, j) => { out[idx].p = j < lines.length ? makePara(items[idx].p, lines[j]) : ""; });
    if (lines.length > nonEmpty.length) {
      const li = nonEmpty[nonEmpty.length - 1];
      out[li].p += lines.slice(nonEmpty.length).map((t) => makePara(items[li].p, t)).join("");
    }
    return out.map((it) => it.s + it.p).join("") + tail;
  }
  function fillTable(el, c) {
    let i = 0;
    return el.replace(re(RE_TC), (m, a1, body, a3) => {
      const idx = i++;
      if (a3 !== undefined) return m;
      const a = attrs(a1);
      if (a.hMerge === "1" || a.vMerge === "1") return m;
      const t = c[idx];
      if (t == null || t === "") return m;
      return m.replace(/<a:txBody>[\s\S]*?<\/a:txBody>/, (tb) => fillBody(tb, [t], true));
    });
  }
  function fillSlide(xml, ns) {
    let k = 0;
    const smart = [], charts = [];
    xml = xml.replace(re(RE_ELEM), (el) => {
      const sl = slotOf(el);
      if (!sl) return el;
      const v = ns[k++];
      if (!v) return el;
      if (sl.k === "smart") { if (v.p) smart.push({ dm: sl.dm, p: v.p }); return el; }
      if (sl.k === "chart") { if (v.ch) charts.push({ rid: sl.rid, ch: v.ch }); return el; }
      if (sl.k === "table") return v.c ? fillTable(el, v.c) : el;
      return v.p ? el.replace(/<p:txBody>[\s\S]*?<\/p:txBody>/, (tb) => fillBody(tb, v.p)) : el;
    });
    return { xml, smart, charts };
  }
  async function fillSmart(zip, slidePath, rels, sm) {
    const r = rels.get(sm.dm);
    if (!r) return;
    const dataPath = resolve(slidePath, r.Target);
    let data = await readText(zip, dataPath);
    if (!data) return;
    const map = new Map();
    smartNodes(data).forEach((nd, j) => { if (sm.p[j]) map.set(nd.id, sm.p[j]); });
    const pres = new Map();
    for (const m of data.matchAll(re(RE_PT))) {
      const a = attrs(m[1]);
      if (a.type !== "pres") continue;
      const ps = /<dgm:prSet\b([^>]*?)\/?>/.exec(m[2] || "");
      if (ps && attrs(ps[1]).presAssocID) pres.set(a.modelId, attrs(ps[1]).presAssocID);
    }
    data = data.replace(re(RE_PT), (m, a1, body) => {
      const a = attrs(a1);
      if ((a.type || "node") !== "node" || !body || !map.has(a.modelId)) return m;
      return m.replace(/<dgm:t>[\s\S]*?<\/dgm:t>/, (t) => fillBody(t, [map.get(a.modelId)]));
    });
    zip.file(dataPath, data);
    const ext = /<dsp:dataModelExt\b([^>]*?)\/?>/.exec(data);
    const dr = ext && rels.get(attrs(ext[1]).relId);
    if (!dr) return;
    const drawPath = resolve(slidePath, dr.Target);
    let dx = await readText(zip, drawPath);
    if (!dx) return;
    dx = dx.replace(/<dsp:sp\b([^>]*)>([\s\S]*?)<\/dsp:sp>/g, (m, a1) => {
      const id = attrs(a1).modelId;
      const t = map.get(pres.get(id) || id);
      return t ? m.replace(/<dsp:txBody>[\s\S]*?<\/dsp:txBody>/, (tb) => fillBody(tb, [t])) : m;
    });
    zip.file(drawPath, dx);
  }

  // rasmni almashtirish: ramka o'lchami saqlanadi, rasm "cover" usulida qirqiladi
  function setPicture(el, rid, img) {
    const isPic = el.startsWith("<p:pic");
    const info = picInfo(el);
    let crop = "";
    if (info && info.cx && info.cy && img.w && img.h) {
      const A = info.cx / info.cy, B = img.w / img.h;
      if (B > A * 1.01) { const c = Math.round(((1 - A / B) / 2) * 100000); crop = `<a:srcRect l="${c}" r="${c}"/>`; }
      else if (A > B * 1.01) { const c = Math.round(((1 - B / A) / 2) * 100000); crop = `<a:srcRect t="${c}" b="${c}"/>`; }
    }
    const fixFill = (fill) => fill.replace(/^(<(p|a):blipFill\b[^>]*>)([\s\S]*?)(<\/\2:blipFill>)$/, (m, open, ns, inner, close) => {
      const b = /<a:blip\b([^>]*?)(?:\/>|>([\s\S]*?)<\/a:blip>)/.exec(inner);
      if (!b) return m;
      const at = b[1].replace(/\s*r:embed="[^"]*"/, "").replace(/\s*r:link="[^"]*"/, "");
      const keep = (b[2] || "").replace(/<a:extLst>[\s\S]*?<\/a:extLst>/g, "");
      const blip = `<a:blip r:embed="${rid}"${at}${keep ? ">" + keep + "</a:blip>" : "/>"}`;
      return `${open}${blip}${crop}<a:stretch><a:fillRect/></a:stretch>${close}`;
    });
    if (isPic) return el.replace(/<p:blipFill\b[^>]*>[\s\S]*?<\/p:blipFill>/, fixFill);
    return el.replace(/<p:spPr\b[^>]*>[\s\S]*?<\/p:spPr>/, (spPr) => spPr.replace(/<a:blipFill\b[^>]*>[\s\S]*?<\/a:blipFill>/, fixFill));
  }

  // qaysi rasmlar uchun qidiruv kerak: [{slide, pic, query, ar}]
  function imageJobs(sk, deck, topic) {
    const jobs = [];
    deck.slides.forEach((ds, i) => {
      const pics = sk.slides[ds.d - 1].pics;
      let q = 0;
      pics.forEach((pc, j) => {
        if (!pc.c) return;
        const title = ((ds.s.find((x) => x && x.p) || {}).p || [])[0] || "";
        jobs.push({ slide: i, pic: j, ar: pc.ar, query: ds.im[q] || ds.im[0] || deck.imgTopic || topic, fallback: deck.imgTopic || topic, fallback2: topic, title });
        q++;
      });
    });
    return jobs;
  }

  // ------------------------------------------------------------ 5) yangi .pptx yasash
  async function build(JSZip, buf, sk, deck, images) {
    const zip = await JSZip.loadAsync(buf);
    const ord = await slideOrder(zip);
    if (ord.slides.length !== sk.slides.length) throw new Error("sample mismatch");
    const src = [];
    for (const p of ord.slides) src.push({ p, xml: await zip.file(p).async("string"), rels: (await readText(zip, relsPath(p))) || EMPTY_RELS });
    let ct = await readText(zip, "[Content_Types].xml");
    let pres = ord.pres;
    let presRels = await readText(zip, "ppt/_rels/presentation.xml.rels");
    const ctOf = (part) => { const m = new RegExp('<Override\\b[^>]*PartName="/' + escRe(part) + '"[^>]*ContentType="([^"]*)"').exec(ct) || new RegExp('<Override\\b[^>]*ContentType="([^"]*)"[^>]*PartName="/' + escRe(part) + '"').exec(ct); return m && m[1]; };
    const dropOverride = (part) => { ct = ct.replace(new RegExp('<Override\\b[^>]*PartName="/' + escRe(part) + '"[^>]*/>', "g"), ""); };
    const addOverride = (part, type) => { if (type) ct = ct.replace("</Types>", `<Override PartName="/${part}" ContentType="${type}"/></Types>`); };

    // eski slaydlar va izohlar (notes) olib tashlanadi
    presRels = presRels.replace(/<Relationship\b[^>]*\bType="[^"]*\/slide"[^>]*\/>/g, "");
    for (const s of src) { zip.remove(s.p); zip.remove(relsPath(s.p)); dropOverride(s.p); }
    const notes = [];
    zip.forEach((path) => { if (/^ppt\/notesSlides\//.test(path)) notes.push(path); });
    notes.forEach((p) => zip.remove(p));
    ct = ct.replace(/<Override\b[^>]*PartName="\/ppt\/notesSlides\/[^"]*"[^>]*\/>/g, "");

    const used = new Map();
    const ids = [];
    let mediaN = 0;
    for (let i = 0; i < deck.slides.length; i++) {
      const ds = deck.slides[i];
      const s = src[ds.d - 1];
      const skS = sk.slides[ds.d - 1];
      const path = `ppt/slides/slide${i + 1}.xml`;
      let rels = s.rels
        .replace(/<Relationship\b[^>]*\bType="[^"]*\/(notesSlide|comments|slide|tags)"[^>]*\/>/g, "");
      let xml = s.xml
        .replace(/<a:hlinkClick\b[^>]*action="ppaction:\/\/hlinksldjump"[^>]*\/>/g, "")
        .replace(/<a:hlinkClick\b[^>]*action="ppaction:\/\/hlinksldjump"[^>]*>[\s\S]*?<\/a:hlinkClick>/g, "")
        .replace(/<p:custDataLst>[\s\S]*?<\/p:custDataLst>/g, "");
      // bir dizayn ikkinchi marta ishlatilsa — diagramma va grafiklar nusxalanadi (har slaydniki o'zida)
      const cnt = (used.get(ds.d) || 0) + 1;
      used.set(ds.d, cnt);
      if (cnt > 1) {
        const relMap = parseRels(rels);
        for (const [id, r] of relMap) {
          if (/External/i.test(r.TargetMode || "")) continue;
          const target = resolve(s.p, r.Target);
          if (!/^ppt\/(charts|diagrams)\//.test(target) || !zip.file(target)) continue;
          const copy = target.replace(/(\.[\w]+)$/, `_q${i + 1}$1`);
          zip.file(copy, await zip.file(target).async("uint8array"));
          const tr = await readText(zip, relsPath(target));
          if (tr) zip.file(relsPath(copy), tr);
          addOverride(copy, ctOf(target));
          rels = rels.replace(new RegExp(`(<Relationship\\b[^>]*\\bId="${escRe(id)}"[^>]*\\bTarget=")[^"]*(")`), `$1${relative(path, copy)}$2`)
            .replace(new RegExp(`(<Relationship\\b[^>]*\\bTarget=")[^"]*("[^>]*\\bId="${escRe(id)}")`), `$1${relative(path, copy)}$2`);
        }
      }
      const filled = fillSlide(xml, ds.s);
      xml = filled.xml;
      const relMap = parseRels(rels);
      for (const sm of filled.smart) { try { await fillSmart(zip, path, relMap, sm); } catch (e) { /* diagramma o'zgarmay qoladi */ } }
      for (const c of filled.charts) {
        const r = relMap.get(c.rid);
        if (!r) continue;
        const cp = resolve(path, r.Target);
        const cx = await readText(zip, cp);
        if (cx) zip.file(cp, fillChartXml(cx, c.ch));
      }
      // rasmlar
      let pi = 0;
      const adds = [];
      xml = xml.replace(re(RE_PICEL), (el) => {
        if (!picInfo(el)) return el;
        const j = pi++;
        const pc = skS.pics[j];
        const img = pc && pc.c && images && images.get(i + ":" + j);
        if (!img) return el;
        const rid = `rIdQ${i + 1}x${j}`;
        const name = `ppt/media/tq${i + 1}_${j}_${++mediaN}.${img.ext}`;
        zip.file(name, img.data);
        adds.push(`<Relationship Id="${rid}" Type="${REL_IMAGE}" Target="../media/${name.split("/").pop()}"/>`);
        return setPicture(el, rid, img);
      });
      if (adds.length) {
        rels = rels.replace("</Relationships>", adds.join("") + "</Relationships>");
        // almashtirilgan eski rasmlarning havolasi olib tashlanadi — eski rasm fayldan o'chadi
        for (const [id, r] of parseRels(rels)) {
          if (r.Type === REL_IMAGE && !xml.includes(`"${id}"`))
            rels = rels.replace(new RegExp(`<Relationship\\b[^>]*\\bId="${escRe(id)}"[^>]*/>`), "");
        }
      }
      zip.file(path, xml);
      zip.file(relsPath(path), rels);
      addOverride(path, CT_SLIDE);
      presRels = presRels.replace("</Relationships>",
        `<Relationship Id="rIdQs${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide${i + 1}.xml"/></Relationships>`);
      ids.push(`<p:sldId id="${256 + i}" r:id="rIdQs${i + 1}"/>`);
    }
    pres = pres.replace(/<p:sldIdLst>[\s\S]*?<\/p:sldIdLst>|<p:sldIdLst\/>/, `<p:sldIdLst>${ids.join("")}</p:sldIdLst>`)
      .replace(/<p:custShowLst>[\s\S]*?<\/p:custShowLst>/g, "")
      .replace(/<p:ext uri="\{521415D9-36F7-43E2-AB2F-B90AF26B5E84\}">[\s\S]*?<\/p:ext>/g, "")
      .replace(/<p:extLst>\s*<\/p:extLst>/g, "");
    for (const [ext, type] of [["jpg", "image/jpeg"], ["jpeg", "image/jpeg"], ["png", "image/png"]]) {
      if (!new RegExp(`<Default\\b[^>]*Extension="${ext}"`, "i").test(ct)) ct = ct.replace("</Types>", `<Default Extension="${ext}" ContentType="${type}"/></Types>`);
    }
    zip.file("ppt/presentation.xml", pres);
    zip.file("ppt/_rels/presentation.xml.rels", presRels);
    // hech qayerda ishlatilmay qolgan eski rasmlar o'chiriladi (fayl hajmi kichrayadi)
    const refd = new Set();
    const relFiles = [];
    zip.forEach((p) => { if (p.endsWith(".rels")) relFiles.push(p); });
    for (const rp of relFiles) {
      const owner = rp.replace(/_rels\/([^/]+)\.rels$/, "$1");
      for (const [, r] of parseRels(await readText(zip, rp))) if (!/External/i.test(r.TargetMode || "")) refd.add(resolve(owner, r.Target));
    }
    const media = [];
    zip.forEach((p, f) => { if (!f.dir && /^ppt\/media\//.test(p) && !refd.has(p)) media.push(p); });
    media.forEach((p) => zip.remove(p));
    zip.file("[Content_Types].xml", ct);
    const app = await readText(zip, "docProps/app.xml");
    if (app) zip.file("docProps/app.xml", app.replace(/<Slides>\d+<\/Slides>/, `<Slides>${deck.slides.length}</Slides>`).replace(/<Notes>\d+<\/Notes>/, "<Notes>0</Notes>"));
    return zip.generateAsync({ type: typeof Buffer !== "undefined" && typeof window === "undefined" ? "nodebuffer" : "blob",
      compression: "DEFLATE", compressionOptions: { level: 6 },
      mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation" });
  }

  // ilovadagi ko'rinish uchun: har slaydning sarlavhasi va qatorlari
  function outline(deck) {
    return deck.slides.map((ds) => {
      const lines = [];
      ds.s.forEach((x) => {
        if (!x) return;
        if (x.p) lines.push(...x.p);
        if (x.c) { const t = x.c.filter(Boolean).slice(0, 8).join(" · "); if (t) lines.push(t); }
        if (x.ch) lines.push("📊 " + x.ch.cats.filter(Boolean).slice(0, 6).join(", "));
      });
      return { title: lines[0] || "", lines: lines.slice(1, 9) };
    });
  }

  const api = { parse, plan, normalize, build, imageJobs, outline, fillBody, makePara };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Exact = api;
})(typeof window !== "undefined" ? window : globalThis);
