# Kino-video bot

Telegram bot: kinoni yuborasiz → bot 1 daqiqalik o‘zbekcha hikoya matnini yozadi → siz o‘qib, ovozli xabar yuborasiz → bot sahnalarni qirqib, rang berib, ovozingiz bilan tayyor video yuboradi.

## Qanday ishlaydi

1. Kinoni botga fayl qilib yuborasiz yoki kanaldan forward qilasiz (2 GB gacha).
2. Bot kino nomini va kodini so‘raydi. Fayl izohiga `Nomi, 125` deb yozsangiz, so‘ramaydi.
3. Gemini kinoning ovozini «eshitib», qahramonlarni, qisqacha mazmunni va muhim lahzalarni vaqti bilan topadi, so‘ng kinoni ko‘rmagan odam ham tushunadigan hikoya matnini yozadi (standart 2 daqiqa, `/uzunlik` bilan 1–3 daqiqa).
4. Hikoya eng qiziq joyida to‘xtaydi. Oxirida bot yoki kod aytilmaydi: kod butun video davomida pastda yoziladi.
5. Siz matnni o‘qib, ovozli xabar yuborasiz. Har gapdan keyin bir lahza to‘xtasangiz, sahnalar aniqroq mos tushadi.
6. Bot har gapga mos sahnalarni oladi: har biri ~2 soniya, boshida tezroq almashadi, sahna almashgan joydan boshlanadi.
7. Video namunadek bo‘ladi: kvadrat 1080×1080 (720p kinodan 720×720), yengil kino rangi va vinyetka. Eng pastda «Kino kodi: 125» (anime bo‘lsa «Anime kodi: 125»). Kino yoki animeligini Gemini o‘zi aniqlaydi, matn tagidagi «🏷 … 🔁» tugmasi bilan almashtirsa bo‘ladi.
8. Ovoz aralashtiriladi: sizning ovozingiz baland, kinoning ovozi orqada past eshitiladi, siz gapirganda yanada pasayadi. Balandlik Instagram me’yoriga (−14 LUFS) keltiriladi.
9. Tayyor videoni yuboradi. «📐 9:16» tugmasi Reels uchun vertikal variantini yasaydi.

Matnni o‘zgartirmoqchi bo‘lsangiz, yangi matnni yozib yuborasiz. «🔄 Boshqa matn» tugmasi boshqa variant yozdiradi. Yangi ovoz yuborsangiz, video qaytadan yig‘iladi.

Buyruqlar: `/start`, `/bekor`, `/kod 125`.

## Oddiy video (ovozsiz, musiqali)

Matn tagidagi yoki tayyor video tagidagi «🎵 Oddiy video» tugmasi:

1. Gemini kinoning eng qiziq uzluksiz sahnasini tanlaydi: 45–120 soniya (uzunligini sahnaning o‘ziga qarab), oxirini oshkor qilmaydi, kino boshidagi logotiplar va oxiridagi qismdan olmaydi.
2. Sahna atrofidagi ovozni eshitib, aniq boshi va oxirini topadi: gap o‘rtasida boshlanmaydi va tugamaydi. Kesish joyi kadr almashgan, jim nuqtaga yopishtiriladi.
3. Video effektsiz: rang berilmaydi, vinyetka yo‘q, kino qanday bo‘lsa shunday (kvadrat qilib qirqiladi). Pastda «Kino kodi: 125» (yoki «Anime kodi»).
4. Ovoz: kinoning o‘z ovozi baland, kutubxonadan mos qo‘shiq orqada past (eng qiziq joyidan boshlanadi). Kino ovozi baland joylarda qo‘shiq biroz pasayadi.
5. Instagram uchun alohida tavsif shu sahnaga qarab yoziladi.

Tugmalar: «🎞 Boshqa sahna», «🎵 Boshqa qo‘shiq», «🔇 Qo‘shiqsiz», «📐 9:16» (oddiy videoda tepasi va pasti qora, xiralashtirilgan fon yo‘q).

## Manga-video (`/manga`)

Manga yoki webtoon sahifalaridan, o‘zingiz yozgan o‘zbekcha matn bilan vertikal (9:16, 720×1280) video:

1. `/manga` deb yozing, so‘ng sahifalarni yuboring: rasm (yaxshisi «fayl» qilib), PDF yoki ZIP / CBZ. Webtoon bo‘laklari ketma-ket yuboriladi, bot ularni o‘zi ulaydi.
2. «✅ Tugadi» ni bosing. Bot panellarni oq/qora oraliqlar bo‘yicha qirqib, raqamlangan rasm yuboradi.
3. Matnni yuboring: **har panel uchun bitta qator** (1-qator → 1-panel). Matn kerak bo‘lmasa qatorga `-` yozing. Qatorlarni raqam bilan ham yozsa bo‘ladi (`3. matn`), shunda faqat shu panellarga qo‘yiladi. Qator kam bo‘lsa, qolgan panellar matnsiz chiqadi (bot so‘raydi). Matn inglizcha yoki boshqa tilda bo‘lsa, Gemini uni o‘zbekchaga (lotin) o‘giradi va avval sizga ko‘rsatadi: «🎬 Yasash» yoki «↩️ Asl matn bilan yasash». O‘zbekcha matn o‘zgarmaydi.
4. Bot videoni yasaydi: panel sekin yaqinlashadi, uzun panel yuqoridan pastga suriladi, eni kichik panelning orqasida xira fon bo‘ladi. Yuqori chapda Telegram belgisi va **MANHORA** yozuvi, pastda qalin oq subtitr. Qo‘shiq kutubxonadan olinadi (kino ovozi yo‘q).
5. **Ovoz** (matndan keyin tanlanadi): «🎬 Ovozsiz yasash»; «🎙 ElevenLabs bilan o‘qit» — bot `/ovoz` da tanlangan diktor bilan o‘qitadi; yoki audioni shu yerga yuborasiz (Blox lab, o‘z ovozingiz — forward ham bo‘ladi). Bot Gemini bilan har qator ovozda qayerda boshlanishini topadi (Gemini javob bermasa — pauzalar bo‘yicha), chegarani gaplar orasidagi pauzaga suradi: har panel o‘z gapi davomida turadi, matnsiz panel 1,3 s. Ovoz baland, qo‘shiq ~15 dB past va gapirganda yana pasayadi. «📋 Ovoz uchun matn» — Blox lab'ga tashlash uchun toza matn (tartib bilan). Uzun matnni Telegram bir necha xabarga bo‘lsa ham bot ularni birlashtiradi (2 soniya kutadi).
6. Tugmalar: «🎵 Boshqa qo‘shiq», «🔇 Qo‘shiqsiz», «🔄 Matnni o‘zgartirish». Panellar teskari tartibda chiqsa (yapon manga), tugma bilan o‘qish yo‘nalishini almashtiring.

Sozlamalar (ixtiyoriy): `MANGA_BADGE` (yuqoridagi yozuv, standart `MANHORA`), `MANGA_FPS` (24), `MANGA_W` (720), `MANGA_MIN_SEC` (2.2), `MANGA_SEC_PER_CHAR` (0.075), `MANGA_MAX_PANELS` (200).
PDF sahifalari rasmga aylantiriladi (300 sahifagacha). Juda uzun sahifalar (webtoon) va uzun rasmlar bo‘laklab o‘qiladi (bepul serverda xotira yetishi uchun), juda baland panel bir necha bo‘lakka bo‘linadi. Seans xotirada turadi: server qayta ishga tushsa, `/manga` dan boshlanadi.

## Sozlamalar (Render → Environment)

| Nomi | Nima | Majburiy |
|---|---|---|
| `BOT_TOKEN` | @BotFather bergan token | ha |
| `API_ID`, `API_HASH` | my.telegram.org → API development tools. 20 MB dan katta fayllar uchun | ha |
| `GEMINI_API_KEY` | aistudio.google.com kaliti | ha |
| `OWNER_IDS` | Botdan faqat shu Telegram ID lar foydalanadi (vergul bilan) | tavsiya |
| `WATERMARK` | Kod ustidagi qo‘shimcha yozuv, masalan `BehruzFilm` (standart: yo‘q; `-` — o‘chirilgan) | yo‘q |
| `VIDEO_SIZE` | Eng katta o‘lcham (standart 1080) | yo‘q |
| `VIDEO_LEN` | Standart uzunlik, soniya (120) | yo‘q |
| `CODE_LABEL_KINO`, `CODE_LABEL_ANIME` | Pastdagi yozuv (standart «Kino kodi: {kod}», «Anime kodi: {kod}») | yo‘q |
| `CTA_SPOKEN` | `1` bo‘lsa, hikoya oxiriga `CTA_TEXT` gapi qo‘shiladi (standart yo‘q) | yo‘q |
| `GEMINI_MODEL` | Modellar ro‘yxati, vergul bilan (birinchisi ishlamasa keyingisi) | yo‘q |
| `FINAL_PRESET` | x264 tezligi: `veryfast` (standart) yoki `superfast` | yo‘q |
| `PLAIN_MIN_SEC`, `PLAIN_MAX_SEC` | Oddiy video uzunligi chegarasi (45 va 120 soniya) | yo‘q |
| `PLAIN_MUSIC_DB` | Oddiy videoda qo‘shiq kino ovozidan necha dB past (standart −9) | yo‘q |

## Server

Render’da Docker xizmat sifatida ishlaydi. Bepul tarif (0.1 CPU) da 2 daqiqalik video taxminan 15–25 daqiqada yig‘iladi (720p kinodan tezroq). Starter tarifi (0.5 CPU, ~$7/oy) 5 barobar tez: 3–5 daqiqa.
Bepul tarifda server 15 daqiqa jim tursa uxlaydi. Ish ketayotganda bot o‘zini uyg‘oq ushlab turadi. Kino fayli faqat ish davomida saqlanadi, keyin o‘chiriladi.
