"""Video yig'ish: ffmpeg yordamchilari, ovozni gaplarga bo'lish, sahnalarni tanlash va yakuniy videoni yasash."""
import asyncio
import glob
import json
import math
import os
import re
import wave

import numpy as np

FPS = 24
SIZE = int(os.environ.get("VIDEO_SIZE", "1080"))  # kvadrat tomoni: 1080x1080 (Instagram uchun eng yaxshi sifat)


def _font(names):
    for p in names:
        if os.path.exists(p):
            return p
    return names[0]


FONT_BOLD = os.environ.get("FONT_BOLD") or _font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
])

# Namunadagidek: biroz qoraytirilgan, sovuq (ko'kish) soyalar, rangi pastroq, kontrast va vinyetka
# Namunadagidek kino ko'rinishi, lekin yorqinroq va tiniqroq: yengil kontrast, sovuqroq ranglar, yumshoq vinyetka, aniqlik
GRADE = os.environ.get("VIDEO_GRADE") or (
    "eq=contrast=1.06:brightness=-0.01:saturation=0.92,"
    "lutyuv=u='clip(val+3,0,255)':v='clip(val-2,0,255)',"
    "vignette=angle=PI/6"
)


THREADS = os.environ.get("FFMPEG_THREADS", "2")  # bepul serverda xotira tejash uchun
FINAL_PRESET = os.environ.get("FINAL_PRESET", "veryfast")
CRF = os.environ.get("VIDEO_CRF", "19")
MAX_MB = float(os.environ.get("MAX_VIDEO_MB", "45"))  # Telegram bot 50 MB gacha video yubora oladi


def video_maxrate(total_sec):
    """Butun video MAX_MB dan oshmasligi uchun video bitreyti chegarasi (kbit/s)."""
    kbps = MAX_MB * 8 * 1024 / max(20.0, float(total_sec)) - 200
    return int(max(1800, min(9000, kbps)))


class FFError(RuntimeError):
    pass


async def run(cmd, timeout=None):
    """Buyruqni ishga tushiradi; xato bo'lsa oxirgi qatorlari bilan FFError."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as e:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        if isinstance(e, asyncio.CancelledError):
            raise
        raise FFError(f"{os.path.basename(cmd[0])}: vaqt tugadi")
    if proc.returncode != 0:
        tail = err.decode("utf-8", "ignore").strip().splitlines()[-5:]
        raise FFError(f"{os.path.basename(cmd[0])} xato ({proc.returncode}): " + " | ".join(tail))
    return out, err


async def probe(path):
    out, _ = await run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height,sample_aspect_ratio:stream_disposition=attached_pic",
        "-of", "json", path,
    ], timeout=180)
    j = json.loads(out or b"{}")
    dur = float((j.get("format") or {}).get("duration") or 0)
    streams = j.get("streams") or []
    vids = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    has_a = any(s.get("codec_type") == "audio" for s in streams)
    side = 0
    if vids:
        w, h = int(vids[0].get("width") or 0), int(vids[0].get("height") or 0)
        try:
            num, den = (vids[0].get("sample_aspect_ratio") or "1:1").split(":")
            sar = float(num) / float(den) if float(den) else 1.0
        except ValueError:
            sar = 1.0
        side = int(min(w * (sar or 1.0), h))
    return {"duration": dur, "video": bool(vids), "audio": has_a, "side": side}


def out_size(side):
    """Kino sifatiga mos chiqish o'lchami: 1080p kinodan 1080x1080, 720p kinodan 720x720 (sun'iy kattalashtirmaslik)."""
    return SIZE if side >= SIZE * 0.92 or side == 0 else (720 if SIZE > 720 else SIZE)


async def audio_codec(movie):
    try:
        out, _ = await run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=codec_name", "-of", "default=nw=1:nk=1", movie], timeout=120)
        return out.decode().strip().lower()
    except FFError:
        return ""


async def extract_audio_chunks(movie, outdir, duration, max_chunk=4500):
    """Kinoning ovozini Gemini uchun bo'laklarga ajratadi (har biri ≤ 75 daqiqa).
    AAC/MP3 bo'lsa qayta kodlamasdan nusxalanadi (deyarli CPU sarflamaydi).
    Qaytaradi: [(fayl, mime, boshlanish_soniya, uzunlik_soniya), ...]"""
    n = max(1, math.ceil(duration / max_chunk))
    chunk = math.ceil(duration / n) + 1
    for old in glob.glob(os.path.join(outdir, "audio_*")):
        os.remove(old)
    codec = await audio_codec(movie)
    if codec in ("aac", "mp3"):
        ext, mime = (".aac", "audio/aac") if codec == "aac" else (".mp3", "audio/mpeg")
        enc = ["-c:a", "copy"]
        pre = []
    else:
        ext, mime = ".mp3", "audio/mpeg"
        enc = ["-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "24k"]
        pre = ["-downmix", "mono"] if codec in ("ac3", "eac3") else []
    pattern = os.path.join(outdir, "audio_%02d" + ext)

    def cmd(pre_opts):
        return ["ffmpeg", "-nostdin", "-v", "error", "-y", "-vn", "-sn", "-dn", *pre_opts, "-i", movie,
                "-map", "0:a:0", *enc, "-f", "segment", "-segment_time", str(chunk), "-reset_timestamps", "1",
                pattern]
    try:
        await run(cmd(pre), timeout=4 * 3600)
    except FFError:
        if not pre:
            raise
        await run(cmd([]), timeout=4 * 3600)
    files = sorted(glob.glob(os.path.join(outdir, "audio_*" + ext)))
    res = []
    for k, f in enumerate(files):
        start = k * chunk
        length = min(chunk, max(1.0, duration - start))
        if os.path.getsize(f) < 2000:  # bo'sh qoldiq
            continue
        res.append((f, mime, float(start), float(length)))
    return res


_PTS = re.compile(r"pts_time:\s*(-?[\d.]+)")


async def scan_keyframes(movie, t0, span):
    """[t0, t0+span] oralig'idagi kalit kadrlar (odatda sahna almashgan joylar) va ularning yorqinligi.
    Faqat kalit kadrlar o'qiladi, shuning uchun juda arzon. Qaytaradi: [(vaqt, yorqinlik 0–255), ...]"""
    t0 = max(0.0, t0)
    try:
        out, err = await run([
            "ffmpeg", "-nostdin", "-v", "info", "-threads", "1", "-skip_frame", "nokey",
            "-ss", f"{t0:.3f}", "-t", f"{span:.3f}", "-copyts", "-i", movie,
            "-map", "0:V:0", "-vf", "scale=32:18,format=gray,showinfo", "-fps_mode", "passthrough",
            "-f", "rawvideo", "-",
        ], timeout=120)
    except FFError:
        return []
    times = [float(x) for x in _PTS.findall(err.decode("utf-8", "ignore"))]
    fsz = 32 * 18
    res = []
    for k, t in enumerate(times):
        chunk = out[k * fsz:(k + 1) * fsz]
        if len(chunk) == fsz and t0 - 0.05 <= t <= t0 + span + 0.05:
            res.append((t, sum(chunk) / fsz))
    return res


def _overlaps(t, d, used, gap=0.3):
    return any(t < ue + gap and t + d > us - gap for us, ue in used)


async def choose_start(movie, t, d, duration, used, min_luma=14.0):
    """Sahnani kalit kadrga (sahna almashgan joyga) yopishtiradi, butunlay qora kadrlardan va
    oldin ishlatilgan sahnalardan qochadi. Kalit kadrdan boshlangan sahna tez qirqiladi."""
    limit = max(0.0, duration - d - 0.3)
    best_dark = None
    for a, span in ((t - 2.5, 8.0), (t + 5.5, 8.0), (t - 10.0, 7.5)):
        cands = await scan_keyframes(movie, a, span)
        ok = []
        for kt, lum in cands:
            if kt > limit or _overlaps(kt, d, used):
                continue
            if lum >= min_luma:
                ok.append((abs(kt - t), kt))
            elif best_dark is None or lum > best_dark[0]:
                best_dark = (lum, kt)
        if ok:
            return min(ok)[1] + 0.01
    if best_dark is not None:
        return best_dark[1] + 0.01
    return min(max(0.0, t), limit)


# ---------------- Ovozni gaplarga bo'lish ----------------

async def audio_duration(path):
    try:
        out, _ = await run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "default=nw=1:nk=1", path], timeout=60)
        return float(out.decode().strip() or 0)
    except (FFError, ValueError):
        return 0.0


async def concat_voice(parts, out, gap=0.4):
    """Bir nechta ovozli xabarni bitta faylga birlashtiradi (orasida qisqa pauza)."""
    n = len(parts)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    for p in parts:
        cmd += ["-i", p]
    fc = "".join(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,apad=pad_dur={gap}[a{i}];"
                 for i in range(n))
    fc += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    cmd += ["-filter_complex", fc, "-map", "[out]", "-c:a", "libopus", "-b:a", "64k", out]
    await run(cmd, timeout=600)
    return out


async def voice_to_wav(src, dst):
    await run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
               "-c:a", "pcm_s16le", dst], timeout=600)


def text_weight(text):
    letters = re.sub(r"[\W_]+", "", text or "", flags=re.UNICODE)
    return len(letters) + 4


def speech_layout(wav_path, hop_s=0.02):
    """Ovozli xabarda nutq qayerda boshlanib tugashini va gaplar orasidagi pauzalarni topadi."""
    with wave.open(wav_path) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    hop = max(1, int(sr * hop_s))
    n = len(x) // hop
    if n < 25:
        raise ValueError("Ovozli xabar juda qisqa")
    fr = x[: n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1) + 1e-12) + 1e-9)
    lo, hi = float(np.percentile(db, 10)), float(np.percentile(db, 95))
    if hi - lo < 6:
        raise ValueError("Ovozli xabarda nutq eshitilmadi")
    thr = lo + (hi - lo) * 0.30
    voiced = db > thr
    # 60 ms dan qisqa uzilishlarni to'ldirish
    gap = 3
    i = 0
    while i < n:
        if not voiced[i]:
            j = i
            while j < n and not voiced[j]:
                j += 1
            if 0 < i and j < n and (j - i) <= gap:
                voiced[i:j] = True
            i = j
        else:
            i += 1
    idx = np.flatnonzero(voiced)
    if len(idx) == 0:
        raise ValueError("Ovozli xabarda nutq eshitilmadi")
    # nutq balandligi (dBFS): yakuniy ovozni bir xil balandlikka keltirish uchun
    level = float(10 * np.log10(np.mean(10 ** (db[voiced] / 10)) + 1e-12))
    start = max(0.0, idx[0] * hop_s - 0.08)
    end = min(n * hop_s, (idx[-1] + 1) * hop_s + 0.12)
    pauses = []
    a, b = int(start / hop_s), int(end / hop_s)
    run_start = None
    for k in range(a, b):
        if not voiced[k]:
            if run_start is None:
                run_start = k
        elif run_start is not None:
            if (k - run_start) * hop_s >= 0.16:
                pauses.append((run_start * hop_s, k * hop_s))
            run_start = None
    return start, end, pauses, level


def sentence_bounds(start, end, pauses, weights):
    """Har bir gap ovozning qaysi qismiga to'g'ri kelishini topadi: harflar soniga qarab taxmin qilib,
    eng yaqin pauzaga yopishtiradi. Qaytaradi: [(boshlanish, tugash), ...] (ovoz vaqti bo'yicha)."""
    k = len(weights)
    if k == 1:
        return [(start, end)]
    total = float(sum(weights))
    dur = end - start
    expected, cum = [], 0.0
    for w in weights[:-1]:
        cum += w
        expected.append(start + dur * cum / total)
    tol = max(1.0, 0.45 * dur / k)
    bounds, prev, used = [], start, set()
    for i, b in enumerate(expected):
        remaining = len(expected) - i
        best, best_score = None, -1e9
        for j, (ps, pe) in enumerate(pauses):
            if j in used:
                continue
            mid = (ps + pe) / 2
            if mid <= prev + 0.4 or mid >= end - 0.4 * remaining:
                continue
            dist = abs(mid - b)
            if dist > tol:
                continue
            score = (pe - ps) * 2.0 - dist
            if score > best_score:
                best, best_score = j, score
        if best is not None:
            used.add(best)
            bnd = (pauses[best][0] + pauses[best][1]) / 2
        else:
            bnd = min(max(b, prev + 0.4), end - 0.4 * remaining)
        bounds.append(bnd)
        prev = bnd
    edges = [start] + bounds + [end]
    return [(edges[i], edges[i + 1]) for i in range(k)]


# ---------------- Sahnalarni rejalash ----------------

def plan_shots(seg_durs, windows, duration, base_len=2.0):
    """Har bir gap uchun kinodan sahnalar (har biri ~2 soniya, birinchi gapda tezroq) tanlaydi.
    seg_durs: yakuniy videoda har bir gap davomiyligi; windows: [(boshlanish, tugash)] kino vaqtida."""
    lo_lim = min(20.0, duration * 0.01)
    hi_lim = max(lo_lim + 1.0, duration - 1.0)
    edges, cum = [0], 0.0
    for d in seg_durs:
        cum += d
        edges.append(int(round(cum * FPS)))
    shots, used = [], []
    last = len(seg_durs) - 1
    for i in range(len(seg_durs)):
        nfr = edges[i + 1] - edges[i]
        if nfr <= 0:
            continue
        shot_len = 1.15 if i == 0 else (base_len + 0.4 if i == last else base_len)
        n = max(1, int(round(nfr / (shot_len * FPS))))
        base, extra = divmod(nfr, n)
        lens = [base + (1 if j < extra else 0) for j in range(n)]
        ws, we = windows[i]
        need = sum(lens) / FPS * 1.6 + 2.0
        if we - ws < need:
            c = (ws + we) / 2
            ws, we = c - need / 2, c + need / 2
        if ws < lo_lim:
            we += lo_lim - ws
            ws = lo_lim
        if we > hi_lim:
            ws -= we - hi_lim
            we = hi_lim
        ws = max(0.0, ws)
        for j, fl in enumerate(lens):
            d = fl / FPS
            if n == 1:
                t = (ws + we) / 2 - d / 2
            else:
                t = ws + (we - ws - d) * j / (n - 1)
            t = min(max(0.0, t), max(0.0, duration - d - 0.5))
            for _ in range(4):
                if any(t < ue and t + d > us for us, ue in used):
                    t = min(max(0.0, duration - d - 0.5), t + d + 0.4)
                else:
                    break
            used.append((t, t + d))
            shots.append({"t": round(t, 3), "frames": fl, "sentence": i})
    return shots


def _crop(size):
    return ("crop=w='2*trunc(if(gt(iw*sar,ih),ih/sar,iw)/2)':h='2*trunc(if(gt(iw*sar,ih),ih,iw*sar)/2)',"
            f"scale={size}:{size}:flags=lanczos,setsar=1")


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _dt(textfile, size, x, y, extra=""):
    return (f"drawtext=fontfile='{FONT_BOLD}':textfile='{textfile}':expansion=none:fontsize={size}:x={x}:y={y}"
            + (":" + extra if extra else ""))


def text_overlays(workdir, watermark="", cta=None, fade=False, size=None):
    """Kadrga yoziladigan yozuvlar: pastda watermark, oxirgi gapda «KOD: 123»."""
    size = size or SIZE
    k = size / 720
    res = []
    sh = max(1, int(round(k)))
    if watermark:
        wm = _write(os.path.join(workdir, "wm.txt"), watermark)
        res.append(_dt(wm, int(26 * k), "(w-text_w)/2", f"h-text_h-{int(34 * k)}",
                       f"fontcolor=white@0.82:shadowcolor=black@0.7:shadowx={sh}:shadowy={sh}"))
    if cta and cta.get("line2"):
        alpha = ":alpha='min(1,t/0.35)'" if fade else ""
        y2 = int(size * 0.66)
        if cta.get("line1"):
            l1 = _write(os.path.join(workdir, "cta1.txt"), cta["line1"])
            res.append(_dt(l1, int(30 * k), "(w-text_w)/2", str(y2 - int(48 * k)),
                           f"fontcolor=white:shadowcolor=black@0.85:shadowx={2 * sh}:shadowy={2 * sh}" + alpha))
        l2 = _write(os.path.join(workdir, "cta2.txt"), cta["line2"])
        res.append(_dt(l2, int(60 * k), "(w-text_w)/2", str(y2),
                       f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw={int(18 * k)}" + alpha))
    return res


async def render_shot(movie, t, frames, out_video, out_audio, has_audio=True, overlays=(), maxrate=6000, size=None):
    """Bitta sahna: kvadrat qirqish + rang + yozuvlar, yakuniy sifatda. Ovozi alohida WAV ga."""
    d = frames / FPS
    vf = ",".join([_crop(size or SIZE), GRADE, f"fps={FPS}", "format=yuv420p", *overlays])
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", THREADS,
           "-threads", THREADS, "-ss", f"{t:.3f}", "-i", movie]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += [
        "-map", "0:V:0", "-vf", vf, "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-preset", FINAL_PRESET, "-crf", CRF, "-maxrate", f"{maxrate}k", "-bufsize", f"{2 * maxrate}k",
        "-g", str(FPS * 2), "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-threads", THREADS, "-video_track_timescale", "24000", out_video,
        "-map", "0:a:0" if has_audio else "1:a:0", "-vn",
        "-af", f"aresample=44100,aformat=sample_fmts=s16:channel_layouts=stereo,atrim=0:{d:.4f},apad=whole_dur={d:.4f}",
        "-c:a", "pcm_s16le", "-t", f"{d:.4f}", out_audio,
    ]
    await run(cmd, timeout=900)


async def concat(files, out, copy=True):
    lst = out + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in files:
            f.write(f"file '{os.path.abspath(p)}'\n")
    await run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
               "-c", "copy", out], timeout=900)
    os.remove(lst)


async def measure_lufs(path):
    try:
        _, err = await run(["ffmpeg", "-nostdin", "-v", "info", "-i", path, "-map", "0:a:0",
                            "-af", "ebur128=framelog=quiet", "-f", "null", "-"], timeout=600)
    except FFError:
        return None
    m = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", err.decode("utf-8", "ignore"))
    return float(m[-1]) if m else None


async def final_mux(video_path, bg_wav, voice_path, v_start, v_end, total, out, voice_db=-20.0, target=-14.0,
                    music=None, size=None):
    """Tayyor sahnalar (nusxalanadi, qayta kodlanmaydi) + sizning ovozingiz + kino ovozi + (bo'lsa) musiqa.
    Kino ovozi va musiqa siz gapirganda pasayadi. Umumiy balandlik Instagram me'yoriga (≈ -14 LUFS) keltiriladi.
    music: {"path": fayl, "start": soniya, "gain_db": dB}"""
    gain = max(-12.0, min(24.0, -15.0 - float(voice_db)))
    total = float(total)
    bg_vol = 0.20 if music else 0.30

    async def mux(extra):
        fc = (
            f"[2:a]atrim=start={v_start:.3f}:end={v_end:.3f},asetpts=PTS-STARTPTS,"
            "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,highpass=f=80,"
            f"volume={gain:.1f}dB,acompressor=threshold=0.08:ratio=3:attack=8:release=200:makeup=2,"
            f"apad=whole_dur={total:.3f},asplit={3 if music else 2}[vo][sc]{'[sc2]' if music else ''};"
            f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,volume={bg_vol}[bg0];"
            "[bg0][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[bga];"
        )
        if music:
            mg = max(-30.0, min(20.0, float(music.get("gain_db", 0.0))))
            fc += (
                f"[3:a]atrim=start={float(music.get('start', 0)):.2f},asetpts=PTS-STARTPTS,aresample=44100,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={mg:.1f}dB,"
                f"afade=t=in:st=0:d=1.5,afade=t=out:st={max(0.0, total - 2.5):.2f}:d=2.5,atrim=end={total:.3f}[mu0];"
                "[mu0][sc2]sidechaincompress=threshold=0.03:ratio=5:attack=30:release=600[mua];"
                "[vo][bga][mua]amix=inputs=3:duration=first:normalize=0,"
            )
        else:
            fc += "[vo][bga]amix=inputs=2:duration=first:normalize=0,"
        fc += f"volume={extra:.1f}dB,alimiter=limit=0.84:attack=5:release=60:level=false[a]"
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video_path, "-i", bg_wav, "-i", voice_path]
        if music:
            cmd += ["-stream_loop", "-1", "-i", music["path"]]
        cmd += ["-filter_complex", fc, "-map", "0:v:0", "-map", "[a]", "-t", f"{total:.3f}",
                "-c:v", "copy", "-c:a", "aac", "-aac_coder", "fast", "-b:a", "160k", "-ar", "44100",
                "-movflags", "+faststart", out]
        await run(cmd, timeout=1800)

    await mux(0.0)
    lufs = await measure_lufs(out)
    if lufs is not None and abs(lufs - target) > 1.0:
        await mux(max(-10.0, min(12.0, target - lufs)))
    return {"width": size or SIZE, "height": size or SIZE, "duration": total}


def vertical_dims(size):
    vh = int(round(size * 16 / 9 / 2)) * 2
    return size, vh, (vh - size) // 2


async def make_vertical(square_mp4, out, total=60.0, size=None):
    """9:16 (Reels) variant: kvadrat video o'rtada, orqasida uning xiralashtirilgan nusxasi."""
    size = size or SIZE
    vw, vh, top = vertical_dims(size)
    mr = video_maxrate(total)
    cw, ch = int(size * 0.47) // 2 * 2, int(size * 0.83) // 2 * 2
    # fonni kichraytirib xiralashtirish → kattalashtirish: chiroyli va juda arzon
    fc = (f"[0:v]split=2[va][vb];[va]crop={cw}:{ch}:(iw-{cw})/2:0,scale=144:256,boxblur=4:1,scale={vw}:{vh}:flags=bilinear,"
          f"eq=brightness=-0.12:saturation=0.8[bg];[bg][vb]overlay=0:{top},format=yuv420p[v]")
    await run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", THREADS, "-filter_complex_threads", THREADS,
        "-i", square_mp4, "-filter_complex", fc, "-map", "[v]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", FINAL_PRESET, "-crf", CRF, "-maxrate", f"{mr}k", "-bufsize", f"{2 * mr}k",
        "-profile:v", "high", "-threads", THREADS, "-c:a", "copy", "-movflags", "+faststart", out,
    ], timeout=1800)
    return {"width": vw, "height": vh}
