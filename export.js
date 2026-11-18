/*
 * export.js — taqdimotni boshqa formatlarga aylantirish (hammasi brauzerning o'zida):
 *   PDF  — har bir slayd rasm sifatida, 13.33×7.5 dyuym sahifa
 *   JPG  — slaydlar rasmlari bitta ZIP faylda
 *   DOCX — Word hujjat: reja, har bir slayd matni, xulosa, adabiyotlar va krossvord
 * Qo'shimcha kutubxona kerak emas.
 */
(function (root) {
  "use strict";
  const W = 13.333, H = 7.5;
  const HEAD = "Georgia";
  const ASC = 0.905, DESC = 0.212;   // Arial/Georgia shrift o'lchamlari (em ulushida)
  const fontStack = (f) => (f === HEAD ? "Georgia, 'Times New Roman', serif" : "Arial, Helvetica, sans-serif");
  const rgba = (c, a) => {
    const n = parseInt(c, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a == null ? 1 : a / 100})`;
  };

  // ------------------------------------------------------------ slaydni canvas'ga chizish
  function shapePath(ctx, it, S) {
    const x = it.x * S, y = it.y * S, w = it.w * S, h = it.h * S;
    ctx.beginPath();
    if (it.shape === "oval") ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
    else if (it.shape === "round") {
      const r = Math.min((it.radius || 0.14) * S, w / 2, h / 2);
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    } else ctx.rect(x, y, w, h);
  }

  function drawRect(ctx, it, S) {
    shapePath(ctx, it, S);
    if (it.fill) { ctx.fillStyle = rgba(it.fill, it.alpha); ctx.fill(); }
    if (it.line) { ctx.strokeStyle = rgba(it.line); ctx.lineWidth = ((it.lineW || 0.75) * S) / 72; ctx.stroke(); }
  }

  const fontOf = (st, P) => `${st.italic ? "italic " : ""}${st.bold ? "700" : "400"} ${st.size * P}px ${fontStack(st.font)}`;

  function measure(ctx, text, st, P, spcPx) {
    ctx.font = fontOf(st, P);
    return ctx.measureText(text).width + (spcPx ? spcPx * Array.from(text).length : 0);
  }

  // Paragrafni so'zlarga ajratamiz; bitta so'z bir nechta "run"dan iborat bo'lishi mumkin
  function tokenize(p, base) {
    const out = [];
    let cur = null;
    for (const r of p) {
      const st = {
        font: r.font || base.font, size: r.size || base.size, color: r.color || base.color,
        bold: r.bold != null ? r.bold : base.bold, italic: base.italic,
      };
      for (const part of String(r.t == null ? "" : r.t).split(/(\s+)/)) {
        if (!part) continue;
        if (/^\s+$/.test(part)) { out.push({ space: true, frags: [{ t: " ", st }] }); cur = null; }
        else {
          if (!cur) { cur = { space: false, frags: [] }; out.push(cur); }
          cur.frags.push({ t: part, st });
        }
      }
    }
    return out;
  }

  function layoutPara(ctx, p, base, maxW, P, spcPx, wrap) {
    const toks = tokenize(p, base);
    const lines = [];
    let line = { frags: [], w: 0 };
    const width = (tok) => tok.frags.reduce((a, f) => a + measure(ctx, f.t, f.st, P, spcPx), 0);
    const pushLine = () => {
      while (line.frags.length && line.frags[line.frags.length - 1].space) line.w -= line.frags.pop().w;
      lines.push(line);
      line = { frags: [], w: 0 };
    };
    for (const tok of toks) {
      const w = width(tok);
      if (tok.space) {
        if (!line.frags.length) continue;
        line.frags.push({ t: " ", st: tok.frags[0].st, w, space: true });
        line.w += w;
        continue;
      }
      if (wrap && line.frags.length && line.w + w > maxW) pushLine();
      if (wrap && w > maxW) {
        // juda uzun so'z — harflab bo'lamiz
        for (const f of tok.frags) {
          for (const ch of Array.from(f.t)) {
            const cw = measure(ctx, ch, f.st, P, spcPx);
            if (line.frags.length && line.w + cw > maxW) pushLine();
            line.frags.push({ t: ch, st: f.st, w: cw });
            line.w += cw;
          }
        }
        continue;
      }
      for (const f of tok.frags) {
        const fw = measure(ctx, f.t, f.st, P, spcPx);
        line.frags.push({ t: f.t, st: f.st, w: fw });
        line.w += fw;
      }
    }
    if (line.frags.length || !lines.length) pushLine();
    for (const ln of lines) ln.size = ln.frags.reduce((m, f) => Math.max(m, f.st.size), base.size);
    return lines;
  }

  function drawText(ctx, it, S) {
    const P = S / 72;
    const padX = 3 * P, padY = 1.5 * P;
    const x0 = it.x * S + padX, y0 = it.y * S + padY;
    const w = it.w * S - 2 * padX, h = it.h * S - 2 * padY;
    const spcPx = it.spc ? (it.spc / 100) * P : 0;
    const base = { font: it.font, size: it.size, color: it.color, bold: it.bold, italic: it.italic };
    const k = (it.spacing || 1.15) * 1.15;
    const paras = it.paras.map((p) => layoutPara(ctx, p, base, w, P, spcPx, it.wrap !== false));
    let total = 0;
    paras.forEach((lines, i) => {
      if (i > 0 && it.gap) total += it.gap * P;
      for (const ln of lines) total += ln.size * P * k;
    });
    let y = y0;
    if (it.valign === "middle") y = y0 + (h - total) / 2;
    else if (it.valign === "bottom") y = y0 + h - total;
    ctx.textBaseline = "alphabetic";
    paras.forEach((lines, i) => {
      if (i > 0 && it.gap) y += it.gap * P;
      for (const ln of lines) {
        const lh = ln.size * P * k;
        const base = y + (lh - (ASC + DESC) * ln.size * P) / 2 + ASC * ln.size * P;
        let x = x0;
        if (it.align === "center") x = x0 + (w - ln.w) / 2;
        else if (it.align === "right") x = x0 + w - ln.w;
        for (const f of ln.frags) {
          if (!f.space) {
            ctx.font = fontOf(f.st, P);
            ctx.fillStyle = rgba(f.st.color);
            if (spcPx) {
              let cx = x;
              for (const ch of Array.from(f.t)) { ctx.fillText(ch, cx, base); cx += ctx.measureText(ch).width + spcPx; }
            } else ctx.fillText(f.t, x, base);
          }
          x += f.w;
        }
        y += lh;
      }
    });
  }

  function drawTable(ctx, it, S) {
    const P = S / 72;
    const padX = 10 * P, padY = 4.3 * P;
    const k = 1.15;
    let y = it.y * S;
    it.cells.forEach((row, ri) => {
      // qator balandligi: berilgan yoki matn sig'ishi uchun kerakli
      const laid = row.map((c, ci) => {
        const st = { font: it.font, size: it.size, color: c.color, bold: !!c.bold };
        const cw = it.colW[ci] * S - 2 * padX;
        return layoutPara(ctx, [{ t: c.t }], st, cw, P, 0, true);
      });
      const need = Math.max(...laid.map((ls) => ls.length)) * it.size * P * k + 2 * padY;
      const rh = Math.max(it.rowH[ri] * S, need);
      let x = it.x * S;
      row.forEach((c, ci) => {
        const cw = it.colW[ci] * S;
        ctx.fillStyle = rgba(c.fill);
        ctx.fillRect(x, y, cw, rh);
        const lines = laid[ci];
        const th = lines.length * it.size * P * k;
        let ty = y + (rh - th) / 2;
        ctx.font = fontOf({ font: it.font, size: it.size, bold: !!c.bold }, P);
        ctx.fillStyle = rgba(c.color);
        for (const ln of lines) {
          const lh = it.size * P * k;
          const base = ty + (lh - (ASC + DESC) * it.size * P) / 2 + ASC * it.size * P;
          ctx.fillText(ln.frags.map((f) => f.t).join(""), x + padX, base);
          ty += lh;
        }
        x += cw;
      });
      // oq chegaralar
      ctx.strokeStyle = "#FFFFFF";
      ctx.lineWidth = P;
      let bx = it.x * S;
      for (const cw of it.colW) { ctx.strokeRect(bx, y, cw * S, rh); bx += cw * S; }
      y += rh;
    });
  }

  // rasm: ramkaga "cover" usulida (qirqib) chiziladi
  function drawImg(ctx, it, S) {
    const img = it._img;
    if (!img) return;
    const iw = img.naturalWidth, ih = img.naturalHeight, A = it.w / it.h, B = iw / ih;
    let sx = 0, sy = 0, sw = iw, sh = ih;
    if (B > A) { sw = ih * A; sx = (iw - sw) / 2; } else { sh = iw / A; sy = (ih - sh) / 2; }
    ctx.drawImage(img, sx, sy, sw, sh, it.x * S, it.y * S, it.w * S, it.h * S);
  }
  async function preload(model) {
    const cache = new Map();
    for (const sl of model.slides) for (const it of sl.items) {
      if (it.k !== "img" || it._img) continue;
      if (!cache.has(it.src)) {
        const im = new Image();
        im.src = it.src;
        cache.set(it.src, im.decode().then(() => im).catch(() => null));
      }
      it._img = await cache.get(it.src);
    }
  }

  function renderSlide(slide, canvas, S) {
    canvas.width = Math.round(W * S);
    canvas.height = Math.round(H * S);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = rgba(slide.bg);
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (const it of slide.items) {
      if (it.k === "rect") drawRect(ctx, it, S);
      else if (it.k === "text") drawText(ctx, it, S);
      else if (it.k === "table") drawTable(ctx, it, S);
      else if (it.k === "img") drawImg(ctx, it, S);
      else if (it.k === "chart" && root.Deck) root.Deck.drawChart(ctx, it, S);
    }
    return canvas;
  }

  const canvasBlob = (canvas, type, q) => new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("canvas"))), type, q));
  const pause = () => new Promise((r) => setTimeout(r, 0));

  // ------------------------------------------------------------ PDF
  function utf16hex(s) {
    let h = "FEFF";
    for (let i = 0; i < s.length; i++) h += s.charCodeAt(i).toString(16).padStart(4, "0").toUpperCase();
    return `<${h}>`;
  }

  async function toPdf(model, onProgress) {
    await preload(model);
    const S = 144;   // 1920×1080 piksel
    const canvas = document.createElement("canvas");
    const enc = new TextEncoder();
    const parts = [];
    const offsets = [];
    let offset = 0;
    const push = (x) => { const b = typeof x === "string" ? enc.encode(x) : x; parts.push(b); offset += b.length; };
    const obj = (num, chunks) => {
      offsets[num] = offset;
      push(`${num} 0 obj\n`);
      for (const c of chunks) push(c);
      push("\nendobj\n");
    };
    const n = model.slides.length;
    const total = 4 + 3 * n;   // 0, katalog, sahifalar, info + har slaydga 3 ta obyekt
    push("%PDF-1.4\n%âãÏÓ\n");
    obj(1, ["<< /Type /Catalog /Pages 2 0 R >>"]);
    obj(2, [`<< /Type /Pages /Kids [${model.slides.map((_, i) => `${4 + 3 * i} 0 R`).join(" ")}] /Count ${n} >>`]);
    obj(3, [`<< /Title ${utf16hex(model.title || "Taqdimot")} /Author ${utf16hex(model.author || "")} /Producer (Taqdimot) >>`]);
    for (let i = 0; i < n; i++) {
      renderSlide(model.slides[i], canvas, S);
      const jpg = new Uint8Array(await (await canvasBlob(canvas, "image/jpeg", 0.88)).arrayBuffer());
      const pg = 4 + 3 * i, cs = pg + 1, im = pg + 2;
      obj(pg, [`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Resources << /XObject << /Im${i} ${im} 0 R >> >> /Contents ${cs} 0 R >>`]);
      const draw = `q 960 0 0 540 0 0 cm /Im${i} Do Q`;
      obj(cs, [`<< /Length ${draw.length} >>\nstream\n`, draw, "\nendstream"]);
      obj(im, [`<< /Type /XObject /Subtype /Image /Width ${canvas.width} /Height ${canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpg.length} >>\nstream\n`, jpg, "\nendstream"]);
      if (onProgress) onProgress(i + 1, n);
      await pause();
    }
    const xref = offset;
    let x = `xref\n0 ${total}\n0000000000 65535 f \n`;
    for (let k = 1; k < total; k++) x += String(offsets[k]).padStart(10, "0") + " 00000 n \n";
    push(`${x}trailer\n<< /Size ${total} /Root 1 0 R /Info 3 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    canvas.width = canvas.height = 1;
    return new Blob(parts, { type: "application/pdf" });
  }

  // ------------------------------------------------------------ ZIP (siqmasdan saqlash)
  const CRC = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(u8) {
    let c = 0xffffffff;
    for (let i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 255] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  function zip(files, mime) {
    const enc = new TextEncoder();
    const d = new Date();
    const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
    const date = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
    const parts = [], central = [];
    let offset = 0, cdSize = 0;
    for (const f of files) {
      const name = enc.encode(f.name);
      const data = typeof f.data === "string" ? enc.encode(f.data) : f.data;
      const crc = crc32(data);
      const h = new DataView(new ArrayBuffer(30));
      h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint16(8, 0, true);
      h.setUint16(10, time, true); h.setUint16(12, date, true); h.setUint32(14, crc, true);
      h.setUint32(18, data.length, true); h.setUint32(22, data.length, true); h.setUint16(26, name.length, true); h.setUint16(28, 0, true);
      parts.push(new Uint8Array(h.buffer), name, data);
      const c = new DataView(new ArrayBuffer(46));
      c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
      c.setUint16(10, 0, true); c.setUint16(12, time, true); c.setUint16(14, date, true); c.setUint32(16, crc, true);
      c.setUint32(20, data.length, true); c.setUint32(24, data.length, true); c.setUint16(28, name.length, true);
      c.setUint16(30, 0, true); c.setUint16(32, 0, true); c.setUint16(34, 0, true); c.setUint16(36, 0, true);
      c.setUint32(38, 0, true); c.setUint32(42, offset, true);
      central.push(new Uint8Array(c.buffer), name);
      cdSize += 46 + name.length;
      offset += 30 + name.length + data.length;
    }
    const e = new DataView(new ArrayBuffer(22));
    e.setUint32(0, 0x06054b50, true); e.setUint16(4, 0, true); e.setUint16(6, 0, true);
    e.setUint16(8, files.length, true); e.setUint16(10, files.length, true);
    e.setUint32(12, cdSize, true); e.setUint32(16, offset, true); e.setUint16(20, 0, true);
    return new Blob([...parts, ...central, new Uint8Array(e.buffer)], { type: mime || "application/zip" });
  }

  async function toImagesZip(model, baseName, onProgress) {
    await preload(model);
    const S = 144;
    const canvas = document.createElement("canvas");
    const files = [];
    const n = model.slides.length;
    for (let i = 0; i < n; i++) {
      renderSlide(model.slides[i], canvas, S);
      const data = new Uint8Array(await (await canvasBlob(canvas, "image/jpeg", 0.92)).arrayBuffer());
      files.push({ name: `${baseName}/${String(i + 1).padStart(2, "0")}.jpg`, data });
      if (onProgress) onProgress(i + 1, n);
      await pause();
    }
    canvas.width = canvas.height = 1;
    return zip(files);
  }

  // ------------------------------------------------------------ Word (.docx)
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");
  const str = (x) => (typeof x === "string" ? x.trim() : x == null ? "" : String(x).trim());
  const arr = (x) => (Array.isArray(x) ? x : []);

  function run(text, o) {
    o = o || {};
    let pr = "";
    if (o.bold) pr += "<w:b/>";
    if (o.italic) pr += "<w:i/>";
    if (o.size) pr += `<w:sz w:val="${o.size * 2}"/><w:szCs w:val="${o.size * 2}"/>`;
    if (o.color) pr += `<w:color w:val="${o.color}"/>`;
    return `<w:r>${pr ? `<w:rPr>${pr}</w:rPr>` : ""}<w:t xml:space="preserve">${esc(text)}</w:t></w:r>`;
  }
  function para(runs, o) {
    o = o || {};
    let pr = "";
    if (o.style) pr += `<w:pStyle w:val="${o.style}"/>`;
    if (o.keepNext) pr += "<w:keepNext/>";
    if (o.pageBreak) pr += "<w:pageBreakBefore/>";
    if (o.spacing) pr += `<w:spacing ${o.spacing}/>`;
    if (o.indent) pr += `<w:ind ${o.indent}/>`;
    if (o.align) pr += `<w:jc w:val="${o.align}"/>`;
    const body = Array.isArray(runs) ? runs.join("") : typeof runs === "string" ? run(runs) : "";
    return `<w:p>${pr ? `<w:pPr>${pr}</w:pPr>` : ""}${body}</w:p>`;
  }
  const bullet = (runs) => para([run("•  "), ...(Array.isArray(runs) ? runs : [run(runs)])],
    { indent: 'w:left="567" w:hanging="283"', align: "both" });
  const text = (s) => para(s, { align: "both", indent: 'w:firstLine="709"' });

  function table(rows, colW, opts) {
    opts = opts || {};
    const grid = colW.map((w) => `<w:gridCol w:w="${w}"/>`).join("");
    const border = opts.noBorder ? "nil" : "single";
    const body = rows.map((row, ri) => {
      const cells = row.map((c, ci) => {
        const cell = typeof c === "object" && c ? c : { t: c };
        const side = (v) => ["top", "left", "bottom", "right"].map((k) => v === "nil" ? `<w:${k} w:val="nil"/>`
          : `<w:${k} w:val="single" w:sz="8" w:space="0" w:color="333333"/>`).join("");
        const borders = cell.empty ? `<w:tcBorders>${side("nil")}</w:tcBorders>`
          : cell.box ? `<w:tcBorders>${side("single")}</w:tcBorders>` : "";
        const shade = cell.fill ? `<w:shd w:val="clear" w:color="auto" w:fill="${cell.fill}"/>` : "";
        const va = opts.vTop ? "" : '<w:vAlign w:val="center"/>';
        const pr = `<w:tcPr><w:tcW w:w="${colW[ci]}" w:type="dxa"/>${borders}${shade}${va}</w:tcPr>`;
        const p = cell.raw || para([run(str(cell.t), { bold: cell.bold || (opts.header && ri === 0), size: opts.size })],
          { spacing: 'w:before="0" w:after="0" w:line="240" w:lineRule="auto"', align: opts.center ? "center" : null });
        return `<w:tc>${pr}${p}</w:tc>`;
      }).join("");
      const trPr = opts.rowH ? `<w:trPr><w:trHeight w:val="${opts.rowH}" w:hRule="exact"/><w:cantSplit/></w:trPr>`
        : (opts.header && ri === 0 ? "<w:trPr><w:tblHeader/></w:trPr>" : "");
      return `<w:tr>${trPr}${cells}</w:tr>`;
    }).join("");
    const b = (side) => `<w:${side} w:val="${border}" w:sz="4" w:space="0" w:color="808080"/>`;
    const borders = `<w:tblBorders>${["top", "left", "bottom", "right", "insideH", "insideV"].map(b).join("")}</w:tblBorders>`;
    const align = opts.center ? '<w:jc w:val="center"/>' : "";
    const mar = opts.rowH ? '<w:tblCellMar><w:left w:w="20" w:type="dxa"/><w:right w:w="20" w:type="dxa"/></w:tblCellMar>'
      : '<w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="90" w:type="dxa"/><w:bottom w:w="40" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar>';
    return `<w:tbl><w:tblPr><w:tblW w:w="${colW.reduce((a, c) => a + c, 0)}" w:type="dxa"/>${align}${borders}<w:tblLayout w:type="fixed"/>${mar}</w:tblPr><w:tblGrid>${grid}</w:tblGrid>${body}</w:tbl>`
      + para("", { spacing: 'w:before="0" w:after="0"' });
  }

  const TEXT_W = 9355;   // A4 kengligi minus 3 sm + 1.5 sm hoshiya (twip)

  function slideBody(sd, L) {
    const out = [];
    const t = sd.type;
    if (t === "bullets") {
      for (const b of arr(sd.bullets)) if (str(b)) out.push(bullet(str(b)));
      if (str(sd.example)) out.push(para([run(`${L.example}: `, { bold: true, italic: true }), run(str(sd.example), { italic: true })],
        { align: "both", indent: 'w:firstLine="709"' }));
    } else if (t === "cards") {
      for (const c of arr(sd.cards)) {
        if (!c) continue;
        out.push(bullet([run(str(c.title), { bold: true }), run(str(c.text) ? ` — ${str(c.text)}` : "")]));
      }
    } else if (t === "table") {
      const cols = arr(sd.columns).map(str);
      const rows = arr(sd.rows).map((r) => arr(r).map(str));
      const nc = Math.max(cols.length, ...rows.map((r) => r.length), 1);
      const pad = (r) => Array.from({ length: nc }, (_, i) => r[i] || "");
      const all = [pad(cols), ...rows.map(pad)];
      const colW = Array.from({ length: nc }, () => Math.floor(TEXT_W / nc));
      if (cols.length || rows.length) out.push(table(all.map((r, ri) => r.map((c) => ({ t: c, fill: ri === 0 ? "E7ECF3" : null }))),
        colW, { header: !!cols.length, size: 12 }));
      if (str(sd.note)) out.push(text(str(sd.note)));
    } else if (t === "comparison") {
      for (const side of ["left", "right"]) {
        if (str(sd[side + "_title"])) out.push(para([run(str(sd[side + "_title"]), { bold: true })], { keepNext: true, indent: 'w:firstLine="709"' }));
        for (const x of arr(sd[side + "_items"])) if (str(x)) out.push(bullet(str(x)));
      }
    } else if (t === "stats") {
      for (const s of arr(sd.stats)) {
        if (!s) continue;
        out.push(bullet([run(str(s.value), { bold: true }), run(str(s.label) ? ` — ${str(s.label)}` : "")]));
      }
      if (str(sd.source)) out.push(para([run(`${L.source}: ${str(sd.source)}`, { italic: true, size: 12 })], { indent: 'w:firstLine="709"' }));
    } else if (t === "chart") {
      const labels = arr(sd.labels).map(str), values = arr(sd.values);
      const head = [str(sd.series) || "—", str(sd.unit) ? `${L.value || "Qiymat"} (${str(sd.unit)})` : (L.value || "Qiymat")];
      const rows = [head].concat(labels.map((l, i) => [l, String(values[i] == null ? "" : values[i]).replace(".", ",")]));
      out.push(table(rows.map((r, ri) => r.map((c) => ({ t: c, fill: ri === 0 ? "E7ECF3" : null }))), [Math.floor(TEXT_W * 0.62), Math.floor(TEXT_W * 0.38)], { header: true, size: 12 }));
      if (str(sd.note)) out.push(text(str(sd.note)));
      if (str(sd.source)) out.push(para([run(`${L.source}: ${str(sd.source)}`, { italic: true, size: 12 })], { indent: 'w:firstLine="709"' }));
    } else if (t === "timeline") {
      for (const e of arr(sd.events)) {
        if (!e) continue;
        out.push(bullet([run(str(e.date), { bold: true }), run(str(e.text) ? ` — ${str(e.text)}` : "")]));
      }
    } else if (t === "quote") {
      if (str(sd.quote)) out.push(para([run(`“${str(sd.quote).replace(/^["“«]+|["”»]+$/g, "")}”`, { italic: true })],
        { indent: 'w:left="709" w:right="709"', align: "both" }));
      if (str(sd.author)) out.push(para([run(`— ${str(sd.author)}`, { bold: true })], { align: "right" }));
    }
    // noma'lum turdagi slayd — undagi barcha matnni yig'amiz
    if (!out.length) {
      const bits = [];
      const walk = (v) => {
        if (typeof v === "string") { if (v.trim() && v !== sd.title && v !== sd.type) bits.push(v.trim()); }
        else if (Array.isArray(v)) v.forEach(walk);
        else if (v && typeof v === "object") Object.values(v).forEach(walk);
      };
      walk(sd);
      for (const b of bits.slice(0, 12)) out.push(bullet(b));
    }
    return out;
  }

  function crosswordXml(cw, L, Deck, only) {
    const lay = Deck.layoutCrossword(cw.words, 15);
    if (!lay || !lay.placed || !lay.placed.length) return "";
    const cell = Math.min(560, Math.floor(TEXT_W / lay.width));
    const map = new Map(lay.cells.map(([r, c, tok]) => [`${r},${c}`, tok]));
    const nums = new Map(lay.starts.map((s) => [`${s.row},${s.col}`, s.num]));
    const grid = (withLetters) => {
      const rows = [];
      for (let r = 0; r < lay.height; r++) {
        const row = [];
        for (let c = 0; c < lay.width; c++) {
          const tok = map.get(`${r},${c}`);
          if (!tok) { row.push({ empty: true, t: "" }); continue; }
          const num = nums.get(`${r},${c}`);
          const runs = [];
          if (num) runs.push(run(String(num), { size: 7 }));
          if (withLetters) runs.push(run((num ? " " : "") + tok.toUpperCase(), { bold: true, size: 10 }));
          row.push({ box: true, raw: para(runs, { spacing: 'w:before="0" w:after="0" w:line="200" w:lineRule="auto"' }) });
        }
        rows.push(row);
      }
      return table(rows, Array.from({ length: lay.width }, () => cell), { rowH: cell, center: true, vTop: true, noBorder: true });
    };
    const clueList = (dir) => lay.placed.filter((p) => p.dir === dir).sort((a, b) => a.num - b.num)
      .map((p) => para([run(`${p.num}. `, { bold: true }), run(`${p.clue} (${p.t.length})`)], { indent: 'w:left="567" w:hanging="425"' }));
    const xml = [];
    if (!only) xml.push(para(`${L.cw}`, { style: "Heading1", pageBreak: true }));
    xml.push(grid(false));
    xml.push(para(L.across, { style: "Heading2" }), ...clueList("a"));
    xml.push(para(L.down, { style: "Heading2" }), ...clueList("d"));
    xml.push(para(L.cwAnswers, { style: "Heading1", pageBreak: true }));
    xml.push(grid(true));
    const ans = lay.placed.slice().sort((a, b) => (a.dir === b.dir ? a.num - b.num : a.dir === "a" ? -1 : 1))
      .map((p) => `${p.num}${p.dir === "a" ? "→" : "↓"} ${p.answer.toUpperCase()}`);
    xml.push(text(ans.join(";  ")));
    return xml.join("");
  }

  function toDocx(data, opts, Deck) {
    opts = opts || {};
    const L = Deck.LABELS[opts.lang] || Deck.LABELS.uz;
    const title = str(data.title) || "Taqdimot";
    const body = [];
    body.push(para([run(title, { bold: true, size: 20 })], { align: "center", spacing: 'w:before="0" w:after="240"' }));
    if (str(opts.subject)) body.push(para([run(L.subject(str(opts.subject)))], { align: "center" }));
    if (str(opts.author)) body.push(para([run(`${L.author}: `, { bold: true }), run(str(opts.author))], { align: "right", spacing: 'w:before="240" w:after="240"' }));

    const plan = arr(data.plan).map(str).filter(Boolean);
    if (plan.length) {
      body.push(para(L.plan, { style: "Heading1" }));
      plan.forEach((p, i) => body.push(para([run(`${i + 1}. ${p}`)], { indent: 'w:left="567" w:hanging="283"' })));
    }
    arr(data.slides).forEach((sd, i) => {
      if (!sd || typeof sd !== "object") return;
      const ttl = str(sd.title) || (sd.type === "quote" ? "—" : "");
      body.push(para(`${i + 1}. ${ttl}`, { style: "Heading1" }));
      body.push(...slideBody(sd, L));
    });
    const concl = arr(data.conclusion).map(str).filter(Boolean);
    if (concl.length) {
      body.push(para(L.conclusion, { style: "Heading1" }));
      body.push(text(concl.join(" ")));
    }
    const refs = arr(data.references).map(str).filter(Boolean);
    if (refs.length) {
      body.push(para(L.references, { style: "Heading1" }));
      refs.forEach((r, i) => body.push(para([run(`${i + 1}. ${r}`)], { indent: 'w:left="567" w:hanging="425"', align: "both" })));
    }
    if (data.crossword && arr(data.crossword.words).length) body.push(crosswordXml(data.crossword, L, Deck, !!opts.cwOnly));

    const NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
    const sect = '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>';
    const documentXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document ${NS}><w:body>${body.join("")}${sect}</w:body></w:document>`;
    const stylesXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="Times New Roman" w:cs="Times New Roman"/><w:sz w:val="28"/><w:szCs w:val="28"/><w:lang w:val="uz-Latn-UZ"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:before="0" w:after="60" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="60"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>`;
    const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    const coreXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>${esc(title)}</dc:title><dc:creator>${esc(str(opts.author) || "Taqdimot")}</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">${now}</dcterms:created></cp:coreProperties>`;
    const files = [
      { name: "[Content_Types].xml", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>` },
      { name: "_rels/.rels", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>` },
      { name: "word/_rels/document.xml.rels", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>` },
      { name: "word/document.xml", data: documentXml },
      { name: "word/styles.xml", data: stylesXml },
      { name: "docProps/core.xml", data: coreXml },
    ];
    return zip(files, "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  }

  // Faqat krossvord uchun Word hujjat
  function crosswordDocx(data, opts, Deck) {
    const L = Deck.LABELS[opts.lang] || Deck.LABELS.uz;
    return toDocx({ title: `${L.cw} — ${str(data.title)}`, crossword: data.crossword }, Object.assign({}, opts, { cwOnly: true }), Deck);
  }

  const api = { renderSlide, toPdf, toImagesZip, toDocx, crosswordDocx, zip, crc32 };
  root.SlideExport = api;
})(typeof window !== "undefined" ? window : globalThis);
